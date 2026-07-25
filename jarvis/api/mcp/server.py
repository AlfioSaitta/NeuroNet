"""
MCP Server — Expone lo stato interno di Jarvis (pipeline trace, gatekeeper,
errori) ad agenti AI esterni tramite il Model Context Protocol (stdio).

Usage:
    python -m jarvis.mcp_server [--jarvis-url http://localhost:8000]

L'agente AI (Claude Code, Cursor, ecc.) lancia questo processo come server
MCP configurato in .mcp.json:
    {
        "mcpServers": {
            "jarvis-telemetry": {
                "command": "python",
                "args": ["-m", "jarvis.mcp_server"]
            }
        }
    }
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.request
import urllib.error
from typing import Any, Optional

logging.basicConfig(
    level=logging.INFO,
    format="[MCP-SRV] %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

JARVIS_URL = os.environ.get("JARVIS_URL", "http://localhost:8000")

# ──────────────────────────────────────────────
# MCP Protocol Helpers
# ──────────────────────────────────────────────

_next_id = 1


def _jsonrpc_send(method: str, params: Optional[dict] = None) -> str:
    """Invia un messaggio JSON-RPC a stdout."""
    global _next_id
    msg = {
        "jsonrpc": "2.0",
        "id": _next_id,
        "method": method,
    }
    if params is not None:
        msg["params"] = params
    _next_id += 1
    payload = json.dumps(msg, ensure_ascii=False)
    # MCP protocol: ogni messaggio è terminato da \n
    sys.stdout.write(payload + "\n")
    sys.stdout.flush()
    return payload


def _jsonrpc_response(result: Any, req_id: int):
    """Invia una risposta JSON-RPC a stdout."""
    msg = {"jsonrpc": "2.0", "id": req_id, "result": result}
    payload = json.dumps(msg, ensure_ascii=False)
    sys.stdout.write(payload + "\n")
    sys.stdout.flush()


def _jsonrpc_error(code: int, message: str, req_id: int):
    """Invia un errore JSON-RPC a stdout."""
    msg = {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }
    payload = json.dumps(msg, ensure_ascii=False)
    sys.stdout.write(payload + "\n")
    sys.stdout.flush()


# ──────────────────────────────────────────────
# Jarvis HTTP Client
# ──────────────────────────────────────────────


def _fetch_json(path: str) -> Optional[dict]:
    """GET un endpoint JSON di Jarvis, restituisce dict o None."""
    url = f"{JARVIS_URL}{path}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        logger.warning(f"HTTP {e.code} da {url}")
        return {"error": f"HTTP {e.code}", "detail": str(e)}
    except urllib.error.URLError as e:
        logger.warning(f"Connessione fallita a {url}: {e}")
        return {"error": "connection_refused", "detail": f"Jarvis non raggiungibile su {JARVIS_URL}"}
    except Exception as e:
        logger.warning(f"Errore fetching {url}: {e}")
        return {"error": str(e)}


# ──────────────────────────────────────────────
# Tool Implementations
# ──────────────────────────────────────────────

# NOTA: il server MCP espone due modalità di accesso:
#   1. Tools (callTool) — il client invoca un'operazione e ottiene un risultato
#   2. Resources (resources/read) — il client legge una risorsa pre-definita
#
# Qui implementiamo entrambe per dare la massima flessibilità agli agenti.


def tool_get_recent_traces(args: dict) -> dict:
    """Ultimi N pipeline trace completati."""
    limit = args.get("limit", 10)
    data = _fetch_json(f"/api/telemetry/traces?limit={limit}")
    if data:
        return data
    return {"traces": [], "count": 0}


def tool_get_active_traces(args: dict) -> dict:
    """Trace correntemente in esecuzione."""
    data = _fetch_json("/api/telemetry/traces/active")
    if data:
        return data
    return {"active_traces": [], "count": 0}


def tool_get_trace_by_id(args: dict) -> dict:
    """Cerca un trace per request_id."""
    request_id = args.get("request_id", "")
    data = _fetch_json(f"/api/telemetry/traces/{request_id}")
    return data or {"error": f"Trace {request_id} non trovato"}


def tool_get_gatekeeper_stats(args: dict) -> dict:
    """Statistiche cumulative del Gatekeeper."""
    data = _fetch_json("/api/telemetry/gatekeeper")
    return data or {"stats": None}


def tool_get_errors(args: dict) -> dict:
    """Contatori di errore."""
    data = _fetch_json("/api/telemetry/errors")
    return data or {"errors": {}}


def tool_get_status(args: dict) -> dict:
    """Stato generale del sistema."""
    data = _fetch_json("/api/telemetry/status")
    return data or {"error": "status non disponibile"}


def tool_get_model_info(args: dict) -> dict:
    """Informazioni sul modello LLM caricato (family, GPU layers, flash attention)."""
    data = _fetch_json("/api/telemetry/model")
    return data or {"error": "model info non disponibile"}


def tool_get_pending_ops(args: dict) -> dict:
    """Operazioni pendenti: background tasks, coda eventi watchdog."""
    data = _fetch_json("/api/telemetry/pending_ops")
    return data or {"error": "pending ops non disponibili"}


def tool_get_llm_call_breakdown(args: dict) -> dict:
    """Analisi delle chiamate LLM dai trace recenti."""
    data = _fetch_json("/api/telemetry/traces?limit=100")
    if not data or "traces" not in data:
        return {"error": "nessun trace disponibile", "breakdown": {}}

    total_llm_calls = 0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    errors = 0
    tool_calls = 0

    for t in data["traces"]:
        llm_calls = t.get("llm_calls", [])
        total_llm_calls += len(llm_calls)
        for c in llm_calls:
            total_prompt_tokens += c.get("tokens_prompt", 0)
            total_completion_tokens += c.get("tokens_completion", 0)
            if c.get("error"):
                errors += 1
        tool_calls += t.get("tool_calls_count", 0)
        if t.get("error"):
            errors += 1

    return {
        "total_traces": len(data["traces"]),
        "total_llm_calls": total_llm_calls,
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "total_tool_calls": tool_calls,
        "errors": errors,
    }


# Registry dei tool: nome → (handler, description, input_schema)
TOOLS = {
    "get_recent_traces": (
        tool_get_recent_traces,
        "Ottiene gli ultimi N pipeline trace completati. Ogni trace contiene step, LLM calls e metriche.",
        {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Numero di trace da restituire (default: 10, max: 100)",
                    "default": 10,
                }
            },
        },
    ),
    "get_active_traces": (
        tool_get_active_traces,
        "Elenca i trace attualmente in esecuzione (richieste non ancora completate).",
        {"type": "object", "properties": {}},
    ),
    "get_trace_by_id": (
        tool_get_trace_by_id,
        "Cerca un pipeline trace completato per request_id. Risposta: 404 se non trovato.",
        {
            "type": "object",
            "properties": {
                "request_id": {
                    "type": "string",
                    "description": "Il request_id a 12 caratteri del trace da cercare",
                }
            },
            "required": ["request_id"],
        },
    ),
    "get_gatekeeper_stats": (
        tool_get_gatekeeper_stats,
        "Statistiche cumulative del Gatekeeper (classificazione intento, bypass rate, confidence media).",
        {"type": "object", "properties": {}},
    ),
    "get_errors": (
        tool_get_errors,
        "Contatori di errore per diagnostica. Mostra quanti errori e di che tipo si sono verificati.",
        {"type": "object", "properties": {}},
    ),
    "get_status": (
        tool_get_status,
        "Stato generale del sistema: uptime, richieste totali, token consumati, trace attivi.",
        {"type": "object", "properties": {}},
    ),
    "get_llm_call_breakdown": (
        tool_get_llm_call_breakdown,
        "Analisi aggregata delle chiamate LLM dagli ultimi 100 trace: token totali, chiamate, tool call, errori.",
        {"type": "object", "properties": {}},
    ),
    "get_model_info": (
        tool_get_model_info,
        "Informazioni sul modello LLM caricato (family, GPU layers, flash attention, model path).",
        {"type": "object", "properties": {}},
    ),
    "get_pending_ops": (
        tool_get_pending_ops,
        "Operazioni pendenti: background tasks in esecuzione, coda eventi watchdog.",
        {"type": "object", "properties": {}},
    ),
}


def _handle_initialize(req_id: int, params: Optional[dict]):
    """Handshake iniziale MCP."""
    protocol_version = (params or {}).get("protocolVersion", "2024-11-05")
    server_info = {
        "name": "jarvis-telemetry",
        "version": "1.0.0",
    }
    capabilities = {
        "tools": {},  # supporta listTools + callTool
        "resources": {},  # supporta resources/list + resources/read
    }
    _jsonrpc_response(
        {
            "protocolVersion": protocol_version,
            "serverInfo": server_info,
            "capabilities": capabilities,
        },
        req_id,
    )


def _handle_list_tools(req_id: int, _params: Optional[dict]):
    """Lista i tool disponibili."""
    result = []
    for name, (_, description, schema) in TOOLS.items():
        result.append({
            "name": name,
            "description": description,
            "inputSchema": schema,
        })
    _jsonrpc_response(result, req_id)


def _handle_call_tool(req_id: int, params: Optional[dict]):
    """Esegue un tool."""
    if not params:
        _jsonrpc_error(-32602, "Params required", req_id)
        return

    name = params.get("name", "")
    args = params.get("arguments", {})

    if name not in TOOLS:
        _jsonrpc_error(-32601, f"Tool '{name}' not found", req_id)
        return

    try:
        handler, _, _ = TOOLS[name]
        result = handler(args)
        # MCP tool result format
        content = []
        if isinstance(result, dict):
            content.append({
                "type": "text",
                "text": json.dumps(result, indent=2, ensure_ascii=False, default=str),
            })
        else:
            content.append({"type": "text", "text": str(result)})

        is_error = "error" in result if isinstance(result, dict) else False
        _jsonrpc_response({"content": content, "isError": is_error}, req_id)
    except Exception as e:
        logger.exception(f"Tool '{name}' error")
        _jsonrpc_response(
            {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True},
            req_id,
        )


def _handle_list_resources(req_id: int, _params: Optional[dict]):
    """Lista le risorse disponibili (URI schema)."""
    resources = [
        {
            "uri": "jarvis://traces/recent",
            "name": "Recent Pipeline Traces",
            "description": "Ultimi 10 pipeline trace completati",
            "mimeType": "application/json",
        },
        {
            "uri": "jarvis://traces/active",
            "name": "Active Traces",
            "description": "Trace correntemente in esecuzione",
            "mimeType": "application/json",
        },
        {
            "uri": "jarvis://gatekeeper/stats",
            "name": "Gatekeeper Stats",
            "description": "Statistiche cumulative del Gatekeeper",
            "mimeType": "application/json",
        },
        {
            "uri": "jarvis://errors/counters",
            "name": "Error Counters",
            "description": "Contatori di errore per diagnostica",
            "mimeType": "application/json",
        },
        {
            "uri": "jarvis://system/status",
            "name": "System Status",
            "description": "Stato generale del sistema (uptime, richieste, token)",
            "mimeType": "application/json",
        },
        {
            "uri": "jarvis://llm/breakdown",
            "name": "LLM Call Breakdown",
            "description": "Analisi aggregata delle chiamate LLM",
            "mimeType": "application/json",
        },
        {
            "uri": "jarvis://model/info",
            "name": "Model Info",
            "description": "Informazioni sul modello LLM caricato (family, GPU layers, flash attention)",
            "mimeType": "application/json",
        },
        {
            "uri": "jarvis://system/pending_ops",
            "name": "Pending Operations",
            "description": "Operazioni pendenti: background tasks, coda watchdog",
            "mimeType": "application/json",
        },
    ]
    _jsonrpc_response(resources, req_id)


def _handle_read_resource(req_id: int, params: Optional[dict]):
    """Legge una risorsa per URI."""
    if not params:
        _jsonrpc_error(-32602, "Params required", req_id)
        return

    uri = params.get("uri", "")

    resource_map = {
        "jarvis://traces/recent": ("/api/telemetry/traces?limit=10", tool_get_recent_traces),
        "jarvis://traces/active": ("/api/telemetry/traces/active", tool_get_active_traces),
        "jarvis://gatekeeper/stats": ("/api/telemetry/gatekeeper", tool_get_gatekeeper_stats),
        "jarvis://errors/counters": ("/api/telemetry/errors", tool_get_errors),
        "jarvis://system/status": ("/api/telemetry/status", tool_get_status),
        "jarvis://llm/breakdown": (None, tool_get_llm_call_breakdown),
        "jarvis://model/info": (None, tool_get_model_info),
        "jarvis://system/pending_ops": (None, tool_get_pending_ops),
    }

    if uri not in resource_map:
        _jsonrpc_error(-32602, f"Unknown resource: {uri}", req_id)
        return

    _, handler = resource_map[uri]
    result = handler({"limit": 10} if uri == "jarvis://traces/recent" else {})

    content = []
    if isinstance(result, dict):
        content.append({
            "type": "text",
            "text": json.dumps(result, indent=2, ensure_ascii=False, default=str),
        })
    else:
        content.append({"type": "text", "text": str(result)})

    _jsonrpc_response({"contents": [{"uri": uri, "mimeType": "application/json", "text": content[0]["text"]}]}, req_id)


def _handle_not_found(req_id: int, _params: Optional[dict]):
    """Metodo non supportato."""
    _jsonrpc_error(-32601, "Method not found", req_id)


# ──────────────────────────────────────────────
# JSON-RPC Dispatcher
# ──────────────────────────────────────────────

METHOD_HANDLERS = {
    "initialize": _handle_initialize,
    "tools/list": _handle_list_tools,
    "tools/call": _handle_call_tool,
    "resources/list": _handle_list_resources,
    "resources/read": _handle_read_resource,
    # ping/pong standard
    "ping": lambda req_id, _: _jsonrpc_response({"status": "ok"}, req_id),
}


def _handle_message(line: str):
    """Processa una singola linea JSON-RPC da stdin."""
    line = line.strip()
    if not line:
        return

    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        logger.warning(f"JSON non valido: {line[:200]}")
        return

    req_id = msg.get("id", 0)
    method = msg.get("method", "")
    params = msg.get("params")

    handler = METHOD_HANDLERS.get(method, _handle_not_found)
    try:
        handler(req_id, params)
    except Exception as e:
        logger.exception(f"Handler error for method '{method}'")
        _jsonrpc_error(-32603, f"Internal error: {e}", req_id)


# ──────────────────────────────────────────────
# Main Loop
# ──────────────────────────────────────────────


def main():
    """Avvia il server MCP in modalità stdio."""
    logger.info(f"🚀 MCP Server avviato (Jarvis URL: {JARVIS_URL})")

    # Verifica connessione a Jarvis
    status = _fetch_json("/api/telemetry/status")
    if status and "error" not in status:
        logger.info(f"✅ Connesso a Jarvis — uptime: {status.get('uptime_hours', '?')}h")
    else:
        logger.warning(f"⚠️ Jarvis non raggiungibile su {JARVIS_URL} — i tool restituiranno errori")

    # Invia notifica initialized al client (MCP requires server -> client)
    # Non tutti i client lo richiedono, ma è buona pratica.
    logger.info("MCP Server pronto, in ascolto su stdin...")

    # Loop principale: legge JSON-RPC da stdin
    for raw_line in sys.stdin:
        if not raw_line.strip():
            continue
        _handle_message(raw_line)

    logger.info("MCP Server terminato (stdin chiuso)")


if __name__ == "__main__":
    main()
