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
        enriched, _ = await build_omniscient_prompt(
            raw_messages,
            user_id=user_id,
            conversation_id="mcp",
            request_id=tracer.request_id,
            finalize_trace=False,
        )

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


async def handle_mcp_post(body: dict) -> dict:
    """Processa una richiesta JSON-RPC MCP e restituisce la risposta.

    Usata dalla route FastAPI POST /api/mcp/v2 in main.py.
    """
    if isinstance(body, list):
        body = body[0] if body else {}

    req_id = body.get("id", 0)
    method = body.get("method", "")
    params = body.get("params")

    if method == "initialize":
        proto = (params or {}).get("protocolVersion", "2025-11-05")
        result = {
            "protocolVersion": proto,
            "serverInfo": {"name": "jarvis-telemetry", "version": "1.0.0"},
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
            },
        }
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    if method == "notifications/initialized":
        return {}

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"status": "ok"}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": await _get_tools_list()}}

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
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}}
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
            is_err = isinstance(data, dict) and data.get("error") == "not found"
            return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}], "isError": is_err}}
        except Exception as e:
            logger.exception(f"Tool '{tool_name}' error")
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(e)}}

    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"resources": await _get_resources_list()}}

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
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": f"Unknown resource: {uri}"}}
        try:
            data = handler()
            return {"jsonrpc": "2.0", "id": req_id, "result": {"contents": [{"uri": uri, "mimeType": "application/json", "text": _json_text(data)}]}}
        except Exception as e:
            logger.exception(f"Resource '{uri}' error")
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(e)}}

    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}
