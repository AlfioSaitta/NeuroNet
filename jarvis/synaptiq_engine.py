"""
SynaptiqEngine — Wrapper asincrono thread-safe per Synaptiq (v2.0.5) in Jarvis.

Incapsula LadybugBackend + run_pipeline + hybrid search per fornire:
  - Analisi strutturale del codice (simboli, relazioni, comunità, dead code)
  - Ricerca ibrida (FTS + fuzzy)
  - Multi-hop BFS traversal
  - Contesto simboli (callers/callees/type refs)
  - Community detection e dead code detection

Usage:
    from synaptiq_engine import synaptiq_engine
    await synaptiq_engine.initialize()
    results = await synaptiq_engine.hybrid_search("telemetry collector")
    await synaptiq_engine.close()
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

from synaptiq.core.daemon.rwlock import AsyncRWLock
from synaptiq.core.graph.model import NodeLabel, RelType
from synaptiq.core.ingestion.pipeline import run_pipeline
from synaptiq.core.storage.ladybug_backend import LadybugBackend

logger = logging.getLogger(__name__)

_DEFAULT_STORAGE = "data/synaptiq/synaptiq.lb"


class SynaptiqEngine:
    """Wrapper asincrono thread-safe per Synaptiq.

    Tutti i metodi di lettura usano AsyncRWLock in modalità reader,
    consentendo accesso concorrente multiplo. initialize() e analyze()
    usano la modalità writer per accesso esclusivo.
    """

    # Cooldown minimo tra analisi incrementali dello stesso progetto (secondi)
    _ANALYSIS_COOLDOWN = 30

    def __init__(
        self,
        storage_path: str = "",
        embedding_tier: str = "quality",
    ) -> None:
        self.storage_path: str = storage_path or _DEFAULT_STORAGE
        self.embedding_tier: str = embedding_tier
        self._storage: LadybugBackend | None = None
        self._rwlock = AsyncRWLock()
        self._analysis_lock = asyncio.Lock()
        self._initialized = False
        self._last_analyze_duration: float = 0.0
        self._last_analyze_result: dict[str, Any] = {}
        # Watchdog integration — debounce per-project
        self._last_analysis_time: dict[str, float] = {}
        self._pending_tasks: dict[str, asyncio.Task] = {}
        self._pending_requests: set[str] = set()

    @property
    def is_initialized(self) -> bool:
        """Whether initialize() has completed successfully."""
        return self._initialized

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Apre/crea LadybugDB. Thread-safe con writer lock."""
        async with self._rwlock.writer():
            if self._initialized:
                return
            path = Path(self.storage_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._storage = LadybugBackend()
            self._storage.initialize(path)
            self._initialized = True
            logger.info(
                "✅ Synaptiq inizializzato: %s (tier=%s)",
                self.storage_path,
                self.embedding_tier,
            )

    async def close(self) -> None:
        """Chiude LadybugDB e rilascia risorse."""
        async with self._rwlock.writer():
            if self._storage:
                try:
                    self._storage.close()
                except Exception as e:
                    logger.warning("Synaptiq close warning: %s", e)
                self._storage = None
            self._initialized = False
            logger.info("Synaptiq chiuso.")

    # ── Analisi ────────────────────────────────────────────────────────────────

    async def analyze(self, path: str, full_rebuild: bool = False) -> dict[str, Any]:
        """Esegue run_pipeline su un percorso in ThreadPoolExecutor.

        Args:
            path: Percorso del progetto da analizzare.
            full_rebuild: True = re-analisi completa, False = incrementale.

        Returns:
            dict con: files, symbols, relationships, clusters (comunità),
            processes, dead_code, duration_seconds, phase_timings.
        """
        if not self._storage or not self._initialized:
            await self.initialize()

        storage = self._storage
        assert storage is not None

        loop = asyncio.get_running_loop()

        def _run() -> tuple[Any, Any]:
            """Esegue run_pipeline nel thread pool."""
            return run_pipeline(
                repo_path=Path(path),
                storage=storage,
                full=full_rebuild,
                skip_embeddings=True,
                embedding_tier=self.embedding_tier,
            )

        try:
            async with self._rwlock.writer():
                _graph, result = await loop.run_in_executor(None, _run)

            data = {
                "files": result.files,
                "symbols": result.symbols,
                "relationships": result.relationships,
                "clusters": result.clusters,
                "processes": result.processes,
                "dead_code": result.dead_code,
                "duration_seconds": result.duration_seconds,
                "phase_timings": dict(result.phase_timings),
                "incremental": result.incremental,
                "changed_files": result.changed_files,
            }
            self._last_analyze_duration = result.duration_seconds
            self._last_analyze_result = data
            logger.info(
                "Analisi completata: %d file, %d simboli, %d relazioni in %.2fs",
                result.files, result.symbols, result.relationships,
                result.duration_seconds,
            )
            return data
        except asyncio.CancelledError:
            logger.debug("Synaptiq analyze cancellato per %s", path)
            raise
        except Exception as e:
            logger.exception("Synaptiq analyze fallito [%s]: %s", type(e).__name__, e)
            raise

    # ── Ricerca ────────────────────────────────────────────────────────────────

    async def hybrid_search(
        self, query: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Ricerca ibrida: FTS + fuzzy + esatta.

        Args:
            query: Testo della query.
            limit: Max risultati.

        Returns:
            list[dict] con: node_name, file_path, label, snippet, score.
        """
        if not self._storage or not self._initialized:
            return []

        async with self._rwlock.reader():
            storage = self._storage
            assert storage is not None
            seen: set[str] = set()
            results: list[dict[str, Any]] = []

            # 1. FTS search
            for r in storage.fts_search(query, limit=limit):
                if r.node_name not in seen:
                    seen.add(r.node_name)
                    results.append({
                        "node_name": r.node_name,
                        "file_path": r.file_path,
                        "label": r.label,
                        "snippet": r.snippet,
                        "score": r.score,
                    })

            # 2. Fuzzy search (se FTS ha pochi risultati)
            if len(results) < 3:
                for r in storage.fuzzy_search(query, limit=limit, max_distance=2):
                    if r.node_name not in seen:
                        seen.add(r.node_name)
                        results.append({
                            "node_name": r.node_name,
                            "file_path": r.file_path,
                            "label": r.label,
                            "snippet": r.snippet,
                            "score": r.score * 0.8,
                        })

            # 3. Exact name search
            for r in storage.exact_name_search(query, limit=5):
                if r.node_name not in seen:
                    seen.add(r.node_name)
                    results.append({
                        "node_name": r.node_name,
                        "file_path": r.file_path,
                        "label": r.label,
                        "snippet": r.snippet,
                        "score": 10.0,
                    })

            # Ordina per score decrescente
            results.sort(key=lambda x: -x["score"])
            return results[:limit]

    # ── Contesto simboli ───────────────────────────────────────────────────────

    async def get_symbol_context(self, symbol_name: str) -> dict[str, Any]:
        """Recupera contesto completo di un simbolo.

        Args:
            symbol_name: Nome del simbolo (funzione, classe, metodo).

        Returns:
            dict con: name, file_path, start_line, label,
            callers=[], callees=[], type_refs=[], community_id=None.
        """
        if not self._storage or not self._initialized:
            return {"name": symbol_name, "error": "Synaptiq non inizializzato"}

        async with self._rwlock.reader():
            storage = self._storage
            assert storage is not None

            # Cerca il nodo per nome esatto
            exact = storage.exact_name_search(symbol_name, limit=1)
            if not exact:
                # Prova FTS
                fts = storage.fts_search(symbol_name, limit=1)
                if not fts:
                    return {"name": symbol_name, "error": "Simbolo non trovato"}
                node = storage.get_node(fts[0].node_id)
            else:
                node = storage.get_node(exact[0].node_id)

            if not node:
                return {"name": symbol_name, "error": "Nodo non trovato nel grafo"}

            ctx: dict[str, Any] = {
                "name": node.name,
                "file_path": node.file_path or "",
                "start_line": node.start_line or 0,
                "label": node.label or "",
            }

            # Callers
            try:
                callers = storage.get_callers(node.id)
                ctx["callers"] = [
                    {"name": c.name, "file_path": c.file_path, "start_line": c.start_line}
                    for c in callers[:15]
                ]
            except Exception:
                ctx["callers"] = []

            # Callees
            try:
                callees = storage.get_callees(node.id)
                ctx["callees"] = [
                    {"name": c.name, "file_path": c.file_path, "start_line": c.start_line}
                    for c in callees[:15]
                ]
            except Exception:
                ctx["callees"] = []

            # Type refs
            try:
                type_refs = storage.get_type_refs(node.id)
                ctx["type_refs"] = [
                    {"name": t.name, "file_path": t.file_path}
                    for t in type_refs[:10]
                ]
            except Exception:
                ctx["type_refs"] = []

            return ctx

    # ── Traversal ──────────────────────────────────────────────────────────────

    async def traverse(
        self, symbol_name: str, depth: int = 3, direction: str = "callers"
    ) -> list[dict[str, Any]]:
        """Multi-hop BFS traversal.

        Args:
            symbol_name: Nome del simbolo di partenza.
            depth: Profondità massima.
            direction: "callers" (su) o "callees" (giu).

        Returns:
            list[dict] con: name, label, file_path, start_line, depth.
        """
        if not self._storage or not self._initialized:
            return []

        async with self._rwlock.reader():
            storage = self._storage
            assert storage is not None

            # Trova il nodo
            exact = storage.exact_name_search(symbol_name, limit=1)
            if not exact:
                return []

            results: list[dict[str, Any]] = []
            seen: set[str] = set()

            try:
                traversed = storage.traverse_with_depth(
                    exact[0].node_id, depth=depth, direction=direction
                )
                for node, dist in traversed:
                    if node.id not in seen:
                        seen.add(node.id)
                        results.append({
                            "name": node.name,
                            "label": node.label,
                            "file_path": node.file_path or "",
                            "start_line": node.start_line or 0,
                            "depth": dist,
                        })
            except Exception as e:
                logger.debug("Traverse error: %s", e)

            return results

    # ── Impatto (Blast Radius) ─────────────────────────────────────────────────

    async def get_impact(
        self, symbol_name: str, depth: int = 3
    ) -> dict[str, Any]:
        """Calcola blast radius: simboli affetti da modifica.

        Args:
            symbol_name: Nome del simbolo modificato.
            depth: Profondità massima in entrambe le direzioni.

        Returns:
            dict con: symbol, callers_affected=[], callees_affected=[],
            total_affected_count.
        """
        callers = await self.traverse(symbol_name, depth=depth, direction="callers")
        callees = await self.traverse(symbol_name, depth=depth, direction="callees")

        return {
            "symbol": symbol_name,
            "callers_affected": callers,
            "callees_affected": callees,
            "total_affected_count": len(callers) + len(callees),
        }

    # ── Dead Code ──────────────────────────────────────────────────────────────

    async def get_dead_code(self) -> list[dict[str, Any]]:
        """Trova simboli non chiamati (nessun incoming CALLS).

        Returns:
            list[dict] con: name, label, file_path, start_line.
        """
        if not self._storage or not self._initialized:
            return []

        async with self._rwlock.reader():
            storage = self._storage
            assert storage is not None

            try:
                graph = storage.load_graph()
                dead: list[dict[str, Any]] = []
                for node in graph.iter_nodes():
                    if node.label in ("function", "method", "class"):
                        if not graph.has_incoming(node.id, RelType.CALLS):
                            dead.append({
                                "name": node.name,
                                "label": node.label,
                                "file_path": node.file_path or "",
                                "start_line": node.start_line or 0,
                            })
                # Ordina per file_path
                dead.sort(key=lambda x: (x["file_path"], x["start_line"]))
                return dead
            except Exception as e:
                logger.debug("Dead code detection error: %s", e)
                return []

    # ── Community Detection ────────────────────────────────────────────────────

    async def get_communities(self) -> list[dict[str, Any]]:
        """Restituisce info sulle community nel grafo.

        Returns:
            list[dict] con almeno: community_id, member_count.
        """
        if not self._storage or not self._initialized:
            return []

        async with self._rwlock.reader():
            storage = self._storage
            assert storage is not None

            try:
                graph = storage.load_graph()
                communities = graph.count_nodes_by_label(NodeLabel.COMMUNITY)
                return [{"total_communities": communities}]
            except Exception:
                return []

    # ── Contestualizzazione LLM ────────────────────────────────────────────────

    async def pack_snippets(self, query: str, limit: int = 10) -> str:
        """Formatta risultati ricerca come Markdown per prompt LLM.

        Args:
            query: Testo della query.
            limit: Max risultati da includere.

        Returns:
            Markdown strutturato con simboli, percorsi e snippet.
        """
        results = await self.hybrid_search(query, limit=limit)
        if not results:
            return ""

        lines = ["## 🧠 Code Context (structural)\n"]
        for r in results:
            lines.append(f"### {r['node_name']}")
            loc = f"`{r['file_path']}`" if r["file_path"] else "?"
            lines.append(f"- **Type:** {r['label']}")
            lines.append(f"- **File:** {loc}")
            if r.get("snippet"):
                lines.append(f"```\n{r['snippet'][:200]}\n```")
            lines.append("")

            # Aggiungi callers/callees se disponibili (max 3 simboli profondi)
            if len(lines) < 60:
                ctx = await self.get_symbol_context(r["node_name"])
                if ctx.get("callers"):
                    callers_str = ", ".join(
                        c["name"] for c in ctx["callers"][:5]
                    )
                    lines.append(f"  - **Callers:** {callers_str}")
                if ctx.get("callees"):
                    callees_str = ", ".join(
                        c["name"] for c in ctx["callees"][:5]
                    )
                    lines.append(f"  - **Callees:** {callees_str}")
                lines.append("")

        return "\n".join(lines)

    # ── Stato ──────────────────────────────────────────────────────────────────

    async def status(self) -> dict[str, Any]:
        """Restituisce stato corrente del motore.

        Returns:
            dict con: initialized, storage_path, nodes_count,
            relationships_count, embedding_tier, db_size_bytes,
            last_analyze_duration.
        """
        stats: dict[str, Any] = {
            "available": True,
            "initialized": self._initialized,
            "storage_path": self.storage_path,
            "embedding_tier": self.embedding_tier,
            "last_analyze_duration": self._last_analyze_duration,
            "nodes_count": 0,
            "relationships_count": 0,
            "db_size_bytes": 0,
        }

        if self._storage and self._initialized:
            async with self._rwlock.reader():
                try:
                    graph = self._storage.load_graph()
                    gs = graph.stats()
                    stats["nodes_count"] = gs.get("nodes", 0)
                    stats["relationships_count"] = gs.get("relationships", 0)
                except Exception:
                    pass

                try:
                    path = Path(self.storage_path)
                    if path.exists():
                        stats["db_size_bytes"] = path.stat().st_size
                except Exception:
                    pass

        return stats


    # ── Watchdog Integration (debounced background analysis) ─────────────────────

    def notify_file_event(self, project_path: str) -> None:
        """Registra un cambiamento file per un progetto e schedula analisi con debounce.

        Chiamato dal watchdog RAG dopo ogni batch di eventi.
        La prima analisi parte immediatamente; le successive rispettano
        ``_ANALYSIS_COOLDOWN`` secondi dall'ultima completata.

        Se un'analisi per lo stesso progetto è già in coda o in esecuzione,
        la nuova richiesta viene scartata (evita accumulo di task cancellati).
        """
        # Se già in coda, skip
        if project_path in self._pending_requests:
            logger.debug("Synaptiq notify: %s già in coda, skip", project_path)
            return

        loop = asyncio.get_running_loop()
        now = loop.time()
        last = self._last_analysis_time.get(project_path, 0.0)
        elapsed = now - last

        # Cancella task pendente precedente solo se non è ancora partito
        prev = self._pending_tasks.pop(project_path, None)
        if prev is not None and not prev.done():
            prev.cancel()

        if elapsed >= self._ANALYSIS_COOLDOWN:
            # Cooldown già trascorso → avvia subito
            self._last_analysis_time[project_path] = now
            self._pending_tasks[project_path] = asyncio.create_task(
                self._analyze_one(project_path),
            )
        else:
            # Cooldown attivo → aspetta il tempo rimanente
            wait = self._ANALYSIS_COOLDOWN - elapsed
            self._pending_tasks[project_path] = asyncio.create_task(
                self._debounced_analyze(project_path, wait),
            )

    async def run_initial_analysis(self, project_paths: list[str]) -> None:
        """Analisi iniziale su tutti i progetti (eseguita al boot).

        Se il grafo è già popolato (nodes_count > 0), salta tutta l'analisi:
        a startup nessun file è cambiato, quindi non serve re-analizzare.

        Lancia le analisi in parallelo via gather così che coesistano
        con l'avvio del server (non bloccante).
        """
        if not project_paths:
            return
        if not self._initialized:
            await self.initialize()

        # Salta se il grafo ha già dati — a startup nessun file è cambiato
        st = await self.status()
        if st.get("nodes_count", 0) > 0:
            logger.info(
                "Synaptiq grafo già popolato (%d nodi, %d relazioni) — "
                "scan iniziale saltato. Per forzare re-analisi, "
                "eliminare %s e riavviare.",
                st["nodes_count"], st.get("relationships_count", 0),
                self.storage_path,
            )
            return

        valid = [p for p in project_paths if p and Path(p).is_dir()]
        if not valid:
            return

        logger.info("Synaptiq initial analysis: %d progetti", len(valid))
        tasks = [self._analyze_one(p) for p in valid]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        ok = sum(1 for r in results if not isinstance(r, Exception))
        logger.info("Synaptiq initial analysis completata: %d/%d ok", ok, len(valid))

    # ── Internal ─────────────────────────────────────────────────────────────────

    async def _analyze_one(self, project_path: str) -> dict[str, Any]:
        """Analisi incrementale di un singolo progetto (wrap analyze).

        Serializzata da ``_analysis_lock`` per evitare contenzione sul writer
        lock sottostante. Richieste concorrenti per lo stesso progetto vengono
        saltate (deduplica via ``_pending_requests``).
        """
        if project_path in self._pending_requests:
            logger.debug("Synaptiq analyze già in coda per %s — skip", project_path)
            return {}
        self._pending_requests.add(project_path)
        try:
            async with self._analysis_lock:
                return await self.analyze(project_path, full_rebuild=False)
        except asyncio.CancelledError:
            logger.debug("Synaptiq analyze cancellato per %s", project_path)
            return {}
        except Exception as e:
            logger.warning(
                "Synaptiq analyze skipped per %s: [%s] %s", project_path, type(e).__name__, e,
            )
            return {}
        finally:
            self._pending_requests.discard(project_path)

    async def _debounced_analyze(self, project_path: str, wait: float) -> None:
        """Attende il cooldown rimanente poi analizza."""
        try:
            await asyncio.sleep(wait)
            now = asyncio.get_running_loop().time()
            self._last_analysis_time[project_path] = now
            await self._analyze_one(project_path)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("Synaptiq debounced analyze fallito: %s", e)


# ── Helpers ──────────────────────────────────────────────────────────────────────

def is_synaptiq_available() -> bool:
    """Verifica se il pacchetto synaptiq è installato e importabile."""
    try:
        import synaptiq  # noqa: F401
        return True
    except ImportError:
        return False


def get_synaptiq_version() -> str:
    """Restituisce la versione di Synaptiq installata."""
    try:
        import synaptiq
        return getattr(synaptiq, "__version__", "sconosciuta")
    except ImportError:
        return "non installato"


# ── Singleton ────────────────────────────────────────────────────────────────────

synaptiq_engine = SynaptiqEngine()
