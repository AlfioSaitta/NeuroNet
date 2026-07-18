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
    from mcp_server_v2 import handle_mcp_post
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
    import state as _s
    return _s


def _import_telemetry():
    from telemetry import PipelineTracer as _PT
    from telemetry import get_recent_traces as _grt
    from telemetry import get_trace_by_id as _gtbi
    return _PT, _grt, _gtbi


def _get_status_dict() -> dict:
    s = _import_state()
    total_s = int(time.time() - s._start_time) if hasattr(s, '_start_time') else 0
    PT, _, _ = _import_telemetry()
    return {
        "uptime_seconds": total_s,
        "uptime_hours": round(total_s / 3600, 1) if total_s else 0,
        "total_requests": s.total_requests,
        "total_prompt_tokens": s.total_prompt_tokens,
        "total_completion_tokens": s.total_completion_tokens,
        "active_traces": len(PT.get_all_active()),
        "gatekeeper_initialized": s.gatekeeper_stats is not None,
    }


def _get_model_info_dict() -> dict:
    from config import MODEL_ID as cfg_model_id
    info: dict[str, Any] = {
        "model_id": cfg_model_id,
        "model_path": None,
        "n_gpu_layers": 0,
        "n_ctx": 0,
        "n_batch": 0,
        "n_ubatch": 0,
        "flash_attn": False,
        "thinking_mode": False,
        "max_tokens": 2048,
        "gatekeeper_model_loaded": False,
        "model_loaded": False,
    }
    try:
        from config import (
            LLAMA_MODEL_PATH, N_GPU_LAYERS, LLM_NUM_CTX,
            LLM_BATCH_SIZE, LLM_UBATCH_SIZE, LLM_FLASH_ATTN,
            LLM_THINKING_MODE, LLM_MAX_TOKENS,
        )
        info["model_path"] = LLAMA_MODEL_PATH
        info["n_gpu_layers"] = N_GPU_LAYERS
        info["n_ctx"] = LLM_NUM_CTX
        info["n_batch"] = LLM_BATCH_SIZE
        info["n_ubatch"] = LLM_UBATCH_SIZE
        info["flash_attn"] = LLM_FLASH_ATTN
        info["thinking_mode"] = LLM_THINKING_MODE
        info["max_tokens"] = LLM_MAX_TOKENS
    except Exception:
        pass
    try:
        from llm_engine import engine
        info["model_loaded"] = engine.chat_model is not None
        info["gatekeeper_model_loaded"] = engine.gatekeeper_model is not None
    except Exception:
        pass
    return info


def _get_pending_ops_dict() -> dict:
    s = _import_state()
    bg_count = len(s.background_tasks)
    qsize = s.file_event_queue.qsize() if hasattr(s, 'file_event_queue') else 0
    return {
        "background_tasks_count": bg_count,
        "file_event_queue_size": qsize,
        "reindexing_in_progress": getattr(s, 'is_reindexing', False),
    }


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
        from telemetry import PipelineTracer
        from prompt_builder import build_omniscient_prompt
        from llm_engine import engine
        from datetime import datetime, UTC

        # ── Crea tracer ──
        tracer = PipelineTracer.begin(user_message=message[:200], user_id=user_id)

        # ── Build enriched messages ──
        raw_messages = [{"role": "user", "content": message}]
        enriched = await build_omniscient_prompt(
            raw_messages,
            user_id=user_id,
            conversation_id="mcp",
            concise=False,
            request_id=tracer.request_id,
            finalize_trace=False,  # Noi gestiamo il finish dopo la generazione LLM
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
        from telemetry import LlmCallRecord
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
        from synaptiq_bridge import hybrid_code_search
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
