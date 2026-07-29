"""
Chunking AST-aware per RAG — estrazione dipendenze, tokenizer, chunking ricorsivo,
e chunking semantico con Tree-sitter per 11 linguaggi.
Estratto da rag/engine.py per modularizzazione.
"""

import hashlib
import logging
import os
import re

import tiktoken
from tree_sitter import Parser

from core.config import (
    AST_ENABLED, CHUNK_SIZE, logger,
    GO, PY, JS, TSX, C, CPP, JAVA, RUST, SQL, YAML,
)

logger = logging.getLogger(__name__)


QWEN3_QUERY_INSTRUCTION = "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: "


# ════════════════════════════════════════════════════════════════
# TOKENIZER
# ════════════════════════════════════════════════════════════════

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


def token_count(text: str) -> int:
    return len(_get_tokenizer().encode(text, disallowed_special=()))


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


def recursive_token_split(text: str, max_tokens: int) -> list[str]:
    """Divide il testo ricorsivamente a max_tokens usando i confini di riga."""
    if token_count(text) <= max_tokens or not text:
        return [text]
    target = len(text) * max_tokens // max(1, token_count(text))
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
    return recursive_token_split(left, max_tokens) + recursive_token_split(right, max_tokens)


# ════════════════════════════════════════════════════════════════
# AST DEPENDENCY EXTRACTION
# ════════════════════════════════════════════════════════════════

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


# ════════════════════════════════════════════════════════════════
# AST CODE CHUNKING
# ════════════════════════════════════════════════════════════════

def ast_code_chunking(content, filepath):
    """Chunking intelligente: usa Tree-sitter per estrarre funzioni/classi
    mantenendo il contesto gerarchico.

    Returns list of dict:
      {text: str, section_hierarchy: list[str] | None, parent_chunk_id: str | None,
       chunk_index: int | None, chunk_count: int | None}
    """
    if not AST_ENABLED:
        return [{"text": c, "section_hierarchy": None}
                for c in recursive_token_split(content, CHUNK_SIZE)]

    ext = os.path.splitext(filepath)[1].lower()
    parser = Parser()

    if ext == '.go':
        parser.language = GO
        nodes = ['function_declaration', 'method_declaration', 'type_declaration',
                 'const_declaration', 'var_declaration', 'struct_type', 'interface_type']
    elif ext == '.py':
        parser.language = PY
        nodes = ['function_definition', 'class_definition', 'async_function_definition']
    elif ext in ['.js', '.jsx']:
        parser.language = JS
        nodes = ['function_declaration', 'lexical_declaration', 'class_declaration',
                 'arrow_function', 'method_definition']
    elif ext in ['.ts', '.tsx']:
        parser.language = TSX
        nodes = ['function_declaration', 'lexical_declaration', 'class_declaration',
                 'arrow_function', 'method_definition', 'interface_declaration',
                 'type_alias_declaration']
    elif ext in ['.c', '.h']:
        parser.language = C
        nodes = ['function_definition', 'declaration', 'struct_specifier', 'enum_specifier']
    elif ext in ['.cpp', '.hpp', '.cc', '.cxx']:
        parser.language = CPP
        nodes = ['function_definition', 'class_specifier', 'struct_specifier',
                 'enum_specifier', 'namespace_definition', 'template_declaration']
    elif ext == '.java':
        parser.language = JAVA
        nodes = ['method_declaration', 'class_declaration', 'interface_declaration',
                 'enum_declaration']
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
        return _chunk_markdown(content)
    else:
        children = [{"text": c, "section_hierarchy": None}
                    for c in recursive_token_split(content, CHUNK_SIZE)]
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
            is_context = n.type in ['class_definition', 'class_declaration', 'class_specifier',
                                    'struct_specifier', 'interface_declaration', 'impl_item',
                                    'type_declaration', 'namespace_definition']

            if is_context:
                sig = get_signature(n)
                if sig:
                    context_stack.append(sig)

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

        # PREAMBOLO
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
            children = [{"text": c, "section_hierarchy": None}
                        for c in recursive_token_split(content, CHUNK_SIZE)]
            return _tag_split_children(children, content)

        # Merge piccoli frammenti AST consecutivi
        merged_chunks = []
        current_chunk = None
        for c in chunks:
            if not current_chunk:
                current_chunk = c
            else:
                combined_text = current_chunk["text"] + "\n\n" + c["text"]
                if token_count(combined_text) <= CHUNK_SIZE:
                    words1 = set(current_chunk["text"].split())
                    words2 = set(c["text"].split())
                    overlap = len(words1 & words2) / len(words1 | words2) if words1 and words2 else 0
                    if overlap > 0.05 or token_count(c["text"]) < CHUNK_SIZE // 4:
                        current_chunk["text"] = combined_text
                    else:
                        merged_chunks.append(current_chunk)
                        current_chunk = c
                else:
                    merged_chunks.append(current_chunk)
                    current_chunk = c
        if current_chunk:
            merged_chunks.append(current_chunk)

        # Raggruppa per prossimità
        return _group_by_proximity(merged_chunks)

    except Exception as e:
        logger.warning(f"Errore tree-sitter parsing: {e}")
        children = [{"text": c, "section_hierarchy": None}
                    for c in recursive_token_split(content, CHUNK_SIZE)]
        return _tag_split_children(children, content)


def _chunk_markdown(content):
    """Chunking semantico per Markdown (per heading)."""
    chunks = []
    current_chunk = []
    for line in content.split('\n'):
        if line.startswith('#') and current_chunk and token_count('\n'.join(current_chunk)) > CHUNK_SIZE // 4:
            chunks.append('\n'.join(current_chunk))
            current_chunk = [line]
        else:
            current_chunk.append(line)
    if current_chunk:
        chunks.append('\n'.join(current_chunk))
    final_md_chunks = []
    for chunk in chunks:
        if token_count(chunk) > CHUNK_SIZE:
            children = [{"text": t, "section_hierarchy": None}
                        for t in recursive_token_split(chunk, CHUNK_SIZE)]
            final_md_chunks.extend(_tag_split_children(children, chunk))
        else:
            final_md_chunks.append({
                "text": chunk, "section_hierarchy": None,
                "parent_chunk_id": None, "chunk_index": None, "chunk_count": None,
            })
    return final_md_chunks


def _group_by_proximity(merged_chunks):
    """Raggruppa chunk consecutivi per prossimità (fino a ~2000 token per gruppo)."""
    PARENT_MAX_TOKENS = 2000
    proximity_groups = []
    current_group = []
    current_tokens = 0
    for chunk in merged_chunks:
        tok = token_count(chunk["text"])
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
                if token_count(c["text"]) > CHUNK_SIZE:
                    for t in recursive_token_split(c["text"], CHUNK_SIZE):
                        final_chunks.append({
                            "text": t, "section_hierarchy": c.get("section_hierarchy"),
                            "parent_chunk_id": pid, "chunk_index": i, "chunk_count": len(group),
                        })
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
            if token_count(chunk["text"]) > CHUNK_SIZE:
                for t in recursive_token_split(chunk["text"], CHUNK_SIZE):
                    final_chunks.append({
                        "text": t, "section_hierarchy": chunk.get("section_hierarchy"),
                        "parent_chunk_id": None, "chunk_index": None, "chunk_count": None,
                    })
            else:
                final_chunks.append(chunk)

    return final_chunks
