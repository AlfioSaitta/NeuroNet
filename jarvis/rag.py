"""
Pipeline RAG completa — GitignoreFilter, AST chunking, ingestion documentale,
ricerca vettoriale, watchdog real-time e generazione project tree.
"""

import os
import json
import hashlib
import uuid
import re
import asyncio
from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchText, PointStruct, VectorParams, Distance
from pathlib import Path

from config import (
    logger, QDRANT_HOST, DOC_COLLECTION, DOC_DIR,
    STATE_FILE, CHUNK_SIZE, CHUNK_OVERLAP, MAX_CONCURRENT_EMBEDDINGS,
    RAG_CONFIG, AST_ENABLED, GO, PY, JS, TSX,
    VECTOR_DB_VERSION, FLASHRANK_MODEL, Qwen3_RERANKER_MODEL, RERANKER_DEVICE,
    WATCHDOG_BATCH_DELAY, SEMANTIC_CACHE_THRESHOLD,
    C, CPP, JAVA, RUST, SQL, YAML,
    PATHSPEC_ENABLED, WATCHDOG_ENABLED, EMBEDDING_DIMS,
    EXTERNAL_PROJECTS
)
import state

# ==============================================================================
# RERANKER: Qwen3-Reranker su CPU (priority), FlashRank fallback
# ==============================================================================
# Entrambi girano su CPU: Qwen3 usa transformers, FlashRank usa ONNX.
# Qwen3 offre multilingua (100+ lingue, incluso italiano) e punteggio MTEB-Code 73.42.
_reranker = None

os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")

try:
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM

    _device = torch.device(RERANKER_DEVICE)
    _tok = AutoTokenizer.from_pretrained(Qwen3_RERANKER_MODEL, padding_side='left', trust_remote_code=True)
    _model = AutoModelForCausalLM.from_pretrained(
        Qwen3_RERANKER_MODEL,
        dtype=torch.float16,
        low_cpu_mem_usage=True,
        trust_remote_code=True
    ).to(_device).eval()

    _yes_id = _tok.convert_tokens_to_ids("yes")
    _no_id = _tok.convert_tokens_to_ids("no")
    logger.info(f"🔀 Reranker: Qwen3-Reranker su {RERANKER_DEVICE} ({Qwen3_RERANKER_MODEL})")

    def _reranker(query, passages):
        texts = [f"Query: {query}\nDocument: {p.get('text', '')}\nRelevance:" for p in passages]
        inputs = _tok(texts, padding=True, truncation=True, max_length=8192, return_tensors="pt").to(_device)
        with torch.no_grad():
            logits = _model(**inputs).logits[:, -1, :]
        scores = torch.softmax(torch.stack([logits[:, _no_id], logits[:, _yes_id]], dim=-1), dim=-1)[:, 1]
        for p, s in zip(passages, scores.tolist()):
            p["score"] = round(s, 4)
        return sorted(passages, key=lambda x: x["score"], reverse=True)

    _reranker = _reranker

except Exception as e:
    logger.warning(f"Qwen3-Reranker non caricabile ({e}), fallback su FlashRank...")
    try:
        from flashrank import Ranker, RerankRequest
        _flash = Ranker(model_name=FLASHRANK_MODEL, cache_dir="/app/mem0_data_v3/flashrank_cache")

        def _reranker(query, passages):
            req = RerankRequest(query=query, passages=passages)
            return _flash.rerank(req)

        _reranker = _reranker
        logger.info(f"🔀 Reranker: FlashRank ({FLASHRANK_MODEL})")
    except Exception as e2:
        logger.warning(f"FlashRank non caricabile ({e2}). Reranker disattivato.")

if AST_ENABLED:
    from tree_sitter import Parser

if PATHSPEC_ENABLED:
    import pathspec

if WATCHDOG_ENABLED:
    from watchdog.events import FileSystemEventHandler


# ==============================================================================
# FILTRO GITIGNORE
# ==============================================================================

class GitignoreFilter:
    """Rispetta i file .gitignore nei progetti monitorati."""

    def __init__(self, doc_dir=DOC_DIR):
        self.specs = {}
        visited_inodes = set()
        for root, dirs, files in os.walk(doc_dir, followlinks=True):
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
            if ".gitignore" in files:
                base = os.path.relpath(root, doc_dir).replace('\\', '/')
                base = "" if base == '.' else base
                with open(os.path.join(root, ".gitignore"), 'r', errors='ignore') as f:
                    if PATHSPEC_ENABLED:
                        self.specs[base] = pathspec.PathSpec.from_lines('gitwildmatch', f)

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
            
        from llm_engine import engine
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
# CHUNKING AST-AWARE
# ==============================================================================

def extract_dependencies(content, ext):
    """Estrae le dipendenze (import/from/require) dalla testa del file."""
    deps = set()
    head = content[:2500]
    if ext == '.go':
        matches = re.findall(r'"([^"]+)"', head)
        for m in matches:
            deps.add(m.split('/')[-1])
    elif ext == '.py':
        matches = re.findall(r'^(?:from|import)\s+([a-zA-Z0-9_\.]+)', head, re.MULTILINE)
        for m in matches:
            deps.add(m.split('.')[0])
    elif ext in ['.js', '.jsx', '.ts', '.tsx']:
        matches = re.findall(r'from\s+[\'"]([^(\'|")]+)[\'"]', head)
        for m in matches:
            deps.add(m.split('/')[-1].replace('.js', '').replace('.ts', ''))
    elif ext == '.md':
        # Per Markdown cerchiamo i link [Testo](file.md) in tutto il documento
        matches = re.findall(r'\[.*?\]\((.*?\.md.*?)\)', content)
        for m in matches:
            deps.add(m.split('/')[-1].split('#')[0]) # Rimuoviamo gli anchor e prendiamo il nome
    return list(deps)


def ast_code_chunking(content, filepath):
    """Chunking intelligente: usa Tree-sitter per estrarre funzioni/classi mantenendo il contesto gerarchico."""
    if not AST_ENABLED:
        return [content[i:i+CHUNK_SIZE] for i in range(0, len(content), CHUNK_SIZE - CHUNK_OVERLAP)]

    ext = os.path.splitext(filepath)[1].lower()
    parser = Parser()

    if ext == '.go':
        parser.language = GO
        nodes = ['function_declaration', 'method_declaration', 'type_declaration', 'const_declaration', 'var_declaration', 'struct_type', 'interface_type']
    elif ext == '.py':
        parser.language = PY
        nodes = ['function_definition', 'class_definition', 'async_function_definition']
    elif ext in ['.js', '.jsx']:
        parser.language = JS
        nodes = ['function_declaration', 'lexical_declaration', 'class_declaration', 'arrow_function', 'method_definition']
    elif ext in ['.ts', '.tsx']:
        parser.language = TSX
        nodes = ['function_declaration', 'lexical_declaration', 'class_declaration', 'arrow_function', 'method_definition', 'interface_declaration', 'type_alias_declaration']
    elif ext in ['.c', '.h']:
        parser.language = C
        nodes = ['function_definition', 'declaration', 'struct_specifier', 'enum_specifier']
    elif ext in ['.cpp', '.hpp', '.cc', '.cxx']:
        parser.language = CPP
        nodes = ['function_definition', 'class_specifier', 'struct_specifier', 'enum_specifier', 'namespace_definition', 'template_declaration']
    elif ext == '.java':
        parser.language = JAVA
        nodes = ['method_declaration', 'class_declaration', 'interface_declaration', 'enum_declaration']
    elif ext == '.rs':
        parser.language = RUST
        nodes = ['function_item', 'struct_item', 'enum_item', 'impl_item', 'trait_item']
    elif ext == '.sql':
        parser.language = SQL
        nodes = ['statement']
    elif ext in ['.yaml', '.yml']:
        parser.language = YAML
        nodes = ['document', 'block_mapping_pair', 'block_sequence_item']
    elif ext == '.md':
        # Markdown Semantic Chunking
        chunks = []
        current_chunk = []
        current_len = 0
        for line in content.split('\n'):
            if line.startswith('#') and current_len > 200:
                chunks.append('\n'.join(current_chunk))
                current_chunk = [line]
                current_len = len(line)
            else:
                current_chunk.append(line)
                current_len += len(line) + 1
        if current_chunk:
            chunks.append('\n'.join(current_chunk))
        
        final_md_chunks = []
        for chunk in chunks:
            if len(chunk) > CHUNK_SIZE:
                for i in range(0, len(chunk), CHUNK_SIZE - CHUNK_OVERLAP):
                    final_md_chunks.append(chunk[i:i+CHUNK_SIZE])
            else:
                final_md_chunks.append(chunk)
        return final_md_chunks
    else:
        return [content[i:i+CHUNK_SIZE] for i in range(0, len(content), CHUNK_SIZE - CHUNK_OVERLAP)]

    try:
        tree = parser.parse(bytes(content, "utf8"))
        chunks = []

        # CHUNK 0: PREAMBOLO (prime 50 righe)
        preamble = "\n".join(content.split("\n")[:50])
        if len(preamble.strip()) > 20:
            chunks.append(f"PREAMBOLO:\n{preamble}")

        def get_signature(n):
            # Tenta di estrarre la firma (es. class MyClass extends Parent)
            b = content[n.start_byte:n.end_byte]
            lines = b.split('\n')
            sig = []
            for line in lines[:3]: # analizza le prime 3 righe per trovare la firma
                s = line.strip()
                sig.append(s)
                if '{' in s or ':' in s:
                    break
            return " ".join(sig).split('{')[0].strip()

        context_stack = []

        def traverse(n):
            is_context = n.type in ['class_definition', 'class_declaration', 'class_specifier', 'struct_specifier', 'interface_declaration', 'impl_item', 'type_declaration', 'namespace_definition']
            
            if is_context:
                sig = get_signature(n)
                if sig: context_stack.append(sig)

            if n.type in nodes:
                b = content[n.start_byte:n.end_byte]
                if len(b.strip()) > 20:
                    start_line = n.start_point[0] + 1
                    end_line = n.end_point[0] + 1
                    
                    # Prepend context (e.g. parent class) to methods/fields
                    prefix = ""
                    if not is_context and context_stack:
                        prefix = f"// CONTESTO GERARCHICO: {' -> '.join(context_stack)}\n"
                        
                    chunks.append(f"RIGHE {start_line}-{end_line}:\n{prefix}{b}")

            for c in n.children:
                traverse(c)
                
            if is_context and context_stack:
                context_stack.pop()

        traverse(tree.root_node)

        if not chunks:
            return [content[i:i+CHUNK_SIZE] for i in range(0, len(content), CHUNK_SIZE - CHUNK_OVERLAP)]

        # Fondere dinamicamente piccoli frammenti AST consecutivi
        merged_chunks = []
        current_chunk = ""
        for c in chunks:
            if not current_chunk:
                current_chunk = c
            else:
                if len(current_chunk) + len(c) < CHUNK_SIZE:
                    words1 = set(current_chunk.split())
                    words2 = set(c.split())
                    overlap = len(words1 & words2) / len(words1 | words2) if words1 and words2 else 0
                    if overlap > 0.05 or len(c) < 300:
                        current_chunk += "\n\n" + c
                    else:
                        merged_chunks.append(current_chunk)
                        current_chunk = c
                else:
                    merged_chunks.append(current_chunk)
                    current_chunk = c
        if current_chunk:
            merged_chunks.append(current_chunk)

        final_chunks = []
        for chunk in merged_chunks:
            if len(chunk) > 6000:
                # Fallback: Se un singolo nodo è troppo grande (es. un'intera classe gigante),
                # il frammento viene diviso ma grazie al 'traverse' avremo comunque i metodi individuali con contesto.
                for i in range(0, len(chunk), CHUNK_SIZE - CHUNK_OVERLAP):
                    final_chunks.append(chunk[i:i+CHUNK_SIZE])
            else:
                final_chunks.append(chunk)

        return final_chunks
    except Exception as e:
        logger.warning(f"Errore tree-sitter parsing: {e}")
        return [content[i:i+CHUNK_SIZE] for i in range(0, len(content), CHUNK_SIZE - CHUNK_OVERLAP)]


# ==============================================================================
# STATO PERSISTENTE RAG
# ==============================================================================

def _get_db():
    import sqlite3
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
        except: pass
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
        ws_name = re.sub(r'[^a-zA-Z0-9_]', '_', parts[0])
        return f"collateral_docs_{ws_name}_{VECTOR_DB_VERSION}"
    return f"collateral_docs_default_{VECTOR_DB_VERSION}"

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

            valid_chunks = [c for c in chunks if len(c.strip()) >= 50]
            if valid_chunks:
                texts_to_embed = valid_chunks
                
                # Dividiamo la chiamata get_embedding in mini-batch da 3 per rilasciare frequentemente 
                # il PriorityLock e permettere ai messaggi chat di inserirsi rapidamente.
                vectors = []
                for i in range(0, len(texts_to_embed), 3):
                    batch = texts_to_embed[i:i+3]
                    batch_vectors = await get_embedding(batch)
                    vectors.extend(batch_vectors)
                    # Yield volontario per permettere all'event loop di servire il PriorityLock
                    await asyncio.sleep(0.01)
                
                # Estrae il nome progetto dal path relativo (prima directory)
                _project_id = rel_path.replace('\\', '/').split('/')[0] if '/' in rel_path.replace('\\', '/') else "default"
                for chunk, vector in zip(valid_chunks, vectors):
                    if vector:
                        points.append(PointStruct(
                            id=str(uuid.uuid4()),
                            vector=vector,
                            payload={"filename": rel_path, "text": chunk, "deps": list(deps), "project": _project_id}
                        ))

            if points:
                from config import MODEL_PROFILE
                for p in points:
                    p.payload["model_family"] = MODEL_PROFILE.family
                    p.payload["model_variant"] = MODEL_PROFILE.variant
                # Salva vecchi ID prima dell'upsert per delete atomica
                old_scroll = await state.qdrant.scroll(
                    collection_name=col_name,
                    scroll_filter=Filter(must=[FieldCondition(key="filename", match=MatchValue(value=rel_path))]),
                    with_payload=False,
                    limit=1000
                )
                old_ids = [p.id for p in old_scroll[0]]
                await state.qdrant.upsert(collection_name=col_name, points=points)
                if old_ids:
                    await state.qdrant.delete(
                        collection_name=col_name,
                        points_selector=old_ids
                    )
            async with state.state_lock:
                state.rag_state[rel_path] = {"hash": file_hash, "mtime": mtime, "size": size}
                _save_file_state_unsafe(rel_path)
            if points:
                logger.info(f"🔄 Vettori Aggiornati: {rel_path} ({len(points)} chunks, {len(deps)} dipendenze)")
        except Exception as e:
            logger.error(f"Errore su {rel_path}: {e}")


async def ingest_local_documents():
    """Scansione completa della cartella documenti: indicizza file nuovi/modificati, rimuove i cancellati."""
    async with state.state_lock:
        _load_state_unsafe()
    ignore_filter = GitignoreFilter(DOC_DIR)
    current_files = {}
    visited_inodes = set()

    for r, d, f in os.walk(DOC_DIR, followlinks=True):
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
            and not ignore_filter.is_ignored(os.path.relpath(os.path.join(r, sub), DOC_DIR))
        ]
        for file in f:
            fp = os.path.join(r, file)
            rp = os.path.relpath(fp, DOC_DIR)
            if rp.endswith(('.go', '.py', '.jsx', '.tsx', '.js', '.ts', '.md', '.json', '.txt', '.c', '.cpp', '.h', '.hpp', '.java', '.rs', '.sql', '.yaml', '.yml')) \
                    and not ignore_filter.is_ignored(rp):
                current_files[rp] = fp

    # Walk diretto per progetti in EXTERNAL_PROJECTS il cui data/documents/
    # coincide con DOC_DIR (Docker volume mount → symlink circolare con watchdog).
    # In questo caso il symlink viene rimosso in main.py per evitare loop,
    # ma i file del progetto vanno comunque indicizzati.
    if EXTERNAL_PROJECTS.strip():
        for pair in EXTERNAL_PROJECTS.split(','):
            pair = pair.strip()
            if ':' not in pair:
                continue
            host_path, folder_name = pair.split(':', 1)
            host_path = host_path.strip()
            folder_name = folder_name.strip()
            project_root = os.path.join("/host_fs", host_path.lstrip('/'))
            # Verifica se data/documents/ del progetto coincide con DOC_DIR
            if os.path.isdir(os.path.join(project_root, "data", "documents")):
                # Questo progetto ha il mount conflict — walk diretto
                proj_ignore = GitignoreFilter(project_root)
                for r, d, f in os.walk(project_root, followlinks=True):
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
                        if sub not in ('.git', 'node_modules', 'venv', 'vendor', 'data')
                        and not proj_ignore.is_ignored(os.path.relpath(os.path.join(r, sub), project_root))
                    ]
                    for file in f:
                        fp = os.path.join(r, file)
                        rp = f"{folder_name}/{os.path.relpath(fp, project_root)}"
                        if rp.endswith(('.go', '.py', '.jsx', '.tsx', '.js', '.ts', '.md', '.json', '.txt', '.c', '.cpp', '.h', '.hpp', '.java', '.rs', '.sql', '.yaml', '.yml')) \
                                and not proj_ignore.is_ignored(os.path.relpath(fp, project_root)):
                            current_files[rp] = fp

    # Pulizia file rimossi dal disco
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
            async with state.state_lock:
                if rp in state.rag_state:
                    del state.rag_state[rp]
                    _save_file_state_unsafe(rp)
            logger.info(f"🗑️ Pulizia: Rimosso {rp} dai vettori.")

    # Processamento file nuovi/modificati
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

    # Genera skeleton architetturali per IDE (Punto 3)
    generate_workspace_skeletons()
    
    # Aggiorna cache del tree in background (Fix 9.4)
    await update_project_tree_cache()


# ==============================================================================
# PROJECT TREE & SKELETON
# ==============================================================================

def generate_workspace_skeletons():
    """Genera e salva uno scheletro del codice per ogni workspace in .ai-skeleton.md"""
    filt = GitignoreFilter(DOC_DIR)
    
    workspaces = {}
    visited_inodes = set()
    for r, d, f in os.walk(DOC_DIR, followlinks=True):
        try:
            st = os.stat(r)
            inode_key = (st.st_dev, st.st_ino)
            if inode_key in visited_inodes:
                d[:] = []
                continue
            visited_inodes.add(inode_key)
        except OSError:
            pass
        d[:] = [sub for sub in d if sub not in ('.git', 'node_modules', 'venv', 'vendor') and not filt.is_ignored(os.path.relpath(os.path.join(r, sub), DOC_DIR))]
        
        for file in f:
            fp = os.path.join(r, file)
            rp = os.path.relpath(fp, DOC_DIR)
            if not filt.is_ignored(rp) and rp.endswith(('.py', '.js', '.ts', '.jsx', '.tsx', '.go', '.java', '.cpp', '.c', '.h', '.hpp', '.rs')):
                parts = rp.split('/')
                ws = parts[0] if len(parts) > 1 else "default"
                if ws not in workspaces:
                    workspaces[ws] = []
                workspaces[ws].append((rp, fp))
                
    for ws, files in workspaces.items():
        skeleton_lines = [f"# Code Skeleton: {ws}\n"]
        for rp, fp in files:
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
            except Exception as e: logger.warning(f"Errore silenziato: {e}")
                
        if ws != "default":
            out_path = os.path.join(DOC_DIR, ws, ".ai-skeleton.md")
            try:
                with open(out_path, "w", encoding="utf-8") as out_file:
                    out_file.write("\n".join(skeleton_lines))
            except Exception as e:
                logger.warning(f"Impossibile salvare skeleton in {out_path}: {e}")

async def update_project_tree_cache():
    """Aggiorna la cache in background (eseguito in to_thread per non bloccare FastAPI)."""
    try:
        t = await asyncio.to_thread(generate_project_tree)
        state.project_tree_cache = t
    except Exception as e:
        logger.warning(f"Errore aggiornamento project tree cache: {e}")

def generate_project_tree():
    """Genera una rappresentazione testuale dell'albero del progetto indicizzato."""
    filt = GitignoreFilter(DOC_DIR)
    t = "📂 PROGETTO:\n"
    visited_inodes = set()
    for r, d, f in os.walk(DOC_DIR, followlinks=True):
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
            and not filt.is_ignored(os.path.relpath(os.path.join(r, sub), DOC_DIR))
        ]
        lvl = r.replace(DOC_DIR, '').count(os.sep)
        t += f"{'    '*lvl}📁 {os.path.basename(r) or 'root'}/\n"
        for file in f:
            rp = os.path.relpath(os.path.join(r, file), DOC_DIR)
            if not filt.is_ignored(rp) and rp.endswith(('.go', '.py', '.jsx', '.tsx', '.js', '.ts', '.md', '.json', '.txt', '.c', '.cpp', '.h', '.hpp', '.java', '.rs', '.sql', '.yaml', '.yml')):
                t += f"{'    '*(lvl+1)}📄 {file}\n"
    return t

def generate_telegram_ls_data(subpath=None):
    """Genera l'elenco dei file e cartelle (tipo ls) per il bot Telegram sotto forma di dizionario strutturato."""
    filt = GitignoreFilter(DOC_DIR)
    
    target_dir = DOC_DIR
    if subpath:
        target_dir = os.path.normpath(os.path.join(DOC_DIR, subpath))
        if not target_dir.startswith(DOC_DIR):
            target_dir = DOC_DIR
            subpath = None
            
    if not os.path.exists(target_dir):
        return {"error": f"Percorso non trovato: {subpath}"}
        
    if not os.path.isdir(target_dir):
        return {"error": f"📄 `{os.path.basename(target_dir)}` è un file, non una cartella."}

    t = f"📂 *{subpath if subpath else 'PROGETTI (Root)'}*:\n"
    folders = []
    files = []
    try:
        items = os.listdir(target_dir)
        
        for item in items:
            if item in ('.git', 'node_modules', 'venv', 'vendor'):
                continue
                
            full_path = os.path.join(target_dir, item)
            rel_path = os.path.relpath(full_path, DOC_DIR)
            
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
        return {
            "text": t,
            "folders": folders,
            "files": files,
            "current_path": subpath
        }
    except Exception as e:
        return {"error": f"Errore: {e}"}


# ==============================================================================
# RICERCA VETTORIALE
# ==============================================================================

async def search_documents(query, is_project_query=False, project_name=None):
    """Cerca documenti rilevanti nei Workspace Qdrant isolati."""
    try:
        # Alta priorità (0) per bypassare l'ingestione in background
        vector = await get_embedding(query, priority=0, is_query=True)
        if not vector:
            return ""

        top_k = RAG_CONFIG["top_k_code"] if is_project_query else RAG_CONFIG["top_k_docs"]
        required_score = RAG_CONFIG["score_threshold_code"] if is_project_query else RAG_CONFIG["score_threshold_docs"]
        
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
                    break
            if not target_cols:
                logger.warning(f"Nessuna collezione trovata per progetto: {project_name}")
                return ""

        if not target_cols:
            query_lower = query.lower()
            for c in col_names:
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

        primary_docs, deps_to_search = [], set()
        for r in best_results:
            filename = r["meta"].get("filename")
            project_label = r["meta"].get("_project", "")
            project_prefix = f"[{project_label}] " if project_label else ""
            if filename:
                primary_docs.append(f"📄 File Primario ({project_prefix}{filename}):\n```\n{r['text']}\n```")
            if r["meta"].get("deps"):
                deps_to_search.update(r["meta"].get("deps"))

        secondary_docs = []
        if is_project_query and deps_to_search:
            should_conditions = [
                FieldCondition(key="filename", match=MatchText(text=dep))
                for dep in list(deps_to_search)[:10]
            ]
            if should_conditions:
                async def _scroll_col(col_name):
                    try:
                        res, _ = await state.qdrant.scroll(
                            collection_name=col_name,
                            scroll_filter=Filter(should=should_conditions),
                            limit=5,
                            with_payload=True
                        )
                        return res
                    except Exception as e:
                        logger.warning(f"Errore silenziato: {e}")
                        return []
                sec_results = []
                for pts in await asyncio.gather(*[_scroll_col(c) for c in target_cols]):
                    sec_results.extend(pts)
                for hit in sec_results:
                    filename = hit.payload.get('filename')
                    if filename and f"📄 File Primario ({filename}):" not in "".join(primary_docs):
                        secondary_docs.append(
                            f"🔗 Dipendenza Inclusa ({filename}):\n```\n{hit.payload.get('text', '')}\n```"
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

        return "\n\n".join(rules_docs + primary_docs + secondary_docs)
    except Exception as e:
        logger.error(f"Errore search_documents: {e}")
        return ""


async def list_rag_projects() -> list[str]:
    """Restituisce la lista dei nomi di progetto indicizzati nel RAG (collezioni Qdrant)."""
    try:
        collections_info = await state.qdrant.get_collections()
        projects = []
        for c in collections_info.collections:
            if c.name.startswith("collateral_docs_"):
                name = c.name.replace("collateral_docs_", "")
                name = re.sub(r'_v\d+$', '', name)
                if name and name != "default":
                    projects.append(name)
        return sorted(set(projects))
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
    """Cerca un progetto conosciuto in una singola query."""
    query_lower = query.lower()

    # Cerca menzione diretta (parola intera con word boundary)
    # Usa \b per evitare false positivi: "web" non matcha "website"
    for alias, project in alias_to_project.items():
        if re.search(r'\b' + re.escape(alias) + r'\b', query_lower):
            return project

    # Cerca prefissi di path: "NeuroNet/src/main.py" o "SlotBuilder/cmd/..."
    path_match = re.search(r'\b([A-Za-z][\w.-]*)[/\\]', query)
    if path_match:
        dir_name = path_match.group(1).lower()
        for alias, project in alias_to_project.items():
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
                rel_path = os.path.relpath(fp, DOC_DIR)
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
            
            state.file_event_queue.task_done()
        except asyncio.CancelledError:
            logger.info("🛑 Spegnimento Graceful del worker Watchdog.")
            return
        except Exception as e:
            logger.error(f"Watchdog worker crashato, riavvio: {e}", exc_info=True)
            await asyncio.sleep(5)

# ==============================================================================
# CACHE SEMANTICA (8.2)
# ==============================================================================

async def semantic_cache_search(prompt: str, threshold: float = SEMANTIC_CACHE_THRESHOLD):
    try:
        vector = await get_embedding(prompt, priority=0, is_query=True)
        if not vector: return None
        res = await state.qdrant.query_points(
            collection_name=f"semantic_cache_{VECTOR_DB_VERSION}",
            query=vector,
            limit=1,
            score_threshold=threshold,
            with_payload=True
        )
        if res and res.points:
            return res.points[0].payload.get("response")
    except Exception as e: logger.warning(f"Errore silenziato: {e}")
    return None

async def semantic_cache_store(prompt: str, response: str):
    try:
        vector = await get_embedding(prompt, is_query=True)
        if vector:
            await state.qdrant.upsert(
                collection_name=f"semantic_cache_{VECTOR_DB_VERSION}",
                points=[PointStruct(
                    id=str(uuid.uuid4()),
                    vector=vector,
                    payload={"prompt": prompt, "response": response}
                )]
            )
    except Exception as e: logger.warning(f"Errore silenziato: {e}")


# ==============================================================================
# WEB KNOWLEDGE PERSISTENCE (Qdrant + Mem0)
# ==============================================================================

async def ensure_web_knowledge_collection():
    """Crea la collezione web_knowledge in Qdrant se non esiste."""
    col_name = f"web_knowledge_{VECTOR_DB_VERSION}"
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
                except Exception as e:
                    logger.warning(f"Errore creazione web knowledge collection: {e}")
                state.created_collections.add(col_name)


async def save_web_knowledge(query: str, context: str, sources: list[str] | None = None):
    """Salva conoscenza da web in Qdrant per future ricerche semantiche."""
    try:
        await ensure_web_knowledge_collection()
        col_name = f"web_knowledge_{VECTOR_DB_VERSION}"

        try:
            await state.qdrant.delete(
                collection_name=col_name,
                points_selector=Filter(must=[FieldCondition(key="query_hash", match=MatchValue(value=hashlib.md5(query.encode()).hexdigest()[:16]))])
            )
        except Exception:
            pass

        chunks = [context[i:i+CHUNK_SIZE] for i in range(0, len(context), CHUNK_SIZE - CHUNK_OVERLAP)]
        valid_chunks = [c for c in chunks if len(c.strip()) >= 50]
        if not valid_chunks:
            return

        texts_to_embed = [f"QUERY: {query} | WEB: {chunk}" for chunk in valid_chunks]
        vectors = []
        for i in range(0, len(texts_to_embed), 3):
            batch = texts_to_embed[i:i+3]
            batch_vectors = await get_embedding(batch)
            vectors.extend(batch_vectors)
            await asyncio.sleep(0.01)

        points = []
        for chunk, vector in zip(valid_chunks, vectors):
            if vector:
                points.append(PointStruct(
                    id=str(uuid.uuid4()),
                    vector=vector,
                    payload={
                        "query": query[:300],
                        "query_hash": hashlib.md5(query.encode()).hexdigest()[:16],
                        "text": chunk,
                        "sources": sources[:5] if sources else [],
                        "type": "web_knowledge"
                    }
                ))

        if points:
            await state.qdrant.upsert(collection_name=col_name, points=points)
            logger.info(f"🌐 Web knowledge salvata in Qdrant: '{query[:60]}...' ({len(points)} chunks)")
    except Exception as e:
        logger.warning(f"Errore save_web_knowledge: {e}")


async def search_web_knowledge(query: str) -> str:
    """Cerca nella knowledge base web Qdrant. Ritorna contesto se trovato, stringa vuota altrimenti."""
    try:
        col_name = f"web_knowledge_{VECTOR_DB_VERSION}"
        try:
            exists = await state.qdrant.collection_exists(collection_name=col_name)
            if not exists:
                return ""
        except Exception:
            return ""

        vector = await get_embedding(query, is_query=True)
        if not vector:
            return ""

        res = await state.qdrant.query_points(
            collection_name=col_name,
            query=vector,
            limit=3,
            score_threshold=0.35,
            with_payload=True
        )
        if res and res.points:
            results = []
            for p in res.points:
                text = p.payload.get("text", "")
                if text:
                    results.append(text)
            if results:
                logger.info(f"🌐 Web knowledge cache HIT per: '{query[:60]}...'")
                return "\n---\n".join(results)
    except Exception as e:
        logger.warning(f"Errore search_web_knowledge: {e}")
    return ""
