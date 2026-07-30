"""
MCP Server (v2) — Implementazione conforme allo standard MCP Streamable HTTP.

Usa il SDK ufficiale `mcp` (v1.28.1+) per type definitions.
Implementa il protocollo MCP direttamente su route FastAPI (nessuna sub-app),
evitando i problemi di lifespan delle sub-app Starlette montate con Granian.

Compatibile con:
  - OpenCode  (type: "remote" in opencode.jsonc)
  - Claude Code / Cursor (via stdio: .mcp.json originale)
  - Qualsiasi cliente MCP Streamable HTTP

Utilizzo in main.py:
    from api.mcp.server_v2 import handle_mcp_post
    app.post("/api/mcp/v2")(handle_mcp_post)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# FastMCP Server Instance (usato per registrazione decoratori)
# ──────────────────────────────────────────────

mcp = FastMCP("jarvis-telemetry")

# ──────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────


def _import_state():
    import core.state as _s
    return _s


def _import_telemetry():
    from core.telemetry import PipelineTracer as _PT
    from core.telemetry import get_recent_traces as _grt
    from core.telemetry import get_trace_by_id as _gtbi
    return _PT, _grt, _gtbi


def _get_status_dict() -> dict:
    from core.telemetry_api import get_status_dict as _gsd
    return _gsd()


def _get_model_info_dict() -> dict:
    from core.telemetry_api import get_model_info_dict as _gmid
    return _gmid()


def _get_pending_ops_dict() -> dict:
    from core.telemetry_api import get_pending_ops_dict as _gpod
    return _gpod()


def _json_text(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


# ──────────────────────────────────────────────
# Tools (registrati su FastMCP per reuse)
# ──────────────────────────────────────────────


@mcp.tool(name="get_recent_traces", description="Ultimi N pipeline trace completati, con step, LLM calls e metriche.")
def get_recent_traces(limit: int = 10) -> str:
    limit = min(max(1, limit), 100)
    _, grt, _ = _import_telemetry()
    return _json_text({"traces": grt(limit=limit), "count": limit})


@mcp.tool(name="get_active_traces", description="Trace correntemente in esecuzione (richieste non ancora completate).")
def get_active_traces() -> str:
    PT, _, _ = _import_telemetry()
    return _json_text({"active_traces": PT.get_all_active(), "count": len(PT.get_all_active())})


@mcp.tool(name="get_trace_by_id", description="Cerca un pipeline trace completato per request_id.")
def get_trace_by_id(request_id: str) -> str:
    _, _, gtbi = _import_telemetry()
    result = gtbi(request_id)
    if result is None:
        return _json_text({"error": f"Trace '{request_id}' not found"})
    return _json_text(result)


@mcp.tool(name="get_gatekeeper_stats", description="Statistiche cumulative del Gatekeeper (bypass rate, confidence media).")
def get_gatekeeper_stats() -> str:
    s = _import_state()
    return _json_text({"stats": s.gatekeeper_stats.to_dict() if s.gatekeeper_stats else None})


@mcp.tool(name="get_errors", description="Contatori di errore per diagnostica.")
def get_errors() -> str:
    s = _import_state()
    return _json_text({"errors": dict(s.error_counters)})


@mcp.tool(name="get_status", description="Stato del sistema: uptime, richieste totali, token, trace attivi.")
def get_status() -> str:
    return _json_text(_get_status_dict())


@mcp.tool(name="get_model_info", description="Informazioni sul modello LLM caricato (family, GPU layers, flash attention).")
def get_model_info() -> str:
    return _json_text(_get_model_info_dict())


@mcp.tool(name="get_pending_ops", description="Operazioni pendenti: background tasks, coda watchdog.")
def get_pending_ops() -> str:
    return _json_text(_get_pending_ops_dict())


@mcp.tool(name="get_trace_full", description="Trace completo con tutti i testi dei prompt intermedi (system, RAG, compressione, risposta LLM).")
def get_trace_full(request_id: str) -> str:
    """Restituisce il trace completo inclusi i campi prompt testuali."""
    _, _, gtbi = _import_telemetry()
    result = gtbi(request_id)
    if result is None:
        return _json_text({"error": f"Trace '{request_id}' not found"})
    return _json_text(result)


@mcp.tool(name="chat_send", description="Invia un messaggio alla pipeline chat di Jarvis. Restituisce la risposta e un trace_id per il debug.")
async def chat_send(message: str, user_id: str = "mcp_user") -> str:
    """
    Invia un messaggio alla pipeline chat di Jarvis.
    
    Il messaggio attraversa l'intera pipeline: gatekeeper, RAG, compressione,
    generazione LLM. Il trace_id può essere usato con get_trace_full per
    ispezionare tutti i prompt intermedi e i tempi di elaborazione.
    """
    try:
        from core.telemetry import PipelineTracer
        from agent.prompt import build_omniscient_prompt
        from core.llm_engine import engine
        from datetime import datetime, UTC

        # ── Crea tracer ──
        tracer = PipelineTracer.begin(user_message=message[:200], user_id=user_id)

        # ── Build enriched messages ──
        raw_messages = [{"role": "user", "content": message}]
        enriched, gk_result = await build_omniscient_prompt(
            raw_messages,
            user_id=user_id,
            conversation_id="mcp",
            request_id=tracer.request_id,
            finalize_trace=False,
        )

        # ── Greeting short-circuit: pure greetings skip LLM ──
        if gk_result and gk_result.intent == "greeting":
            greeting_text = "Ciao! 👋 Come posso aiutarti?"
            tracer.finish()
            return _json_text({"response": greeting_text, "trace_id": tracer.request_id, "model": "greeting"})

        # ── Generazione LLM ──
        tracer.start_step("gemma_generation")
        response = await engine.generate_chat_with_router(
            enriched, tools=None, options={"temperature": 0.7}, stream=False
        )
        if "error" in response:
            tracer.set_error(response["error"])
            tracer.finish()
            return _json_text({"error": response["error"], "trace_id": tracer.request_id})

        usage = response.get("usage", {})
        from core.telemetry import LlmCallRecord
        tracer.add_llm_call(LlmCallRecord(
            model="chat",
            step="gemma_generation",
            duration_ms=0,
            tokens_prompt=usage.get("prompt_tokens", 0),
            tokens_completion=usage.get("completion_tokens", 0),
            temperature=0.7,
        ))

        choice = response["choices"][0]["message"]
        content = choice.get("content", "")
        tracer.set_llm_response(content)
        tracer.end_step("gemma_generation", details={
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "char_count": len(content),
        })

        # ── Finalizza trace ──
        tracer.finish()

        return _json_text({
            "response": content,
            "trace_id": tracer.request_id,
            "model": response.get("model", "unknown"),
        })

    except Exception as e:
        logger.exception(f"chat_send error")
        return _json_text({"error": str(e)})


@mcp.tool(name="code_intelligence", description="Ricerca ibrida RAG + Synaptiq: contesto semantico da Qdrant + analisi strutturale (simboli, callers, blast radius). Usa questo tool quando l'agente AI ha bisogno di capire come funziona un componente, trovare dipendenze, o esplorare il codice.")
async def code_intelligence(query: str, project: str = "") -> str:
    """
    Esegue una ricerca ibrida sul codice: RAG vettoriale (Qdrant) + Synaptiq
    (grafo strutturale). Restituisce contesto unificato Markdown.

    Args:
        query:   Descrizione in linguaggio naturale del componente da analizzare.
        project: Nome del progetto (opzionale). Se vuoto, cerca in tutti i progetti.
    """
    try:
        from graph.synaptiq_bridge import hybrid_code_search
        ctx = await hybrid_code_search(
            query,
            is_project_query=bool(project),
            project_name=project if project else None,
            user_message=query,
        )
        if ctx and ctx.strip():
            return ctx
        return "Nessun contesto trovato per la query specificata."
    except Exception as e:
        logger.exception(f"code_intelligence error")
        return _json_text({"error": str(e)})


# ──────────────────────────────────────────────
# Jarvis Core Tools
# ──────────────────────────────────────────────


@mcp.tool(name="jarvis_chat", description="Invia un messaggio alla pipeline chat completa di Jarvis (RAG + memoria + Synaptiq + web search). Restituisce risposta testuale e trace_id per debug.")
async def jarvis_chat(message: str, user_id: str = "mcp_user") -> str:
    """Wraps chat_send with the full Jarvis pipeline."""
    try:
        from core.telemetry import PipelineTracer
        from agent.prompt import build_omniscient_prompt
        from core.llm_engine import engine

        tracer = PipelineTracer.begin(user_message=message[:200], user_id=user_id)
        raw_messages = [{"role": "user", "content": message}]
        enriched, gk_result = await build_omniscient_prompt(
            raw_messages, user_id=user_id, conversation_id="mcp",
            request_id=tracer.request_id, finalize_trace=False,
        )

        # ── Greeting short-circuit: pure greetings skip LLM ──
        if gk_result and gk_result.intent == "greeting":
            greeting_text = "Ciao! 👋 Come posso aiutarti?"
            tracer.finish()
            return _json_text({"response": greeting_text, "trace_id": tracer.request_id, "model": "greeting"})

        tracer.start_step("jarvis_chat")
        response = await engine.generate_chat_with_router(
            enriched, tools=None, options={"temperature": 0.7}, stream=False
        )
        if "error" in response:
            tracer.set_error(response["error"])
            tracer.finish()
            return _json_text({"error": response["error"], "trace_id": tracer.request_id})

        usage = response.get("usage", {})
        from core.telemetry import LlmCallRecord
        tracer.add_llm_call(LlmCallRecord(
            model="chat", step="jarvis_chat", duration_ms=0,
            tokens_prompt=usage.get("prompt_tokens", 0),
            tokens_completion=usage.get("completion_tokens", 0),
            temperature=0.7,
        ))

        choice = response["choices"][0]["message"]
        content = choice.get("content", "")
        tracer.set_llm_response(content)
        tracer.end_step("jarvis_chat", details={
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "char_count": len(content),
        })
        tracer.finish()

        return _json_text({
            "response": content,
            "trace_id": tracer.request_id,
            "model": response.get("model", "unknown"),
        })
    except Exception as e:
        logger.exception("jarvis_chat error")
        return _json_text({"error": str(e)})


@mcp.tool(name="jarvis_exec", description="Esegue un comando shell whitelisted su Jarvis. Solo comandi in EXEC_ALLOWED_COMMANDS. I comandi readonly (EXEC_READONLY_COMMANDS) non richiedono conferma.")
async def jarvis_exec(command: str, args: str = "") -> str:
    """Execute a whitelisted shell command via Jarvis."""
    import asyncio as _asyncio

    try:
        from core.config import EXEC_READONLY_COMMANDS, EXEC_ALLOWED_COMMANDS
        full_cmd = f"{command} {args}".strip() if args else command

        # Check whitelist
        cmd_first = command.split()[0] if command else ""
        is_allowed = any(full_cmd.startswith(c) or cmd_first == c for c in EXEC_ALLOWED_COMMANDS)
        if not is_allowed:
            return _json_text({"error": f"Comando non autorizzato: {command}. Comandi permessi: {', '.join(EXEC_ALLOWED_COMMANDS[:10])}..."})

        is_readonly = any(full_cmd.startswith(c) or cmd_first == c for c in EXEC_READONLY_COMMANDS)

        proc = await _asyncio.create_subprocess_shell(
            full_cmd,
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=30)
        except _asyncio.TimeoutError:
            proc.kill()
            return _json_text({"error": "Timeout 30s", "command": full_cmd})

        return _json_text({
            "command": full_cmd,
            "stdout": stdout.decode("utf-8", errors="replace")[-5000:],
            "stderr": stderr.decode("utf-8", errors="replace")[-1000:],
            "returncode": proc.returncode,
            "readonly": is_readonly,
        })
    except Exception as e:
        logger.exception("jarvis_exec error")
        return _json_text({"error": str(e)})


@mcp.tool(name="jarvis_rag_search", description="Cerca nel RAG (Qdrant) documenti e codice semanticamente simili alla query. Opzionale: filtra per progetto. Restituisce chunk di codice con punteggi di similarità.")
async def jarvis_rag_search(query: str, project: str = "", top_k: int = 5) -> str:
    """Search RAG (Qdrant) for semantically similar documents and code."""
    try:
        from rag.engine import hybrid_search
        from core.config import RAG_CONFIG

        k = min(max(1, top_k), 20)
        results = await hybrid_search(
            query=query,
            project_name=project if project else None,
            top_k=k,
        )
        if not results:
            return _json_text({"query": query, "results": [], "count": 0})

        formatted = []
        for r in results[:k]:
            formatted.append({
                "project": r.get("project", ""),
                "file_path": r.get("file_path", r.get("path", "")),
                "score": round(r.get("score", 0), 4),
                "snippet": r.get("content", r.get("text", ""))[:300],
            })

        return _json_text({"query": query, "results": formatted, "count": len(formatted)})
    except Exception as e:
        logger.exception("jarvis_rag_search error")
        return _json_text({"error": str(e)})


@mcp.tool(name="jarvis_memory_search", description="Cerca nella memoria episodica (Mem0) per user_id. Restituisce ricordi recenti e ricorrenti ordinati per rilevanza.")
async def jarvis_memory_search(query: str, user_id: str = "mcp_user") -> str:
    """Search episodic memory (Mem0) for relevant memories."""
    try:
        from memory.engine import search_memories

        results = await search_memories(
            query=query,
            user_id=user_id,
            limit=10,
        )
        if not results:
            return _json_text({"query": query, "memories": [], "count": 0})

        formatted = []
        for mem in results:
            formatted.append({
                "id": mem.get("id", ""),
                "content": mem.get("memory", mem.get("content", ""))[:300],
                "score": round(mem.get("score", 0), 4),
                "created_at": str(mem.get("created_at", "")),
            })

        return _json_text({"query": query, "memories": formatted, "count": len(formatted)})
    except Exception as e:
        logger.exception("jarvis_memory_search error")
        return _json_text({"error": str(e)})


@mcp.tool(name="jarvis_synaptiq_query", description="Analisi strutturale del codice via Synaptiq: simboli, callers, blast radius. Restituisce analisi del grafo delle dipendenze del codice.")
async def jarvis_synaptiq_query(query: str, project: str = "") -> str:
    """Structural code analysis via Synaptiq (symbols, callers, blast radius)."""
    try:
        from graph.synaptiq_bridge import hybrid_code_search

        ctx = await hybrid_code_search(
            query,
            is_project_query=bool(project),
            project_name=project if project else None,
            user_message=query,
        )
        if ctx and ctx.strip():
            return ctx
        return _json_text({"query": query, "result": "Nessun contesto trovato.", "count": 0})
    except Exception as e:
        logger.exception("jarvis_synaptiq_query error")
        return _json_text({"error": str(e)})


@mcp.tool(name="jarvis_web_search", description="Ricerca web via SearXNG. Restituisce snippet e URL dei risultati. Opzionale: specifica numero risultati (max 20).")
async def jarvis_web_search(query: str, num_results: int = 5) -> str:
    """Web search via SearXNG metasearch engine."""
    try:
        from rag.web_search import web_search

        n = min(max(1, num_results), 20)
        results = await web_search(query, num_results=n)

        if not results:
            return _json_text({"query": query, "results": [], "count": 0})

        formatted = []
        for r in results[:n]:
            formatted.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", r.get("snippet", ""))[:300],
            })

        return _json_text({"query": query, "results": formatted, "count": len(formatted)})
    except Exception as e:
        logger.exception("jarvis_web_search error")
        return _json_text({"error": str(e)})


# ════════════════════════════════════════════════════════════════
# BENCHMARK TOOLS — Raw LLM vs Full Pipeline
# ════════════════════════════════════════════════════════════════


@mcp.tool(name="benchmark_raw", description="Test raw LLM speed: prompt diretto SENZA pipeline (no RAG, no thinking). Misura TTFT, tok/s.")
async def benchmark_raw(prompt: str = "Dammi data e ora attuale", max_tokens: int = 100) -> str:
    """
    Invia un prompt GREZZO direttamente al LLM, bypassando TUTTA la pipeline Jarvis
    (no gatekeeper, no RAG, no compressione, no thinking mode injection).
    
    Misurazioni:
    - ttft_ms:       tempo fino al primo token
    - total_ms:      tempo totale
    - tok_s:         token al secondo (da conteggio chunk streaming)
    - prompt_tok:    token di input (da usage)
    - completion_tok: token di output (da usage)
    
    Usalo per confrontare con benchmark_pipeline() e isolare il costo della pipeline.
    """
    try:
        from core.llm_engine import engine as _eng

        messages = [{"role": "user", "content": prompt}]
        total_start = time.monotonic()

        generator = await _eng.generate_chat(
            messages, stream=True,
            options={"temperature": 0.0, "num_predict": max_tokens},
            model="chat",
        )

        ttft = None
        chunk_count = 0
        full_text = ""
        async for chunk in generator:
            if ttft is None:
                ttft = time.monotonic() - total_start
            delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
            if delta:
                chunk_count += 1
                full_text += delta

        total = time.monotonic() - total_start
        tok_s = chunk_count / total if total > 0 else 0

        return _json_text({
            "mode": "RAW — no pipeline, no thinking",
            "prompt": prompt[:100],
            "ttft_ms": round(ttft * 1000) if ttft else None,
            "total_duration_ms": round(total * 1000),
            "chunks_received": chunk_count,
            "tokens_per_second": round(tok_s, 2),
            "response_chars": len(full_text),
            "response_preview": full_text[:300],
            "note": "tok_s basato su chunk streaming, non token reali. completion_tok reali da usage non disponibili in streaming.",
        })
    except Exception as e:
        logger.exception("benchmark_raw error")
        return _json_text({"error": str(e)})


@mcp.tool(name="benchmark_pipeline", description="Test LLM via pipeline completa: gatekeeper + RAG + compressione + thinking. Misura overhead pipeline.")
async def benchmark_pipeline(prompt: str = "Dammi data e ora attuale", max_tokens: int = 100) -> str:
    """
    Invia un prompt ATTRAVERSO l'INTERA pipeline Jarvis:
    gatekeeper (keyword_bypass + Gemma 4), RAG, compressione, thinking mode.
    
    Misurazioni identiche a benchmark_raw() per confronto diretto.
    La differenza tra i due test rivela l'overhead della pipeline.
    
    Per vedere i dettagli intermedi, usa il trace_id restituito con get_trace_full().
    """
    try:
        from core.telemetry import PipelineTracer as _PT
        from agent.prompt import build_omniscient_prompt as _bop
        from core.llm_engine import engine as _eng

        tracer = _PT.begin(user_message=prompt[:200], user_id="benchmark_mcp")
        raw_messages = [{"role": "user", "content": prompt}]

        total_start = time.monotonic()

        enriched, _ = await _bop(
            raw_messages, user_id="benchmark_mcp",
            conversation_id="benchmark", concise=False,
            request_id=tracer.request_id, finalize_trace=False,
        )

        tracer.start_step("gemma_generation")
        response = await _eng.generate_chat_with_router(
            enriched, tools=None,
            options={"temperature": 0.0, "num_predict": max_tokens},
            stream=False,
        )

        if "error" in response:
            total = time.monotonic() - total_start
            return _json_text({"error": response["error"], "total_duration_ms": round(total * 1000)})

        usage = response.get("usage", {})
        from core.telemetry import LlmCallRecord as _LCR
        tracer.add_llm_call(_LCR(
            model="chat", step="gemma_generation", duration_ms=0,
            tokens_prompt=usage.get("prompt_tokens", 0),
            tokens_completion=usage.get("completion_tokens", 0),
            temperature=0.0,
        ))

        choice = response["choices"][0]["message"]
        content = choice.get("content", "")
        tracer.set_llm_response(content)
        tracer.end_step("gemma_generation", details={
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "char_count": len(content),
        })
        tracer.finish()

        total = time.monotonic() - total_start
        p_tok = usage.get("prompt_tokens", 0)
        c_tok = usage.get("completion_tokens", 0)
        tok_s = c_tok / total if total > 0 else 0

        return _json_text({
            "mode": "FULL PIPELINE — gatekeeper + RAG + thinking",
            "prompt": prompt[:100],
            "total_duration_ms": round(total * 1000),
            "prompt_tokens": p_tok,
            "completion_tokens": c_tok,
            "tokens_per_second": round(tok_s, 2),
            "pipeline_overhead_pct": None,  # Calcolato dal confronto con benchmark_raw
            "trace_id": tracer.request_id,
            "response_preview": content[:300],
            "gatekeeper": {
                "intent": tracer.gatekeeper_intent,
                "bypassed": tracer.gatekeeper_bypassed,
                "model": tracer.gatekeeper_model,
            } if hasattr(tracer, 'gatekeeper_intent') else None,
        })
    except Exception as e:
        logger.exception("benchmark_pipeline error")
        return _json_text({"error": str(e)})


# ──────────────────────────────────────────────
# Resources (registrati su FastMCP)
# ──────────────────────────────────────────────


@mcp.resource(uri="jarvis://traces/recent", name="Recent Traces", description="Ultimi 10 pipeline trace completati.", mime_type="application/json")
async def recent_traces() -> str:
    _, grt, _ = _import_telemetry()
    return _json_text({"traces": grt(limit=10)})


@mcp.resource(uri="jarvis://traces/active", name="Active Traces", description="Trace correntemente in esecuzione.", mime_type="application/json")
async def active_traces() -> str:
    PT, _, _ = _import_telemetry()
    return _json_text({"active_traces": PT.get_all_active()})


@mcp.resource(uri="jarvis://gatekeeper/stats", name="Gatekeeper Stats", description="Statistiche cumulative Gatekeeper.", mime_type="application/json")
async def gatekeeper_stats() -> str:
    s = _import_state()
    return _json_text({"stats": s.gatekeeper_stats.to_dict() if s.gatekeeper_stats else None})


@mcp.resource(uri="jarvis://errors/counters", name="Error Counters", description="Contatori di errore.", mime_type="application/json")
async def error_counters() -> str:
    s = _import_state()
    return _json_text({"errors": dict(s.error_counters)})


@mcp.resource(uri="jarvis://system/status", name="System Status", description="Stato generale del sistema.", mime_type="application/json")
async def system_status() -> str:
    return _json_text(_get_status_dict())


@mcp.resource(uri="jarvis://model/info", name="Model Info", description="Informazioni sul modello LLM.", mime_type="application/json")
async def model_info() -> str:
    return _json_text(_get_model_info_dict())


@mcp.resource(uri="jarvis://system/pending_ops", name="Pending Ops", description="Operazioni pendenti.", mime_type="application/json")
async def pending_ops() -> str:
    return _json_text(_get_pending_ops_dict())


# ──────────────────────────────────────────────
# Session Resources
# ──────────────────────────────────────────────


def _get_store():
    s = _import_state()
    return getattr(s, 'chat_session_store', None)


@mcp.resource(uri="jarvis://sessions/list", name="Sessions List", description="Lista delle sessioni chat disponibili con metadati.", mime_type="application/json")
async def sessions_list() -> str:
    store = _get_store()
    if not store:
        return _json_text({"sessions": [], "error": "Session store not initialized"})
    return _json_text({"sessions": store.list_sessions(limit=50)})


# ──────────────────────────────────────────────
# Session Tools
# ──────────────────────────────────────────────


@mcp.tool(name="list_sessions", description="Lista sessioni chat con metadati (turn count, progetto, ultima attività).")
def list_sessions(limit: int = 20, sort_by: str = "last_activity", user_id: str = "") -> str:
    store = _get_store()
    if not store:
        return _json_text({"error": "Session store not initialized"})
    uid = user_id if user_id else None
    return _json_text({
        "sessions": store.list_sessions(limit=min(limit, 200), sort_by=sort_by, user_id=uid),
    })


@mcp.tool(name="get_session", description="Recupera una sessione chat completa per conversation_id con tutti i turni.")
def get_session(conversation_id: str) -> str:
    store = _get_store()
    if not store:
        return _json_text({"error": "Session store not initialized"})
    turns = store.get_session(conversation_id)
    if not turns:
        return _json_text({"error": f"Session '{conversation_id}' not found"})
    return _json_text({"conversation_id": conversation_id, "turns": turns, "turn_count": len(turns)})


@mcp.tool(name="search_sessions", description="Cerca testo in tutte le sessioni chat. Restituisce snippet del primo match per sessione.")
def search_sessions(query: str, user_id: str = "", limit: int = 20) -> str:
    store = _get_store()
    if not store:
        return _json_text({"error": "Session store not initialized"})
    uid = user_id if user_id else None
    return _json_text({"results": store.search_sessions(query, user_id=uid, limit=limit)})


@mcp.tool(name="get_session_stats", description="Statistiche aggregate su tutte le sessioni chat (tokens, turni, durata).")
def get_session_stats() -> str:
    store = _get_store()
    if not store:
        return _json_text({"error": "Session store not initialized"})
    return _json_text({"stats": store.get_stats()})


@mcp.tool(name="export_session", description="Esporta una sessione chat in formato JSON o Markdown per analisi esterna.")
def export_session(conversation_id: str, format: str = "json") -> str:
    store = _get_store()
    if not store:
        return _json_text({"error": "Session store not initialized"})
    if format not in ("json", "markdown"):
        return _json_text({"error": "Formato non supportato. Usa 'json' o 'markdown'."})
    return store.export_session(conversation_id, format=format)


# ──────────────────────────────────────────────
# Active SSE sessions
# ──────────────────────────────────────────────

_sse_sessions: dict[str, asyncio.Queue] = {}


async def handle_mcp_sse(request):
    """SSE transport for MCP Streamable HTTP.

    Client connects to GET /api/mcp/v2/sse, receives session_id,
    then uses POST /api/mcp/v2 with session_id for JSON-RPC.
    """
    session_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _sse_sessions[session_id] = queue

    endpoint_url = str(request.url).replace("/sse", "")

    async def event_generator():
        try:
            yield f"event: endpoint\ndata: {json.dumps({'url': endpoint_url, 'session_id': session_id})}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30)
                    if msg is None:
                        break
                    event = msg.get("event", "message")
                    data = msg.get("data", "")
                    yield f"event: {event}\ndata: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _sse_sessions.pop(session_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


def _send_sse_event(session_id: str, event: str, data: str):
    """Send event to an active SSE session."""
    queue = _sse_sessions.get(session_id)
    if queue:
        try:
            queue.put_nowait({"event": event, "data": data})
        except asyncio.QueueFull:
            logger.warning(f"SSE session {session_id}: queue full, dropping event")


# ──────────────────────────────────────────────
# FastAPI route handler — MCP Streamable HTTP
# ──────────────────────────────────────────────
# Implementazione diretta su route FastAPI per evitare
# problemi di lifespan delle sub-app Starlette montate con Granian.
# ──────────────────────────────────────────────


async def _get_tools_list() -> list[dict]:
    """Recupera la lista tool dalla registry FastMCP."""
    tools = await mcp.list_tools()
    return [
        {
            "name": t.name,
            "description": t.description or "",
            "inputSchema": getattr(t, "inputSchema", getattr(t, "parameters", {"type": "object", "properties": {}})),
        }
        for t in tools
    ]


async def _get_resources_list() -> list[dict]:
    """Recupera la lista risorse dalla registry FastMCP."""
    resources = await mcp.list_resources()
    result = [
        {
            "uri": str(r.uri),
            "name": r.name or "",
            "description": r.description or "",
            "mimeType": getattr(r, "mimeType", "application/json"),
        }
        for r in resources
    ]
    # Aggiungi resource template per sessioni dinamiche
    result.append({
        "uri": "jarvis://sessions/{conversation_id}",
        "name": "Chat Session",
        "description": "Sessione chat completa per conversation_id. Sostituisci {conversation_id} con l'ID della sessione.",
        "mimeType": "application/json",
    })
    return result


# Tool handler map
# I tool asincroni (chat_send) non possono essere lambdas — vengono gestiti
# separatamente in handle_mcp_post tramite la registry FastMCP.
_TOOL_HANDLERS: dict[str, callable] = {
    "get_recent_traces": lambda args: {"traces": _import_telemetry()[1](limit=min(max(1, (args or {}).get("limit", 10)), 100)), "count": (args or {}).get("limit", 10)},
    "get_active_traces": lambda args: {"active_traces": _import_telemetry()[0].get_all_active(), "count": len(_import_telemetry()[0].get_all_active())},
    "get_trace_by_id": lambda args: _import_telemetry()[2]((args or {}).get("request_id", "")) or {"error": "not found"},
    "get_trace_full": lambda args: _import_telemetry()[2]((args or {}).get("request_id", "")) or {"error": "not found"},
    "get_gatekeeper_stats": lambda args: {"stats": _import_state().gatekeeper_stats.to_dict() if _import_state().gatekeeper_stats else None},
    "get_errors": lambda args: {"errors": dict(_import_state().error_counters)},
    "get_status": lambda args: _get_status_dict(),
    "get_model_info": lambda args: _get_model_info_dict(),
    "get_pending_ops": lambda args: _get_pending_ops_dict(),
}

# Resource handler map
_RESOURCE_HANDLERS: dict[str, callable] = {
    "jarvis://traces/recent": lambda: {"traces": _import_telemetry()[1](limit=10)},
    "jarvis://traces/active": lambda: {"active_traces": _import_telemetry()[0].get_all_active()},
    "jarvis://gatekeeper/stats": lambda: {"stats": _import_state().gatekeeper_stats.to_dict() if _import_state().gatekeeper_stats else None},
    "jarvis://errors/counters": lambda: {"errors": dict(_import_state().error_counters)},
    "jarvis://system/status": _get_status_dict,
    "jarvis://model/info": _get_model_info_dict,
    "jarvis://system/pending_ops": _get_pending_ops_dict,
    "jarvis://sessions/list": lambda: {"sessions": _get_store().list_sessions(limit=50) if _get_store() else []},
}


async def handle_mcp_post(body: dict, session_id: str = None) -> dict:
    """Processa una richiesta JSON-RPC MCP e restituisce la risposta.

    Se session_id è fornito e corrisponde a una sessione SSE attiva,
    la risposta viene inviata via SSE invece di essere restituita direttamente.

    Usata dalla route FastAPI POST /api/mcp/v2 in main.py.
    """
    if isinstance(body, list):
        body = body[0] if body else {}

    req_id = body.get("id", 0)
    method = body.get("method", "")
    params = body.get("params")

    # Se session_id fornito, estrai dal body se non passato come arg
    if not session_id and params and isinstance(params, dict):
        session_id = params.get("session_id") or body.get("session_id")

    if method == "initialize":
        proto = (params or {}).get("protocolVersion", "2025-11-05")
        result = {
            "protocolVersion": proto,
            "serverInfo": {"name": "jarvis", "version": "2.0.0"},
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
                "streaming": {"transport": "sse"},
            },
        }
        resp = {"jsonrpc": "2.0", "id": req_id, "result": result}
        if session_id and session_id in _sse_sessions:
            _send_sse_event(session_id, "message", json.dumps(resp))
            return {}
        return resp

    if method == "notifications/initialized":
        return {}

    if method == "ping":
        resp = {"jsonrpc": "2.0", "id": req_id, "result": {"status": "ok"}}
        return _sse_or_direct(resp, session_id)

    if method == "tools/list":
        resp = {"jsonrpc": "2.0", "id": req_id, "result": {"tools": await _get_tools_list()}}
        return _sse_or_direct(resp, session_id)

    if method == "tools/call":
        tool_name = (params or {}).get("name", "")
        args = (params or {}).get("arguments", {})
        handler = _TOOL_HANDLERS.get(tool_name)
        # Per tool non in _TOOL_HANDLERS (es. async chat_send), cerca nel modulo
        if handler is None:
            try:
                import sys
                mod = sys.modules.get(__name__)
                if mod:
                    fn = getattr(mod, tool_name, None)
                    if fn and callable(fn):
                        handler = fn
            except Exception:
                pass
        if not handler:
            resp = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}}
            return _sse_or_direct(resp, session_id)
        try:
            # I tool registrati su FastMCP (es. chat_send) hanno firme con keyword args.
            # I tool in _TOOL_HANDLERS accettano un singolo dict `args`.
            # Distinguiamo: se il tool è nella registry FastMCP, usa **args.
            is_fastmcp_tool = handler is not _TOOL_HANDLERS.get(tool_name)
            if is_fastmcp_tool:
                result = handler(**args) if args else handler()
            else:
                result = handler(args)
            if asyncio.iscoroutine(result):
                result = await result
            data = result
            if isinstance(data, str):
                # Il tool ha già fatto _json_text (es. chat_send, get_trace_full)
                text = data
            else:
                text = _json_text(data)
            is_err = isinstance(data, dict) and (data.get("error") == "not found" or "error" in data)
            resp = {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}], "isError": is_err}}
            return _sse_or_direct(resp, session_id)
        except Exception as e:
            logger.exception(f"Tool '{tool_name}' error")
            resp = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(e)}}
            return _sse_or_direct(resp, session_id)

    if method == "resources/list":
        resp = {"jsonrpc": "2.0", "id": req_id, "result": {"resources": await _get_resources_list()}}
        return _sse_or_direct(resp, session_id)

    if method == "resources/read":
        uri = (params or {}).get("uri", "")
        handler = _RESOURCE_HANDLERS.get(uri)
        # Fallback per resource dinamiche (sessioni, template)
        if handler is None and uri.startswith("jarvis://sessions/"):
            _conv_id = uri[len("jarvis://sessions/"):]
            store = _get_store()
            if store:
                turns = store.get_session(_conv_id)
                if turns:
                    handler = lambda: {"conversation_id": _conv_id, "turns": turns, "turn_count": len(turns)}
        if not handler:
            resp = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": f"Unknown resource: {uri}"}}
            return _sse_or_direct(resp, session_id)
        try:
            data = handler()
            resp = {"jsonrpc": "2.0", "id": req_id, "result": {"contents": [{"uri": uri, "mimeType": "application/json", "text": _json_text(data)}]}}
            return _sse_or_direct(resp, session_id)
        except Exception as e:
            logger.exception(f"Resource '{uri}' error")
            resp = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(e)}}
            return _sse_or_direct(resp, session_id)

    resp = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return _sse_or_direct(resp, session_id)


def _sse_or_direct(resp: dict, session_id: str = None) -> dict:
    """If an SSE session is active, send response via SSE and return empty.
    Otherwise return the response dict directly."""
    if session_id and session_id in _sse_sessions:
        _send_sse_event(session_id, "message", json.dumps(resp))
        return {}
    return resp
