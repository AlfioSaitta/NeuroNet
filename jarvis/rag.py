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
import tiktoken

from config import (
    logger, QDRANT_HOST, DOC_COLLECTION, DOC_DIR,
    STATE_FILE, CHUNK_SIZE, CHUNK_OVERLAP, MAX_CONCURRENT_EMBEDDINGS,
    RAG_CONFIG, AST_ENABLED, GO, PY, JS, TSX,
    VECTOR_DB_VERSION, FLASHRANK_MODEL, Qwen3_RERANKER_MODEL,
    QENABLED_QWEN3_RERANKER, RERANKER_DEVICE,
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

# ==============================================================================
# RERANKER STRATEGY:
#   1. FlashRank ONNX (leggero e veloce, sempre disponibile)
#   2. Se QENABLED_QWEN3_RERANKER=true E la directory esiste, prova Qwen3 (migliore qualità)
# ==============================================================================
_use_qwen3_reranker = QENABLED_QWEN3_RERANKER and os.path.isdir(Qwen3_RERANKER_MODEL)

if _use_qwen3_reranker:
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
        logger.warning(f"Qwen3-Reranker errore ({e}), fallback su FlashRank...")
        _use_qwen3_reranker = False

if not _use_qwen3_reranker:
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
        loop = asyncio.get_event_loop()
        for root, dirs, files in loop.run_in_executor(None, lambda: list(os.walk(doc_dir, followlinks=True))).result():
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
    """Estrae le dipendenze (import/from/require) usando tree-sitter per Go, Python, JS/TS.
    Fallback a regex per linguaggi non supportati o se AST disabilitato."""
    deps = set()

    if AST_ENABLED:
        try:
            if ext == '.go':
                parser = Parser()
                parser.language = GO
                tree = parser.parse(bytes(content, "utf8"))
                def _find_specs(n):
                    if n.type == 'import_spec':
                        for ch in n.children:
                            if ch.type in ('interpreted_string_literal', 'raw_string_literal'):
                                path = ch.text.decode().strip('"`')
                                deps.add(path.split('/')[-1])
                    for c in n.children:
                        _find_specs(c)
                def _walk(n):
                    if n.type == 'import_declaration':
                        _find_specs(n)
                    for c in n.children:
                        _walk(c)
                _walk(tree.root_node)

            elif ext == '.py':
                parser = Parser()
                parser.language = PY
                tree = parser.parse(bytes(content, "utf8"))
                def _walk(n):
                    if n.type == 'import_statement':
                        for c in n.children:
                            if c.type == 'dotted_name':
                                module = c.text.decode().split('.')[0]
                                if module: deps.add(module)
                            elif c.type == 'aliased_import':
                                for ac in c.children:
                                    if ac.type == 'dotted_name':
                                        module = ac.text.decode().split('.')[0]
                                        if module: deps.add(module)
                    elif n.type == 'import_from_statement':
                        for c in n.children:
                            if c.type == 'dotted_name':
                                module = c.text.decode().split('.')[0]
                                if module:
                                    deps.add(module)
                                break
                    for c in n.children:
                        _walk(c)
                _walk(tree.root_node)

            elif ext in ('.js', '.jsx', '.ts', '.tsx'):
                lang = JS if ext in ('.js', '.jsx') else TSX
                parser = Parser()
                parser.language = lang
                tree = parser.parse(bytes(content, "utf8"))
                def _walk(n):
                    if n.type == 'import_statement':
                        for c in n.children:
                            if c.type == 'string':
                                path = c.text.decode().strip('\'"`')
                                name = path.split('/')[-1].replace('.js', '').replace('.ts', '').replace('.jsx', '').replace('.tsx', '')
                                if name: deps.add(name)
                    elif n.type == 'call_expression':
                        first = n.children[0] if n.children else None
                        if first and first.type == 'identifier' and first.text.decode() == 'require':
                            for c in n.children:
                                if c.type == 'arguments':
                                    for a in c.children:
                                        if a.type == 'string':
                                            path = a.text.decode().strip('\'"`')
                                            name = path.split('/')[-1].replace('.js', '').replace('.ts', '').replace('.jsx', '').replace('.tsx', '')
                                            if name: deps.add(name)
                    for c in n.children:
                        _walk(c)
                _walk(tree.root_node)
        except Exception:
            pass

    # Fallback regex se tree-sitter non ha prodotto risultati o AST disabilitato
    if not deps:
        head = content[:2500]
        if ext == '.go':
            for m in re.findall(r'"([^"]+)"', head):
                deps.add(m.split('/')[-1])
        elif ext == '.py':
            for m in re.findall(r'^(?:from|import)\s+([a-zA-Z0-9_\.]+)', head, re.MULTILINE):
                deps.add(m.split('.')[0])
        elif ext in ('.js', '.jsx', '.ts', '.tsx'):
            for m in re.findall(r'from\s+[\'"]([^(\'|")]+)[\'"]', head):
                deps.add(m.split('/')[-1].replace('.js', '').replace('.ts', ''))
        elif ext == '.md':
            for m in re.findall(r'\[.*?\]\((.*?\.md.*?)\)', content):
                deps.add(m.split('/')[-1].split('#')[0])

    return list(deps)


# Tokenizer per chunking ricorsivo a 512 token
_tokenizer = None

def _get_tokenizer():
    global _tokenizer
    if _tokenizer is not None:
        return _tokenizer

    cache_dir = os.environ.get("TIKTOKEN_CACHE_DIR", "")
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)

    for enc_name in ("o200k_base", "cl100k_base", "gpt2"):
        try:
            _tokenizer = tiktoken.get_encoding(enc_name)
            return _tokenizer
        except Exception:
            continue

    raise RuntimeError("Nessun tokenizer tiktoken disponibile (offline e cache vuota)")

def _token_count(text: str) -> int:
    return len(_get_tokenizer().encode(text, disallowed_special=()))

def _recursive_token_split(text: str, max_tokens: int) -> list[str]:
    """Divide il testo ricorsivamente a max_tokens usando i confini di riga."""
    if _token_count(text) <= max_tokens or not text:
        return [text]
    # Trova il punto di rottura: cerca \n\n (paragrafo) poi \n (riga) poi spazio
    target = len(text) * max_tokens // max(1, _token_count(text))
    # Arretra fino a un boundary
    boundary = text.rfind("\n\n", 0, max(target, 1))
    if boundary < max(target // 2, 1):
        boundary = text.rfind("\n", 0, max(target, 1))
    if boundary < max(target // 2, 1):
        boundary = text.rfind(" ", 0, max(target, 1))
    if boundary < max(target // 2, 1):
        boundary = target
    left = text[:boundary].rstrip()
    right = text[boundary:].lstrip()
    if not left or not right:
        return [text]
    return _recursive_token_split(left, max_tokens) + _recursive_token_split(right, max_tokens)

def _make_parent_chunk_id(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:12]

def _tag_split_children(chunks: list[dict], parent_text: str) -> list[dict]:
    """Assegna parent_chunk_id, chunk_index, chunk_count a figli di uno split."""
    if not chunks:
        return chunks
    if len(chunks) <= 1:
        chunks[0]["parent_chunk_id"] = None
        chunks[0]["chunk_index"] = None
        chunks[0]["chunk_count"] = None
        return chunks
    pid = _make_parent_chunk_id(parent_text)
    for i, c in enumerate(chunks):
        c["parent_chunk_id"] = pid
        c["chunk_index"] = i
        c["chunk_count"] = len(chunks)
    return chunks

def ast_code_chunking(content, filepath):
    """Chunking intelligente: usa Tree-sitter per estrarre funzioni/classi mantenendo il contesto gerarchico.
    Returns list of dict: {text: str, section_hierarchy: list[str] | None, parent_chunk_id: str | None,
    chunk_index: int | None, chunk_count: int | None}"""
    if not AST_ENABLED:
        return [{"text": c, "section_hierarchy": None} for c in _recursive_token_split(content, CHUNK_SIZE)]

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
        # Markdown Semantic Chunking (per heading)
        chunks = []
        current_chunk = []
        for line in content.split('\n'):
            if line.startswith('#') and current_chunk and _token_count('\n'.join(current_chunk)) > CHUNK_SIZE // 4:
                chunks.append('\n'.join(current_chunk))
                current_chunk = [line]
            else:
                current_chunk.append(line)
        if current_chunk:
            chunks.append('\n'.join(current_chunk))
        final_md_chunks = []
        for chunk in chunks:
            if _token_count(chunk) > CHUNK_SIZE:
                children = [{"text": t, "section_hierarchy": None} for t in _recursive_token_split(chunk, CHUNK_SIZE)]
                final_md_chunks.extend(_tag_split_children(children, chunk))
            else:
                final_md_chunks.append({"text": chunk, "section_hierarchy": None, "parent_chunk_id": None, "chunk_index": None, "chunk_count": None})
        return final_md_chunks
    else:
        children = [{"text": c, "section_hierarchy": None} for c in _recursive_token_split(content, CHUNK_SIZE)]
        return _tag_split_children(children, content)

    try:
        tree = parser.parse(bytes(content, "utf8"))
        chunks = []

        def get_signature(n):
            try:
                raw = n.text.decode()
            except (AttributeError, UnicodeDecodeError):
                raw = content[n.start_byte:n.end_byte]
            lines = raw.split('\n')
            sig = []
            for line in lines[:3]:
                s = line.strip()
                sig.append(s)
                if '{' in s or ':' in s:
                    break
            return " ".join(sig).split('{')[0].strip()

        context_stack = []
        seen_byte_ranges = set()

        def traverse(n):
            is_context = n.type in ['class_definition', 'class_declaration', 'class_specifier', 'struct_specifier', 'interface_declaration', 'impl_item', 'type_declaration', 'namespace_definition']
            
            if is_context:
                sig = get_signature(n)
                if sig: context_stack.append(sig)

            if n.type in nodes:
                byte_range = (n.start_byte, n.end_byte)
                if byte_range not in seen_byte_ranges:
                    seen_byte_ranges.add(byte_range)
                    b = content[n.start_byte:n.end_byte]
                    if len(b.strip()) > 20:
                        start_line = n.start_point[0] + 1
                        end_line = n.end_point[0] + 1

                        chunks.append({
                            "text": f"RIGHE {start_line}-{end_line}:\n{b}",
                            "section_hierarchy": list(context_stack) if context_stack else None
                        })

            for c in n.children:
                traverse(c)
                
            if is_context and context_stack:
                context_stack.pop()

        traverse(tree.root_node)

        # PREAMBOLO solo se nessun chunk AST si sovrappone alle prime 50 righe
        preamble_overlap = False
        for c in chunks:
            m = re.match(r"RIGHE (\d+)-\d+:", c["text"])
            if m and int(m.group(1)) <= 50:
                preamble_overlap = True
                break
        if not preamble_overlap:
            preamble = "\n".join(content.split("\n")[:50])
            if len(preamble.strip()) > 20:
                chunks.insert(0, {"text": f"PREAMBOLO:\n{preamble}", "section_hierarchy": None})

        if not chunks:
            children = [{"text": c, "section_hierarchy": None} for c in _recursive_token_split(content, CHUNK_SIZE)]
            return _tag_split_children(children, content)

        # Fondere dinamicamente piccoli frammenti AST consecutivi
        merged_chunks = []
        current_chunk = None
        for c in chunks:
            if not current_chunk:
                current_chunk = c
            else:
                combined_text = current_chunk["text"] + "\n\n" + c["text"]
                if _token_count(combined_text) <= CHUNK_SIZE:
                    words1 = set(current_chunk["text"].split())
                    words2 = set(c["text"].split())
                    overlap = len(words1 & words2) / len(words1 | words2) if words1 and words2 else 0
                    if overlap > 0.05 or _token_count(c["text"]) < CHUNK_SIZE // 4:
                        current_chunk["text"] = combined_text
                        # Mantieni la gerarchia del primo chunk (più esterna)
                    else:
                        merged_chunks.append(current_chunk)
                        current_chunk = c
                else:
                    merged_chunks.append(current_chunk)
                    current_chunk = c
        if current_chunk:
            merged_chunks.append(current_chunk)

        # ── Raggruppa chunk consecutivi per prossimità (fino a ~2000 token per gruppo) ──
        PARENT_MAX_TOKENS = 2000
        proximity_groups: list[list[dict]] = []
        current_group = []
        current_tokens = 0
        for chunk in merged_chunks:
            tok = _token_count(chunk["text"])
            if current_group and current_tokens + tok > PARENT_MAX_TOKENS:
                proximity_groups.append(current_group)
                current_group = []
                current_tokens = 0
            current_group.append(chunk)
            current_tokens += tok
        if current_group:
            proximity_groups.append(current_group)

        final_chunks = []
        for group in proximity_groups:
            if len(group) > 1:
                parent_text = "\n\n".join(c["text"] for c in group)
                pid = _make_parent_chunk_id(parent_text)
                for i, c in enumerate(group):
                    if _token_count(c["text"]) > CHUNK_SIZE:
                        for t in _recursive_token_split(c["text"], CHUNK_SIZE):
                            final_chunks.append({"text": t, "section_hierarchy": c.get("section_hierarchy"),
                                                  "parent_chunk_id": pid, "chunk_index": i, "chunk_count": len(group)})
                    else:
                        c["parent_chunk_id"] = pid
                        c["chunk_index"] = i
                        c["chunk_count"] = len(group)
                        final_chunks.append(c)
            else:
                chunk = group[0]
                chunk["parent_chunk_id"] = None
                chunk["chunk_index"] = None
                chunk["chunk_count"] = None
                if _token_count(chunk["text"]) > CHUNK_SIZE:
                    for t in _recursive_token_split(chunk["text"], CHUNK_SIZE):
                        final_chunks.append({"text": t, "section_hierarchy": chunk.get("section_hierarchy"),
                                              "parent_chunk_id": None, "chunk_index": None, "chunk_count": None})
                else:
                    final_chunks.append(chunk)

        return final_chunks
    except Exception as e:
        logger.warning(f"Errore tree-sitter parsing: {e}")
        children = [{"text": c, "section_hierarchy": None} for c in _recursive_token_split(content, CHUNK_SIZE)]
        return _tag_split_children(children, content)


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

def get_file_profile_col_name():
    return f"file_profiles_{VECTOR_DB_VERSION}"

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
                    await asyncio.sleep(0.01)
                
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
                        points.append(PointStruct(
                            id=str(uuid.uuid4()),
                            vector=vector,
                            payload=payload
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


async def ingest_local_documents():
    """Scansione completa della cartella documenti: indicizza file nuovi/modificati, rimuove i cancellati."""
    if state.is_reindexing:
        logger.info("Re-indexing già in corso, salto scansione duplicata")
        return
    state.is_reindexing = True
    async with state.state_lock:
        _load_state_unsafe()
    ignore_filter = GitignoreFilter(DOC_DIR)
    current_files = {}
    visited_inodes = set()

    loop = asyncio.get_running_loop()
    for r, d, f in await loop.run_in_executor(None, lambda: list(os.walk(DOC_DIR, followlinks=True))):
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
                for r, d, f in await loop.run_in_executor(None, lambda: list(os.walk(project_root, followlinks=True))):
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
            # Delete file profile too
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
    await generate_workspace_skeletons()
    
    # Aggiorna cache del tree in background (Fix 9.4)
    await update_project_tree_cache()
    
    state.is_reindexing = False


# ==============================================================================
# PROJECT TREE & SKELETON
# ==============================================================================

async def generate_workspace_skeletons():
    """Genera e salva uno scheletro del codice per ogni workspace in .ai-skeleton.md"""
    filt = GitignoreFilter(DOC_DIR)
    loop = asyncio.get_running_loop()
    
    workspaces = {}
    visited_inodes = set()
    for r, d, f in await loop.run_in_executor(None, lambda: list(os.walk(DOC_DIR, followlinks=True))):
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
        t = await generate_project_tree()
        state.project_tree_cache = t
    except Exception as e:
        logger.warning(f"Errore aggiornamento project tree cache: {e}")

async def generate_project_tree():
    """Genera una rappresentazione testuale dell'albero del progetto indicizzato."""
    filt = GitignoreFilter(DOC_DIR)
    loop = asyncio.get_running_loop()
    t = "📂 PROGETTO:\n"
    visited_inodes = set()
    for r, d, f in await loop.run_in_executor(None, lambda: list(os.walk(DOC_DIR, followlinks=True))):
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
                if state.is_reindexing:
                    logger.debug("Re-indexing in corso, salto evento watchdog")
                    continue
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
