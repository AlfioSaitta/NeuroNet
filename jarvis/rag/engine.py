"""
Pipeline RAG completa — GitignoreFilter, AST chunking, ingestion documentale,
ricerca vettoriale, watchdog real-time e generazione project tree.
"""

import os
import json
import hashlib
import re
import asyncio
import shutil
from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchText, PointStruct, VectorParams, Distance
from pathlib import Path
from datetime import datetime

import sqlite3
from collections import defaultdict

from core.llm_engine import engine

from core.config import (
    logger, MODEL_ID, DOC_DIR,
    STATE_FILE, MAX_CONCURRENT_EMBEDDINGS,
    RAG_CONFIG,
    VECTOR_DB_VERSION,
    WATCHDOG_BATCH_DELAY,
    PATHSPEC_ENABLED, WATCHDOG_ENABLED, EMBEDDING_DIMS,
    EXTERNAL_PROJECTS, WORKSPACE_DIR, WORKSPACE_PROJECTS,
    DATA_DIR, HOST_FS_PREFIX,
    MODEL_PROFILE,
    SYNAPTIQ_ENABLED,
    parse_external_projects,
)
import core.state as state

# Runtime cache per progetti registrati via API (colma gap fino al restart)
# Popolato da routes/projects.py POST /register, usato da get_project_path()
_registered_project_paths: dict[str, str] = {}

# Estensioni valide per file sorgente/documentazione (usato ovunque)
VALID_EXTENSIONS = (
    '.go', '.py', '.jsx', '.tsx', '.js', '.ts',
    '.md', '.json', '.txt',
    '.c', '.cpp', '.h', '.hpp',
    '.java', '.rs', '.sql', '.yaml', '.yml'
)

# ==============================================================================
# RERANKER: estratto in rag_reranker.py
# ==============================================================================
from rag.reranker import _reranker

if PATHSPEC_ENABLED:
    import pathspec

if WATCHDOG_ENABLED:
    from watchdog.events import FileSystemEventHandler


# ==============================================================================
# FILTRO GITIGNORE
# ==============================================================================

class GitignoreFilter:
    """Filtro .aiignore / .gitignore per l'esclusione di file dal RAG.

    - Se esiste .aiignore in una directory → usa quello (l'utente ha il controllo esplicito).
    - Se non esiste .aiignore ma esiste .gitignore → crea .aiignore copiando .gitignore,
      poi usa .aiignore. In questo modo l'utente può personalizzare le esclusioni AI
      senza toccare il .gitignore originale.
    - Se non esiste né .aiignore né .gitignore → nessuna regola custom per quella directory.
    """

    def __init__(self, doc_dir=DOC_DIR):
        self.specs = {}
        visited_inodes = set()
        # followlinks=False: evita di seguire symlink in progetti esterni (SlotBuilder 76k, StreamAI 34k)
        # che causano CPU al 100% per 8+ ore durante l'ingestione iniziale.
        # I symlink rimangono in DOC_DIR per il project-tree display, ma os.walk non li segue.
        for root, dirs, files in os.walk(doc_dir, followlinks=False):
            # Evita loop da symlink circolari (NeuroNet/data/documents/NeuroNet)
            try:
                st = os.stat(root)
                inode_key = (st.st_dev, st.st_ino)
                if inode_key in visited_inodes:
                    dirs[:] = []
                    continue
                visited_inodes.add(inode_key)
            except OSError:
                pass
            dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules', '__pycache__', 'venv', 'vendor')]
            if ".aiignore" in files or ".gitignore" in files:
                base = os.path.relpath(root, doc_dir).replace('\\', '/')
                base = "" if base == '.' else base

                aiignore = os.path.join(root, ".aiignore")
                gitignore = os.path.join(root, ".gitignore")

                if ".aiignore" in files:
                    ignore_path = aiignore
                elif base == "":
                    # Root del progetto: crea .aiignore da .gitignore
                    try:
                        shutil.copy2(gitignore, aiignore)
                        logger.info(f"📄 Creato {aiignore} da .gitignore")
                    except OSError as e:
                        logger.warning(f"⚠️ Impossibile creare {aiignore}: {e}")
                    ignore_path = aiignore if os.path.exists(aiignore) else gitignore
                else:
                    # Sottodirectory: usa .gitignore direttamente senza creare .aiignore
                    ignore_path = gitignore

                if PATHSPEC_ENABLED:
                    with open(ignore_path, 'r', errors='ignore') as f:
                        self.specs[base] = pathspec.PathSpec.from_lines('gitignore', f)

    def is_ignored(self, rel_path):
        norm = rel_path.replace('\\', '/')
        if PATHSPEC_ENABLED:
            for b, s in self.specs.items():
                if b == "" or norm.startswith(b + "/"):
                    if s.match_file(norm if b == "" else norm[len(b)+1:]):
                        return True
        return False


# ==============================================================================
# EMBEDDING
# ==============================================================================

QWEN3_QUERY_INSTRUCTION = "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: "

async def get_embedding(texts, priority=10, is_query=False):
    """Genera vettori di embedding tramite LlamaEngine in locale (supporta batch).
    
    is_query: se True, applica il prefisso istruzione Qwen3 per query di ricerca.
    """
    try:
        is_single = isinstance(texts, str)
        if is_single:
            texts = [texts]
            
        # Filtra testi vuoti
        texts = [str(t) for t in texts if t and str(t).strip()]
        if not texts:
            return [] if not is_single else []
            
        # Qwen3-Embedding: le query richiedono prefisso istruzione, i documenti no
        if is_query:
            texts = [QWEN3_QUERY_INSTRUCTION + t for t in texts]
            
        result = await engine.get_embeddings(texts, priority=priority)
        if "error" in result:
            return [[] for _ in texts] if not is_single else []
            
        embeddings_list = []
        data = result.get("data", [])
        for d in data:
            embeddings_list.append(d.get("embedding", []))
            
        if is_single:
            return embeddings_list[0] if embeddings_list else []
        return embeddings_list
    except Exception as e:
        return []


# ==============================================================================
# CHUNKING AST-AWARE — estratto in rag/chunking.py
# ==============================================================================
from rag.chunking import extract_dependencies, ast_code_chunking


# ==============================================================================
# STATO PERSISTENTE RAG
# ==============================================================================

def _get_db():
    db_path = STATE_FILE.replace('.json', '.db')
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute('CREATE TABLE IF NOT EXISTS file_state (filepath TEXT PRIMARY KEY, hash TEXT, mtime REAL, size INTEGER)')
    return conn

def _load_state_unsafe():
    """Carica lo stato RAG da SQLite o migra da JSON (NON thread-safe, usare dentro state_lock)."""
    db_path = STATE_FILE.replace('.json', '.db')
    state.rag_state = {}
    
    if not os.path.exists(db_path) and os.path.exists(STATE_FILE):
        logger.info("Migrazione rag_state.json a SQLite in corso...")
        try:
            with open(STATE_FILE, 'r') as f:
                old_state = json.load(f)
            
            with _get_db() as conn:
                for k, v in old_state.items():
                    if isinstance(v, dict):
                        conn.execute(
                            'INSERT OR REPLACE INTO file_state (filepath, hash, mtime, size) VALUES (?, ?, ?, ?)',
                            (k, v.get("hash", ""), v.get("mtime", 0.0), v.get("size", 0))
                        )
                    elif isinstance(v, str):
                        conn.execute(
                            'INSERT OR REPLACE INTO file_state (filepath, hash, mtime, size) VALUES (?, ?, ?, ?)',
                            (k, v, 0.0, 0)
                        )
                conn.commit()
            os.rename(STATE_FILE, STATE_FILE + ".bak")
            logger.info("Migrazione SQLite completata. Vecchio file rinominato in .bak")
        except Exception as e:
            logger.warning(f"Impossibile migrare JSON a SQLite: {e}")
            
    try:
        with _get_db() as conn:
            cursor = conn.execute('SELECT filepath, hash, mtime, size FROM file_state')
            for row in cursor:
                state.rag_state[row[0]] = {
                    "hash": row[1],
                    "mtime": row[2],
                    "size": row[3]
                }
    except Exception as e:
         logger.warning(f"Errore lettura SQLite state: {e}")

def _save_file_state_unsafe(rel_path):
    """Salva su SQLite lo stato RAG per un SINGOLO file in modo sincrono e fulmineo."""
    file_data = state.rag_state.get(rel_path)
    if not file_data:
        try:
            with _get_db() as conn:
                conn.execute('DELETE FROM file_state WHERE filepath = ?', (rel_path,))
                conn.commit()
        except Exception as e:
            logger.warning(f"SQLite DELETE error for {rel_path}: {e}")
    else:
        try:
            with _get_db() as conn:
                conn.execute(
                    'INSERT OR REPLACE INTO file_state (filepath, hash, mtime, size) VALUES (?, ?, ?, ?)',
                    (rel_path, file_data.get("hash", ""), file_data.get("mtime", 0.0), file_data.get("size", 0))
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Errore scrittura SQLite per {rel_path}: {e}")

def _save_state_unsafe():
    """Mantenuta per retrocompatibilità. Le scritture avvengono ora puntualmente via _save_file_state_unsafe."""
    pass


def get_workspace_col_name(rel_path):
    parts = rel_path.replace('\\', '/').split('/')
    if len(parts) > 1:
        ws_name = sanitize_project_name(parts[0])
        return f"collateral_docs_{ws_name}_{VECTOR_DB_VERSION}"
    return f"collateral_docs_default_{VECTOR_DB_VERSION}"

# get_project_col_name e get_file_profile_col_name sono ora in core.qdrant_utils.
# Re-importate qui per retrocompatibilità con i moduli che importano da rag.engine.
from core.qdrant_utils import get_project_col_name, get_file_profile_col_name, sanitize_project_name


def get_project_path(project_name: str) -> str | None:
    """Cerca il path assoluto di un progetto per nome.
    Cerca PRIMA nel runtime cache _registered_project_paths,
    poi in WORKSPACE_PROJECTS, poi in EXTERNAL_PROJECTS.

    NOTA: get_project_col_name() sanitizza il nome con re.sub(r'[^a-zA-Z0-9_]', '_', ...)
    prima di creare la collezione Qdrant. list_rag_projects() estrae il nome SANITIZZATO,
    quindi un progetto "My-Project" in Qdrant diventa "My_Project".
    Il match diretto per basename fallisce. Gestiamo questo caso con un match sanitizzato
    come fallback.
    """
    name_lower = project_name.lower()
    # 1. Runtime cache (per progetti appena registrati via API)
    if project_name in _registered_project_paths:
        return _registered_project_paths[project_name]

    def _basename_matches(path: str) -> bool:
        base = os.path.basename(path)
        # Match diretto (caso ideale: nessun carattere speciale)
        if base.lower() == name_lower:
            return True
        # Match sanitizzato: stessa trasformazione di sanitize_project_name()
        sanitized = sanitize_project_name(base)
        return sanitized.lower() == name_lower

    # 2. Cerca in WORKSPACE_PROJECTS
    for proj_path in WORKSPACE_PROJECTS:
        if _basename_matches(proj_path):
            return proj_path
    # 3. Cerca in EXTERNAL_PROJECTS
    for ep_path in parse_external_projects():
        if _basename_matches(ep_path):
            return ep_path
    # 4. Cerca per path completo (se name è un path)
    if os.path.isdir(project_name):
        return os.path.normpath(project_name)
    return None


def get_project_last_indexed(project_name: str) -> int | None:
    """Ritorna il timestamp più recente tra i file indicizzati del progetto."""
    prefix = project_name.replace(' ', '_').replace('-', '_') + "/"
    max_mtime: float | None = None
    for rel_path, data in state.rag_state.items():
        if rel_path.startswith(prefix):
            mtime = data.get("mtime") if isinstance(data, dict) else None
            if mtime and (max_mtime is None or mtime > max_mtime):
                max_mtime = mtime
    return int(max_mtime) if max_mtime else None

def _mean_vector(vectors: list[list[float]]) -> list[float] | None:
    """Media elemento-per-elemento di una lista di vettori."""
    if not vectors:
        return None
    n = len(vectors)
    dim = len(vectors[0])
    result = [0.0] * dim
    for v in vectors:
        for i in range(dim):
            result[i] += v[i]
    return [x / n for x in result]


async def ensure_workspace_collection(col_name):
    if col_name not in state.created_collections:
        async with state.state_lock:
            if col_name not in state.created_collections:
                try:
                    exists = await state.qdrant.collection_exists(collection_name=col_name)
                    if not exists:
                        await state.qdrant.create_collection(
                            collection_name=col_name,
                            vectors_config=VectorParams(size=EMBEDDING_DIMS, distance=Distance.COSINE)
                        )
                except Exception as e: logger.warning(f"Errore silenziato: {e}")
                state.created_collections.add(col_name)


async def ensure_file_profile_collection():
    col_name = get_file_profile_col_name()
    if col_name not in state.created_collections:
        async with state.state_lock:
            if col_name not in state.created_collections:
                try:
                    exists = await state.qdrant.collection_exists(collection_name=col_name)
                    if not exists:
                        await state.qdrant.create_collection(
                            collection_name=col_name,
                            vectors_config=VectorParams(size=EMBEDDING_DIMS, distance=Distance.COSINE)
                        )
                except Exception as e: logger.warning(f"Errore silenziato: {e}")
                state.created_collections.add(col_name)


# ==============================================================================
# INGESTION DOCUMENTALE
# ==============================================================================

async def process_single_file(rel_path, filepath, semaphore, content_bytes=None, file_hash=None, mtime=None, size=None):
    """Processa un singolo file: calcola hash, chunka, genera embedding, upsert in Qdrant."""
    async with semaphore:
        try:
            if mtime is None or size is None:
                if not os.path.exists(filepath):
                    return
                stat = os.stat(filepath)
                mtime, size = stat.st_mtime, stat.st_size

            if content_bytes is None:
                if not os.path.exists(filepath):
                    return
                content_bytes = Path(filepath).read_bytes()
            
            if file_hash is None:
                file_hash = hashlib.md5(content_bytes).hexdigest()

            col_name = get_workspace_col_name(rel_path)
            await ensure_workspace_collection(col_name)

            content = content_bytes.decode('utf-8', errors='ignore')
            ext = os.path.splitext(filepath)[1].lower()
            deps = extract_dependencies(content, ext)
            chunks = ast_code_chunking(content, filepath)
            points = []

            valid_chunks = [c for c in chunks if len(c["text"].strip()) >= 50]

            # Ricalcola chunk_count e chunk_index dopo il filtro valid_chunks
            groups: dict[str | None, list[dict]] = {}
            for c in valid_chunks:
                pid = c.get("parent_chunk_id")
                groups.setdefault(pid, []).append(c)
            for _, group in groups.items():
                for i, c in enumerate(group):
                    c["chunk_index"] = i
                    c["chunk_count"] = len(group)

            if valid_chunks:
                texts_to_embed = [c["text"] for c in valid_chunks]
                
                vectors = []
                for i in range(0, len(texts_to_embed), MAX_CONCURRENT_EMBEDDINGS):
                    batch = texts_to_embed[i:i+3]
                    batch_vectors = await get_embedding(batch)
                    vectors.extend(batch_vectors)
                    # Yield volontario per permettere all'event loop di servire il PriorityLock
                    await asyncio.sleep(0)
                
                # Estrae il nome progetto dal path relativo (prima directory)
                _project_id = rel_path.replace('\\', '/').split('/')[0] if '/' in rel_path.replace('\\', '/') else "default"
                for chunk, vector in zip(valid_chunks, vectors):
                    if vector:
                        payload = {"filename": rel_path, "text": chunk["text"], "deps": list(deps), "project": _project_id}
                        if chunk.get("section_hierarchy"):
                            payload["section_hierarchy"] = chunk["section_hierarchy"]
                        if chunk.get("parent_chunk_id"):
                            payload["parent_chunk_id"] = chunk["parent_chunk_id"]
                            payload["chunk_index"] = chunk.get("chunk_index", 0)
                            payload["chunk_count"] = chunk.get("chunk_count", 1)
                        chunk_id = hashlib.md5(f"{rel_path}:{chunk.get('chunk_index', 0)}".encode()).hexdigest()
                        points.append(PointStruct(
                            id=chunk_id,
                            vector=vector,
                            payload=payload
                        ))

            if points:
                for p in points:
                    p.payload["model_family"] = MODEL_PROFILE.family
                    p.payload["model_variant"] = MODEL_PROFILE.variant
                # Upsert con ID deterministico = sovrascrittura automatica per hash
                await state.qdrant.upsert(collection_name=col_name, points=points)

                # ── File-level co-embedding ──────────────────────────────────
                valid_vectors = [v for v in vectors if v and len(v) == EMBEDDING_DIMS]
                if valid_vectors:
                    _project_id = rel_path.replace('\\', '/').split('/')[0] if '/' in rel_path.replace('\\', '/') else "default"
                    mean_v = _mean_vector(valid_vectors)
                    if mean_v:
                        await ensure_file_profile_collection()
                        fp_col = get_file_profile_col_name()
                        fp_id = hashlib.md5(rel_path.encode()).hexdigest()
                        # Delete old profile first (re-index)
                        try:
                            await state.qdrant.delete(
                                collection_name=fp_col,
                                points_selector=[fp_id]
                            )
                        except Exception:
                            pass
                        await state.qdrant.upsert(
                            collection_name=fp_col,
                            points=[PointStruct(
                                id=fp_id,
                                vector=mean_v,
                                payload={
                                    "filename": rel_path,
                                    "project": _project_id,
                                    "deps": list(deps),
                                    "chunk_count": len(valid_chunks),
                                    "total_chars": sum(len(c["text"]) for c in valid_chunks)
                                }
                            )]
                        )
            async with state.state_lock:
                state.rag_state[rel_path] = {"hash": file_hash, "mtime": mtime, "size": size}
                _save_file_state_unsafe(rel_path)
            if points:
                logger.info(f"🔄 Vettori Aggiornati: {rel_path} ({len(points)} chunks, {len(deps)} dipendenze)")
        except Exception as e:
            logger.error(f"Errore su {rel_path}: {e}")


async def _walk_directory(base_dir: str, folder_prefix: str | None = None,
                           ignore_filter: GitignoreFilter | None = None,
                           visited_inodes: set | None = None) -> dict[str, str]:
    """Walk di una directory, ritorna dict {rel_path: abs_path}.
    
    folder_prefix: se impostato, il rel_path sarà prefissato con questo nome
                   (es. "SlotBuilder/main.go" invece di "main.go").
    """
    if not base_dir or not os.path.isdir(base_dir):
        return {}
    if visited_inodes is None:
        visited_inodes = set()
    result = {}
    loop = asyncio.get_running_loop()
    filt = ignore_filter or GitignoreFilter(base_dir)
    for r, d, f in await loop.run_in_executor(None, lambda: list(os.walk(base_dir, followlinks=False))):
        try:
            st = os.stat(r)
            inode_key = (st.st_dev, st.st_ino)
            if inode_key in visited_inodes:
                d[:] = []
                continue
            visited_inodes.add(inode_key)
        except OSError:
            pass
        d[:] = [
            sub for sub in d
            if sub not in ('.git', 'node_modules', 'venv', 'vendor')
            and not filt.is_ignored(os.path.relpath(os.path.join(r, sub), base_dir))
        ]
        for file in f:
            fp = os.path.join(r, file)
            rp = os.path.relpath(fp, base_dir)
            if folder_prefix:
                rp = f"{folder_prefix}/{rp}"
            if rp.endswith(VALID_EXTENSIONS) and not filt.is_ignored(os.path.relpath(fp, base_dir)):
                result[rp] = fp
    return result


async def ingest_local_documents(single_project_path: str | None = None):
    """Scansione completa di WORKSPACE_DIR + EXTERNAL_PROJECTS: 
    indicizza file nuovi/modificati, rimuove i cancellati.
    
    Se single_project_path è fornito, indicizza SOLO quel progetto
    (usato per re-index singolo progetto dal pannello admin)."""
    if state.is_reindexing:
        logger.info("Re-indexing già in corso, salto scansione duplicata")
        return
    state.is_reindexing = True
    async with state.state_lock:
        _load_state_unsafe()

    current_files: dict[str, str] = {}
    visited_inodes: set = set()

    # ── Single project mode (registrato via API) ──
    if single_project_path:
        if not os.path.isdir(single_project_path):
            logger.warning(f"Single project path not found: {single_project_path}")
            state.is_reindexing = False
            return
        proj_name = os.path.basename(single_project_path)
        logger.info(f"📁 Single project re-index: {proj_name} ({single_project_path})")
        proj_filter = GitignoreFilter(single_project_path)
        files = await _walk_directory(
            single_project_path, folder_prefix=proj_name,
            ignore_filter=proj_filter, visited_inodes=visited_inodes
        )
        for rp, fp in files.items():
            if rp not in current_files:
                current_files[rp] = fp
    else:
        # Guard: skip full scan se nessun progetto configurato
        if not WORKSPACE_DIR and not EXTERNAL_PROJECTS.strip():
            logger.warning("⚠️ Nessun WORKSPACE_DIR o EXTERNAL_PROJECTS configurato — salto ingestion.")
            state.is_reindexing = False
            return

    # ── 1. Walk WORKSPACE_DIR (auto-discovered projects) — SKIP in single-project mode ──
    if not single_project_path and WORKSPACE_DIR and os.path.isdir(WORKSPACE_DIR):
        for proj_dir in WORKSPACE_PROJECTS:
            if not os.path.isdir(proj_dir):
                continue
            proj_name = os.path.basename(proj_dir)
            logger.info(f"📁 Workspace project: {proj_name} ({proj_dir})")
            proj_filter = GitignoreFilter(proj_dir)
            files = await _walk_directory(
                proj_dir, folder_prefix=proj_name,
                ignore_filter=proj_filter, visited_inodes=visited_inodes
            )
            # Dedup: EXTERNAL_PROJECTS potrebbe già aver caricato lo stesso progetto
            for rp, fp in files.items():
                if rp not in current_files:
                    current_files[rp] = fp
    elif single_project_path:
        logger.info("Single-project mode — skip WORKSPACE_DIR auto-discovery.")
    elif not WORKSPACE_DIR:
        logger.info("WORKSPACE_DIR non configurato — skip workspace auto-discovery.")
    else:
        logger.info(f"WORKSPACE_DIR ({WORKSPACE_DIR}) non accessibile — skip workspace auto-discovery.")

    # ── 2. Walk EXTERNAL_PROJECTS (backward compat) — SKIP in single-project mode ──
    # Skip progetti che sono già dentro WORKSPACE_DIR (già indicizzati al punto 1)
    if not single_project_path and EXTERNAL_PROJECTS.strip():
        for pair in EXTERNAL_PROJECTS.split(','):
            pair = pair.strip()
            if ':' not in pair:
                continue
            host_path, folder_name = pair.split(':', 1)
            host_path = host_path.strip()
            folder_name = folder_name.strip()
            project_root = os.path.join(HOST_FS_PREFIX, host_path.lstrip('/')) if HOST_FS_PREFIX else host_path

            # Skip se già coperto dal workspace walk
            if WORKSPACE_DIR and project_root.startswith(os.path.normpath(WORKSPACE_DIR)):
                logger.debug(f"⏭️ EXTERNAL_PROJECTS skip (già in workspace): {folder_name}")
                continue

            if not os.path.isdir(project_root):
                logger.warning(f"⏭️ EXTERNAL_PROJECTS path non valido: {project_root}")
                continue

            proj_filter = GitignoreFilter(project_root)
            files = await _walk_directory(
                project_root, folder_prefix=folder_name,
                ignore_filter=proj_filter, visited_inodes=visited_inodes
            )
            for rp, fp in files.items():
                if rp not in current_files:
                    current_files[rp] = fp

    # ── 3. Walk DOC_DIR legacy (solo se ha contenuto) — SKIP in single-project mode ──
    if not single_project_path and os.path.isdir(DOC_DIR):
        doc_items = os.listdir(DOC_DIR)
        if doc_items:
            doc_filter = GitignoreFilter(DOC_DIR)
            files = await _walk_directory(
                DOC_DIR, folder_prefix=None,
                ignore_filter=doc_filter, visited_inodes=visited_inodes
            )
            for rp, fp in files.items():
                if rp not in current_files:
                    current_files[rp] = fp

    # ── 4. Pulizia file rimossi dal disco (SALTA in single-project mode) ──
    if not single_project_path:
        async with state.state_lock:
            state_keys = list(state.rag_state.keys())

        for rp in state_keys:
            if rp not in current_files:
                col_name = get_workspace_col_name(rp)
                try:
                    await state.qdrant.delete(
                        collection_name=col_name,
                        points_selector=Filter(must=[FieldCondition(key="filename", match=MatchValue(value=rp))])
                    )
                except Exception as e: logger.warning(f"Errore silenziato: {e}")
                try:
                    fp_col = get_file_profile_col_name()
                    fp_id = hashlib.md5(rp.encode()).hexdigest()
                    await state.qdrant.delete(
                        collection_name=fp_col,
                        points_selector=[fp_id]
                    )
                except Exception:
                    pass
                async with state.state_lock:
                    if rp in state.rag_state:
                        del state.rag_state[rp]
                        _save_file_state_unsafe(rp)
                logger.info(f"🗑️ Pulizia: Rimosso {rp} dai vettori.")

    # ── 4b. Pulizia collezioni Qdrant orfane (progetti eliminati dal disco) ──
    if not single_project_path:
        try:
            cols_info = await state.qdrant.get_collections()
            all_col_names = [c.name for c in cols_info.collections if c.name.startswith("collateral_docs_")]
            active_project_names = set()
            for rp in current_files:
                parts = rp.replace('\\', '/').split('/')
                if len(parts) > 1:
                    active_project_names.add(parts[0])
            for col_name in all_col_names:
                col_proj = col_name.replace("collateral_docs_", "")
                col_proj = re.sub(r'_v\d+$', '', col_proj)
                if col_proj == "default":
                    continue
                if col_proj not in active_project_names and not get_project_path(col_proj):
                    try:
                        await state.qdrant.delete_collection(collection_name=col_name)
                        logger.info(f"🗑️ Collezione orfana eliminata: {col_name} (progetto '{col_proj}' non trovato su disco)")
                    except Exception as e:
                        logger.warning(f"Errore eliminazione collezione orfana {col_name}: {e}")
        except Exception as e:
            logger.warning(f"Errore scansione collezioni orfane: {e}")

    # ── 5. Processamento file nuovi/modificati ──
    files_to_process = []
    for rp, fp in current_files.items():
        try:
            stat = os.stat(fp)
            mtime = stat.st_mtime
            size = stat.st_size
            
            async with state.state_lock:
                cached = state.rag_state.get(rp)
                
            if isinstance(cached, dict) and cached.get("mtime") == mtime and cached.get("size") == size:
                continue
                
            content_bytes = Path(fp).read_bytes()
            file_hash = hashlib.md5(content_bytes).hexdigest()
            
            if isinstance(cached, str) and cached == file_hash:
                async with state.state_lock:
                    state.rag_state[rp] = {"hash": file_hash, "mtime": mtime, "size": size}
                    _save_file_state_unsafe(rp)
                continue
            if isinstance(cached, dict) and cached.get("hash") == file_hash:
                async with state.state_lock:
                    state.rag_state[rp] = {"hash": file_hash, "mtime": mtime, "size": size}
                    _save_file_state_unsafe(rp)
                continue
                
            files_to_process.append((rp, fp, content_bytes, file_hash, mtime, size))
        except Exception as e:
            logger.error(f"Errore controllo {fp}: {e}")

    if files_to_process:
        logger.info(f"📚 Avvio ingestion Graph RAG per {len(files_to_process)} file...")
        sem = asyncio.Semaphore(MAX_CONCURRENT_EMBEDDINGS)
        
        batch_size = 20
        for i in range(0, len(files_to_process), batch_size):
            batch = files_to_process[i:i+batch_size]
            await asyncio.gather(*[process_single_file(rp, fp, sem, c, h, m, s) for rp, fp, c, h, m, s in batch])
            async with state.state_lock:
                _save_state_unsafe()
            logger.info(f"💾 Stato salvato su disco (elaborati {min(i+batch_size, len(files_to_process))}/{len(files_to_process)})")
            
        logger.info("✅ Sincronizzazione Graph RAG completata.")

    # Genera root node per ogni progetto (sostituisce .ai-skeleton.md)
    files_by_project: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for rp, fp in current_files.items():
        proj = rp.replace('\\', '/').split('/')[0] if '/' in rp.replace('\\', '/') else "default"
        files_by_project[proj].append((rp, fp))
    for proj_name, proj_files in files_by_project.items():
        await update_project_root_node(proj_name, proj_files)
    
    # Aggiorna cache del tree (SOLO in full scan — single-project ha dati parziali)
    if not single_project_path:
        await update_project_tree_cache()
    
    state.is_reindexing = False


# ==============================================================================
# PROJECT TREE & SKELETON
# ==============================================================================

async def update_project_root_node(project_name: str, files: list[tuple[str, str]]):
    """Genera skeleton text per un progetto, lo embedda e upserta un root node in Qdrant.

    Il root node è un punto speciale nella collezione Qdrant del progetto con:
    - type=project_root (filtrabile)
    - vettore = embedding del skeleton text (elenco signature function/class)
    - payload con metadati del progetto (total_files, linguaggi, last_indexed)
    """
    if not files:
        logger.warning(f"⚠️ Root node: progetto '{project_name}' senza file, skip")
        return

    # ── Genera skeleton text dalle signature dei file ──
    skeleton_lines = [f"# Code Skeleton: {project_name}\n"]
    lang_count: dict[str, int] = {}
    for rp, fp in sorted(files, key=lambda x: x[0]):
        ext = os.path.splitext(rp)[1].lower()
        lang_count[ext] = lang_count.get(ext, 0) + 1
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as file_obj:
                content = file_obj.read()
            signatures = []
            for idx, line in enumerate(content.split('\n')):
                line_strip = line.strip()
                if re.match(r'^(?:export\s+)?(?:async\s+)?(?:def|class|function|func|type|interface)\s+[a-zA-Z0-9_]', line_strip) or \
                   re.match(r'^(?:public|private|protected)\s+(?:static\s+)?(?:class|interface|enum|[a-zA-Z0-9_<>\[\]]+\s+[a-zA-Z0-9_]+)\(', line_strip):
                    signatures.append(f"  L{idx+1}: {line_strip}")
            if signatures:
                skeleton_lines.append(f"📄 {rp}")
                skeleton_lines.extend(signatures)
                skeleton_lines.append("")
        except Exception as e:
            logger.warning(f"Root node: errore lettura {fp} per skeleton: {e}")

    skeleton_text = "\n".join(skeleton_lines)
    if not skeleton_text.strip():
        logger.warning(f"⚠️ Root node: skeleton vuoto per '{project_name}', skip")
        return

    # ── Embedding dello skeleton text ──
    vector = await get_embedding(skeleton_text)
    if not vector:
        logger.warning(f"⚠️ Root node: embedding fallito per '{project_name}'")
        return

    # ── Upsert root node nella collezione del progetto ──
    col_name = get_project_col_name(project_name)
    await ensure_workspace_collection(col_name)

    root_id = hashlib.md5(f"root__{project_name}".encode()).hexdigest()
    try:
        await state.qdrant.delete(
            collection_name=col_name,
            points_selector=[root_id]
        )
    except Exception:
        pass

    await state.qdrant.upsert(
        collection_name=col_name,
        points=[PointStruct(
            id=root_id,
            vector=vector,
            payload={
                "type": "project_root",
                "project": project_name,
                "total_files": len(files),
                "languages": lang_count,
                "last_indexed": datetime.now().isoformat(),
                "skeleton_text": skeleton_text
            }
        )]
    )
    logger.info(f"🏗️ Root node aggiornato per progetto '{project_name}' ({len(files)} file, {sum(lang_count.values())} sorgenti)")

async def update_project_tree_cache():
    """Aggiorna la cache in background (eseguito in to_thread per non bloccare FastAPI)."""
    try:
        t = await generate_project_tree()
        state.project_tree_cache = t
    except Exception as e:
        logger.warning(f"Errore aggiornamento project tree cache: {e}")

async def _tree_for_dir(base_dir: str, visited_inodes: set) -> str:
    """Genera l'albero testuale per una directory, rispettando .gitignore."""
    if not base_dir or not os.path.isdir(base_dir):
        return ""
    filt = GitignoreFilter(base_dir)
    loop = asyncio.get_running_loop()
    t = ""
    for r, d, f in await loop.run_in_executor(None, lambda: list(os.walk(base_dir, followlinks=False))):
        try:
            st = os.stat(r)
            inode_key = (st.st_dev, st.st_ino)
            if inode_key in visited_inodes:
                d[:] = []
                continue
            visited_inodes.add(inode_key)
        except OSError:
            pass
        d[:] = [
            sub for sub in d
            if sub not in ('.git', 'node_modules', 'venv', 'vendor')
            and not filt.is_ignored(os.path.relpath(os.path.join(r, sub), base_dir))
        ]
        lvl = r.replace(base_dir, '').count(os.sep)
        t += f"{'    '*lvl}📁 {os.path.basename(r) or 'root'}/\n"
        for file in f:
            rp = os.path.relpath(os.path.join(r, file), base_dir)
            if not filt.is_ignored(rp) and rp.endswith(VALID_EXTENSIONS):
                t += f"{'    '*(lvl+1)}📄 {file}\n"
    return t


async def generate_project_tree():
    """Genera una rappresentazione testuale dell'albero del progetto indicizzato."""
    visited_inodes: set = set()
    parts = []
    if os.path.isdir(DOC_DIR):
        doc_tree = await _tree_for_dir(DOC_DIR, visited_inodes)
        if doc_tree:
            parts.append(f"📂 DOC_DIR:\n{doc_tree}")
    if WORKSPACE_DIR and os.path.isdir(WORKSPACE_DIR):
        ws_tree = await _tree_for_dir(WORKSPACE_DIR, visited_inodes)
        if ws_tree:
            parts.append(f"📂 WORKSPACE_DIR:\n{ws_tree}")
    return "\n\n".join(parts) if parts else "📂 PROGETTO:\n_(vuoto)_\n"

def _ls_for_dir(target_dir: str, base_dir: str, subpath: str | None = None):
    """Elenco file/cartelle per una directory specifica."""
    filt = GitignoreFilter(base_dir)
    if not os.path.exists(target_dir):
        return None
    if not os.path.isdir(target_dir):
        return {"error": f"📄 `{os.path.basename(target_dir)}` è un file, non una cartella."}
    
    t = f"📂 *{subpath or os.path.basename(target_dir) or 'Root'}*:\n"
    folders, files = [], []
    try:
        for item in os.listdir(target_dir):
            if item in ('.git', 'node_modules', 'venv', 'vendor'):
                continue
            full_path = os.path.join(target_dir, item)
            rel_path = os.path.relpath(full_path, base_dir)
            if filt.is_ignored(rel_path):
                continue
            if os.path.isdir(full_path):
                folders.append(item)
            else:
                files.append(item)
        folders.sort()
        files.sort()
        if not folders and not files:
            t += "_Cartella vuota._\n"
        else:
            t += f"_({len(folders)} cartelle, {len(files)} file)_\n"
        t += "\n💡 Seleziona un elemento per esplorarlo o scaricarlo."
        return {"text": t, "folders": folders, "files": files, "current_path": subpath}
    except Exception as e:
        return {"error": f"Errore: {e}"}


def generate_telegram_ls_data(subpath=None):
    """Genera l'elenco dei file e cartelle (tipo ls) per il bot Telegram.
    
    Se subpath è specificato, naviga in quella sottodirectory (prima cerca in
    WORKSPACE_DIR, poi in DOC_DIR). Se subpath è None, mostra la root che
    include sia i progetti di WORKSPACE_DIR che di DOC_DIR.
    """
    if subpath:
        # Prima cerca in WORKSPACE_DIR, poi in DOC_DIR
        if WORKSPACE_DIR and os.path.isdir(WORKSPACE_DIR):
            ws_target = os.path.normpath(os.path.join(WORKSPACE_DIR, subpath))
            if ws_target.startswith(WORKSPACE_DIR) and os.path.exists(ws_target):
                return _ls_for_dir(ws_target, WORKSPACE_DIR, subpath)
        doc_target = os.path.normpath(os.path.join(DOC_DIR, subpath))
        if doc_target.startswith(DOC_DIR):
            return _ls_for_dir(doc_target, DOC_DIR, subpath)
        return {"error": f"Percorso non trovato: {subpath}"}
    
    # Root view: merge WORKSPACE_DIR + DOC_DIR progetti
    ws_projects: list[str] = []
    doc_projects: list[str] = []
    seen: set = set()
    
    if WORKSPACE_DIR and os.path.isdir(WORKSPACE_DIR):
        for item in sorted(os.listdir(WORKSPACE_DIR)):
            if item.startswith('.') or item in ('.git', 'node_modules', 'venv', 'vendor'):
                continue
            full = os.path.join(WORKSPACE_DIR, item)
            if os.path.isdir(full):
                ws_projects.append(item)
                seen.add(item.lower())
    
    if os.path.isdir(DOC_DIR):
        for item in sorted(os.listdir(DOC_DIR)):
            if item.startswith('.'):
                continue
            full = os.path.join(DOC_DIR, item)
            if os.path.isdir(full) and item.lower() not in seen:
                doc_projects.append(item)
    
    all_projects = sorted(set(ws_projects + doc_projects))
    t = f"📂 *PROGETTI (Workspace)*:\n_({len(all_projects)} progetti)_\n\n" if all_projects else "📂 *PROGETTI:*\n_(nessun progetto)_\n"
    t += "\n💡 Seleziona un progetto per esplorarlo."
    return {
        "text": t,
        "folders": all_projects,
        "files": [],
        "current_path": None
    }


# ==============================================================================
# RICERCA VETTORIALE
# ==============================================================================

async def search_file_profiles(query_vector: list[float], top_k: int = 5) -> list[dict]:
    """Cerca file semanticamente simili nella collezione file_profiles."""
    try:
        fp_col = get_file_profile_col_name()
        exists = await state.qdrant.collection_exists(collection_name=fp_col)
        if not exists:
            return []
        res = await state.qdrant.query_points(
            collection_name=fp_col,
            query=query_vector,
            limit=top_k,
            with_payload=True
        )
        return [
            {
                "filename": p.payload.get("filename", ""),
                "project": p.payload.get("project", ""),
                "score": p.score,
                "deps": p.payload.get("deps", []),
                "chunk_count": p.payload.get("chunk_count", 0)
            }
            for p in res.points
        ]
    except Exception as e:
        logger.warning(f"Errore search_file_profiles: {e}")
        return []


async def search_documents(query, is_project_query=False, project_name=None, user=None):
    """Cerca documenti rilevanti nei Workspace Qdrant isolati."""
    try:
        # Alta priorità (0) per bypassare l'ingestione in background
        vector = await get_embedding(query, priority=0, is_query=True)
        if not vector:
            return ""

        # Docs/ special threshold: se la query menziona documentazione
        is_docs_query = any(kw in query.lower() for kw in (' docs', '/docs', 'docs/', 'documentazion', 'documentation', 'documenti'))
        top_k = RAG_CONFIG["top_k_docs"] if (is_docs_query or not is_project_query) else RAG_CONFIG["top_k_code"]
        required_score = RAG_CONFIG["score_threshold_docs"] if (is_docs_query or not is_project_query) else RAG_CONFIG["score_threshold_code"]
        
        # Individua i workspace appropriati
        try:
            collections_info = await state.qdrant.get_collections()
            col_names = [c.name for c in collections_info.collections if c.name.startswith("collateral_docs_")]
        except Exception:
            col_names = list(state.created_collections)

        # Estrae il nome del workspace rimuovendo prefisso e suffisso versione (es. _v3, _v4)
        def _ws_name(col):
            name = col.replace("collateral_docs_", "")
            name = re.sub(r'_v\d+$', '', name)  # rimuove _v3, _v4, ecc.
            return name

        target_cols = []

        # Se un progetto specifico è stato identificato, cerca SOLO in quella collezione
        if project_name:
            pn_normalized = project_name.replace('-', '_').lower()
            for c in col_names:
                if _ws_name(c).lower() == pn_normalized:
                    target_cols.append(c)
                    # Se il progetto è Docs, usa soglie documentazione
                    if _ws_name(c).lower() in ('docs', 'documentation', 'documents'):
                        top_k = RAG_CONFIG["top_k_docs"]
                        required_score = RAG_CONFIG["score_threshold_docs"]
                    break
            if not target_cols:
                logger.warning(f"Nessuna collezione trovata per progetto: {project_name}")
                return ""

        if not target_cols:
            query_lower = query.lower()
            # Longest name first: evita che "RumpiIPTV" matchi prima di "RumpiIPTV_OLD"
            sorted_cols = sorted(col_names, key=lambda c: len(_ws_name(c)), reverse=True)
            for c in sorted_cols:
                ws = _ws_name(c)
                ws_lower = ws.lower()
                # Match diretto (nomi singola parola come "NeuroNet", "SlotBuilder")
                if ws_lower in query_lower:
                    target_cols.append(c)
                # Match con underscore→spazio (nomi multi-parola: "StreamAI_IPTV" → "streamai iptv")
                elif ws_lower.replace('_', ' ') in query_lower:
                    target_cols.append(c)
                # Match con underscore→trattino (nomi con trattino: "StreamAI_IPTV" → "streamai-iptv")
                elif ws_lower.replace('_', '-') in query_lower:
                    target_cols.append(c)
                elif ws == "default":
                    target_cols.append(c)

        # Se nessuna collezione è stata identificata per nome:
        # - Per query di codice (is_project_query=True) → restituisce vuoto.
        #   L'utente deve specificare un progetto per evitare contaminazione.
        # - Per query generiche → cerca su tutte le collezioni, max 2 per collezione.
        cross_collection_mode = not target_cols
        if cross_collection_mode:
            if is_project_query:
                logger.info(f"📁 Nessun progetto rilevato per query codice, RAG vuoto (evita contaminazione)")
                return ""
            target_cols = col_names
            per_col_limit = max(1, top_k // max(len(col_names), 1))
        else:
            per_col_limit = top_k

        # ── ACL filter: non-admin users see only authorized projects ──
        if user and user.get("role") != "admin":
            allowed = user.get("allowed_projects", [])
            if isinstance(allowed, str):
                import json
                try:
                    allowed = json.loads(allowed)
                except (json.JSONDecodeError, TypeError):
                    allowed = []
            if allowed == ["*"]:
                pass  # All projects accessible
            elif allowed:
                allowed_lower = [p.lower() for p in allowed]
                # If a specific project was requested, check it's allowed
                if project_name and project_name.lower() not in allowed_lower:
                    logger.info(
                        "🔒 User %s not authorized for project %s",
                        user.get("username"), project_name,
                    )
                    return ""
                target_cols = [
                    c for c in target_cols
                    if _ws_name(c).lower() in allowed_lower
                ]
                if not target_cols:
                    logger.info(
                        "🔒 User %s has no accessible projects for this query",
                        user.get("username"),
                    )
                    return ""
            else:
                # allowed_projects = [] → no RAG access
                logger.info(
                    "🔒 User %s has no RAG projects configured",
                    user.get("username"),
                )
                return ""

        async def _query_col(col_name):
            try:
                res = await state.qdrant.query_points(
                    collection_name=col_name,
                    query=vector,
                    limit=per_col_limit,
                    score_threshold=required_score,
                    with_payload=True
                )
                ws = _ws_name(col_name)
                for point in res.points:
                    point.payload["_project"] = ws
                return res.points
            except Exception as e:
                logger.warning(f"Errore silenziato: {e}")
                return []

        col_results = await asyncio.gather(*[_query_col(c) for c in target_cols])
        results = []
        for pts in col_results:
            results.extend(pts)

        results = sorted(results, key=lambda x: x.score, reverse=True)[:10]

        # Reranking: Qwen3-Reranker su CPU (o FlashRank fallback)
        if _reranker and results:
            try:
                passages = [{"id": i, "text": r.payload.get("text", ""), "meta": r.payload} for i, r in enumerate(results)]
                reranked = _reranker(query, passages)
                best_results = reranked[:top_k]
            except Exception as e:
                logger.warning(f"Errore Reranker: {e}")
                best_results = [{"text": r.payload.get("text", ""), "meta": r.payload} for r in results[:top_k]]
        else:
            best_results = [{"text": r.payload.get("text", ""), "meta": r.payload} for r in results[:top_k]]

        # ── Parent-child ricostruzione ──────────────────────────────────────
        parent_ids = set()
        for r in best_results:
            pid = r["meta"].get("parent_chunk_id")
            if pid:
                parent_ids.add(pid)

        parent_siblings = {}
        if parent_ids:
            for col in target_cols:
                try:
                    sibling_scroll, _ = await asyncio.wait_for(
                        state.qdrant.scroll(
                            collection_name=col,
                            scroll_filter=Filter(should=[
                                FieldCondition(key="parent_chunk_id", match=MatchValue(value=pid))
                                for pid in parent_ids
                            ]),
                            limit=100,
                            with_payload=True
                        ),
                        timeout=5.0
                    )
                    for s in sibling_scroll:
                        pid = s.payload.get("parent_chunk_id")
                        if pid not in parent_siblings:
                            parent_siblings[pid] = []
                        parent_siblings[pid].append(s)
                except asyncio.TimeoutError:
                    logger.warning(f"Qdrant scroll parent reconstruction timeout su {col}")
                except Exception:
                    pass

            # Ricostruisci il testo del genitore da tutti i frammenti
            for pid in list(parent_siblings.keys()):
                siblings = sorted(parent_siblings[pid], key=lambda s: s.payload.get("chunk_index", 0))
                parent_text = "\n\n".join(s.payload.get("text", "") for s in siblings)
                parent_siblings[pid] = {
                    "text": parent_text,
                    "meta": siblings[0].payload,
                    "sibling_count": len(siblings)
                }

        primary_docs, deps_to_search = [], set()
        seen_parents = set()
        seen_filenames = set()
        for r in best_results:
            filename = r["meta"].get("filename")
            pid = r["meta"].get("parent_chunk_id")
            project_label = r["meta"].get("_project", "")
            project_prefix = f"[{project_label}] " if project_label else ""

            if pid and pid in parent_siblings and pid not in seen_parents:
                seen_parents.add(pid)
                parent = parent_siblings[pid]
                hierarchy = parent["meta"].get("section_hierarchy")
                hierarchy_prefix = f"// CONTESTO GERARCHICO: {' -> '.join(hierarchy)}\n" if hierarchy else ""
                if filename:
                    seen_filenames.add(filename)
                    primary_docs.append(
                        f"📄 File Primario ({project_prefix}{filename}) [Padre: {parent['sibling_count']} frammenti]:\n"
                        f"```\n{hierarchy_prefix}{parent['text']}\n```"
                    )
            elif not pid:
                hierarchy = r["meta"].get("section_hierarchy")
                hierarchy_prefix = f"// CONTESTO GERARCHICO: {' -> '.join(hierarchy)}\n" if hierarchy else ""
                if filename:
                    seen_filenames.add(filename)
                    primary_docs.append(f"📄 File Primario ({project_prefix}{filename}):\n```\n{hierarchy_prefix}{r['text']}\n```")
            if r["meta"].get("deps"):
                deps_to_search.update(r["meta"].get("deps"))

        # ── Dependency graph traversal ──────────────────────────────────────
        secondary_docs = []
        if is_project_query and deps_to_search:
            deps_list = list(deps_to_search)[:15]
            dep_scroll_tasks = []
            for col in target_cols:
                dep_scroll_tasks.append(
                    asyncio.ensure_future(
                        state.qdrant.scroll(
                            collection_name=col,
                            scroll_filter=Filter(should=[
                                FieldCondition(key="filename", match=MatchText(text=dep))
                                for dep in deps_list
                            ]),
                            limit=20,
                            with_payload=True
                        )
                    )
                )
            dep_raw = []
            for fut in asyncio.as_completed(dep_scroll_tasks):
                try:
                    res, _ = await fut
                    dep_raw.extend(res)
                except Exception:
                    pass

            # Apply parent-child reconstruction to dep results
            dep_parent_ids = set()
            for r in dep_raw:
                pid = r.payload.get("parent_chunk_id")
                if pid:
                    dep_parent_ids.add(pid)

            dep_parent_texts = {}
            if dep_parent_ids:
                for col in target_cols:
                    try:
                        siblings, _ = await asyncio.wait_for(
                            state.qdrant.scroll(
                                collection_name=col,
                                scroll_filter=Filter(should=[
                                    FieldCondition(key="parent_chunk_id", match=MatchValue(value=pid))
                                    for pid in dep_parent_ids
                                ]),
                                limit=100,
                                with_payload=True
                            ),
                            timeout=5.0
                        )
                        groups = {}
                        for s in siblings:
                            gpid = s.payload.get("parent_chunk_id")
                            if gpid:
                                groups.setdefault(gpid, []).append(s)
                        for gpid, group in groups.items():
                            if gpid not in dep_parent_texts:
                                group.sort(key=lambda x: x.payload.get("chunk_index", 0))
                                dep_parent_texts[gpid] = "\n\n".join(
                                    s.payload.get("text", "") for s in group
                                )
                    except (asyncio.TimeoutError, Exception):
                        pass

            seen_dep = set()
            for r in dep_raw:
                filename = r.payload.get("filename")
                if not filename or filename in seen_filenames or filename in seen_dep:
                    continue
                seen_dep.add(filename)
                pid = r.payload.get("parent_chunk_id")
                text = dep_parent_texts.get(pid, r.payload.get("text", ""))
                secondary_docs.append(
                    f"🔗 Dipendenza ({filename}):\n```\n{text}\n```"
                )

        # Raccogli e inietta le regole di progetto (se presenti) per i workspace coinvolti
        workspaces = set()
        for r in best_results:
            filename = r["meta"].get("filename")
            if filename:
                parts = filename.replace('\\', '/').split('/')
                if len(parts) > 1:
                    workspaces.add(parts[0])
                else:
                    workspaces.add("") # root

        rules_docs = []
        for ws in workspaces:
            ws_path = os.path.join(DOC_DIR, ws) if ws else DOC_DIR
            rule_files_to_check = [
                ".ai-rules.md", ".cursorrules", "RULES.md", "AGENT.md", ".agent.md", 
                ".copilot-instructions.md", ".github/copilot-instructions.md"
            ]
            for rule_file in rule_files_to_check:
                rule_path = os.path.join(ws_path, rule_file)
                if os.path.exists(rule_path):
                    try:
                        with open(rule_path, "r", encoding="utf-8") as rf:
                            content = rf.read()
                            rules_docs.append(f"📜 Regole del Progetto ({rule_file} in {ws or 'root'}):\n```\n{content}\n```")
                    except Exception as e: logger.warning(f"Errore silenziato: {e}")
                    break

        # ── Arricchimento Synaptiq: annota chunk RAG con metadati grafo ────
        sy_blocks = []
        if is_project_query and SYNAPTIQ_ENABLED:
            try:
                from graph.synaptiq_engine import synaptiq_engine
                if synaptiq_engine.is_initialized:
                    q = query[:100]
                    sy_results = await synaptiq_engine.hybrid_search(q, limit=4)
                    if sy_results:
                        lines = ["\n<SYNAPTIQ_ENRICH>"]
                        for s in sy_results[:4]:
                            label = s.get("label", "?")
                            fpath = s.get("file_path", "?")
                            name = s.get("node_name", "?")
                            lines.append(f"  {name} ({label}) — {fpath}")
                        lines.append("</SYNAPTIQ_ENRICH>")
                        sy_blocks = ["\n".join(lines)]
            except Exception:
                pass

        return "\n\n".join(rules_docs + primary_docs + secondary_docs + sy_blocks)
    except Exception as e:
        logger.error(f"Errore search_documents: {e}")
        return ""


async def list_rag_projects(user=None) -> list[str]:
    """Restituisce la lista dei nomi di progetto indicizzati nel RAG (collezioni Qdrant).

    If user is provided, filters by user's allowed_projects (admins see all).
    """
    try:
        collections_info = await state.qdrant.get_collections()
        projects = []
        for c in collections_info.collections:
            if c.name.startswith("collateral_docs_"):
                name = c.name.replace("collateral_docs_", "")
                name = re.sub(r'_v\d+$', '', name)
                if name and name != "default":
                    projects.append(name)
        all_projects = sorted(set(projects))

        # ACL filter
        if user and user.get("role") != "admin":
            allowed = user.get("allowed_projects", [])
            if isinstance(allowed, str):
                import json
                try:
                    allowed = json.loads(allowed)
                except (json.JSONDecodeError, TypeError):
                    allowed = []
            if allowed == ["*"]:
                return all_projects
            elif allowed:
                allowed_lower = [p.lower() for p in allowed]
                return [p for p in all_projects if p.lower() in allowed_lower]
            else:
                return []

        return all_projects
    except Exception as e:
        logger.warning(f"Errore list_rag_projects: {e}")
        return []


def _alias_to_project(projects: list[str]) -> dict[str, str]:
    """Costruisce mappa alias → nome progetto (gestisce - _ spazio)."""
    alias_map = {}
    for p in projects:
        alias_map[p.lower()] = p
        alias_map[p.replace('_', '-').lower()] = p
        alias_map[p.replace('_', ' ').lower()] = p
    return alias_map


def _match_project_in_query(query: str, alias_to_project: dict[str, str]) -> str | None:
    """Cerca un progetto conosciuto in una singola query.
    
    Longest-match-first: gli alias più lunghi vengono controllati prima,
    per evitare che "RumpiIPTV" matchi prima di "RumpiIPTV_OLD" quando
    la query menziona "RumpiIPTV-OLD".
    """
    query_lower = query.lower()

    # Longest alias first per evitare falsi positivi con prefissi
    sorted_aliases = sorted(alias_to_project.keys(), key=len, reverse=True)

    # Cerca menzione diretta (parola intera con word boundary)
    for alias in sorted_aliases:
        project = alias_to_project[alias]
        if re.search(r'\b' + re.escape(alias) + r'\b', query_lower):
            return project

    # Cerca prefissi di path: "NeuroNet/src/main.py" o "SlotBuilder/cmd/..."
    path_match = re.search(r'\b([A-Za-z][\w.-]*)[/\\]', query)
    if path_match:
        dir_name = path_match.group(1).lower()
        for alias in sorted_aliases:
            project = alias_to_project[alias]
            if dir_name == alias:
                return project

    return None


async def detect_project(query: str) -> str | None:
    """Identifica a quale progetto si riferisce la query dell'utente."""
    projects = await list_rag_projects()
    if not projects:
        return None
    alias_map = _alias_to_project(projects)
    return _match_project_in_query(query, alias_map)


async def detect_project_in_conversation(user_messages: list[str]) -> str | None:
    """Cerca in tutta la conversazione (dal più recente) a quale progetto ci si riferisce.
    Carica la lista progetti una volta sola invece che per ogni messaggio."""
    if not user_messages:
        return None
    projects = await list_rag_projects()
    if not projects:
        return None
    alias_map = _alias_to_project(projects)
    # Scorri dal più recente al più vecchio
    for msg in reversed(user_messages):
        result = _match_project_in_query(msg, alias_map)
        if result:
            return result
    return None


# ==============================================================================
# WATCHDOG REAL-TIME
# ==============================================================================

if WATCHDOG_ENABLED:
    class DynamicRagEventHandler(FileSystemEventHandler):
        """Handler per eventi filesystem: re-embedding automatico al salvataggio."""

        def __init__(self, loop, queue, doc_dir, path_mapping=None):
            self.loop, self.queue, self.doc_dir = loop, queue, doc_dir
            self.path_mapping = path_mapping or {}
            self.ignore_filter = GitignoreFilter(doc_dir)

        def _get_canonical_path(self, path):
            for real_path, symlink_path in self.path_mapping.items():
                if path.startswith(real_path):
                    return path.replace(real_path, symlink_path, 1)
            return path

        def is_valid(self, path, is_dir):
            if is_dir:
                return False
            if not path.endswith(('.go', '.py', '.jsx', '.tsx', '.js', '.ts', '.md', '.json', '.txt', '.c', '.cpp', '.h', '.hpp', '.java', '.rs', '.sql', '.yaml', '.yml')):
                return False
            if self.ignore_filter.is_ignored(os.path.relpath(path, self.doc_dir)):
                return False
            return True

        def _safe_queue(self, action, path):
            """Invia un evento alla coda asincrona, catturando eccezioni per non killare il thread dispatch."""
            try:
                asyncio.run_coroutine_threadsafe(self.queue.put((action, path)), self.loop)
            except Exception as e:
                logger.error(f"Watchdog: Errore invio evento {action} per {path}: {e}")

        def on_created(self, event):
            canon_path = self._get_canonical_path(event.src_path)
            if self.is_valid(canon_path, event.is_directory):
                self._safe_queue('process', canon_path)

        def on_modified(self, event):
            canon_path = self._get_canonical_path(event.src_path)
            if self.is_valid(canon_path, event.is_directory):
                self._safe_queue('process', canon_path)

        def on_deleted(self, event):
            canon_path = self._get_canonical_path(event.src_path)
            if self.is_valid(canon_path, event.is_directory):
                self._safe_queue('delete', canon_path)

        def on_moved(self, event):
            canon_src = self._get_canonical_path(event.src_path)
            canon_dest = self._get_canonical_path(event.dest_path)
            if self.is_valid(canon_src, event.is_directory):
                self._safe_queue('delete', canon_src)
            if self.is_valid(canon_dest, event.is_directory):
                self._safe_queue('process', canon_dest)


def _get_watchdog_rel_path(fp: str) -> str:
    """Calcola il path relativo appropriato in base a quale watchdog ha generato l'evento."""
    if WORKSPACE_DIR and fp.startswith(WORKSPACE_DIR):
        return os.path.relpath(fp, WORKSPACE_DIR)
    return os.path.relpath(fp, DOC_DIR)


def _find_project_root(filepath: str) -> str | None:
    """Trova il project root a partire da un filepath assoluto.

    Cerca prima in ``WORKSPACE_PROJECTS`` poi in ``EXTERNAL_PROJECTS``.
    """
    norm = os.path.normpath(filepath) + os.sep
    # Cerca in WORKSPACE_PROJECTS
    for proj in WORKSPACE_PROJECTS:
        p_norm = os.path.normpath(proj) + os.sep
        if norm.startswith(p_norm):
            return os.path.normpath(proj)
    # Cerca in EXTERNAL_PROJECTS
    for ep in parse_external_projects():
        p_norm = os.path.normpath(ep) + os.sep
        if norm.startswith(p_norm):
            return os.path.normpath(ep)
    return None

# Public alias for _find_project_root
find_project_root = _find_project_root


async def rag_queue_worker():
    """Worker asincrono che processa eventi di file dalla coda del watchdog con debounce."""
    sem = asyncio.Semaphore(MAX_CONCURRENT_EMBEDDINGS)
    while True:
        try:
            action, filepath = await state.file_event_queue.get()
            pending = {filepath: action}
            
            # Debounce di 1 secondo per catturare eventi IDE duplicati
            await asyncio.sleep(WATCHDOG_BATCH_DELAY)
            
            while not state.file_event_queue.empty():
                try:
                    a, f = state.file_event_queue.get_nowait()
                    pending[f] = a
                    state.file_event_queue.task_done()
                except asyncio.QueueEmpty:
                    break
                    
            for fp, act in pending.items():
                if state.is_reindexing:
                    logger.debug("Re-indexing in corso, salto evento watchdog")
                    continue
                rel_path = _get_watchdog_rel_path(fp)
                try:
                    if act == 'delete':
                        col_name = get_workspace_col_name(rel_path)
                        try:
                            await state.qdrant.delete(
                                collection_name=col_name,
                                points_selector=Filter(must=[FieldCondition(key="filename", match=MatchValue(value=rel_path))])
                            )
                        except Exception as e: logger.warning(f"Errore silenziato: {e}")
                        async with state.state_lock:
                            if rel_path in state.rag_state:
                                del state.rag_state[rel_path]
                                _save_file_state_unsafe(rel_path)
                        logger.info(f"🗑️ Watcher: Rimosso {rel_path} dai vettori.")
                    elif act == 'process':
                        if os.path.exists(fp):
                            await process_single_file(rel_path, fp, sem)
                except Exception as e:
                    logger.error(f"Errore Coda Watcher su {fp}: {e}")
            
            # Flush of the state file ONCE per event-batch (Fix 2.3)
            async with state.state_lock:
                _save_state_unsafe()
            
            # Aggiorna la cache del tree (Fix 9.4)
            await update_project_tree_cache()

            # ── Notifica Synaptiq dei file cambiati (debounced per progetto) ──
            if SYNAPTIQ_ENABLED and pending:
                try:
                    from graph.synaptiq_engine import synaptiq_engine
                    # Usa il primo file del batch per determinare il progetto
                    first_fp = next(iter(pending))
                    project_root = _find_project_root(first_fp)
                    if project_root:
                        synaptiq_engine.notify_file_event(project_root)
                except Exception:
                    pass

            state.file_event_queue.task_done()
        except asyncio.CancelledError:
            logger.info("🛑 Spegnimento Graceful del worker Watchdog.")
            return
        except Exception as e:
            logger.error(f"Watchdog worker crashato, riavvio: {e}", exc_info=True)
            await asyncio.sleep(5)

# Cache semantica e Web Knowledge — estratte in rag_cache.py
from rag.cache import (
    semantic_cache_search,
    semantic_cache_store,
    semantic_cache_clear,
    ensure_web_knowledge_collection,
    save_web_knowledge,
    search_web_knowledge,
)
