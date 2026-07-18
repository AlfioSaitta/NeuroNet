"""
Handler MCP condivisi — logica di processamento richieste MCP usata sia dal
server stdio (mcp_server.py) sia dall'endpoint SSE in-app (main.py).

Mantenuto separato per evitare dependency heavy (llama-cpp-python, tiktoken)
durante i test e permettere import leggero.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# In-process handlers (accedono a state/telemetry)
# ──────────────────────────────────────────────


def handle_mcp_request(method: str, params: Optional[dict] = None) -> Any:
    """Processa una richiesta MCP direttamente (in-process, senza HTTP proxy).

    Usato dall'endpoint SSE in main.py.
    """
    if method == "initialize":
        proto = (params or {}).get("protocolVersion", "2024-11-05")
        return {
            "protocolVersion": proto,
            "serverInfo": {"name": "jarvis-telemetry", "version": "1.0.0"},
            "capabilities": {"tools": {}, "resources": {}},
        }

    if method == "tools/list":
        return _list_tools()

    if method == "tools/call":
        return _call_tool(params)

    if method == "resources/list":
        return _list_resources()

    if method == "resources/read":
        return _read_resource(params)

    if method == "ping":
        return {"status": "ok"}

    return {"error": f"Method not found: {method}", "code": -32601}


# ──────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────


def _import_state():
    """Import state in modo safe (modulo leggero)."""
    import state as _s
    return _s


def _import_telemetry():
    """Import telemetry in modo safe."""
    from telemetry import PipelineTracer as _PT, get_recent_traces as _grt, get_trace_by_id as _gtbi
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
    info = {
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


# ── Session Store helper ──


def _get_store():
    s = _import_state()
    return getattr(s, 'chat_session_store', None)


def _list_sessions(limit: int = 20, sort_by: str = "last_activity", user_id: str = "") -> dict:
    store = _get_store()
    if not store:
        return {"error": "Session store not initialized"}
    uid = user_id if user_id else None
    return {"sessions": store.list_sessions(limit=min(limit, 200), sort_by=sort_by, user_id=uid)}


def _get_session(conversation_id: str) -> dict:
    store = _get_store()
    if not store:
        return {"error": "Session store not initialized"}
    turns = store.get_session(conversation_id)
    if not turns:
        return {"error": f"Session '{conversation_id}' not found"}
    return {"conversation_id": conversation_id, "turns": turns, "turn_count": len(turns)}


def _search_sessions(query: str, user_id: str = "", limit: int = 20) -> dict:
    store = _get_store()
    if not store:
        return {"error": "Session store not initialized"}
    uid = user_id if user_id else None
    return {"results": store.search_sessions(query, user_id=uid, limit=limit)}


def _get_session_stats() -> dict:
    store = _get_store()
    if not store:
        return {"error": "Session store not initialized"}
    return {"stats": store.get_stats()}


def _export_session(conversation_id: str, format: str = "json") -> dict:
    store = _get_store()
    if not store:
        return {"error": "Session store not initialized"}
    if format not in ("json", "markdown"):
        return {"error": "Formato non supportato. Usa 'json' o 'markdown'."}
    text = store.export_session(conversation_id, format=format)
    return {"content": text, "format": format, "conversation_id": conversation_id}


def _list_tools() -> list[dict]:
    return [
        {"name": "get_recent_traces",
         "description": "Ultimi N pipeline trace completati, con step, LLM calls e metriche.",
         "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 10}}}},
        {"name": "get_active_traces",
         "description": "Trace correntemente in esecuzione (richieste non ancora completate).",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "get_trace_by_id",
         "description": "Cerca un pipeline trace completato per request_id.",
         "inputSchema": {"type": "object", "properties": {"request_id": {"type": "string"}}, "required": ["request_id"]}},
        {"name": "get_gatekeeper_stats",
         "description": "Statistiche cumulative del Gatekeeper (bypass rate, confidence media).",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "get_errors",
         "description": "Contatori di errore per diagnostica.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "get_status",
         "description": "Stato del sistema: uptime, richieste totali, token, trace attivi.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "get_model_info",
         "description": "Informazioni sul modello LLM caricato (family, GPU layers, flash attention).",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "get_pending_ops",
         "description": "Operazioni pendenti: background tasks, coda watchdog.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "list_sessions",
         "description": "Lista sessioni chat con metadati (turn count, progetto, ultima attività).",
         "inputSchema": {"type": "object", "properties": {
             "limit": {"type": "integer", "default": 20},
             "sort_by": {"type": "string", "default": "last_activity"},
             "user_id": {"type": "string", "default": ""},
         }}},
        {"name": "get_session",
         "description": "Recupera una sessione chat completa per conversation_id con tutti i turni.",
         "inputSchema": {"type": "object", "properties": {
             "conversation_id": {"type": "string"},
         }, "required": ["conversation_id"]}},
        {"name": "search_sessions",
         "description": "Cerca testo in tutte le sessioni chat. Restituisce snippet del primo match per sessione.",
         "inputSchema": {"type": "object", "properties": {
             "query": {"type": "string"},
             "user_id": {"type": "string", "default": ""},
             "limit": {"type": "integer", "default": 20},
         }, "required": ["query"]}},
        {"name": "get_session_stats",
         "description": "Statistiche aggregate su tutte le sessioni chat (tokens, turni, durata).",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "export_session",
         "description": "Esporta una sessione chat in formato JSON o Markdown per analisi esterna.",
         "inputSchema": {"type": "object", "properties": {
             "conversation_id": {"type": "string"},
             "format": {"type": "string", "default": "json"},
         }, "required": ["conversation_id"]}},
    ]


def _call_tool(params: Optional[dict]) -> dict:
    s = _import_state()
    PT, grt, gtbi = _import_telemetry()

    tool_name = (params or {}).get("name", "")
    args = (params or {}).get("arguments", {})
    limit = args.get("limit", 10) if isinstance(args, dict) else 10

    tool_map = {
        "get_recent_traces": lambda: {"traces": grt(limit=limit), "count": len(grt(limit=limit))},
        "get_active_traces": lambda: {"active_traces": PT.get_all_active(), "count": len(PT.get_all_active())},
        "get_trace_by_id": lambda: gtbi(args.get("request_id", "")) or {"error": "not found"},
        "get_gatekeeper_stats": lambda: {"stats": s.gatekeeper_stats.to_dict() if s.gatekeeper_stats else None},
        "get_errors": lambda: {"errors": dict(s.error_counters)},
        "get_status": _get_status_dict,
        "get_model_info": _get_model_info_dict,
        "get_pending_ops": _get_pending_ops_dict,
        "list_sessions": lambda: _list_sessions(
            limit=args.get("limit", 20),
            sort_by=args.get("sort_by", "last_activity"),
            user_id=args.get("user_id", ""),
        ),
        "get_session": lambda: _get_session(args.get("conversation_id", "")),
        "search_sessions": lambda: _search_sessions(
            query=args.get("query", ""),
            user_id=args.get("user_id", ""),
            limit=args.get("limit", 20),
        ),
        "get_session_stats": lambda: _get_session_stats(),
        "export_session": lambda: _export_session(
            conversation_id=args.get("conversation_id", ""),
            format=args.get("format", "json"),
        ),
    }

    handler = tool_map.get(tool_name)
    if not handler:
        return {"error": f"Unknown tool: {tool_name}", "code": -32601}

    try:
        data = handler()
        return {
            "content": [{"type": "text", "text": json.dumps(data, indent=2, ensure_ascii=False, default=str)}],
            "isError": isinstance(data, dict) and "error" in data,
        }
    except Exception as e:
        logger.exception(f"Tool '{tool_name}' error")
        return {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}


def _list_resources() -> list[dict]:
    return [
        {"uri": "jarvis://traces/recent", "name": "Recent Traces", "mimeType": "application/json"},
        {"uri": "jarvis://traces/active", "name": "Active Traces", "mimeType": "application/json"},
        {"uri": "jarvis://gatekeeper/stats", "name": "Gatekeeper Stats", "mimeType": "application/json"},
        {"uri": "jarvis://errors/counters", "name": "Error Counters", "mimeType": "application/json"},
        {"uri": "jarvis://system/status", "name": "System Status", "mimeType": "application/json"},
        {"uri": "jarvis://model/info", "name": "Model Info", "mimeType": "application/json"},
        {"uri": "jarvis://system/pending_ops", "name": "Pending Ops", "mimeType": "application/json"},
        {"uri": "jarvis://sessions/list", "name": "Recent Sessions", "mimeType": "application/json"},
        {"uri": "jarvis://sessions/stats", "name": "Session Stats", "mimeType": "application/json"},
    ]


def _read_resource(params: Optional[dict]) -> dict:
    s = _import_state()
    PT, grt, gtbi = _import_telemetry()
    uri = (params or {}).get("uri", "")

    resource_map = {
        "jarvis://traces/recent": lambda: {"traces": grt(limit=10)},
        "jarvis://traces/active": lambda: {"active_traces": PT.get_all_active()},
        "jarvis://gatekeeper/stats": lambda: {"stats": s.gatekeeper_stats.to_dict() if s.gatekeeper_stats else None},
        "jarvis://errors/counters": lambda: {"errors": dict(s.error_counters)},
        "jarvis://system/status": _get_status_dict,
        "jarvis://model/info": _get_model_info_dict,
        "jarvis://system/pending_ops": _get_pending_ops_dict,
        "jarvis://sessions/list": lambda: _list_sessions(limit=20),
        "jarvis://sessions/stats": _get_session_stats,
    }

    handler = resource_map.get(uri)
    # Fallback per resource dinamiche (sessioni singole)
    if handler is None and uri.startswith("jarvis://sessions/"):
        conv_id = uri[len("jarvis://sessions/"):]
        if conv_id and conv_id not in ("list", "stats"):
            handler = lambda: _get_session(conv_id)
    if not handler:
        return {"error": f"Unknown resource: {uri}", "code": -32602}

    data = handler()
    text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}
