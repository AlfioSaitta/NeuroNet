"""
Collateral Studios Agent v8.6.7 — Entry point dell'applicazione.
App FastAPI, lifespan e tutti gli endpoint HTTP.
"""

import os
import json
import time
import asyncio
import warnings
import traceback
import threading
import uuid

_default_showwarning = warnings.showwarning

def custom_showwarning(message, category, filename, lineno, file=None, line=None):
    if category is DeprecationWarning and "msg" in str(message) and "cancel" in str(message):
        return
    _default_showwarning(message, category, filename, lineno, file, line)

warnings.showwarning = custom_showwarning

# Cattura tutte le eccezioni non gestite nei thread (es. watchdog emitter)
_default_thread_excepthook = threading.excepthook
def _thread_excepthook(args):
    logger.critical(f"💥 Thread '{args.thread.name}' crashato: {args.exc_type.__name__}: {args.exc_value}")
    logger.critical(f"Traceback:\n{''.join(traceback.format_tb(args.exc_traceback))}")
    _default_thread_excepthook(args)
threading.excepthook = _thread_excepthook

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from core.config import (
    logger, MODEL_ID, MODEL_PROFILE, DOC_COLLECTION, DOC_DIR, MEM0_CONFIG, STATE_FILE, VECTOR_DB_VERSION,
    API_RATE_LIMIT_DEFAULT, API_RATE_LIMIT_HEAVY, API_RATE_LIMIT_EMBED,
    SYNAPTIQ_ENABLED,
)
import core.state as state
from core.chat_utils import handle_confirmation_token, spawn_background, resolve_user_id
from core.telemetry_api import get_status_dict, get_model_info_dict, get_pending_ops_dict
from rag.engine import ingest_local_documents, search_documents
from rag.cache import semantic_cache_search, semantic_cache_store
from memory.engine import extract_memories, save_to_memory, process_response_tags, reindex_graph_connections
from agent.tags import strip_action_tags, TagSafeStream
from agent.prompt import build_omniscient_prompt
from core.telemetry import PipelineTracer
from core.llm_engine import engine, extract_content, MODEL_PROFILE
from core.reasoning import configura_richiesta_agente, genera_stream_agente
from agent.tools import execute_tool_call
from agent.confirmation import ConfirmationManager
from agent.classifier import is_internal_query
from openai_api import router as openai_router, init_openai_routes
init_openai_routes()  # populate the router with all endpoint sub-modules

from core.lifecycle import lifespan


# ==============================================================================
# APP FASTAPI
# ==============================================================================

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any

def _rate_limit_key_localhost(request: Request):
    """Rate limiting separato per richieste interne (localhost vs esterno).

    Mem0 chiama /api/embed internamente per l'entity embedding.
    Le chiamate da localhost hanno un loro bucket (60/min) separato da
    quello degli IP esterni, quindi non entrano in conflitto.
    """
    client = get_remote_address(request)
    if client in ("127.0.0.1", "::1", "localhost", "0.0.0.0"):
        return "127.0.0.1"  # bucket separato per chiamate interne
    return client

limiter = Limiter(key_func=get_remote_address)

class Message(BaseModel):
    role: str
    content: str
    
    model_config = ConfigDict(extra="allow")

class ChatRequest(BaseModel):
    model: str
    messages: List[Message]
    stream: Optional[bool] = True
    options: Optional[Dict[str, Any]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    conversation_id: Optional[str] = None
    provider: Optional[str] = None
    confirmation_token: Optional[str] = None

    model_config = ConfigDict(extra="allow")

class GenerateRequest(BaseModel):
    model: str
    prompt: str
    stream: Optional[bool] = True

class TelegramOTPRequest(BaseModel):
    phone: str
    code: str
    password: Optional[str] = None
    options: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="allow")

app = FastAPI(title="Collateral Studios Agent", version="8.6.7", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"]
)

# ── Dashboard Auth Middleware ──────────────────────────────────────────
# Protects /api/dashboard/* and /admin/* routes with JWT auth.
# Public paths are excluded. Admin-only paths checked for admin role.

from api.auth import get_current_user

ADMIN_ONLY_PATHS = (
    "/api/dashboard/settings", "/api/dashboard/models",
    "/api/dashboard/tasks", "/api/dashboard/cron",
    "/api/dashboard/analytics", "/api/dashboard/logs",
    "/api/dashboard/system", "/api/dashboard/graph",
)

PUBLIC_PATHS = (
    "/api/auth/login", "/api/auth/logout", "/api/auth/me",
    "/admin/login", "/admin/static/",
    "/api/chat",
    "/api/project-tree", "/api/webhook/git",

    "/api/synaptiq/",
)


@app.middleware("http")
async def dashboard_auth_middleware(request: Request, call_next):
    path = request.url.path

    # Skip OPTIONS preflight
    if request.method == "OPTIONS":
        return await call_next(request)

    # Check if path is covered by any public prefix
    is_public = any(
        path == p or path.startswith(p)
        for p in PUBLIC_PATHS
    )
    if is_public:
        return await call_next(request)

    # Require auth for dashboard and admin paths
    if path.startswith("/api/dashboard/") or path.startswith("/admin/") or path == "/":
        user = await get_current_user(request)
        if not user:
            if path.startswith("/admin/"):
                from starlette.responses import RedirectResponse
                return RedirectResponse(url="/admin/login", status_code=303)
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"},
            )

        # Admin-only paths
        if path.startswith(ADMIN_ONLY_PATHS) and user.get("role") != "admin":
            return JSONResponse(
                status_code=403,
                content={"detail": "Admin access required"},
            )

        request.state.user = user

    return await call_next(request)


# ── API Key Auth Middleware ────────────────────────────────────────────
# Protects /v1/* OpenAI-compatible endpoints with API key auth.
# Backward compat: localhost requests without key are allowed.
# Registered AFTER dashboard middleware → runs FIRST (LIFO).


def _is_private_ip(ip: str) -> bool:
    """True if IP is loopback or private network (incl Docker 172.x)."""
    if ip in ("127.0.0.1", "::1", "localhost", "0.0.0.0"):
        return True
    if ip.startswith("10.") or ip.startswith("192.168."):
        return True
    if ip.startswith("172."):
        try:
            return 16 <= int(ip.split(".")[1]) <= 31
        except (IndexError, ValueError):
            return False
    return False


def _is_local_request(request: Request) -> bool:
    """Determine if request comes from localhost/private network.
    Checks X-Forwarded-For, X-Real-IP, then request.client.host."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return _is_private_ip(forwarded.split(",")[0].strip())
    real_ip = request.headers.get("X-Real-IP", "")
    if real_ip:
        return _is_private_ip(real_ip.strip())
    client_ip = request.client.host if request.client else ""
    return _is_private_ip(client_ip)


@app.middleware("http")
async def openai_api_key_middleware(request: Request, call_next):
    """Resolve API key for /v1/* endpoints.
    - Valid key → resolve user, set request.state.user
    - Localhost without key → backward compat (pass through)
    - External without key → 401
    - Invalid key → 401
    """
    # Skip OPTIONS preflight
    if request.method == "OPTIONS":
        return await call_next(request)

    if not request.url.path.startswith("/v1/"):
        return await call_next(request)

    auth = request.headers.get("Authorization", "")

    if auth.startswith("Bearer sk-jarvis-"):
        raw_key = auth[7:].strip()
        # Internal service key bypass (Mem0, etc.) — skip DB lookup
        if raw_key == os.environ.get("OPENAI_API_KEY", ""):
            return await call_next(request)

        from api.auth.user_manager import user_manager

        try:
            result = await user_manager.resolve_api_key(raw_key)
            if result:
                key_row, user_row = result
                request.state.user = user_row
                request.state.api_key_id = key_row["id"]
            else:
                return JSONResponse(
                    status_code=401,
                    content={"error": "Invalid API key. Use a valid sk-jarvis-... key."},
                )
        except Exception as exc:
            logger.warning("API key resolve error: %s", exc)
            return JSONResponse(
                status_code=500,
                content={"error": "Internal authentication error"},
            )
    elif auth.startswith("Bearer "):
        # Key provided but doesn't start with sk-jarvis- → always reject
        return JSONResponse(
            status_code=401,
            content={
                "error": "Invalid API key format. API keys start with 'sk-jarvis-'. Set 'Authorization: Bearer sk-jarvis-...'."
            },
        )
    elif not _is_local_request(request):
        return JSONResponse(
            status_code=401,
            content={
                "error": "API key required. Set 'Authorization: Bearer sk-jarvis-...'."
            },
        )
    else:
        # No auth header + localhost → backward compat pass through
        logger.info(
            "⚠️ OpenAI endpoint %s called without API key from localhost",
            request.url.path,
        )

    return await call_next(request)


from admin.dashboard import dashboard_router
app.include_router(dashboard_router)

from api.auth import router as auth_router
app.include_router(auth_router)

from routes.users import router as users_router
app.include_router(users_router)

from routes.profile import router as profile_router
app.include_router(profile_router)

from routes.projects import router as projects_router
app.include_router(projects_router)

from admin.panel import setup_admin_panel
setup_admin_panel(app)


# ── T1+T2 Helper: Ricostruisce tool_calls da chunk streaming ──────────
def _reconstruct_tool_calls(stream_chunks):
    """Reconstruct complete tool_calls list from OpenAI streaming delta chunks.

    Nel formato streaming, tool_calls arrivano come delta incrementali su
    piú chunk. Ogni chunk ha 'delta.tool_calls' con un array di oggetti
    ciascuno con un campo 'index'. Gli arguments di funzione sono frammentati
    e vanno concatenati per index.

    Returns:
        List[dict]: Lista completa di tool_calls (id, type, function.name, function.arguments),
                    pronta per essere inserita in assistant message.
    """
    by_index = {}
    for chunk in stream_chunks:
        choices = chunk.get("choices", [])
        if not choices:
            continue
        delta = choices[0].get("delta", {})
        tc_list = delta.get("tool_calls", [])
        if not tc_list:
            # Fallback: tool_calls a livello choices (alcuni provider)
            tc_list = choices[0].get("tool_calls", [])
        for tc in tc_list:
            idx = tc.get("index", 0)
            if idx not in by_index:
                by_index[idx] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
            func = tc.get("function", {})
            if func.get("name"):
                by_index[idx]["function"]["name"] = func["name"]
            if func.get("arguments"):
                by_index[idx]["function"]["arguments"] += func["arguments"]
            if tc.get("id"):
                by_index[idx]["id"] = tc["id"]

    result = []
    for idx in sorted(by_index.keys()):
        tc = by_index[idx]
        if not tc["id"]:
            tc["id"] = f"call_{uuid.uuid4().hex[:12]}"
        result.append({
            "id": tc["id"],
            "type": tc["type"],
            "function": dict(tc["function"])
        })
    return result


# ==============================================================================
# ENDPOINTS
# ==============================================================================

@app.get("/api/project-tree")
@limiter.limit(API_RATE_LIMIT_DEFAULT)
async def get_project_tree(request: Request):
    """Restituisce l'albero del progetto indicizzato."""
    t = state.project_tree_cache
    return JSONResponse({"status": "success", "tree": t})


@app.api_route("/api/reset-all", methods=["GET", "POST"])
@limiter.limit(API_RATE_LIMIT_HEAVY)
async def reset_all(request: Request):
    """Reset nucleare: cancella tutte le collezioni Qdrant e riesegue l'ingestion."""
    from mem0 import Memory

    async with state.state_lock:
        state.rag_state.clear()
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
        
        # FIX LOGICA: Eliminare anche il DB SQLite! Altrimenti ingest_local_documents lo ricarica subito.
        db_path = STATE_FILE.replace('.json', '.db')
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except Exception as e:
                logger.warning(f"Errore cancellazione DB: {e}")
                
        bak_path = STATE_FILE.replace('.json', '.bak')
        if os.path.exists(bak_path):
            try:
                os.remove(bak_path)
            except Exception as e:
                pass

    state.created_collections.clear()
    state.last_project_context.clear()

    try:
        collections_info = await state.qdrant.get_collections()
        cols_to_delete = [
            c.name for c in collections_info.collections 
            if c.name.startswith("collateral_docs_") 
            or c.name.startswith("semantic_cache_")
        ]
    except Exception:
        cols_to_delete = [DOC_COLLECTION, f"semantic_cache_{VECTOR_DB_VERSION}"]

    for col in cols_to_delete:
        try:
            await state.qdrant.delete_collection(collection_name=col)
        except Exception as e:
            logger.warning(f"Impossibile eliminare collezione {col}: {e}")
            
    # Non ricreiamo le collezioni di memoria (mem0) in modo distruttivo.
    loop = asyncio.get_running_loop()
    state.memory = await loop.run_in_executor(state.mem0_executor, Memory.from_config, MEM0_CONFIG)
    spawn_background(ingest_local_documents())
    return JSONResponse({"status": "success", "message": "Reset totale eseguito. Ingestion Graph RAG ripartita."})


@app.post("/api/graph/reindex")
@limiter.limit(API_RATE_LIMIT_HEAVY)
async def graph_reindex(request: Request):
    """Ricrea le connessioni (entity linking) tra tutti i nodi di memoria esistenti.

    Scansiona tutte le memorie salvate in ``collateral_memories_v3``, estrae
    le entità via spaCy e le collega nella entity store
    (``collateral_memories_v3_entities``). Utile dopo aver attivato
    ``infer=True`` per collegare retroattivamente i nodi pre-esistenti.
    """
    try:
        body = await request.json()
        user_id = body.get("user_id", "alfio_dev")
    except Exception:
        user_id = "alfio_dev"

    logger.info(f"🔄 Avvio graph reindex per user={user_id}...")
    result = await reindex_graph_connections(user_id=user_id)

    status_code = 200 if result.get("success") else 500
    return JSONResponse(content=result, status_code=status_code)


@app.post("/api/webhook/git")
async def git_webhook(request: Request):
    """Gestisce i webhook da GitHub/Gitea/GitLab per triggerare l'aggiornamento RAG via git pull."""
    from core.config import GIT_WEBHOOK_SECRET
    
    # Sicurezza: Validazione secret token
    if GIT_WEBHOOK_SECRET:
        token_query = request.query_params.get("secret")
        token_gitlab = request.headers.get("X-Gitlab-Token")
        token_gitea = request.headers.get("X-Gitea-Token")
        
        if GIT_WEBHOOK_SECRET not in [token_query, token_gitlab, token_gitea]:
            return JSONResponse(status_code=403, content={"error": "Non autorizzato. Secret mancante o errato."})

    try:
        payload = await request.json()
    except Exception:
        payload = {}
        
    repo_name = None
    if "repository" in payload and isinstance(payload["repository"], dict):
        repo_name = payload["repository"].get("name")
        
    target_dir = DOC_DIR
    if repo_name:
        potential_dir = os.path.join(DOC_DIR, repo_name)
        if os.path.isdir(potential_dir) and os.path.isdir(os.path.join(potential_dir, ".git")):
            target_dir = potential_dir
            
    if not os.path.isdir(os.path.join(target_dir, ".git")):
        if os.path.isdir(os.path.join(DOC_DIR, ".git")):
            target_dir = DOC_DIR
        else:
            return JSONResponse(status_code=400, content={"error": "Nessun repository git valido trovato nella directory."})

    async def run_git_pull():
        logger.info(f"🔄 Ricevuto Webhook Git per {target_dir}. Esecuzione git pull...")
        try:
            result = await asyncio.create_subprocess_shell(
                "git pull",
                cwd=target_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await result.communicate()
            if result.returncode == 0:
                logger.info(f"✅ Git pull su {target_dir} completato: {stdout.decode().strip()}")
                # Il watchdog file_events si occuperà autonomamente di indicizzare i nuovi/vecchi file
            else:
                logger.error(f"❌ Errore git pull su {target_dir}: {stderr.decode().strip()}")
        except Exception as e:
            logger.error(f"❌ Errore esecuzione git pull: {e}")

    spawn_background(run_git_pull())
    
    return JSONResponse({"status": "success", "message": "Git pull avviato, l'ingestion partirà a breve."})


# ═══════════════════════════════════════════
# Pipeline Telemetry Endpoints
# ═══════════════════════════════════════════

@app.get("/api/telemetry/traces")
async def get_telemetry_traces(limit: int = 10):
    """Ultimi N pipeline trace completati."""
    from core.telemetry import get_recent_traces
    traces = get_recent_traces(limit=limit)
    return JSONResponse({"traces": traces, "count": len(traces)})


@app.get("/api/telemetry/traces/active")
async def get_telemetry_active_traces():
    """Trace correntemente in esecuzione."""
    active = PipelineTracer.get_all_active()
    return JSONResponse({"active_traces": active, "count": len(active)})


@app.get("/api/telemetry/traces/{request_id}")
async def get_telemetry_trace_by_id(request_id: str):
    """Cerca un trace completato per request_id."""
    from core.telemetry import get_trace_by_id
    trace = get_trace_by_id(request_id)
    if trace is None:
        return JSONResponse(status_code=404, content={"error": "Trace not found"})
    return JSONResponse(trace)


@app.get("/api/telemetry/gatekeeper")
async def get_telemetry_gatekeeper_stats():
    """Statistiche cumulative del Gatekeeper."""
    stats = state.gatekeeper_stats
    if stats is None:
        return JSONResponse({"stats": None, "message": "GatekeeperStats non ancora inizializzato"})
    return JSONResponse({"stats": stats.to_dict()})


@app.get("/api/telemetry/errors")
async def get_telemetry_errors():
    """Contatori di errore per diagnostica."""
    return JSONResponse({"errors": dict(state.error_counters)})


@app.get("/api/telemetry/status")
async def get_telemetry_status():
    """Stato generale del sistema per diagnostica."""
    return JSONResponse(get_status_dict())


@app.get("/api/telemetry/model")
async def get_telemetry_model():
    """Informazioni sul modello LLM caricato."""
    return JSONResponse(get_model_info_dict())


@app.get("/api/telemetry/pending_ops")
async def get_telemetry_pending_ops():
    """Operazioni pendenti: background tasks in esecuzione, coda eventi watchdog."""
    return JSONResponse(get_pending_ops_dict())


# ═══════════════════════════════════════════
# Init telemetry state in lifespan
# ═══════════════════════════════════════════
# gatekeeper_stats e pipeline_traces sono già definiti in state.py.
# Inizializziamo il contatore errori e il timestamp di avvio qui.
if not hasattr(state, '_start_time'):
    state._start_time = time.time()

# Inizializzazione Chat Session Store per tracciamento sessioni complete
from session.store import ChatSessionStore
if state.chat_session_store is None:
    state.chat_session_store = ChatSessionStore(max_sessions=500, max_turns_per_session=200)
    # Prova a caricare sessioni persistenti dal disco
    _store_path = "./data/sessions.json"
    state.chat_session_store.load(_store_path)


# ────────────────────────────────────────────────
# Helper: salvataggio turno chat nel SessionStore
# ────────────────────────────────────────────────


def _save_session_turn(tracer, role, content, user_id, conversation_id,
                       model="", prompt_tokens=0, completion_tokens=0,
                       tool_calls=None, error=None):
    """Salva un turno chat nel ChatSessionStore (fire-and-forget).

    Chiamato dopo ogni risposta LLM sia in streaming che non-stream.
    Il salvataggio è asincrono e non blocca la risposta al client.
    """
    if not content or not state.chat_session_store:
        return
    try:
        from session.store import MessageTurn
        _project = None
        _gk_intent = None
        if tracer:
            _project = getattr(tracer, '_rag_project', None)
            _gk = getattr(tracer, '_gatekeeper', None)
            if _gk:
                _gk_intent = _gk.get("intent")
        turn = MessageTurn(
            role=role,
            content=content,
            timestamp=time.time(),
            request_id=tracer.request_id if tracer else "",
            conversation_id=conversation_id,
            user_id=user_id,
            project=_project,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_ms=0.0,
            model=model,
            has_tool_calls=bool(tool_calls),
            tool_names=[tc.get("function", {}).get("name", "unknown") for tc in tool_calls] if tool_calls else None,
            error=error,
            gatekeeper_intent=_gk_intent,
        )
        state.chat_session_store.add_turn(turn)
        # Persistenza immediata su disco per non perdere messaggi in caso di crash
        state.chat_session_store.persist("./data/sessions.json")
    except Exception as e:
        logger.warning(f"SessionStore save error: {e}")


# ═══════════════════════════════════════════
# MCP Server v2
# ═══════════════════════════════════════════
# Il nuovo server MCP v2 è registrato più sotto (sezione MCP Server v2).
# I vecchi endpoint (/api/mcp/sse, /api/mcp/message, /api/mcp/remote)
# sono stati rimossi — tutto passa da /api/mcp/v2.
# ═══════════════════════════════════════════


@app.post("/api/chat")
@limiter.limit(API_RATE_LIMIT_DEFAULT)
async def chat(payload: ChatRequest, request: Request):
    state.total_requests += 1
    """Endpoint chat Jarvis nativo con LlamaEngine in locale."""
    from datetime import datetime, UTC
    
    body = payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()
    body["model"] = MODEL_ID
    
    raw_messages = body.get("messages", [])
    
    options = body.get("options") or {}
    # Propaga tool_choice dal body alle options per generate_chat
    if "tool_choice" in body:
        options["tool_choice"] = body["tool_choice"]
        body["options"] = options  # BAIT: body.get("options") era {} nuovo, rilega a body
    
    # Bypass per Mem0 internal queries o per Worker Offloading (skip_rag)
    is_internal = False
    if isinstance(options, dict) and options.get("skip_rag") is True:
        is_internal = True
    else:
        for m in raw_messages:
            if isinstance(m, dict) and m.get("role") == "user":
                txt = str(m.get("content", ""))
                if is_internal_query(txt):
                    is_internal = True
                    break
                
    # User from JWT (dashboard login) takes precedence over body.user_id
    jwt_user = await get_current_user(request)
    jwt_user_id = jwt_user["id"] if jwt_user else None
    current_user_id = jwt_user_id or body.get("user_id") or (options.get("user_id") if isinstance(options, dict) else None) or "alfio_dev"
    conversation_id = body.get("conversation_id") or request.headers.get("X-Conversation-Id")
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
    concise = isinstance(options, dict) and options.get("concise") is True
    provider = body.get("provider") or payload.provider

    # ── Confirmation token handling ──
    confirmation_mgr = None
    confirm_resp = await handle_confirmation_token(body, conversation_id=str(conversation_id))
    confirmation_mgr = None
    confirm_resp = await handle_confirmation_token(body, conversation_id=str(conversation_id))
    if confirm_resp is not None:
        return confirm_resp

    # ── Pipeline Telemetry ──
    request_id = str(uuid.uuid4())[:12] if not is_internal else None
    tracer = PipelineTracer.begin(user_message=raw_messages[-1]["content"][:200] if raw_messages else "", user_id=current_user_id) if not is_internal else None
    if tracer:
        tracer._conversation_id = str(conversation_id)

    gatekeeper_result = None  # inizializzato prima del conditional, aggiornato da build_omniscient_prompt

    # ── Global request timeout ──
    _PROMPT_TIMEOUT = 120   # max 2 min per contesto + compressione
    _LLM_TIMEOUT    = 300   # max 5 min per generazione LLM

    if not is_internal:
        tracer.start_step("build_omniscient_prompt")
        try:
            body["messages"], gatekeeper_result = await asyncio.wait_for(
                build_omniscient_prompt(
                    raw_messages, user_id=current_user_id,
                    conversation_id=str(conversation_id), concise=concise,
                    request_id=tracer.request_id,
                    user=jwt_user,
                ),
                timeout=_PROMPT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error(f"⏱️ build_omniscient_prompt timed out after {_PROMPT_TIMEOUT}s")
            if tracer:
                tracer.set_error("prompt_builder_timeout")
                tracer.finish()
            fallback_msg = "Mi dispiace, la preparazione del contesto ha richiesto troppo tempo. Prova con una domanda più specifica."
            return JSONResponse(status_code=200, content={
                "id": f"chatcmpl-{request_id or uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": MODEL_ID,
                "conversation_id": str(conversation_id),
                "choices": [{"index": 0, "message": {"role": "assistant", "content": fallback_msg}, "finish_reason": "stop"}],
            })
        tracer.end_step("build_omniscient_prompt")

    # ── Reasoning config: GatekeeperResult + ModelProfile → generation options ──
    _last_user_msg = ""
    for m in reversed(body.get("messages", [])):
        if isinstance(m, dict) and m.get("role") == "user":
            _last_user_msg = m.get("content", "")
            break
    if gatekeeper_result and _last_user_msg:
        _content_prompt, _chat_kwargs, _settings = configura_richiesta_agente(
            MODEL_PROFILE, gatekeeper_result, _last_user_msg,
        )
        # Merge chat_template_kwargs e logit_bias nelle options
        opts = body.get("options") or {}
        opts.setdefault("chat_template_kwargs", {}).update(_chat_kwargs)
        if _settings.get("logit_bias"):
            opts.setdefault("logit_bias", {}).update(_settings["logit_bias"])
        # Temperature / top_p override solo se la richiesta non specifica già
        if "temperature" not in opts and "temperature" in _settings:
            opts["temperature"] = _settings["temperature"]
        if "top_p" not in opts and "top_p" in _settings:
            opts["top_p"] = _settings["top_p"]
        if "repeat_penalty" not in opts and "repeat_penalty" in _settings:
            opts["repeat_penalty"] = _settings["repeat_penalty"]
        body["options"] = opts
        # Inject /no_think prefix nell'ultimo messaggio utente
        if _content_prompt and _content_prompt != _last_user_msg:
            msg_list = body.get("messages", [])
            for m in reversed(msg_list):
                if m.get("role") == "user":
                    m["content"] = _content_prompt
                    break

    is_stream = body.get("stream", True)
    
    if not is_stream:
        # Non-stream
        if tracer:
            tracer.start_step("gemma_generation")
        try:
            _opts_debug = body.get("options", {})
            logger.info(f"🔧 Calling generate_chat: tools={'set' if body.get('tools') else None} options_keys={list(_opts_debug.keys())} tool_choice={_opts_debug.get('tool_choice')!r}")
            response = await asyncio.wait_for(
                engine.generate_chat_with_router(
                    body["messages"], tools=body.get("tools"), options=body.get("options"),
                    stream=False, preferred_provider=provider
                ),
                timeout=_LLM_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error(f"⏱️ generate_chat timed out after {_LLM_TIMEOUT}s")
            if tracer:
                tracer.set_error("llm_timeout")
                tracer.finish()
            fallback_msg = "Mi dispiace, la generazione della risposta ha richiesto troppo tempo. Prova con una domanda più specifica."
            return JSONResponse(status_code=200, content={
                "id": f"chatcmpl-{request_id or uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": MODEL_ID,
                "conversation_id": str(conversation_id),
                "choices": [{"index": 0, "message": {"role": "assistant", "content": fallback_msg}, "finish_reason": "stop"}],
            })
        if "error" in response:
            if tracer:
                tracer.set_error(response["error"])
                tracer.finish()
            return JSONResponse(status_code=500, content={"error": response["error"]})
        
        usage = response.get("usage", {})
        state.total_prompt_tokens += usage.get("prompt_tokens", 0)
        state.total_completion_tokens += usage.get("completion_tokens", 0)
        
        if tracer:
            from core.telemetry import LlmCallRecord
            tracer.add_llm_call(LlmCallRecord(
                model="chat",
                step="gemma_generation",
                duration_ms=0,  # duration misurata da start_step/end_step
                tokens_prompt=usage.get("prompt_tokens", 0),
                tokens_completion=usage.get("completion_tokens", 0),
                temperature=options.get("temperature", 1.0),
            ))
            tracer.end_step("gemma_generation", details={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
            })
        
        # Risposta in formato OpenAI
        choice = response["choices"][0]
        chat_resp = {
            "id": f"chatcmpl-{request_id or uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": MODEL_ID,
            "conversation_id": str(conversation_id),
            "choices": [choice],
            "usage": response.get("usage", {}),
        }
        
        # Gestione Agentica per intercettare i tools non stream (iterazione)
        # Since we don't pass tools to llama-cpp-python (Qwen XML format conflict),
        # the model emits tool calls as XML in content. Parse them here.
        tool_calls = choice["message"].get("tool_calls", [])
        if not tool_calls:
            _raw_content = choice["message"].get("content", "")
            if _raw_content:
                try:
                    from core.llm_engine import parse_qwen_tool_calls
                    import re as _re
                    tool_calls = parse_qwen_tool_calls(_raw_content)
                    if tool_calls:
                        # Strip raw XML from content before passing to tool loop
                        choice["message"]["content"] = _re.sub(
                            r'<tool_call[^>]*>.*?</tool_call\s*>\s*', '',
                            _raw_content, flags=_re.DOTALL
                        ).strip()
                except ImportError:
                    pass
        if tool_calls:
            # Lazy init: confirmation_mgr solo se servono tool calls
            if confirmation_mgr is None:
                confirmation_mgr = ConfirmationManager.from_request(request_id=conversation_id)
            body["messages"].append(choice["message"])
            
            # T1+T2: Esecuzione tool IN PARALLELO (asyncio.gather)
            # Invece di for-sequenziale, eseguiamo tutti i tool contemporaneamente.
            # Il contenuto della prima risposta LLM è già preservato in
            # body["messages"] (linea sopra), quindi la seconda chiamata LLM
            # vede cosa il modello ha già "detto" e continua naturalmente.
            _first_content = choice["message"].get("content", "")
            
            async def _exec_one_tool(tc):
                _tool_name = tc.get("function", {}).get("name", "unknown")
                if tracer:
                    tracer.start_step("tool_execution")
                _res = await execute_tool_call(tc, confirmation_mgr=confirmation_mgr)
                if tracer:
                    tracer.end_step("tool_execution", details={"tool": _tool_name})
                    tracer.increment_tool_calls()
                return {"role": "tool", "content": _res, "name": _tool_name}
            
            _tool_msgs = await asyncio.gather(*[_exec_one_tool(tc) for tc in tool_calls])
            body["messages"].extend(_tool_msgs)
            
            # Ricorsione simulata per far generare la risposta finale dopo il tool
            # Rimuovi tool_choice forzato per non interferire con la risposta finale
            if isinstance(body.get("options"), dict):
                body["options"].pop("tool_choice", None)
            if tracer:
                tracer.start_step("gemma_generation_tool_final")
            response = await engine.generate_chat_with_router(
                body["messages"], tools=body.get("tools"), options=body.get("options"),
                stream=False, preferred_provider=provider
            )
            final_choice = response["choices"][0]
            chat_resp["choices"] = [final_choice]
            chat_resp["usage"] = response.get("usage", {})
            # T2: Se il primo contenuto era significativo e il finale non lo include,
            # prependiamolo per non perdere la continuità del discorso.
            _first_content = _first_content.strip() if _first_content else ""
            _final_content = final_choice["message"].get("content", "").strip() if final_choice["message"].get("content") else ""
            if _first_content and _final_content and _first_content not in _final_content:
                final_choice["message"]["content"] = _first_content + "\n\n" + _final_content
            if tracer:
                tracer.set_llm_response(final_choice["message"].get("content", ""))
                usage2 = response.get("usage", {})
                tracer.add_llm_call(LlmCallRecord(
                    model="chat",
                    step="gemma_generation_tool_final",
                    duration_ms=0,
                    tokens_prompt=usage2.get("prompt_tokens", 0),
                    tokens_completion=usage2.get("completion_tokens", 0),
                ))
                tracer.end_step("gemma_generation_tool_final")
        
        content = chat_resp["choices"][0]["message"].get("content", "")
        if tracer:
            tracer.set_llm_response(content)
            # Popola tracer con metadati risposta
            tracer._model_used = body["model"]
            tracer._is_streaming = False
            tracer._total_prompt_tokens = usage.get("prompt_tokens", 0)
            tracer._total_completion_tokens = usage.get("completion_tokens", 0)
            _tool_names_list = [tc.get("function", {}).get("name", "unknown") for tc in tool_calls] if tool_calls else None
            tracer._tool_names = _tool_names_list
            tracer._agentic_loop_depth = len(tool_calls) if tool_calls else 0
            tracer.finish()

        # Strip tag veloce (regex, senza handler) per la risposta immediata.
        # Il processaggio completo dei tag (MEMORY, SCHEDULE, SSH, ecc.) va in background
        # per non bloccare la risposta (può impiegare 15s+ con loopback Mem0).
        clean_content = strip_action_tags(content, model_family=MODEL_PROFILE.family) if content else ""
        chat_resp["choices"][0]["message"]["content"] = clean_content or content

        # Processa i tag in BACKGROUND per effetti collaterali
        if content:
            try:
                spawn_background(process_response_tags(content, user_id=current_user_id))
            except Exception as e:
                logger.warning(f"⚠️ Background tag processing error: {e}")

        # Salva turno nel ChatSessionStore (fire-and-forget)
        _save_session_turn(
            tracer=tracer, role="assistant", content=content,
            user_id=current_user_id, conversation_id=str(conversation_id),
            model=body["model"], prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            tool_calls=tool_calls if tool_calls else None,
            error=None,
        )
        # Salva anche il messaggio utente (primo turno)
        if raw_messages:
            _save_session_turn(
                tracer=tracer, role="user",
                content=raw_messages[-1].get("content", "") if isinstance(raw_messages[-1], dict) else str(raw_messages[-1]),
                user_id=current_user_id, conversation_id=str(conversation_id),
            )

        return JSONResponse(status_code=200, content=chat_resp)
        
    else:
        # Streaming
        async def stream_gen():
            nonlocal confirmation_mgr
            if tracer:
                tracer.start_step("gemma_generation_stream")
            _stream_start = time.monotonic()
            _first_token_recorded = False
            gen = await engine.generate_chat_with_router(body["messages"], tools=body.get("tools"), options=body.get("options"), stream=True, preferred_provider=provider)
            if isinstance(gen, dict) and "error" in gen:
                if tracer:
                    tracer.set_error(gen["error"])
                    tracer.finish()
                yield json.dumps({"error": gen["error"]}).encode() + b"\n"
                return

            # Wrap raw stream with genera_stream_agente per convertire thinking
            # tag (<think>...</think>) in HTML <details> per UI esterne
            parsed_stream = genera_stream_agente(gen, MODEL_PROFILE)

            # ── T1+T2: Streaming loop con tool-calling support ──
            # T2: Primo contenuto (se c'è) viene inviato subito al client,
            # prima che i tool vengano eseguiti. Questo riduce l'attesa percepita
            # da ~8s (doppia generazione completa) a ~1-3s (prima risposta visibile).
            # T1: Tutti i tool vengono eseguiti IN PARALLELO (asyncio.gather).
            full_chunks = []
            _role_sent_in_stream = False
            tool_calls_stream_acc = []  # chunks con delta.tool_calls
            tool_calls_detected = False
            # Stateful XML buffer for fragmented <tool_call> across tiny chunks (1-3 chars)
            _xml_buf = None       # None = not buffering, str = accumulating XML
            _delay_buf = []       # 3-chunk lookahead buffer to detect fragmented XML

            async for chunk in parsed_stream:
                if "choices" in chunk and len(chunk["choices"]) > 0:
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    finish_reason = chunk["choices"][0].get("finish_reason")

                    # T1: Raccogli tool_calls dai chunk streaming
                    tc_delta = delta.get("tool_calls")
                    if tc_delta:
                        tool_calls_stream_acc.append(chunk)
                        tool_calls_detected = True

                    # Misura TTFT al primo chunk con contenuto
                    if not _first_token_recorded and content:
                        _first_token_recorded = True
                        if tracer:
                            tracer._ttft_ms = (time.monotonic() - _stream_start) * 1000

                    # T2: Se finish_reason=tool_calls, interrompiamo il primo stream
                    # (il contenuto finora raccolto è già stato inviato al client)
                    if finish_reason == "tool_calls":
                        tool_calls_detected = True
                        if not tc_delta:
                            tool_calls_stream_acc.append(chunk)
                        break

                    if content:
                        full_chunks.append(content)

                        # ── Stateful XML tool-call buffer ──
                        # Qwen emits <tool_call>...JSON...</tool_call> across tiny streaming
                        # chunks (1-3 chars). A per-chunk regex can't match fragmented XML.
                        # Instead: 3-chunk lookahead detects <tool_call start, then we enter
                        # buffering mode. When </tool_call> arrives, we strip XML and yield
                        # the cleaned text. False alarms >200 chars are flushed back.
                        import re as _re_strip

                        if _xml_buf is not None:
                            # ── XML BUFFERING MODE: accumulate, don't yield ──
                            _xml_buf += content
                            if "</tool_call>" in _xml_buf:
                                # Complete XML block — strip and flush cleaned text
                                _clean = _re_strip.sub(
                                    r'<tool_call[^>]*>.*?</tool_call\s*>\s*',
                                    '', _xml_buf, flags=_re_strip.DOTALL
                                ).strip()
                                _xml_buf = None
                                if _clean:
                                    _stream_content = _clean
                                    delta_payload = {"role": "assistant", "content": _stream_content} if not _role_sent_in_stream else {"content": _stream_content}
                                    _role_sent_in_stream = True
                                    openai_chunk = {
                                        "id": f"chatcmpl-{request_id or uuid.uuid4().hex[:12]}",
                                        "object": "chat.completion.chunk",
                                        "created": int(time.time()),
                                        "model": MODEL_ID,
                                        "conversation_id": str(conversation_id),
                                        "choices": [{"index": 0, "delta": delta_payload, "finish_reason": None}]
                                    }
                                    yield json.dumps(openai_chunk).encode() + b"\n"
                            elif len(_xml_buf) > 200:
                                # False alarm — flush and resume normal mode
                                if _xml_buf.strip():
                                    logger.info(f"🌀 XML buffer false alarm ({len(_xml_buf)} chars), flushing")
                                    _stream_content = _xml_buf.strip()
                                    delta_payload = {"role": "assistant", "content": _stream_content} if not _role_sent_in_stream else {"content": _stream_content}
                                    _role_sent_in_stream = True
                                    openai_chunk = {
                                        "id": f"chatcmpl-{request_id or uuid.uuid4().hex[:12]}",
                                        "object": "chat.completion.chunk",
                                        "created": int(time.time()),
                                        "model": MODEL_ID,
                                        "conversation_id": str(conversation_id),
                                        "choices": [{"index": 0, "delta": delta_payload, "finish_reason": None}]
                                    }
                                    yield json.dumps(openai_chunk).encode() + b"\n"
                                _xml_buf = None
                            continue  # don't yield while buffering

                        # ── NORMAL MODE: 3-chunk lookahead for XML detection ──
                        _delay_buf.append(content)
                        _recent_text = "".join(_delay_buf)

                        if "<tool_call" in _recent_text:
                            # Detected XML start in lookahead buffer
                            _idx = _recent_text.find("<tool_call")
                            _pre_xml = _recent_text[:_idx]
                            if _pre_xml.strip():
                                # Yield any text that appeared BEFORE the XML tag
                                _stream_content = _pre_xml.strip()
                                delta_payload = {"role": "assistant", "content": _stream_content} if not _role_sent_in_stream else {"content": _stream_content}
                                _role_sent_in_stream = True
                                openai_chunk = {
                                    "id": f"chatcmpl-{request_id or uuid.uuid4().hex[:12]}",
                                    "object": "chat.completion.chunk",
                                    "created": int(time.time()),
                                    "model": MODEL_ID,
                                    "conversation_id": str(conversation_id),
                                    "choices": [{"index": 0, "delta": delta_payload, "finish_reason": None}]
                                }
                                yield json.dumps(openai_chunk).encode() + b"\n"
                            if "</tool_call>" in _recent_text:
                                # Complete XML in lookahead buffer — strip and yield text after
                                _after_xml = _re_strip.sub(
                                    r'<tool_call[^>]*>.*?</tool_call\s*>\s*',
                                    '', _recent_text[_idx:], flags=_re_strip.DOTALL
                                ).strip()
                                _delay_buf = []
                                if _after_xml:
                                    _stream_content = _after_xml
                                    delta_payload = {"role": "assistant", "content": _stream_content} if not _role_sent_in_stream else {"content": _stream_content}
                                    _role_sent_in_stream = True
                                    openai_chunk = {
                                        "id": f"chatcmpl-{request_id or uuid.uuid4().hex[:12]}",
                                        "object": "chat.completion.chunk",
                                        "created": int(time.time()),
                                        "model": MODEL_ID,
                                        "conversation_id": str(conversation_id),
                                        "choices": [{"index": 0, "delta": delta_payload, "finish_reason": None}]
                                    }
                                    yield json.dumps(openai_chunk).encode() + b"\n"
                                continue
                            # Incomplete XML — enter buffering mode
                            _xml_buf = _recent_text[_idx:]
                            _delay_buf = []
                            continue

                        # ── YIELD OLDEST CHUNK FROM DELAY BUFFER ──
                        if len(_delay_buf) >= 3:
                            _to_yield = _delay_buf.pop(0)
                            # Also apply regex for non-fragmented cases
                            _clean_content = _re_strip.sub(
                                r'<tool_call[^>]*>.*?</tool_call\s*>\s*',
                                '', _to_yield, flags=_re_strip.DOTALL
                            ).strip()
                            if _clean_content:
                                _stream_content = _clean_content
                                delta_payload = {"role": "assistant", "content": _stream_content} if not _role_sent_in_stream else {"content": _stream_content}
                                _role_sent_in_stream = True
                                openai_chunk = {
                                    "id": f"chatcmpl-{request_id or uuid.uuid4().hex[:12]}",
                                    "object": "chat.completion.chunk",
                                    "created": int(time.time()),
                                    "model": MODEL_ID,
                                    "conversation_id": str(conversation_id),
                                    "choices": [{"index": 0, "delta": delta_payload, "finish_reason": None}]
                                }
                                yield json.dumps(openai_chunk).encode() + b"\n"

            # ── Flush delay buffer (any content left after loop break) ──
            import re as _re_flush
            for _remaining in _delay_buf:
                _clean_rem = _re_flush.sub(
                    r'<tool_call[^>]*>.*?</tool_call\s*>\s*', '',
                    _remaining, flags=_re_flush.DOTALL
                ).strip()
                if _clean_rem:
                    delta_payload = {"role": "assistant", "content": _clean_rem} if not _role_sent_in_stream else {"content": _clean_rem}
                    _role_sent_in_stream = True
                    yield json.dumps({
                        "id": f"chatcmpl-{request_id or uuid.uuid4().hex[:12]}",
                        "object": "chat.completion.chunk", "created": int(time.time()),
                        "model": MODEL_ID, "conversation_id": str(conversation_id),
                        "choices": [{"index": 0, "delta": delta_payload, "finish_reason": None}]
                    }).encode() + b"\n"

            # ── T1+T2: Tool-calling handling ──
            # Since we don't pass tools to llama-cpp-python (Qwen XML format conflict),
            # the model emits tool calls as XML in content. Also check here.
            _xml_tools_from_content = None
            if not tool_calls_detected:
                _full_text_tc = "".join(full_chunks)
                if _full_text_tc:
                    try:
                        from core.llm_engine import parse_qwen_tool_calls
                        _xml_tools_from_content = parse_qwen_tool_calls(_full_text_tc)
                        if _xml_tools_from_content:
                            tool_calls_detected = True
                    except ImportError:
                        pass
            
            if tool_calls_detected:
                if _xml_tools_from_content:
                    tool_calls = _xml_tools_from_content
                else:
                    tool_calls = _reconstruct_tool_calls(tool_calls_stream_acc)
                first_text = "".join(full_chunks)
                # Strip raw XML from first_text for the assistant message content
                if _xml_tools_from_content:
                    import re as _re
                    first_text = _re.sub(
                        r'<tool_call[^>]*>.*?</tool_call\s*>\s*', '',
                        first_text, flags=_re.DOTALL
                    ).strip()

                # Costruisce assistant message con contenuto catturato + tool_calls
                # Il contenuto della prima risposta è preservato, quindi la seconda
                # chiamata LLM sa cosa il modello ha già "detto".
                assistant_msg = {"role": "assistant", "content": first_text}
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                body["messages"].append(assistant_msg)

                # T1: Esecuzione tool IN PARALLELO
                if confirmation_mgr is None:
                    confirmation_mgr = ConfirmationManager.from_request(request_id=conversation_id)

                async def _exec_one_tool(tc):
                    _res = await execute_tool_call(tc, confirmation_mgr=confirmation_mgr)
                    _tname = tc.get("function", {}).get("name", "unknown")
                    if tracer:
                        tracer.increment_tool_calls()
                    return {"role": "tool", "content": _res, "name": _tname}

                tool_msgs = await asyncio.gather(*[_exec_one_tool(tc) for tc in tool_calls])
                body["messages"].extend(tool_msgs)

                # Rimuovi tool_choice forzato per non interferire con la risposta finale
                if isinstance(body.get("options"), dict):
                    body["options"].pop("tool_choice", None)

                # Seconda chiamata LLM in streaming (continuazione dopo tool)
                if tracer:
                    tracer.start_step("gemma_generation_stream_tool_final")
                gen2 = await engine.generate_chat_with_router(
                    body["messages"], tools=body.get("tools"), options=body.get("options"),
                    stream=True, preferred_provider=provider
                )

                if not (isinstance(gen2, dict) and "error" in gen2):
                    parsed_stream2 = genera_stream_agente(gen2, MODEL_PROFILE)
                    async for chunk in parsed_stream2:
                        if "choices" in chunk and len(chunk["choices"]) > 0:
                            delta2 = chunk["choices"][0].get("delta", {})
                            content2 = delta2.get("content", "")
                            if content2:
                                full_chunks.append(content2)
                                t2_payload = {"content": content2} if _role_sent_in_stream else {"role": "assistant", "content": content2}
                                _role_sent_in_stream = True
                                t2_chunk = {
                                    "id": f"chatcmpl-{request_id or uuid.uuid4().hex[:12]}",
                                    "object": "chat.completion.chunk",
                                    "created": int(time.time()),
                                    "model": MODEL_ID,
                                    "conversation_id": str(conversation_id),
                                    "choices": [{
                                        "index": 0,
                                        "delta": t2_payload,
                                        "finish_reason": None
                                    }]
                                }
                                yield json.dumps(t2_chunk).encode() + b"\n"

                    if tracer:
                        tracer.end_step("gemma_generation_stream_tool_final")
                elif tracer:
                    tracer.end_step("gemma_generation_stream_tool_final", details={"error": gen2.get("error", "unknown")})

            full_text = "".join(full_chunks)

            if tracer:
                from core.telemetry import LlmCallRecord
                tracer.add_llm_call(LlmCallRecord(
                    model="chat",
                    step="gemma_generation_stream",
                    duration_ms=0,
                    tokens_prompt=0,
                    tokens_completion=0,
                ))
                tracer.end_step("gemma_generation_stream", details={"char_count": len(full_text)})

            # Invia SUBITO il messaggio done (con strip veloce regex, senza handler)
            # per non bloccare il client con process_response_tags (che può impiegare 15s+).
            clean_text = strip_action_tags(full_text, model_family=MODEL_PROFILE.family) if full_text else ""
            yield json.dumps({
                "id": f"chatcmpl-{request_id or uuid.uuid4().hex[:12]}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": MODEL_ID,
                "conversation_id": str(conversation_id),
                "choices": [{
                    "index": 0,
                    "delta": {"content": clean_text or full_text},
                    "finish_reason": "stop"
                }]
            }).encode() + b"\n"

            # Processa i tag in BACKGROUND per effetti collaterali (MEMORY, SCHEDULE, SSH, ecc.)
            # Non blocca la risposta — il client ha già ricevuto done=true.
            if full_text:
                try:
                    spawn_background(process_response_tags(full_text, user_id=current_user_id))
                except Exception as e:
                    logger.warning(f"⚠️ Background tag processing error: {e}")

            if tracer:
                tracer.set_llm_response(full_text)
                # Popola tracer con metadati risposta streaming
                tracer._model_used = body["model"]
                tracer._is_streaming = True
                # Calcola generation_speed_tok_s se abbiamo TTFT e durata totale
                _gen_duration = (time.monotonic() - _stream_start) * 1000
                if _gen_duration > 0 and len(full_text) > 0:
                    # Stima tok/s: ~4 char per token in media
                    tracer._generation_speed_tok_s = (len(full_text) / 4) / (_gen_duration / 1000)
                tracer.finish()

            # Salva turno nel ChatSessionStore (fire-and-forget)
            _save_session_turn(
                tracer=tracer, role="assistant", content=full_text,
                user_id=current_user_id, conversation_id=str(conversation_id),
                model=body["model"],
            )
            if raw_messages:
                _save_session_turn(
                    tracer=tracer, role="user",
                    content=raw_messages[-1].get("content", "") if isinstance(raw_messages[-1], dict) else str(raw_messages[-1]),
                    user_id=current_user_id, conversation_id=str(conversation_id),
                )

        return StreamingResponse(stream_gen(), media_type="application/x-ndjson")

@app.post("/api/generate")
@limiter.limit(API_RATE_LIMIT_DEFAULT)
async def ollama_generate(payload: GenerateRequest, request: Request):
    state.total_requests += 1
    """Endpoint generate Ollama simulato con iniezione RAG."""
    from datetime import datetime, UTC
    body = payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()
    body["model"] = MODEL_ID
    prompt = body.get("prompt", "")
    is_stream = body.get("stream", True)
    
    if state.memory and prompt and len(prompt) < 500:
        # Recuperiamo un eventuale user_id dalle options (se passato dal client)
        options = body.get("options", {})
        jwt_user = await get_current_user(request)
        jwt_user_id = jwt_user["id"] if jwt_user else None
        current_user_id = jwt_user_id or (options.get("user_id", "alfio_dev") if isinstance(options, dict) else "alfio_dev")
        
        try:
            loop = asyncio.get_running_loop()
            from functools import partial
            search_func = partial(state.memory.search, query=prompt, filters={"user_id": current_user_id}, limit=2)
            mem_res = await loop.run_in_executor(state.mem0_executor, search_func)
            mem_ctx = extract_memories(mem_res)
            rag_ctx = await search_documents(prompt)
            if mem_ctx or rag_ctx:
                full_ctx = ' | '.join(filter(None, [mem_ctx, rag_ctx]))
                if len(full_ctx) > 12000:
                    full_ctx = full_ctx[:12000] + "...[Troncato]"
                prompt = f" [Contesto -> {full_ctx}] " + prompt
        except Exception as e:
            pass

    # Controllo Cache Semantica
    cached_resp = await semantic_cache_search(prompt)
    if cached_resp:
        if is_stream:
            async def cache_stream():
                yield json.dumps({"model": body["model"], "response": cached_resp, "done": True}).encode() + b"\n"
            return StreamingResponse(cache_stream(), media_type="application/x-ndjson")
        else:
            return JSONResponse({"model": body["model"], "response": cached_resp, "done": True})

    # Usiamo il modello chat convertendo in message format
    messages = [{"role": "user", "content": prompt}]
    
    if not is_stream:
        opts = body.get("options", {})
        if not isinstance(opts, dict): opts = {}
        response = await engine.generate_chat(messages, options=opts, stream=False)
        if "error" in response:
            return JSONResponse(status_code=500, content={"error": response["error"]})
        
        state.total_prompt_tokens += response.get("usage", {}).get("prompt_tokens", 0)
        state.total_completion_tokens += response.get("usage", {}).get("completion_tokens", 0)
        
        content = extract_content(response)
        
        # Strip tag veloce (regex, senza handler) per risposta immediata
        clean_resp = strip_action_tags(content, model_family=MODEL_PROFILE.family) if content else ""

        # Processa i tag in BACKGROUND per effetti collaterali
        if content:
            spawn_background(process_response_tags(content, user_id=current_user_id))
        try:
            spawn_background(semantic_cache_store(prompt, content))
        except Exception as e:
            logger.warning(f"⚠️ Background semantic cache error: {e}")
        
        # Salva prompt utente in memoria (endpoint generate non usa build_omniscient_prompt)
        try:
            spawn_background(save_to_memory(prompt, user_id=current_user_id))
        except Exception as e:
            logger.warning(f"⚠️ Background save_to_memory error: {e}")
        
        return JSONResponse(status_code=200, content={
            "model": body["model"],
            "created_at": datetime.now(UTC).isoformat() + "Z",
            "response": clean_resp or content,
            "done": True
        })
    else:
        async def stream_gen():
            full_resp = []
            gen = await engine.generate_chat(messages, stream=True)
            parsed_stream = genera_stream_agente(gen, MODEL_PROFILE)
            async for chunk in parsed_stream:
                if "choices" in chunk and len(chunk["choices"]) > 0:
                    content = chunk["choices"][0].get("delta", {}).get("content", "")
                    full_resp.append(content)

                    yield json.dumps({
                        "model": body["model"],
                        "created_at": datetime.now(UTC).isoformat() + "Z",
                        "response": content,
                        "done": False
                    }).encode() + b"\n"

            final_content = "".join(full_resp)

            # Salva prompt utente in background
            try:
                spawn_background(save_to_memory(prompt, user_id=current_user_id))
            except Exception as e:
                logger.warning(f"⚠️ Background save_to_memory error: {e}")

            # Strip tag veloce (regex, senza handler) per risposta immediata
            clean_resp = strip_action_tags(final_content, model_family=MODEL_PROFILE.family) if final_content else ""

            yield json.dumps({
                "model": body["model"],
                "created_at": datetime.now(UTC).isoformat() + "Z",
                "response": clean_resp or final_content,
                "done": True
            }).encode() + b"\n"

            # Processa i tag in BACKGROUND per effetti collaterali (MEMORY, SCHEDULE, SSH, ecc.)
            if final_content:
                try:
                    spawn_background(process_response_tags(final_content, user_id=current_user_id))
                except Exception as e:
                    logger.warning(f"⚠️ Background tag processing error: {e}")
                try:
                    spawn_background(semantic_cache_store(prompt, final_content))
                except Exception as e:
                    logger.warning(f"⚠️ Background semantic cache error: {e}")

        return StreamingResponse(stream_gen(), media_type="application/x-ndjson")

@app.post("/api/embeddings")
@limiter.limit(API_RATE_LIMIT_EMBED, key_func=_rate_limit_key_localhost)
async def ollama_embeddings(request: Request):
    """Endpoint simulato per Embeddings (usato da mem0 o esterni, legacy)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    
    prompt = body.get("prompt", "")
    if isinstance(prompt, str):
        prompt = [prompt]
        
    embeddings = await engine.get_embeddings(prompt, priority=0)
    if "error" in embeddings:
        return JSONResponse(status_code=500, content={"error": embeddings["error"]})
        
    data = embeddings.get("data", [{}])
    if len(data) == 0:
        return JSONResponse(status_code=500, content={"error": "Nessun embedding generato"})
        
    # Ollama restituisce {"embedding": [...]} per singola stringa (legacy)
    return JSONResponse(status_code=200, content={"embedding": data[0].get("embedding", [])})

@app.post("/api/embed")
@limiter.limit(API_RATE_LIMIT_EMBED, key_func=_rate_limit_key_localhost)
async def ollama_embed_batch(request: Request):
    """Endpoint simulato per Embeddings in batch (usato da mem0 o esterni)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
        
    inputs = body.get("input", [])
    if isinstance(inputs, str):
        inputs = [inputs]
        
    embeddings = await engine.get_embeddings(inputs, priority=0)
    if "error" in embeddings:
        return JSONResponse(status_code=500, content={"error": embeddings["error"]})
        
    data = embeddings.get("data", [])
    result = [d.get("embedding", []) for d in data]
    
    # Ollama restituisce {"embeddings": [[...], [...]]}
    return JSONResponse(status_code=200, content={"embeddings": result})


# ==============================================================================
# OpenAI-compatible endpoints via APIRouter
# ==============================================================================
app.include_router(openai_router)
# ═══════════════════════════════════════════
# MCP Server v2 — Streamable HTTP (route FastAPI diretta)
# ═══════════════════════════════════════════
# Implementazione conforme MCP Streamable HTTP (RFC 2025-11-25)
# su route FastAPI diretta, senza sub-app Starlette montate
# (evita problemi di lifespan con Granian).
#
# Endpoint: POST /api/mcp/v2 → JSON-RPC request/response
# ═══════════════════════════════════════════

try:
    from api.mcp.server_v2 import handle_mcp_post

    @app.post("/api/mcp/v2")
    async def mcp_v2(request: Request):
        try:
            body = await request.json()
        except Exception:
            from starlette.responses import JSONResponse as _JR
            return _JR({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}}, status_code=400)

        resp = await handle_mcp_post(body) if hasattr(handle_mcp_post, '__call__') else handle_mcp_post(body)

        # Se è una notifica, rispondi 202 Accepted
        if not resp or resp == {}:
            from starlette.responses import Response as _Resp
            return _Resp(status_code=202)

        from starlette.responses import JSONResponse as _JR2
        return _JR2(resp)

    logger.info("MCP Server v2 su /api/mcp/v2 (Streamable HTTP)")
except Exception as exc:
    logger.warning(f"MCP Server v2 non disponibile — installa 'mcp' package: pip install mcp ({exc})")


# ═══════════════════════════════════════════
# Synaptiq CodeGraph API Routes
# ═══════════════════════════════════════════

if SYNAPTIQ_ENABLED:
    from graph.synaptiq_engine import synaptiq_engine

    @app.get("/api/synaptiq/status")
    async def synaptiq_status():
        """Stato del motore Synaptiq (grafo strutturale)."""
        try:
            s = await synaptiq_engine.status()
            return s
        except Exception as e:
            return {"initialized": False, "error": str(e)}

    @app.post("/api/synaptiq/analyze")
    async def synaptiq_analyze(request: Request):
        """Avvia analisi strutturale di un repository.

        Body: {"path": "/percorso/repo", "full_rebuild": false}
        """
        body = await request.json()
        repo_path = body.get("path", "")
        full_rebuild = body.get("full_rebuild", False)
        if not repo_path:
            return JSONResponse({"error": "path required"}, status_code=400)
        try:
            result = await synaptiq_engine.analyze(repo_path, full_rebuild=full_rebuild)
            return result
        except FileNotFoundError:
            return JSONResponse({"error": f"Percorso non trovato: {repo_path}"}, status_code=404)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/synaptiq/search")
    async def synaptiq_search(q: str = "", limit: int = 10):
        """Ricerca ibrida nel grafo strutturale (FTS + fuzzy + esatta)."""
        if not q:
            return {"results": []}
        results = await synaptiq_engine.hybrid_search(q, limit=limit)
        return {"results": results, "count": len(results)}

    @app.get("/api/synaptiq/symbol")
    async def synaptiq_symbol(name: str = ""):
        """Contesto completo di un simbolo (callers, callees, type refs)."""
        if not name:
            return {"error": "name required"}, 400
        ctx = await synaptiq_engine.get_symbol_context(name)
        return ctx

    @app.get("/api/synaptiq/traverse")
    async def synaptiq_traverse(symbol: str = "", depth: int = 3, direction: str = "callers"):
        """BFS traversal multi-hop."""
        if not symbol:
            return {"error": "symbol required"}, 400
        results = await synaptiq_engine.traverse(symbol, depth=depth, direction=direction)
        return {"symbol": symbol, "nodes": results, "count": len(results)}

    @app.get("/api/synaptiq/impact")
    async def synaptiq_impact(symbol: str = "", depth: int = 3):
        """Analisi blast radius."""
        if not symbol:
            return {"error": "symbol required"}, 400
        impact = await synaptiq_engine.get_impact(symbol, depth=depth)
        return impact

    @app.get("/api/synaptiq/dead-code")
    async def synaptiq_dead_code():
        """Elenco simboli non chiamati (candidati dead code)."""
        dead = await synaptiq_engine.get_dead_code()
        return {"dead_code": dead, "count": len(dead)}

    @app.get("/api/synaptiq/communities")
    async def synaptiq_communities():
        """Info sulle comunità nel grafo."""
        communities = await synaptiq_engine.get_communities()
        return {"communities": communities}

    logger.info("🧬 Route Synaptiq registrate: /api/synaptiq/*")
else:
    logger.info("🧬 Route Synaptiq non registrate (motore non disponibile)")
