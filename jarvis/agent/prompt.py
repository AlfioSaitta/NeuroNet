"""
Prompt Builder — Pipeline di generazione prompt a 4 step con Caveman Compression.

FLUSSO:
  STEP 1: Intent Router (agent/intent_router.py) — fast-path regex 0 LLM +
          classificazione LLM su intent nativo (22 valori)
  STEP 2: Routing per intent (greeting/general/meta/project/web/...)
  STEP 3: Qwen3.5 Caveman Prompt Architect (CPU, compressione 40-60%)
          Skip automatico se contesto < 1000 chars (Op1/Op8)
  STEP 4: Qwen3.5-4B su GPU → risposta
"""

import datetime
import os
import re
import asyncio
import time
from functools import partial

from core.config import logger, BOT_NAME, LLM_OPTIONS, MODEL_PROFILE, DOC_DIR
from rag.engine import search_documents, generate_project_tree, list_rag_projects, detect_project_in_conversation, GitignoreFilter
from rag.cache import search_web_knowledge, save_web_knowledge
from memory.engine import extract_memories, save_to_memory
from rag.web_search import perform_web_search_and_crawl, is_web_requiring_query, clean_web_query
from agent.tags import build_tag_instructions
from scheduler.tasks import get_open_tasks
from core.llm_engine import extract_content
from agent import intent_router
from agent.context_compressor import compress as _compress_context, compress_concise as _compress_concise
try:
    from graph.synaptiq_engine import synaptiq_engine
except ImportError:
    synaptiq_engine = None
from core.telemetry import PipelineTracer, IntentStats, LlmCallRecord
from core.hardware import get_hardware_block
import core.state as state

# ════════════════════════════════════════════════════════════════
# CACHE RAG PROJECTS (TTL 60s — evita chiamate Qdrant per ogni richiesta)
# ════════════════════════════════════════════════════════════════

_RAG_PROJECTS_CACHE: dict[str, tuple[float, list[str]]] = {}
_RAG_PROJECTS_CACHE_TTL = 60.0

async def _get_cached_rag_projects(user=None) -> list[str]:
    """Wrapper per list_rag_projects() con cache TTL 60s per utente."""
    cache_key = user.get("username", "__anon__") if user else "__anon__"
    now = time.monotonic()
    if cache_key in _RAG_PROJECTS_CACHE:
        ts, data = _RAG_PROJECTS_CACHE[cache_key]
        if now - ts < _RAG_PROJECTS_CACHE_TTL:
            return data
    data = await list_rag_projects(user=user)
    _RAG_PROJECTS_CACHE[cache_key] = (now, data)
    return data


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
# Le costanti di routing (fast-path regex/parole chiave) sono centralizzate
# in agent.intent_router (importate sopra):
# Fase 1 del piano intent_understanding_llm.md — unica fonte di verità.

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
    "- You CAN use tools. When the user asks you to DO something (read/write files, "
    "run commands, search code), use <tool_call> XML. That is the ONLY XML tag you may use.\n"
    "- No thinking tags, no Jarvis action XML tags (MEMORY, SCHEDULE, SSH, TODO, WEB, etc.).\n"
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


def _hardware_identity_block() -> str:
    """Blocco identità hardware (GPU/CPU/RAM reali) per il system prompt.

    Rilevato all'avvio da core/hardware.py. Concatenato a runtime (non nelle
    costanti module-level, valutate all'import — prima del detection).
    """
    try:
        return get_hardware_block()
    except Exception:
        return ""


# ────────────────────────────────────────────────────────────────
# General conversation system prompt (for greeting + general intents)
# ────────────────────────────────────────────────────────────────
# Qwen3.5-4B tende a produrre ragionamento interno in inglese come testo piatto
# quando non ha istruzioni esplicite. Questo prompt minimalista previene la fuga
# di CoT e forza risposte dirette nella lingua dell'utente.
GENERAL_CONVERSATION_SYSTEM = (
    "You are Jarvis, a direct and friendly assistant.\n\n"
    "CRITICAL — You MUST follow these rules without exception:\n"
    "- Answer IMMEDIATELY. Do NOT analyze, reason, or think out loud before responding.\n"
    "- NEVER describe what you're going to do. JUST RESPOND.\n"
    '- NEVER start with "the user", "user", or any meta-analysis of the request.\n'
    "- NEVER narrate your thought process or internal instructions.\n"
    "- Respond in the SAME LANGUAGE as the user.\n"
    "- No thinking tags, no XML tags of any kind.\n"
    "- Be concise but warm.\n\n"
    "[CURRENT DATE/TIME]\n"
    "The user message contains the current date and time in square brackets.\n"
    "- USE it ONLY when the user's request requires time/date information: "
    "\"che ore sono?\", \"what time is it?\", \"quando ho fatto l'ultima modifica "
    "al codice?\", \"quanti giorni mancano all'evento X?\", deadlines, countdowns.\n"
    "- IGNORE it for all other requests: general chat, greetings, coding help, "
    "explanations. Do NOT mention, reason about, or comment on the date unless "
    "the request asks for it.\n"
    "- NEVER write code (Python datetime, shell date, etc.) to compute the current "
    "time/date — the value is ALREADY provided in the prompt. Just read it and answer.\n"
)

# ════════════════════════════════════════════════════════════════
# FUNZIONI DI SUPPORTO (estratte da build_omniscient_prompt)
# ════════════════════════════════════════════════════════════════

def _record_intent_stats(intent: str, confidence: float, bypassed: bool, project: str | None = None, source: str | None = None):
    """Aggiorna le statistiche cumulative del classificatore intenti (esposte via MCP)."""
    try:
        if state.intent_stats is None:
            state.intent_stats = IntentStats()
        state.intent_stats.record(intent, confidence, bypassed, project, source)
    except Exception as exc:
        logger.warning(f"Errore aggiornamento intent_stats: {exc}")


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
                f"[Current date/time: {_dt_now}]\n\n"
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
            "- No thinking tags, no Jarvis action XML tags (MEMORY, SCHEDULE, SSH, TODO, WEB, etc.).\n"
            "- Use <tool_call> XML for tool calling when instructed — that is the ONLY allowed XML tag.\n"
            + MERMAID_RULES + "\n"
            + _hardware_identity_block()
        )
        user_content = f"Context:\n{compressed}"
    else:
        system_prompt = (
            f"[{_dt}]\n\n"
            + CAVEMAN_GEMMA_SYSTEM + "\n" + CAVEMAN_GEMMA_SYSTEM_ADDENDUM
            + "\n\n" + _hardware_identity_block()
        )
        user_content = compressed

    # ── Remove ALL existing system messages, insert new one at 0 ──
    # _inject_datetime() inserted a datetime system message at index 0,
    # but some chat templates (Qwen) REQUIRE system messages to be at
    # the very beginning — appending a second system message after user
    # content triggers "System message must be at the beginning."
    # The new system_prompt already includes datetime info, so the old
    # one is redundant.  Clean up ALL previous system messages and
    # insert the new one at position 0 (BEFORE user).
    messages[:] = [m for m in messages if m["role"] != "system"]
    messages.insert(0, {"role": "system", "content": system_prompt})
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
      STEP 1: Intent Router (agent/intent_router.py) — fast-path regex 0 LLM +
              classificazione LLM su intent nativo (22 valori)
      STEP 2: Routing per intent (greeting/general/meta/project/web/...)
      STEP 3: Qwen3.5 Caveman Compression (CPU, comprime RAG+history+query)
              Skip automatico se contesto < 1000 chars (Op1/Op8)
      STEP 4: Qwen3.5-4B (GPU) → risposta

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
        return (messages, None)

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
        compressed = await _compress_concise(clean_msg)
        tracer.add_llm_call(
            compressed._as_llm_record("caveman_compression") if hasattr(compressed, '_as_llm_record') else
            LlmCallRecord(
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
        system_prompt = f"[{_datetime_context()}]\n\n" + CAVEMAN_GEMMA_SYSTEM + "\n" + CAVEMAN_GEMMA_SYSTEM_ADDENDUM + "\n\n" + _hardware_identity_block()
        # Remove previous system messages, insert new one at index 0
        messages[:] = [m for m in messages if m["role"] != "system"]
        messages.insert(0, {"role": "system", "content": system_prompt})
        for m in reversed(messages):
            if m["role"] == "user":
                m["content"] = user_content
                break
        tracer.set_system_prompt(system_prompt)
        tracer.set_user_content(user_content)
        tracer.set_compressed_text(compressed if isinstance(compressed, str) else str(compressed))
        if finalize_trace:
            tracer.finish()
        return (messages, None)
    mem_ctx, rag_ctx, web_ctx = "", "", ""
    latest_msg, user_overrides = _parse_super_tags(latest_msg)
    _user_override_persona = user_overrides.get("persona", "")
    _user_override_focus = user_overrides.get("focus", "")
    _user_override_lang = user_overrides.get("lang", "")
    _user_override_mem_count = user_overrides.get("mem_count", 0)

    # ════════════════════════════════════════════════════
    # STEP 1 + 2: INTENT ROUTING
    # ════════════════════════════════════════════════════
    _active_before = state.get_last_project(current_user_id, conversation_id)
    _all_projects = await _get_cached_rag_projects(user=user)
    _recent_user_msgs = [m["content"] for m in messages if m["role"] == "user"][-3:]

    _gk_context = {
        "active_project": _active_before,
        "projects_available": _all_projects,
        "recent_messages": _recent_user_msgs,
    }
    tracer.start_step("intent_classify")
    gk = await intent_router.classify(latest_msg, _gk_context)
    _bypassed = gk.source in ("regex", "fallback")
    tracer.end_step("intent_classify", details={"intent": gk.intent, "project": gk.project, "confidence": gk.confidence, "source": gk.source, "bypassed": _bypassed})

    tracer.set_gatekeeper(intent=gk.intent, project=gk.project, confidence=gk.confidence, bypassed=_bypassed)
    tracer._gatekeeper_model = "bypass" if _bypassed else "llm"
    _record_intent_stats(gk.intent, gk.confidence, _bypassed, gk.project, gk.source)

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

    elif gk.intent in ("project", "analyze", "plan", "code"):
        # Fase 4.9-4.11: analyze/plan/code seguono il branch project — richiedono
        # contesto codice (RAG + Synaptiq + memoria) per analisi/piani/modifiche.
        # (Matrice §4.3: fallback di analyze/plan/code → project.)
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
            logger.info(f"📁 Progetto attivo (intent={gk.intent}): {active_project}")
            state.set_last_project(current_user_id, conversation_id, active_project)

    clean_msg = latest_msg  # per save_to_memory in _bg_add (Python 3.13 free variable scoping)

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
    # EARLY RETURNS: greeting / general / meta
    # ════════════════════════════════════════════════════
    if intent_router.is_greeting_result(gk):
        logger.info(f"👋 Intento GREETING: saluto puro, skip LLM, messaggio originale preservato")
        tracer.step("context_gathering", status="skipped", details={"reason": "greeting_intent"})
        tracer.step("caveman_compression", status="skipped", details={"reason": "greeting_intent"})
        # FIX 2026-08-02: datetime preservato (coerente con ramo general).
        # Il saluto è short-circuited da main.py (0 token LLM), ma se il modello
        # viene comunque chiamato, GENERAL_CONVERSATION_SYSTEM gli dice di
        # ignorare il datetime quando non serve. Rimuoviamo solo il system
        # datetime duplicato (la fonte è il prefisso user).
        if messages:
            messages[:] = [m for m in messages if not (m.get("role") == "system" and "Current date" in m.get("content", ""))]
            messages.insert(0, {"role": "system", "content": GENERAL_CONVERSATION_SYSTEM + "\n\n" + _hardware_identity_block()})
        if finalize_trace:
            tracer.finish()
        return (messages, gk)

    if gk.intent in ("general", "web"):
        logger.info(f"🗣️ Intento {gk.intent.upper()}: skip caveman compression, messaggio originale preservato")
        tracer.step("context_gathering", status="skipped", details={"reason": f"{gk.intent}_intent"})
        tracer.step("caveman_compression", status="skipped", details={"reason": f"{gk.intent}_intent"})

        # ── Web-aware general: query che richiedono dati live (meteo, news, prezzi) ──
        # Normalmente il branch general risponde immediatamente SENZA contesto, e il
        # modello usa la conoscenza interna (stantia). Se la query richiede dati
        # attuali, esegue una web search e inietta i risultati nel prompt.
        # Fase 3.2: intent nativo "web" → query costruita dagli SLOTS del router
        # ({query} esplicita > {topic}+{city} > {topic} > clean_web_query fallback);
        # intent "general" → legacy is_web_requiring_query + clean_web_query.
        web_ctx_general = ""
        _search_query = ""
        if gk.intent == "web":
            _slot_q = (gk.slots.get("query") or "").strip()
            _topic = (gk.slots.get("topic") or "").strip()
            _city = (gk.slots.get("city") or "").strip()
            if _slot_q:
                _search_query = _slot_q
            elif _topic and _city:
                _search_query = f"{_topic} {_city}"
            elif _topic:
                _search_query = _topic
            else:
                _search_query = clean_web_query(clean_msg)
        elif is_web_requiring_query(clean_msg):
            _search_query = clean_web_query(clean_msg)

        if _search_query:
            tracer.start_step("web_general_search")
            try:
                search_query = _search_query
                web_ctx_general = await search_web_knowledge(search_query)
                if not web_ctx_general:
                    web_ctx_general, _ = await asyncio.wait_for(
                        perform_web_search_and_crawl(search_query, force=True),
                        timeout=45.0,
                    )
                    if web_ctx_general and web_ctx_general != "Nessun risultato online.":
                        sources = [line[5:] for line in web_ctx_general.split("\n") if line.startswith("URL: ")]
                        await save_web_knowledge(search_query, web_ctx_general, sources)
                        tracer._web_search_performed = True
                        async def _bg_save_web_general():
                            summary = f"[Web Knowledge] Query: {search_query[:200]}\nFonti: {', '.join(sources[:3])}\nRisultati: {web_ctx_general[:600]}"
                            await save_to_memory(summary, user_id=current_user_id)
                        task = asyncio.create_task(_bg_save_web_general())
                        state.background_tasks.add(task)
                        task.add_done_callback(state.background_tasks.discard)
                if web_ctx_general:
                    logger.info(f"🌐 Web context per query: {len(web_ctx_general)} chars")
            except Exception as e:
                logger.warning(f"Web search fallita in branch general (non critico): {e}")
            tracer.end_step("web_general_search", details={"web_len": len(web_ctx_general or "")})

        # FIX 2026-08-02: datetime PRESERVATO in tutti i rami. Le query fattuali
        # (data/ora/tempo) passano da questo ramo e il modello DEVE avere il valore
        # corrente per rispondere — senza, un modello coding (Qwen3.5-super-coder)
        # scrive codice Python per calcolarlo. GENERAL_CONVERSATION_SYSTEM istruisce
        # il modello a usare il datetime SOLO quando la richiesta lo richiede.
        # Rimuoviamo solo il system message datetime DUPLICATO (iniettato due volte
        # da _inject_datetime): il prefisso "[Current date/time: ...]" nel user
        # message è la fonte unica — GENERAL_CONVERSATION_SYSTEM vi fa riferimento.
        if messages:
            messages[:] = [m for m in messages if not (m.get("role") == "system" and "Current date" in m.get("content", ""))]
            if web_ctx_general:
                # Web context iniettato: system prompt dedicato + user content con [WEB]
                web_system = GENERAL_CONVERSATION_SYSTEM + (
                    "\n\n[WEB DATA]\nThe user's request requires current/live information. "
                    "Use the web search results below (labeled [WEB]) as your ONLY source "
                    "for factual data. If the results don't answer the question, say so "
                    "honestly instead of guessing or using outdated knowledge.\n"
                ) + "\n\n" + _hardware_identity_block()
                messages.insert(0, {"role": "system", "content": web_system})
                for m in reversed(messages):
                    if m["role"] == "user":
                        m["content"] = f"[WEB]\n{web_ctx_general}\n\nQuery: {m['content']}"
                        break
            else:
                # Inject system prompt to prevent CoT leakage — Qwen3.5 genera
                # ragionamento in inglese come testo piatto se non ha istruzioni
                messages.insert(0, {"role": "system", "content": GENERAL_CONVERSATION_SYSTEM + "\n\n" + _hardware_identity_block()})
        if finalize_trace:
            tracer.finish()
        return (messages, gk)

    if gk.intent == "meta":
        logger.info("🗂️ Intento META: skip caveman compression, risposta conversazionale")
        meta_context = "\n".join(f"- {p}" for p in _all_projects) if _all_projects else "Nessun progetto indicizzato."
        meta_prompt = f"[CURRENT DATETIME — YOU MUST USE THIS: {_dt_now}]\n\nProgetti disponibili:\n{meta_context}\n\nDomanda: {clean_msg}"
        for m in reversed(messages):
            if m["role"] == "user":
                m["content"] = meta_prompt
                break
        tracer.set_user_content(meta_prompt)
        # FIX 2026-08-02: identità hardware iniettata anche nel ramo meta.
        # Senza system prompt il modello vede solo datetime + lista progetti e
        # risponde evasivamente a "che hardware hai?" (trace d2811fb00043).
        if messages:
            messages[:] = [m for m in messages if not (m.get("role") == "system" and "Current date" in m.get("content", ""))]
            _meta_system = GENERAL_CONVERSATION_SYSTEM + "\n\n" + _hardware_identity_block()
            messages.insert(0, {"role": "system", "content": _meta_system})
            tracer.set_system_prompt(_meta_system)
        tracer.step("context_gathering", status="skipped", details={"reason": "meta_intent"})
        tracer.step("caveman_compression", status="skipped", details={"reason": "meta_intent"})
        if finalize_trace:
            tracer.finish()
        return (messages, gk)

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
        if _is_project_query and not _is_meta_query:
            _rag_project = _user_override_focus if _user_override_focus else active_project
            rag_ctx_local = await search_documents(clean_msg, is_project_query=True, project_name=_rag_project, user=user)
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
    try:
        mem_ctx, rag_ctx, cg_ctx = await asyncio.wait_for(
            asyncio.gather(mem_task, rag_task, synaptiq_task),
            timeout=60.0,
        )
    except asyncio.TimeoutError:
        logger.warning("⏱️ Context gather timed out (60s) — using partial results")
        mem_ctx = mem_task.result() if mem_task.done() and not mem_task.cancelled() else ""
        rag_ctx = rag_task.result() if rag_task.done() and not rag_task.cancelled() else ""
        cg_ctx = synaptiq_task.result() if synaptiq_task.done() and not synaptiq_task.cancelled() else ""
        # Cancel still-running tasks
        for t in (mem_task, rag_task, synaptiq_task):
            if not t.done():
                t.cancel()
    logger.info(f"📊 Context gathered: mem={len(mem_ctx or '')} rag={len(rag_ctx or '')} synaptiq={len(cg_ctx or '')} chars")

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
    logger.info(f"🗜️ Starting caveman compression: {raw_size} chars raw → budget={MAX_BUDGET}")
    logger.info(f"   raw_size={raw_size} rag_alloc={len(rag_final or '')} web_alloc={len(web_final or '')} mem_alloc={len(mem_final or '')} synaptiq_alloc={len(cg_ctx or '')}")

    compressed, _compression_is_raw = await _compress_context(
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
    logger.info(f"✅ Caveman compression {'raw-fallback' if _compression_is_raw else 'ok'}: {raw_size} → {len(compressed)} chars")

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
    logger.info(f"🧠 Final prompt built: system={len(CAVEMAN_GEMMA_SYSTEM) if not _compression_is_raw else 500} user={len(compressed)}")

    if finalize_trace:
        tracer.finish()
        logger.info(f"🏁 build_omniscient_prompt complete — returning to main.py")
    return (messages, gk)
