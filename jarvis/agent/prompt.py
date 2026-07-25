"""
Prompt Builder — Pipeline di generazione prompt a 4 step con Caveman Compression.

FLUSSO:
  STEP 1: Keyword Bypass (regex, 0 LLM)
  STEP 2: Qwen3.5 Gatekeeper (classificazione intento con grammar)
  STEP 3: Qwen3.5 Caveman Prompt Architect (compressione 40-60%)
  STEP 4: Gemma 4 su GPU → risposta in stile caveman
"""

import datetime
import os
import re
import asyncio
from functools import partial

from core.config import logger, BOT_NAME, LLM_OPTIONS, MODEL_PROFILE, DOC_DIR
from rag.engine import search_documents, generate_project_tree, list_rag_projects, detect_project_in_conversation, GitignoreFilter
from rag.cache import search_web_knowledge, save_web_knowledge
from memory.engine import extract_memories, save_to_memory
from rag.web_search import perform_web_search_and_crawl
from agent.tags import build_tag_instructions
from scheduler.tasks import get_open_tasks
from core.llm_engine import engine, extract_content, GatekeeperResult
try:
    from graph.synaptiq_engine import synaptiq_engine
except ImportError:
    synaptiq_engine = None
from core.telemetry import PipelineTracer, GatekeeperStats
import core.state as state

# ════════════════════════════════════════════════════════════════
# CONTESTO TEMPORALE
# ════════════════════════════════════════════════════════════════

def _datetime_context() -> str:
    """Current date/time string with timezone for LLM context injection."""
    now = datetime.datetime.now()
    return (
        f"Current date and time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}. "
        f"Today is {now.strftime('%A')}, day {now.timetuple().tm_yday} of {now.year}."
    )

# ════════════════════════════════════════════════════════════════
# STEP 1: FAST PATHS — Keyword Bypass (0 LLM calls)
# ════════════════════════════════════════════════════════════════

META_PHRASES = re.compile(
    # ITALIANO: richieste di progetto/elenco
    r'(quali\s+(sono\s+)?(i\s+|i\s+tuoi\s+|i\s+nostri\s+)?progetti'
    r'|dammi\s+(la\s+)?lista(\s+dei)?(\s+\w+)?\s+progetti'
    r'|mostra\s+(la\s+)?lista(\s+dei)?(\s+\w+)?\s+progetti'
    r'|lista\s+(dei\s+)?(\w+\s+)?progetti'
    r'|che\s+progetti'
    r'|progetti\s+in\s+(memoria|rag)'
    r'|elenco\s+(dei\s+)?(\w+\s+)?progetti'
    r'|quanti\s+progetti'
    r'|progetti\s+(hai|conosci|hai\s+in|in\s+corso|ci\s+sono|sono\s+disponibili)'
    r'|a\s+quali\s+progetti'
    r'|quali\s+sono\s+(i\s+)?(tuoi\s+|nostri\s+|miei\s+|vostri\s+|suoi\s+)?progetti'
    # INGLESE: project listing requests
    r'|which\s+(are\s+)?(the\s+)?(your\s+|our\s+|all\s+)?projects'
    r'|list\s+(of\s+)?(the\s+)?(your\s+|our\s+|all\s+)?projects'
    r'|give\s+me\s+(the\s+)?(list\s+of\s+)?(the\s+)?(your\s+|our\s+|all\s+)?projects'
    r'|show\s+me\s+(the\s+)?(list\s+of\s+)?(the\s+)?(your\s+|our\s+|all\s+)?projects'
    r'|projects\s+in\s+(memory|rag)'
    # CAPACITÀ / HELP
    r'|cosa\s+sai\s+fare'
    r'|what\s+can\s+you\s+do'
    r'|come\s+funzioni'
    r'|how\s+(do\s+)?you\s+work'
    r'|quali\s+(sono\s+)?le\s+tue\s+capacit)',
    re.IGNORECASE
)

PURE_GREETING = re.compile(
    r'^(ciao|hello|hi|hey|buongiorno|buonasera|buonpomeriggio|salve|'
    r'grazie|thanks|ok|okay|sì|si|no|'
    r'come\s+stai|come\s+va|tutto\s+bene|che\s+si\s+fa|'
    r'grazie\s+(mille|tante|tanto)|'
    r'buona\s+(giornata|serata|notte))$',
    re.IGNORECASE
)

PROJECT_KEYWORDS = {
    'codice', 'progetto', 'file', 'script', 'funzione', 'classe', 'metodo',
    'bug', 'errore', 'riga', 'cartella', 'struttura', 'repo', 'repository',
    'implementa', 'refactor', 'test', 'compila', 'variabile', 'log', 'modifica',
    'aggiungi', 'rimuovi', 'codebase',
    'configurazione', 'gestione', 'sicurezza', 'autenticazione', 'connessione',
    'websocket', 'database', 'api', 'endpoint', 'middleware', 'protocollo',
    'server', 'client', 'richiesta', 'risposta', 'proxy', 'rete', 'network',
    'pool', 'worker', 'buffer', 'cache', 'thread', 'processo', 'memoria',
    'algoritmo', 'compressione', 'crittografia', 'token', 'sessione',
    'debug', 'deploy', 'build', 'config', 'runtime', 'dependency', 'package',
    'versione', 'release', 'commit', 'branch', 'migrazione', 'backup'
}

# System prompt per Gemma 4 in risposta diretta ma naturale
CAVEMAN_GEMMA_SYSTEM = (
    "You are Jarvis, a direct coding assistant. Be concise but natural. "
    "IMPORTANT: The input you receive contains structured labels (Project:, Task:, "
    "Context:, etc.) for your reference only. DO NOT echo or mirror this structure "
    "in your response. "
    "Skip pleasantries and fluff — get straight to the point. "
    "When providing code: output clean SEARCH/REPLACE blocks. "
    "Never say 'I think', 'I believe', 'I'd suggest'. Just state facts."
)

MERMAID_RULES = (
    "\n"
    "- Mermaid diagrams: wrap in ```mermaid blocks. NEVER use parentheses () inside"
    " square bracket node labels A[...] — they break the parser. Use quotes:"
    ' A["Node (with parens)"] instead of A[Node (with parens)].\n'
    "- Link labels use pipe syntax: A -->|label| B  (NOT A -- label --> B).\n"
    "- Put comments on their own line with %%, never inline with % after a statement.\n"
    "- Valid node shapes: A[rect], A(round), A{rhombus}, A[(cylinder DB)], A>flag].\n"
)

CAVEMAN_GEMMA_SYSTEM_ADDENDUM = (
    "\n\n[RESPONSE RULES]\n"
    "- No thinking tags, no XML tags.\n"
    "- Code changes: SEARCH/REPLACE blocks only.\n"
    "- Use Markdown formatting for readability: tables for comparisons/data, "
    "code blocks for code/config/schemas, bullet lists for multiple items, "
    "bold for key terms.\n"
    "- ALWAYS end with a concise final notes section. Format:"
    "\n---"
    "\n**Riepilogo:** ... (2-3 bullet points max)"
    "\n**Attenzione:** ... (edge cases, warnings, o ometti se non serve)"
    "\n"
    "- Be concise but readable.\n"
    "- Stop once the answer is complete."
    + MERMAID_RULES
)


# ════════════════════════════════════════════════════════════════
# FUNZIONI DI SUPPORTO (estratte da build_omniscient_prompt)
# ════════════════════════════════════════════════════════════════

async def _keyword_bypass(user_message: str, context: dict) -> GatekeeperResult | None:
    """STEP 1: Fast path bypass — 0 LLM calls.

    Returns GatekeeperResult se matcha, None se deve passare a STEP 2.
    """
    msg_lower = user_message.lower().strip()
    if len(msg_lower) < 3:
        return GatekeeperResult(intent="general", confidence=1.0)

    projects = context.get("projects_available", [])
    for proj in projects:
        proj_lower = proj.lower()
        for variant in (proj_lower, proj_lower.replace('_', '-'), proj_lower.replace('_', ' ')):
            if variant in msg_lower:
                logger.info(f"🧠 Bypass: PROJECT (nome progetto in query: {proj})")
                return GatekeeperResult(intent="project", project=proj, confidence=1.0)

    if META_PHRASES.search(msg_lower):
        logger.info("🧠 Bypass: META (frase match)")
        return GatekeeperResult(intent="meta", confidence=1.0)

    if PURE_GREETING.match(msg_lower):
        logger.info("🧠 Bypass: GENERAL (saluto puro)")
        return GatekeeperResult(intent="general", confidence=1.0)

    words = set(re.findall(r'\b\w+\b', msg_lower))
    if words.intersection(PROJECT_KEYWORDS):
        logger.info("🧠 Bypass: PROJECT (keyword match)")
        return GatekeeperResult(intent="project", confidence=1.0)
    if re.search(r'(\.[a-z]{1,4}\b|\b(src|app|lib|bin)/)', msg_lower):
        logger.info("🧠 Bypass: PROJECT (path regex match)")
        return GatekeeperResult(intent="project", confidence=1.0)

    return None  # Nessun bypass → STEP 2


async def _run_gatekeeper(user_message: str, context: dict) -> GatekeeperResult:
    """STEP 2: Qwen3.5 Gatekeeper — classificazione intento con grammar.

    Usa engine.classify_intent() che invoca Qwen3.5 su CPU.
    """
    return await engine.classify_intent(user_message, context)


def _record_gatekeeper_stats(intent: str, confidence: float, bypassed: bool, project: str | None = None):
    """Aggiorna le statistiche cumulative del Gatekeeper (esposte via MCP)."""
    try:
        if state.gatekeeper_stats is None:
            state.gatekeeper_stats = GatekeeperStats()
        state.gatekeeper_stats.record(intent, confidence, bypassed, project)
    except Exception as exc:
        logger.warning(f"Errore aggiornamento gatekeeper_stats: {exc}")


# ────────────────────────────────────────────────────────────────
# Helper: inject datetime into messages
# ────────────────────────────────────────────────────────────────

def _inject_datetime(messages) -> str:
    """Truncate history, inject current datetime as system + user message.

    Returns _dt_now string for downstream use.
    """
    # Truncate
    if len(messages) > 20:
        messages[:] = messages[-20:]  # mutate in place
    for m in messages[:-1]:
        if m.get("content") and len(m["content"]) > 1500:
            m["content"] = m["content"][:1500] + "\n...[TRUNCATED FOR CONTEXT LIMIT]..."

    _dt_now = _datetime_context()
    messages.insert(0, {"role": "system", "content": _dt_now})
    for _i in range(len(messages) - 1, -1, -1):
        if messages[_i]["role"] == "user":
            messages[_i]["content"] = (
                f"[CURRENT DATETIME — YOU MUST USE THIS: {_dt_now}]\n\n"
                f"{messages[_i]['content']}"
            )
            break
    return _dt_now


# ────────────────────────────────────────────────────────────────
# Helper: parse <PERSONA>, <FOCUS>, <LANG>, <MEMORY_COUNT> tags
# ────────────────────────────────────────────────────────────────

def _parse_super_tags(msg: str) -> tuple[str, dict]:
    """Extract super-prompt tags from user message. Returns (clean_msg, overrides_dict).

    overrides_dict keys: persona, focus, lang, mem_count (int, default 0)
    """
    overrides = {
        "persona": "",
        "focus": "",
        "lang": "",
        "mem_count": 0,
    }
    tag_re = re.compile(r"<(PERSONA|FOCUS|LANG|MEMORY_COUNT)\b([^>]*)>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
    for match in tag_re.finditer(msg):
        tag_name = match.group(1).upper()
        tag_content = match.group(3).strip()
        if tag_name == "PERSONA":
            overrides["persona"] = tag_content
        elif tag_name == "FOCUS":
            overrides["focus"] = tag_content
        elif tag_name == "LANG":
            overrides["lang"] = tag_content
        elif tag_name == "MEMORY_COUNT":
            try:
                overrides["mem_count"] = max(0, int(tag_content))
            except ValueError:
                pass
    if tag_re.search(msg):
        msg = tag_re.sub("", msg).strip()
    return msg, overrides


# ────────────────────────────────────────────────────────────────
# Helper: compute Max budget for compression
# ────────────────────────────────────────────────────────────────

def _compute_max_budget() -> int:
    """Calcola il budget massimo di caratteri per il contesto compresso."""
    num_ctx = int(LLM_OPTIONS.get("num_ctx", MODEL_PROFILE.default_ctx))
    if num_ctx > MODEL_PROFILE.max_ctx:
        num_ctx = MODEL_PROFILE.max_ctx
    safe_tokens_for_prompt = num_ctx - 5000
    MAX_BUDGET = int(safe_tokens_for_prompt * 1.3)
    if MAX_BUDGET > 15000:
        MAX_BUDGET = 15000
    elif MAX_BUDGET < 4000:
        MAX_BUDGET = 4000
    return MAX_BUDGET


# ────────────────────────────────────────────────────────────────
# Helper: allocate budget across context sources
# ────────────────────────────────────────────────────────────────

def _allocate_budget(
    rag_ctx: str, web_ctx: str, mem_ctx: str, cg_ctx: str,
    active_project: str | None, MAX_BUDGET: int,
    recent_user_msgs: list[str], user_id: str | None,
) -> tuple[str, str, str, str, str, str, str, int]:
    """Distribuisce il budget tra RAG, tree, web, memory, tasks.

    Returns (rag_final, tree_ctx, web_final, mem_final, tasks_final,
             history_str, rag_context_for_compress, raw_size).
    """
    rag_budget = int(MAX_BUDGET * 0.55)
    rag_final = rag_ctx.strip()[:rag_budget] if rag_ctx and rag_ctx.strip() else ""

    remaining = MAX_BUDGET - len(rag_final)
    if rag_ctx and rag_ctx.strip() and active_project:
        _tree_lines = state.project_tree_cache.split('\n')
        _filtered = []
        _capture = None
        for _line in _tree_lines:
            if _line.startswith('📁 ') and _line.endswith('/'):
                _proj_name = _line[2:-1]
                _capture = _proj_name == active_project
            if _capture:
                _filtered.append(_line)
        _tree_str = '\n'.join(_filtered) if any(l.startswith('📁') for l in _filtered) else state.project_tree_cache
        tree_ctx = _tree_str[:min(800, remaining)]
    elif rag_ctx and rag_ctx.strip():
        tree_ctx = state.project_tree_cache[:min(800, remaining)]
    else:
        tree_ctx = ""
    remaining -= len(tree_ctx)

    web_final = web_ctx.strip()[:min(1500, remaining)] if web_ctx and web_ctx.strip() else ""
    remaining -= len(web_final)

    mem_final = mem_ctx.strip()[:min(800, remaining)] if mem_ctx and mem_ctx.strip() else ""

    open_tasks = get_open_tasks(user_id)
    tasks_final = ""
    if open_tasks:
        tasks_final = "Task Aperti:\n"
        for k, v in open_tasks.items():
            t_type = "Progetto" if v.get("owner", "global") == "global" else "Personale"
            tasks_final += f"- [{k}] [{t_type}] {v['desc']} (Prio: {v['priority']}, Scad: {v['deadline']})\n"

    history_str = " | ".join(recent_user_msgs) if recent_user_msgs else ""
    if tasks_final:
        history_str = (history_str + "\n" + tasks_final) if history_str else tasks_final

    # Assemble context for compressor
    rag_context_for_compress = rag_final
    if tree_ctx:
        rag_context_for_compress = tree_ctx + "\n" + rag_context_for_compress if rag_context_for_compress else tree_ctx
    if web_final:
        rag_context_for_compress = rag_context_for_compress + "\n[WEB]\n" + web_final if rag_context_for_compress else "[WEB]\n" + web_final
    if mem_final:
        rag_context_for_compress = rag_context_for_compress + "\n[MEMORY]\n" + mem_final if rag_context_for_compress else "[MEMORY]\n" + mem_final
    if cg_ctx:
        rag_context_for_compress = rag_context_for_compress + "\n" + cg_ctx if rag_context_for_compress else cg_ctx

    raw_size = 0  # Caller recalculates with clean_msg

    return (rag_final, tree_ctx, web_final, mem_final, tasks_final,
            history_str, rag_context_for_compress, raw_size)


# ────────────────────────────────────────────────────────────────
# Helper: caveman compression with fallback
# ────────────────────────────────────────────────────────────────

async def _run_compression(
    clean_msg: str, rag_context_for_compress: str, history_str: str,
    active_project: str | None, mem_final: str, tasks_final: str,
    rag_final: str, web_final: str,
) -> tuple[str, bool]:
    """Compress context via Qwen3.5 caveman, fall back to raw labels on failure.

    Returns (compressed_text, is_raw_fallback).
    """
    compressed = await engine.compress_prompt(
        user_query=clean_msg,
        rag_context=rag_context_for_compress,
        history=history_str,
        active_project=active_project,
    )

    is_raw = False
    if not compressed or len(compressed) < 20:
        logger.warning("⚠️ Caveman compression fallita, uso fallback raw")
        is_raw = True
        fallback_parts = []
        if mem_final:
            fallback_parts.append(f"Memory: {mem_final[:500]}")
        if tasks_final:
            fallback_parts.append(f"Tasks: {tasks_final[:300]}")
        if active_project:
            fallback_parts.append(f"Project: {active_project}")
        if rag_final:
            fallback_parts.append(f"Context:\n{rag_final[:2000]}")
        if web_final:
            fallback_parts.append(f"Web: {web_final[:500]}")
        fallback_parts.append(f"Query: {clean_msg}")
        compressed = "\n".join(fallback_parts)[:4096]

    if compressed.startswith("[PROJECT:") or compressed.startswith("[RAG_CONTEXT]"):
        is_raw = True
        logger.warning("⚠️ Caveman compression fallback raw (raw_data labels)")

    return compressed, is_raw


# ────────────────────────────────────────────────────────────────
# Helper: build final Gemma 4 prompt
# ────────────────────────────────────────────────────────────────

def _build_final_prompt(
    compressed: str, is_raw: bool, messages: list,
    _dt_now: str, mem_ctx: str, rag_final: str, web_ctx: str,
    cg_ctx: str, tracer: PipelineTracer,
) -> list:
    """Build system prompt + user content from compressed context.

    Mutates messages in-place (appends system prompt, replaces user content).
    Returns messages for convenience.
    """
    _dt = _datetime_context()
    if is_raw:
        system_prompt = (
            f"[{_dt}]\n\n"
            "You are Jarvis, a helpful coding assistant with access to project context.\n"
            "The context below uses labels (Project:, Task:, Context:) for your reference only. "
            "DO NOT echo them.\n\n"
            "Please respond naturally and helpfully based on the context above.\n\n"
            "[FORMAT RULES]\n"
            "- Use Markdown formatting: tables for comparisons/data, "
            "code blocks for code/config/schemas, bullet lists for multiple items, "
            "bold for key terms.\n"
            "- FINAL NOTES: Always close your response with:\n"
            "---\n"
            "Riepilogo: (2-3 bullet riassuntivi)\n"
            "Attenzione: (warnings/note, ometti se non serve)\n"
            "\n"
            "- No thinking tags, no XML tags.\n"
            + MERMAID_RULES + "\n"
        )
        user_content = f"Context:\n{compressed}"
    else:
        system_prompt = (
            f"[{_dt}]\n\n"
            + CAVEMAN_GEMMA_SYSTEM + "\n" + CAVEMAN_GEMMA_SYSTEM_ADDENDUM
        )
        user_content = compressed

    messages.append({"role": "system", "content": system_prompt})
    for m in reversed(messages):
        if m["role"] == "user":
            m["content"] = user_content
            break

    # Tracer
    tracer.set_system_prompt(system_prompt)
    tracer.set_user_content(user_content)
    tracer.set_compressed_text(str(compressed) if compressed else "")
    _rag_ctx_combined = (
        (f"[MEMORY]\n{mem_ctx}\n\n" if mem_ctx else "")
        + (f"[RAG]\n{rag_final}\n\n" if rag_final else "")
        + (f"[WEB]\n{web_ctx}\n\n" if web_ctx else "")
        + (f"[SYNAPTIQ]\n{cg_ctx}\n\n" if cg_ctx else "")
    )
    tracer.set_rag_context(_rag_ctx_combined.strip())
    return messages


# ════════════════════════════════════════════════════════════════
# BUILD OMNISCIENT PROMPT (orchestrator)
# ════════════════════════════════════════════════════════════════

async def build_omniscient_prompt(messages, user_id=None, conversation_id="default", concise=False, request_id=None, finalize_trace: bool = True, user=None):
    """
    Pipeline di arricchimento a 4 step con Caveman Compression.

    FLUSSO:
      STEP 1: Keyword Bypass (regex, 0 LLM)
      STEP 2: Qwen3.5 Gatekeeper (CPU, classificazione intento)
      STEP 3: Qwen3.5 Caveman Compression (CPU, comprime RAG+history+query)
      STEP 4: Gemma 4 (GPU) → risposta caveman

    Se concise=True, salta RAG/memoria/web e usa compressed prompt minimo.

    Args:
        request_id: Se fornito, riusa un PipelineTracer esistente (da main.py).
                    Altrimenti ne crea uno nuovo internamente.
        finalize_trace: Se True (default), chiama tracer.finish() prima di tornare.
                        Se False, lascia il tracer aperto per uso esterno (MCP chat_send).
    """
    # ── Extract latest message and setup tracer ──
    user_messages = [m["content"] for m in messages if m["role"] == "user"]
    latest_msg = user_messages[-1] if user_messages else ""
    if not latest_msg:
        if request_id:
            tracer = PipelineTracer.get(request_id)
            if tracer:
                tracer.step("build_omniscient_prompt", status="skipped", details={"reason": "empty_message"})
                if finalize_trace:
                    tracer.finish()
        return messages

    current_user_id = user_id if user_id else "alfio_dev"
    tracer: PipelineTracer | None = None
    if request_id:
        tracer = PipelineTracer.get(request_id)
    if tracer is None:
        tracer = PipelineTracer.begin(user_message=latest_msg, user_id=current_user_id)
    tracer.start_step("prompt_preprocessing")

    # ── Inject datetime, truncate history ──
    _dt_now = _inject_datetime(messages)

    tracer.end_step("prompt_preprocessing", details={"msg_len": len(latest_msg), "history_len": len(messages)})

    # ════════════════════════════════════════════════════
    # CONCISE MODE
    # ════════════════════════════════════════════════════
    if concise:
        tracer.start_step("concise_pipeline")
        _, clean_msg = await perform_web_search_and_crawl(latest_msg)
        if state.memory:
            try:
                async def _bg_add_concise():
                    await save_to_memory(clean_msg, user_id=current_user_id)
                task = asyncio.create_task(_bg_add_concise())
                state.background_tasks.add(task)
                task.add_done_callback(state.background_tasks.discard)
            except Exception:
                pass
        if PURE_GREETING.match(clean_msg.strip().lower()):
            logger.info("🗣️ Concise + saluto: skip caveman compression")
            tracer.end_step("concise_pipeline", status="skipped", details={"reason": "greeting"})
            if finalize_trace:
                tracer.finish()
            return messages

        compressed = await engine.compress_prompt(
            user_query=clean_msg, rag_context="", history="", active_project=None,
        )
        tracer.add_llm_call(
            compressed._as_llm_record("caveman_compression") if hasattr(compressed, '_as_llm_record') else
            __import__('telemetry', fromlist=['LlmCallRecord']).LlmCallRecord(
                model="gatekeeper", step="caveman_compression", duration_ms=0, temperature=0.0
            )
        )
        if not compressed or len(compressed) < 20:
            logger.warning("⚠️ Caveman compress fallita in concise mode, fallback raw")
            user_content = f"Query: {clean_msg}"
            tracer.end_step("concise_pipeline", status="error", details={"fallback": "raw", "comp_len": len(compressed) if compressed else 0})
        else:
            user_content = compressed
            tracer.end_step("concise_pipeline", details={"comp_len": len(compressed)})
        system_prompt = f"[{_datetime_context()}]\n\n" + CAVEMAN_GEMMA_SYSTEM + "\n" + CAVEMAN_GEMMA_SYSTEM_ADDENDUM
        messages.append({"role": "system", "content": system_prompt})
        for m in reversed(messages):
            if m["role"] == "user":
                m["content"] = user_content
                break
        tracer.set_system_prompt(system_prompt)
        tracer.set_user_content(user_content)
        if not PURE_GREETING.match(clean_msg.strip().lower()):
            tracer.set_compressed_text(compressed if isinstance(compressed, str) else str(compressed))
        if finalize_trace:
            tracer.finish()
        return messages

    # ════════════════════════════════════════════════════
    # WEB SEARCH + SUPER TAG PARSING
    # ════════════════════════════════════════════════════
    web_ctx, clean_msg = await perform_web_search_and_crawl(latest_msg)
    mem_ctx, rag_ctx = "", ""
    latest_msg, user_overrides = _parse_super_tags(latest_msg)
    _user_override_persona = user_overrides.get("persona", "")
    _user_override_focus = user_overrides.get("focus", "")
    _user_override_lang = user_overrides.get("lang", "")
    _user_override_mem_count = user_overrides.get("mem_count", 0)

    # ════════════════════════════════════════════════════
    # STEP 1 + 2: GATEKEEPER
    # ════════════════════════════════════════════════════
    _active_before = state.get_last_project(current_user_id, conversation_id)
    _all_projects = await list_rag_projects(user=user)
    _recent_user_msgs = [m["content"] for m in messages if m["role"] == "user"][-3:]

    _gk_context = {
        "active_project": _active_before,
        "projects_available": _all_projects,
        "recent_messages": _recent_user_msgs,
    }
    tracer.start_step("keyword_bypass")
    gk = await _keyword_bypass(latest_msg, _gk_context)
    _bypassed = gk is not None
    tracer.end_step("keyword_bypass", details={"bypassed": _bypassed, "intent": gk.intent if gk else None, "project": gk.project if gk else None})

    if gk is None:
        tracer.start_step("gatekeeper_llm")
        gk = await _run_gatekeeper(latest_msg, _gk_context)
        tracer.end_step("gatekeeper_llm", details={"intent": gk.intent, "project": gk.project, "confidence": gk.confidence})

    tracer.set_gatekeeper(intent=gk.intent, project=gk.project, confidence=gk.confidence, bypassed=_bypassed)
    tracer._gatekeeper_model = "bypass" if _bypassed else "qwen"
    _record_gatekeeper_stats(gk.intent, gk.confidence, _bypassed, gk.project)

    # ════════════════════════════════════════════════════
    # ROUTING: project / meta / general
    # ════════════════════════════════════════════════════
    active_project: str | None = None
    _is_project_query: bool = False
    _is_meta_query: bool = False

    if gk.intent == "meta":
        _is_meta_query = True
        if _all_projects:
            rag_ctx = "📚 Progetti indicizzati nel RAG:\n" + "\n".join(f"- {p}" for p in _all_projects)
        logger.info("🗂️ Gatekeeper META: lista progetti, contesto progetto saltato")

    elif gk.intent == "project":
        _is_project_query = True
        if gk.project and gk.project in _all_projects:
            active_project = gk.project
        else:
            active_project = await detect_project_in_conversation(user_messages)
        if not active_project:
            active_project = state.get_last_project(current_user_id, conversation_id)
            if active_project:
                logger.info(f"📁 Progetto ripristinato dal contesto: {active_project}")
        if active_project:
            logger.info(f"📁 Progetto attivo: {active_project}")
            state.set_last_project(current_user_id, conversation_id, active_project)

    if state.memory:
        try:
            async def _bg_add():
                await save_to_memory(clean_msg, user_id=current_user_id, project=active_project)
            task = asyncio.create_task(_bg_add())
            state.background_tasks.add(task)
            task.add_done_callback(state.background_tasks.discard)
        except Exception as e:
            logger.warning(f"Errore memory add: {e}")

    # ════════════════════════════════════════════════════
    # EARLY RETURNS: general / meta
    # ════════════════════════════════════════════════════
    if gk.intent == "general":
        logger.info("🗣️ Intento GENERAL: skip caveman compression, messaggio originale preservato")
        tracer.step("context_gathering", status="skipped", details={"reason": "general_intent"})
        tracer.step("caveman_compression", status="skipped", details={"reason": "general_intent"})
        if finalize_trace:
            tracer.finish()
        return messages

    if gk.intent == "meta":
        logger.info("🗂️ Intento META: skip caveman compression, risposta conversazionale")
        meta_context = "\n".join(f"- {p}" for p in _all_projects) if _all_projects else "Nessun progetto indicizzato."
        meta_prompt = f"[CURRENT DATETIME — YOU MUST USE THIS: {_dt_now}]\n\nProgetti disponibili:\n{meta_context}\n\nDomanda: {clean_msg}"
        for m in reversed(messages):
            if m["role"] == "user":
                m["content"] = meta_prompt
                break
        tracer.set_user_content(meta_prompt)
        tracer.step("context_gathering", status="skipped", details={"reason": "meta_intent"})
        tracer.step("caveman_compression", status="skipped", details={"reason": "meta_intent"})
        if finalize_trace:
            tracer.finish()
        return messages

    # ════════════════════════════════════════════════════
    # CONTEXT GATHERING (STEP 3)
    # ════════════════════════════════════════════════════
    tracer.start_step("context_gathering")

    async def _gather_memory():
        if not state.memory:
            return ""
        try:
            loop = asyncio.get_running_loop()
            _mem_limit = _user_override_mem_count if _user_override_mem_count > 0 else 5
            memory_results = []
            gen_search = partial(state.memory.search, query=clean_msg, filters={"user_id": current_user_id}, limit=_mem_limit)
            gen_res = await loop.run_in_executor(state.mem0_executor, gen_search)
            if gen_res:
                memory_results.append(gen_res)
            if active_project:
                proj_search = partial(state.memory.search, query=clean_msg, filters={"user_id": current_user_id, "project": active_project}, limit=_mem_limit)
                proj_res = await loop.run_in_executor(state.mem0_executor, proj_search)
                if proj_res:
                    memory_results.append(proj_res)
            all_memories = []
            if isinstance(memory_results, list):
                for r in memory_results:
                    extracted = extract_memories(r)
                    if extracted:
                        all_memories.append(extracted)
            return "\n".join(all_memories) if all_memories else ""
        except Exception as e:
            logger.warning(f"Errore memory search: {e}")
            return ""

    async def _gather_rag():
        if latest_msg.startswith("/web "):
            return ""
        full_files_content = ""
        if _is_project_query:
            matches = set(re.findall(r'\b([\w\.\-/]+\.(?:py|js|ts|jsx|tsx|go|c|cpp|h|hpp|rs|sql|yaml|yml|md|json))\b', latest_msg))
            if matches:
                filt = GitignoreFilter(DOC_DIR)
                for match in matches:
                    filename_only = match.split('/')[-1]
                    for root, dirs, files in os.walk(DOC_DIR):
                        dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules', 'venv', 'vendor')]
                        if filename_only in files:
                            fp = os.path.join(root, filename_only)
                            rp = os.path.relpath(fp, DOC_DIR)
                            if not filt.is_ignored(rp):
                                if match in rp or match == filename_only:
                                    try:
                                        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                                            fc = f.read()
                                        full_files_content += f"\n\n📄 FILE COMPLETO RICHIESTO ({rp}):\n```\n{fc}\n```\n"
                                    except Exception:
                                        pass
        if not _is_meta_query:
            _rag_project = _user_override_focus if _user_override_focus else active_project
            rag_ctx_local = await search_documents(clean_msg, is_project_query=_is_project_query, project_name=_rag_project, user=user)
        else:
            rag_ctx_local = ""
        if full_files_content:
            rag_ctx_local = full_files_content + "\n" + rag_ctx_local
        return rag_ctx_local

    async def _gather_synaptiq():
        if latest_msg.startswith("/web "):
            return ""
        if not (_is_project_query and synaptiq_engine and synaptiq_engine.is_initialized):
            return ""
        try:
            sy_raw = await synaptiq_engine.pack_snippets(clean_msg, limit=8)
            if sy_raw and len(sy_raw) > 100:
                logger.info(f"🧠 Synaptiq context: {len(sy_raw)} chars")
                return f"\n<SYNAPTIQ>\n{sy_raw[:3000]}\n</SYNAPTIQ>\n"
        except Exception as e:
            logger.debug(f"Synaptiq explore non disponibile: {e}")
        return ""

    mem_task = asyncio.create_task(_gather_memory())
    rag_task = asyncio.create_task(_gather_rag())
    synaptiq_task = asyncio.create_task(_gather_synaptiq())
    mem_ctx, rag_ctx, cg_ctx = await asyncio.gather(mem_task, rag_task, synaptiq_task)

    # Auto web discovery (dipende da rag_ctx → non parallelizzabile)
    _is_short_greeting = len(clean_msg.strip()) < 20 and not _is_project_query
    if not _is_short_greeting and not rag_ctx.strip() and not web_ctx:
        search_query = clean_msg
        if _is_project_query and active_project and active_project not in search_query:
            search_query = f"{active_project} {search_query}"
        web_knowledge_ctx = await search_web_knowledge(search_query)
        if web_knowledge_ctx:
            web_ctx = web_knowledge_ctx
            logger.info(f"🌐 Web knowledge cache HIT: '{clean_msg[:60]}...'")
        else:
            web_search_ctx, _ = await perform_web_search_and_crawl(latest_msg, force=True)
            if web_search_ctx and web_search_ctx != "Nessun risultato online.":
                sources = []
                for line in web_search_ctx.split("\n"):
                    if line.startswith("URL: "):
                        sources.append(line[5:])
                await save_web_knowledge(search_query, web_search_ctx, sources)
                web_ctx = web_search_ctx
                tag = f" [progetto: {active_project}]" if active_project else ""
                logger.info(f"🌐 Auto web discovery: ricercato e salvato '{clean_msg[:60]}...'{tag}")
                async def _bg_save_web():
                    summary = f"[Web Knowledge] Query: {clean_msg[:200]}\nFonti: {', '.join(sources[:3])}\nRisultati: {web_search_ctx[:600]}"
                    await save_to_memory(summary, user_id=current_user_id, project=active_project)
                task = asyncio.create_task(_bg_save_web())
                state.background_tasks.add(task)
                task.add_done_callback(state.background_tasks.discard)

    ctx_details = {
        "rag_len": len(rag_ctx) if rag_ctx else 0,
        "mem_len": len(mem_ctx) if mem_ctx else 0,
        "web_len": len(web_ctx) if web_ctx else 0,
        "project": active_project,
    }
    tracer.end_step("context_gathering", details=ctx_details)

    # Popola tracer con dati contesto
    _rag_project = (_user_override_focus if _user_override_focus else active_project) if _is_project_query else None
    tracer._rag_ctx_len = len(rag_ctx) if rag_ctx else 0
    tracer._rag_project = _rag_project
    tracer._memory_records = len(mem_ctx.split("\n")) if mem_ctx else 0
    tracer._web_search_performed = bool(web_ctx)
    tracer._synaptiq_performed = bool(cg_ctx)
    tracer._synaptiq_chars = len(cg_ctx) if cg_ctx else 0

    # ════════════════════════════════════════════════════
    # STEP 3: CAVEMAN COMPRESSION
    # ════════════════════════════════════════════════════
    tracer.start_step("caveman_compression")

    MAX_BUDGET = _compute_max_budget()
    (rag_final, tree_ctx, web_final, mem_final, tasks_final,
     history_str, rag_context_for_compress, _raw_size) = _allocate_budget(
        rag_ctx, web_ctx, mem_ctx, cg_ctx, active_project, MAX_BUDGET,
        _recent_user_msgs, user_id,
    )

    # Calcola raw_size esatto (clean_msg not available in _allocate_budget)
    raw_size = len(rag_context_for_compress) + len(history_str) + len(clean_msg)

    compressed, _compression_is_raw = await _run_compression(
        clean_msg, rag_context_for_compress, history_str, active_project,
        mem_final, tasks_final, rag_final, web_final,
    )

    comp_details = {
        "raw_size": raw_size,
        "comp_size": len(compressed),
        "is_raw_fallback": _compression_is_raw,
        "budget": MAX_BUDGET,
    }
    tracer.end_step("caveman_compression", details=comp_details)
    tracer._compression_raw_size = raw_size
    tracer._compression_is_raw = _compression_is_raw

    # ════════════════════════════════════════════════════
    # STEP 4: BUILD GEMMA 4 PROMPT
    # ════════════════════════════════════════════════════
    tracer.start_step("build_prompt")
    _build_final_prompt(compressed, _compression_is_raw, messages, _dt_now,
                        mem_ctx, rag_final, web_ctx, cg_ctx, tracer)
    tracer.end_step("build_prompt", details={
        "system_prompt_len": len(CAVEMAN_GEMMA_SYSTEM) if not _compression_is_raw else 500,
        "user_content_len": len(compressed),
    })

    if finalize_trace:
        tracer.finish()
    return messages
