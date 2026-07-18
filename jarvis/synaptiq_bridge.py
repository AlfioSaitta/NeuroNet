"""
synaptiq_bridge.py — Bridge functions che sostituiscono code_intelligence.py.

Fornisce hybrid search che fonde RAG (Qdrant) + Synaptiq (grafo strutturale)
e formattazione Markdown per l'iniezione nei prompt LLM.

Usage:
    from synaptiq_bridge import hybrid_code_search, format_symbol_context
    ctx = await hybrid_code_search("come funziona il telemetry collector?")
"""

import re
import asyncio
from typing import Any, Optional

from config import logger
from synaptiq_engine import synaptiq_engine

# ── Pattern regex (copiati da code_intelligence.py) ────────────────────────

_CALLERS_PATTERN = re.compile(
    r"(?:chi\s+)?(?:chiama|usa|invoca|utilizza|referenzia)\s+",
    re.IGNORECASE,
)

_CODE_TERMS = {
    "funzione", "funzion", "classe", "metodo", "modulo", "file", "codice",
    "script", "api", "endpoint", "route", "router", "middleware",
    "function", "class", "method", "module", "code", "implementazione",
    "implement", "definizione", "definition", "variabile", "variable",
    "tipo", "type", "interfaccia", "interface", "errore", "error",
    "bug", "fix", "refactor", "test", "chiamata", "call",
    "callback", "handler", "listener", "evento", "event",
    "config", "configurazione", "configuration",
    "dipendenza", "dependency", "dipende", "import",
    "flusso", "flow", "architettura", "architecture",
    "pipeline", "catena", "chain", "processo", "process",
    "struttura", "structure", "schema",
    "how does", "what is", "where is", "find the",
    "caller", "callee", "call graph", "impact",
}


# ── Helper ─────────────────────────────────────────────────────────────────

def _has_code_terms(query: str) -> bool:
    """Rileva se la query contiene termini che suggeriscono codice."""
    ql = query.lower()
    return any(term in ql for term in _CODE_TERMS)


def _extract_caller_target(text: str) -> Optional[str]:
    """Estrae il nome del simbolo se la query chiede 'chi chiama X'."""
    m = _CALLERS_PATTERN.search(text)
    if not m:
        return None
    after = text[m.end():].strip().rstrip("?.!").strip()
    sym_match = re.match(r'[a-zA-Z_][\w.]*(?:\.[a-zA-Z_][\w.]*)*', after)
    return sym_match.group(0) if sym_match else None


# ── Formattazione ──────────────────────────────────────────────────────────

async def format_symbol_context(results: list[dict]) -> str:
    """Formatta risultati Synaptiq come Markdown strutturato per il prompt.

    Per ogni risultato, arricchisce con callers/callees via get_symbol_context.
    Max 5 simboli profondi, totale sotto 3000 caratteri.

    Args:
        results: Lista di dict da hybrid_search.

    Returns:
        Markdown strutturato o stringa vuota.
    """
    if not results:
        return ""

    lines = ["## 🧠 Code Context (structural)\n"]
    char_count = 0
    max_chars = 3000

    for r in results[:5]:
        if char_count >= max_chars:
            break

        entry = f"### {r['node_name']}\n"
        loc = f"`{r['file_path']}`" if r.get("file_path") else "`?`"
        entry += f"- **Type:** {r.get('label', '?')}\n"
        entry += f"- **File:** {loc}\n"

        # Arricchisci con contesto se possibile
        ctx = await synaptiq_engine.get_symbol_context(r["node_name"])
        if ctx and "error" not in ctx:
            if ctx.get("callers"):
                c_list = ", ".join(c["name"] for c in ctx["callers"][:5])
                entry += f"- **Callers:** {c_list}\n"
            if ctx.get("callees"):
                c_list = ", ".join(c["name"] for c in ctx["callees"][:5])
                entry += f"- **Callees:** {c_list}\n"

        if r.get("snippet"):
            entry += f"```\n{r['snippet'][:200]}\n```\n"

        entry += "\n"

        if char_count + len(entry) > max_chars:
            entry = entry[: max_chars - char_count]
            lines.append(entry)
            break

        lines.append(entry)
        char_count += len(entry)

    return "".join(lines).strip()


# ── Ricerca Ibrida ─────────────────────────────────────────────────────────

async def hybrid_code_search(
    query: str,
    *,
    is_project_query: bool = False,
    project_name: Optional[str] = None,
    user_message: str = "",
) -> str:
    """Cerca contesto codice usando RAG + Synaptiq in parallelo.

    Sostituisce code_intelligence.hybrid_code_search().

    Args:
        query: Testo della query (ripulito da tag/prefix).
        is_project_query: Se True, la query riguarda codice di un progetto.
        project_name: Nome del progetto (es. "NeuroNet").
        user_message: Messaggio utente originale (per pattern matching).

    Returns:
        Contesto unificato Markdown, vuoto se nessuna fonte ha risultati.
    """
    tasks: list[tuple[str, Any]] = []

    # ── 1. RAG search (sempre) ──
    tasks.append(("rag", _rag_search(query, is_project_query, project_name)))

    # ── 2. Synaptiq search (se disponibile e query di codice) ──
    if is_project_query or _has_code_terms(query):
        tasks.append(("synaptiq", _synaptiq_search(query, user_message or query)))

    if not tasks:
        return ""

    # Esegui in parallelo
    results = await asyncio.gather(
        *[t[1] for t in tasks], return_exceptions=True
    )

    ctx_parts = []
    for (label, _), r in zip(tasks, results):
        if isinstance(r, Exception):
            logger.debug("Hybrid search %s fallito: %s", label, r)
            continue
        if r and r.strip():
            ctx_parts.append(r)

    return "\n\n".join(ctx_parts)


async def _rag_search(
    query: str, is_project_query: bool, project_name: Optional[str]
) -> str:
    """Esegue RAG search e restituisce contesto testuale."""
    try:
        from rag import search_documents

        rag_ctx = await search_documents(
            query,
            is_project_query=is_project_query,
            project_name=project_name,
        )
        if rag_ctx and rag_ctx.strip():
            return f"## 🔍 RAG Context (vector)\n\n{rag_ctx.strip()}"
    except Exception as e:
        logger.debug("RAG search fallita: %s", e)
    return ""


async def _synaptiq_search(query: str, user_message: str) -> str:
    """Esegue Synaptiq search e restituisce contesto strutturale."""
    try:
        # 1. Ricerca ibrida
        results = await synaptiq_engine.hybrid_search(query, limit=8)
        if not results:
            return ""

        lines = []

        # 2. Callers — se la query chiede "chi chiama X"
        caller_symbol = _extract_caller_target(user_message)
        if caller_symbol:
            ctx = await synaptiq_engine.get_symbol_context(caller_symbol)
            if ctx and "error" not in ctx and ctx.get("callers"):
                callers_lines = [f"**Callers of `{caller_symbol}`:**"]
                for c in ctx["callers"][:10]:
                    fpath = c.get("file_path", "?")
                    sl = c.get("start_line", "?")
                    callers_lines.append(
                        f"- `{c['name']}` ({fpath}:{sl})"
                    )
                lines.append("\n".join(callers_lines))

        # 3. Contesto strutturale dai risultati
        ctx_md = await format_symbol_context(results)
        if ctx_md:
            lines.append(ctx_md)

        if not lines:
            return ""

        return "\n\n".join(lines)

    except Exception as e:
        logger.debug("Synaptiq search error: %s", e)
        return ""

