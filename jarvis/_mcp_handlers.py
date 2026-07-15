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
    info = {"model_id": cfg_model_id}
    try:
        from llm_engine import engine
        if hasattr(engine, 'model_path') and engine.chat_model is not None:
            info["model_path"] = str(getattr(engine, 'model_path', ''))
        info["n_gpu_layers"] = getattr(engine, 'n_gpu_layers', 0)
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
    }

    handler = resource_map.get(uri)
    if not handler:
        return {"error": f"Unknown resource: {uri}", "code": -32602}

    data = handler()
    text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}
