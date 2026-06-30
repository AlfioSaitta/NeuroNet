"""
Collateral Studios Agent v8.6.7 — Entry point dell'applicazione.
App FastAPI, lifespan e tutti gli endpoint HTTP.
"""

import os
import json
import time
import asyncio
import io
import tempfile
import warnings
import sys
import traceback
import threading
from contextlib import asynccontextmanager

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

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import VectorParams, Distance

from config import (
    logger, MODEL_ID, QDRANT_HOST,
    DOC_COLLECTION, DOC_DIR, HOST_FS_PREFIX,
    TELEGRAM_TOKEN, ALLOWED_USERS, MEM0_CONFIG, STATE_FILE,
    TELEGRAM_ENABLED, WATCHDOG_ENABLED, WATCHDOG_TIMEOUT, WATCHDOG_WATCH_MODE,
    VECTOR_DB_VERSION,
    API_RATE_LIMIT_DEFAULT, API_RATE_LIMIT_HEAVY, API_RATE_LIMIT_EMBED, EMBEDDING_DIMS, EXTERNAL_PROJECTS,
    WORKSPACE_DIR, WORKSPACE_PROJECTS,
    MCP_ENABLED, MCP_AUTO_INIT
)
import state
from rag import ingest_local_documents, rag_queue_worker, generate_project_tree, search_documents
from rag_cache import semantic_cache_search, semantic_cache_store
from memory import init_mem0_delayed, extract_memories, save_to_memory, process_response_tags, reindex_graph_connections
from tag_processor import strip_action_tags, TagSafeStream
from prompt_builder import build_omniscient_prompt
from llm_engine import engine, extract_content
from agent_tools import TOOLS_SCHEMA, execute_tool_call
from confirmation_manager import ApiTokenProvider, PendingConfirmation, ConfirmationManager
from classificatore import is_internal_query, classify_confirmation
from openai import router as openai_router, init_openai_routes
init_openai_routes()  # populate the router with all endpoint sub-modules

if WATCHDOG_ENABLED:
    from watchdog.observers.polling import PollingObserver as Observer
    from rag import DynamicRagEventHandler

if TELEGRAM_ENABLED:
    from telegram import BotCommand, Update
    from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler, TypeHandler
    from telegram.request import BaseRequest, HTTPXRequest
    from telegram_bot import telegram_start, handle_telegram_message, telegram_callback_handler, auth_middleware

try:
    from telegram_userbot_manager import auto_start_existing, stop_all_userbots
except ImportError:
    pass

observer = None


# ==============================================================================
# LIFESPAN (Startup / Shutdown)
# ==============================================================================

import re

async def cleanup_old_collections():
    """Rimuove automaticamente le vecchie collezioni Qdrant non più utilizzate (migrazioni precedenti e legacy)."""
    try:
        cols_response = await state.qdrant.get_collections()
        col_names = [c.name for c in cols_response.collections]
        current_v = VECTOR_DB_VERSION.replace('v', '')
        
        legacy_exact = ["collateral_documents", "collateral_memories", "collateral_memories_entities", "semantic_cache"]
        
        for name in col_names:
            delete_it = False
            
            # Match legacy esatti
            if name in legacy_exact:
                delete_it = True
            
            # Match regex per trovare versioni vecchie o legacy senza suffisso versione
            elif name.startswith("collateral_docs_") or name.startswith("collateral_memories_") or name.startswith("semantic_cache_"):
                # Cerca il suffisso "_vX" alla fine
                match = re.search(r'_v(\d+)(_entities)?$', name)
                if match:
                    version = match.group(1)
                    if version != current_v:
                        delete_it = True
                else:
                    # Non ha il suffisso _vX alla fine, è una legacy
                    if name != "collateral_memories_entities": # Già gestito in legacy_exact, ma per sicurezza
                        delete_it = True
            
            if delete_it:
                logger.info(f"🗑️ Eliminazione collezione obsoleta: {name}")
                await state.qdrant.delete_collection(collection_name=name)
                
    except Exception as e:
        logger.warning(f"Errore durante la pulizia delle vecchie collezioni: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global observer

    logger.info("Avvio caricamento modelli Llama-cpp (Qwen + Nomic)...")
    await asyncio.to_thread(engine.load_models)
    logger.info("Modelli Llama caricati in locale (No Ollama).")

    # Inizializzazione provider esterni (Gemini, ecc.)
    try:
        router = engine.init_provider_router()
        if router:
            providers = router.get_available_providers()
            if providers:
                logger.info(f"☁️ Provider esterni disponibili: {', '.join(providers)}")
            else:
                logger.info("☁️ Nessun provider esterno configurato (GEMINI_API_KEY non impostata)")
    except Exception as e:
        logger.warning(f"ProviderRouter: errore inizializzazione: {e}")

    state.http_client = httpx.AsyncClient(timeout=300.0)
    if QDRANT_HOST == "local":
        state.qdrant = AsyncQdrantClient(path="./data/qdrant_local")
        logger.info("[SYSTEM] Qdrant inizializzato in modalità LOCALE (in-process).")
    else:
        state.qdrant = AsyncQdrantClient(host=QDRANT_HOST, port=6333)
        logger.info(f"[SYSTEM] Qdrant inizializzato in modalità HTTP (host: {QDRANT_HOST}).")
    
    # Pulizia automatica delle vecchie migrazioni Qdrant
    await cleanup_old_collections()

    try:
        await state.qdrant.create_collection(
            collection_name=f"semantic_cache_{VECTOR_DB_VERSION}",
            vectors_config=VectorParams(size=EMBEDDING_DIMS, distance=Distance.COSINE)
        )
        logger.info(f"[SYSTEM] Collezione semantic_cache_{VECTOR_DB_VERSION} creata con successo.")
    except Exception as e:
        # Ignore error if collection already exists
        if "already exists" not in str(e).lower() and "409" not in str(e):
            logger.warning(f"Errore silenziato in create_collection: {e}")

    # ==========================================================================
    # PULIZIA SYMLINK IN DOC_DIR
    # ==========================================================================
    # I symlink in DOC_DIR non sono più creati: con followlinks=False in tutti
    # gli os.walk di rag.py, il RAG e il project tree usano EXTERNAL_PROJECTS
    # tramite percorso diretto. I symlink facevano sì che il PollingObserver
    # del watchdog (che segue sempre i symlink) ricadesse in 119k file esterni
    # ogni secondo, consumando ~56% CPU.
    if os.path.exists(DOC_DIR):
        for item in os.listdir(DOC_DIR):
            item_path = os.path.join(DOC_DIR, item)
            if os.path.islink(item_path):
                os.remove(item_path)
    # ==========================================================================

    # Avvio asincrono di Mem0 (con ritardo per il loopback proxy)
    task_mem0 = asyncio.create_task(init_mem0_delayed())
    state.background_tasks.add(task_mem0)
    task_mem0.add_done_callback(state.background_tasks.discard)

    # Ingestion iniziale documenti — ATTENDE il completamento del warmup Mem0
    # (spaCy/BM25 lazy init) prima di processare workspace e aggiornare nodi.
    # Questo evita race condition in cui RAG scrive chunk prima che Mem0 sia pronto.
    async def _ingest_after_mem0():
        await task_mem0
        await ingest_local_documents()

    task_ingest = asyncio.create_task(_ingest_after_mem0())
    state.background_tasks.add(task_ingest)
    task_ingest.add_done_callback(state.background_tasks.discard)

    # Watchdog filesystem (PollingObserver per compatibilità Docker bind mount / symlink)
    # Nota: usiamo PollingObserver (non Observer/inotify) perché inotify:
    #   - Non segue i symlink dentro DOC_DIR
    #   - Non si propaga in modo affidabile attraverso i bind mount Docker (HOST_FS_PREFIX)
    # PollingObserver periodicamente esegue os.stat() sui file — funziona sempre.
    if WATCHDOG_ENABLED:
        worker_task = asyncio.create_task(rag_queue_worker())
        state.background_tasks.add(worker_task)
        observer = Observer(timeout=WATCHDOG_TIMEOUT)
        
        # Watch #1: DOC_DIR (legacy — skip se non esiste)
        if os.path.isdir(DOC_DIR):
            handler_doc = DynamicRagEventHandler(asyncio.get_running_loop(), state.file_event_queue, DOC_DIR)
            observer.schedule(handler_doc, DOC_DIR, recursive=True)
            logger.info(f"👀 Watchdog DOC_DIR: {DOC_DIR}")
        else:
            logger.warning(f"⚠️ DOC_DIR non trovato ({DOC_DIR}), watchdog su DOC_DIR saltato.")
        
        # Watch #2: per-project (default) o full WORKSPACE_DIR
        if WORKSPACE_DIR and os.path.isdir(WORKSPACE_DIR):
            if WATCHDOG_WATCH_MODE == "per_project":
                for proj_dir in WORKSPACE_PROJECTS:
                    if os.path.isdir(proj_dir):
                        proj_handler = DynamicRagEventHandler(asyncio.get_running_loop(), state.file_event_queue, proj_dir)
                        observer.schedule(proj_handler, proj_dir, recursive=True)
                        proj_name = os.path.basename(proj_dir)
                        logger.info(f"👀 Watchdog progetto: {proj_name} ({proj_dir})")
            else:
                handler_ws = DynamicRagEventHandler(asyncio.get_running_loop(), state.file_event_queue, WORKSPACE_DIR)
                observer.schedule(handler_ws, WORKSPACE_DIR, recursive=True)
                logger.info(f"👀 Watchdog WORKSPACE_DIR: {WORKSPACE_DIR}")
                
        observer.start()
        logger.info(f"👀 Watchdog PollingObserver Partito (timeout={WATCHDOG_TIMEOUT}s, mode={WATCHDOG_WATCH_MODE}).")

        # Health monitor watchdog: verifica ogni 60s che i thread observer siano vivi
        async def watchdog_health():
            global observer
            while True:
                await asyncio.sleep(60)
                try:
                    emitters = getattr(observer, '_emitters', [])
                    emitter_alive = any(e.is_alive() for e in emitters)
                    dispatch_alive = observer.is_alive()
                    qsize = state.file_event_queue.qsize()
                    if not emitter_alive or not dispatch_alive:
                        logger.warning(f"Watchdog: emitter={emitter_alive} dispatch={dispatch_alive} coda={qsize} — riavvio...")
                        observer.stop()
                        observer.join(timeout=5)
                        observer = Observer(timeout=WATCHDOG_TIMEOUT)
                        if os.path.isdir(DOC_DIR):
                            new_handler_doc = DynamicRagEventHandler(asyncio.get_running_loop(), state.file_event_queue, DOC_DIR)
                            observer.schedule(new_handler_doc, DOC_DIR, recursive=True)
                        if WORKSPACE_DIR and os.path.isdir(WORKSPACE_DIR):
                            if WATCHDOG_WATCH_MODE == "per_project":
                                for proj_dir in WORKSPACE_PROJECTS:
                                    if os.path.isdir(proj_dir):
                                        proj_handler = DynamicRagEventHandler(asyncio.get_running_loop(), state.file_event_queue, proj_dir)
                                        observer.schedule(proj_handler, proj_dir, recursive=True)
                            else:
                                new_handler_ws = DynamicRagEventHandler(asyncio.get_running_loop(), state.file_event_queue, WORKSPACE_DIR)
                                observer.schedule(new_handler_ws, WORKSPACE_DIR, recursive=True)
                        observer.start()
                        logger.info("Watchdog: nuovo Observer avviato dopo crash.")
                    elif qsize > 100:
                        logger.warning(f"Watchdog: coda eventi {qsize}, possibile blocco worker")
                except Exception as e:
                    logger.error(f"Watchdog health check error: {e}", exc_info=True)
        health_task = asyncio.create_task(watchdog_health())
        state.background_tasks.add(health_task)
        health_task.add_done_callback(state.background_tasks.discard)

    # Bot Telegram
    if TELEGRAM_ENABLED and TELEGRAM_TOKEN and ALLOWED_USERS:
        try:
            # HTTPXRequest con retry automatico su OSError (DNS/network sporadici)
            # PTB 21.x: _request_wrapper è un metodo, non un attributo → subclassing invece di monkey-patch
            from telegram.error import BadRequest, NetworkError

            class _RetryHTTPXRequest(HTTPXRequest):
                """HTTPXRequest con retry 5x su errori di rete (DNS, timeout, OSError)."""

                async def _request_wrapper(self, url, method, **kw):
                    for _attempt in range(5):
                        try:
                            return await super()._request_wrapper(url, method, **kw)
                        except (OSError, NetworkError) as _e:
                            if isinstance(_e, BadRequest):
                                raise
                            if _attempt < 4:
                                logger.warning(
                                    f"DNS/Network error su Telegram API, retry {_attempt+2}/5: {_e}"
                                )
                                await asyncio.sleep(2 ** _attempt + 0.5 * _attempt)
                            else:
                                raise

            _base_req = _RetryHTTPXRequest(
                read_timeout=120.0,
                write_timeout=120.0,
                connect_timeout=60.0,
                pool_timeout=60.0,
                connection_pool_size=50,
            )
            logger.info("📡 Telegram HTTP client con retry DNS (5 tentativi) attivo")

            state.telegram_app = (
                ApplicationBuilder()
                .token(TELEGRAM_TOKEN)
                .request(_base_req)
                .build()
            )
            # Middleware di sicurezza per bloccare utenti non autorizzati su tutti gli handler
            state.telegram_app.add_handler(TypeHandler(Update, auth_middleware), group=-1)

            state.telegram_app.add_handler(CommandHandler("start", telegram_start))
            state.telegram_app.add_handler(CallbackQueryHandler(telegram_callback_handler))
            state.telegram_app.add_handler(MessageHandler((filters.TEXT | filters.VOICE | filters.AUDIO | filters.Document.ALL) & (~filters.COMMAND), handle_telegram_message))

            await state.telegram_app.initialize()
            
            # Setup Menu Bot (Ora limitato a /start poiché usiamo la tastiera reply)
            await state.telegram_app.bot.set_my_commands([
                BotCommand("start", "Mostra il menu principale a pulsanti")
            ])

            await state.telegram_app.start()
            await state.telegram_app.updater.start_polling(drop_pending_updates=True)
            logger.info("📱 Bot Telegram avviato all'interno del Proxy.")
        except Exception as e:
            logger.error(f"⚠️ Impossibile avviare Telegram: {e}")
    else:
        logger.info("📱 Bot Telegram disabilitato (Manca Token o Utenti Autorizzati).")

    # Avvio Multi-Userbot MTProto
    try:
        task_userbots = asyncio.create_task(auto_start_existing())
        state.background_tasks.add(task_userbots)
        task_userbots.add_done_callback(state.background_tasks.discard)
    except NameError:
        pass

    # Avvio Scheduler
    try:
        from cron_agent import init_scheduler
        init_scheduler()
    except Exception as e:
        logger.error(f"Errore inizializzazione cron scheduler: {e}\n{traceback.format_exc()}")

    # ═══════════════════════════════════════════
    # MCP (Model Context Protocol) Initialization
    # ═══════════════════════════════════════════
    if MCP_ENABLED and MCP_AUTO_INIT:
        try:
            from mcp_client import init_mcp_from_config, get_mcp_manager
            # Scan default config paths (.mcp.json, etc.)
            total = await init_mcp_from_config()
            if total > 0:
                logger.info(f"🔌 MCP: {total} servers initialized from config files")

                # Register skill-embedded MCP servers
                if MCP_ENABLED:
                    try:
                        from skills_manager import register_skill_mcp_servers
                        reg_count = register_skill_mcp_servers()
                        if reg_count > 0:
                            logger.info(f"🔌 MCP: {reg_count} skill-embedded servers registered")
                            # Re-init to pick up new servers
                            await get_mcp_manager().initialize_all()
                    except ImportError:
                        pass

                # Refresh MCP tools in TOOLS_SCHEMA
                from agent_tools import refresh_mcp_tools_async
                mcp_count = await refresh_mcp_tools_async()
                if mcp_count > 0:
                    logger.info(f"🔌 MCP: {mcp_count} tools injected into TOOLS_SCHEMA")

                # Log all registered servers
                mgr = get_mcp_manager()
                for srv_name in mgr.list_servers():
                    logger.info(f"  ├─ MCP Server: {srv_name}")

        except ImportError as e:
            logger.debug(f"MCP client not available (non-critical): {e}")
        except Exception as e:
            logger.warning(f"MCP initialization: {e}")

    yield

    # Shutdown
    if observer:
        observer.stop()
        observer.join()
    for t in list(state.background_tasks):
        t.cancel()

    if state.telegram_app:
        await state.telegram_app.updater.stop()
        await state.telegram_app.stop()
        await state.telegram_app.shutdown()

    try:
        await stop_all_userbots()
    except NameError:
        pass

    # MCP shutdown
    if MCP_ENABLED:
        try:
            from mcp_client import get_mcp_manager
            mgr = get_mcp_manager()
            await mgr.close_all()
            logger.info("🔌 MCP: all servers shut down")
        except ImportError:
            pass

    await state.qdrant.close()
    await state.http_client.aclose()


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

from dashboard import dashboard_router
app.include_router(dashboard_router)


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
    task = asyncio.create_task(ingest_local_documents())
    state.background_tasks.add(task)
    task.add_done_callback(state.background_tasks.discard)
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
    from config import GIT_WEBHOOK_SECRET
    
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

    task = asyncio.create_task(run_git_pull())
    state.background_tasks.add(task)
    task.add_done_callback(state.background_tasks.discard)
    
    return JSONResponse({"status": "success", "message": "Git pull avviato, l'ingestion partirà a breve."})


@app.post("/api/chat")
@limiter.limit(API_RATE_LIMIT_DEFAULT)
async def ollama_chat(payload: ChatRequest, request: Request):
    state.total_requests += 1
    """Endpoint chat Ollama-nativa simulata con LlamaEngine in locale."""
    from datetime import datetime, UTC
    
    body = payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()
    body["model"] = MODEL_ID
    
    raw_messages = body.get("messages", [])
    
    options = body.get("options", {})
    
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
                
    current_user_id = body.get("user_id") or (options.get("user_id") if isinstance(options, dict) else None) or "alfio_dev"
    conversation_id = body.get("conversation_id") or request.headers.get("X-Conversation-Id", "default")
    concise = isinstance(options, dict) and options.get("concise") is True
    provider = body.get("provider") or payload.provider

    # ── Confirmation token handling ──
    confirmation_mgr = None
    confirmation_token = body.get("confirmation_token") or payload.confirmation_token
    if confirmation_token:
        resolved = ApiTokenProvider.resolve(confirmation_token, approved=True)
        if resolved:
            return JSONResponse(status_code=200, content={
                "model": body["model"],
                "message": {"role": "assistant", "content": "✅ Conferma ricevuta. Operazione autorizzata."},
                "done": True
            })
    elif raw_messages:
        last_msg = raw_messages[-1] if isinstance(raw_messages[-1], dict) else {}
        if last_msg.get("role") == "user":
            msg_text = str(last_msg.get("content", ""))
            result = classify_confirmation(msg_text)
            if result:
                token, approved = result
                api_resolved = ApiTokenProvider.resolve(token, approved=approved)
                if api_resolved:
                    return JSONResponse(status_code=200, content={
                        "model": body["model"],
                        "message": {"role": "assistant", "content": "✅ Conferma ricevuta. Operazione autorizzata."},
                        "done": True
                    })
                else:
                    return JSONResponse(status_code=200, content={
                        "model": body["model"],
                        "message": {"role": "assistant", "content": "⚠️ Token di conferma non valido o scaduto."},
                        "done": True
                    })
        # Lazy: ConfirmationManager creato solo quando servono tool calls

    if not is_internal:
        body["messages"] = await build_omniscient_prompt(
            raw_messages, user_id=current_user_id,
            conversation_id=str(conversation_id), concise=concise
        )
    
    is_stream = body.get("stream", True)
    
    if not is_stream:
        # Non-stream
        response = await engine.generate_chat_with_router(
            body["messages"], tools=body.get("tools"), options=body.get("options"),
            stream=False, preferred_provider=provider
        )
        if "error" in response:
            return JSONResponse(status_code=500, content={"error": response["error"]})
        
        state.total_prompt_tokens += response.get("usage", {}).get("prompt_tokens", 0)
        state.total_completion_tokens += response.get("usage", {}).get("completion_tokens", 0)
        
        # Mappa formato OpenAI a Ollama
        choice = response["choices"][0]["message"]
        ollama_resp = {
            "model": body["model"],
            "created_at": datetime.now(UTC).isoformat() + "Z",
            "message": {
                "role": choice.get("role", "assistant"),
                "content": choice.get("content", "")
            },
            "done": True
        }
        if choice.get("tool_calls"):
            ollama_resp["message"]["tool_calls"] = choice.get("tool_calls")
        
        # Gestione Agentica per intercettare i tools non stream (iterazione)
        tool_calls = ollama_resp["message"].get("tool_calls", [])
        if tool_calls:
            # Lazy init: confirmation_mgr solo se servono tool calls
            if confirmation_mgr is None:
                confirmation_mgr = ConfirmationManager.from_request(request_id=conversation_id)
            body["messages"].append(ollama_resp["message"])
            for tc in tool_calls:
                tool_res = await execute_tool_call(tc, confirmation_mgr=confirmation_mgr)
                body["messages"].append({"role": "tool", "content": tool_res, "name": tc.get("function", {}).get("name", "unknown")})
            
            # Ricorsione simulata per far generare la risposta finale dopo il tool
            response = await engine.generate_chat_with_router(
                body["messages"], tools=body.get("tools"), options=body.get("options"),
                stream=False, preferred_provider=provider
            )
            choice = response["choices"][0]["message"]
            ollama_resp["message"] = {"role": choice.get("role", "assistant"), "content": choice.get("content", "")}
        content = ollama_resp["message"].get("content", "")
        try:
            cleaned = await asyncio.wait_for(
                process_response_tags(content, user_id=current_user_id),
                timeout=15.0
            )
        except asyncio.TimeoutError:
            logger.warning("⏱️ process_response_tags timed out (15s) — returning raw text")
            cleaned = content
        except Exception as e:
            logger.warning(f"⚠️ process_response_tags error: {e}")
            cleaned = content
        ollama_resp["message"]["content"] = cleaned
        return JSONResponse(status_code=200, content=ollama_resp)
        
    else:
        # Streaming
        async def stream_gen():
            gen = await engine.generate_chat_with_router(body["messages"], tools=body.get("tools"), options=body.get("options"), stream=True, preferred_provider=provider)
            if isinstance(gen, dict) and "error" in gen:
                yield json.dumps({"error": gen["error"]}).encode() + b"\n"
                return

            safe_stream = TagSafeStream()
            full_chunks = []
            async for chunk in gen:
                if "choices" in chunk and len(chunk["choices"]) > 0:
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    full_chunks.append(content)

                    # Strip XML action tags (MEMORY, SCHEDULE, etc.) BEFORE streaming
                    # Usa TagSafeStream per gestire tag spalmati su piu' chunk
                    cleaned_content = safe_stream.process(content) if content else ""

                    ollama_chunk = {
                        "model": body["model"],
                        "created_at": datetime.now(UTC).isoformat() + "Z",
                        "message": {
                            "role": "assistant",
                            "content": cleaned_content
                        },
                        "done": False
                    }
                    yield json.dumps(ollama_chunk).encode() + b"\n"

            # Processa TUTTI i tag dalla risposta completa (MEMORY, SCHEDULE, SSH, ecc.)
            full_text = "".join(full_chunks)
            if full_text:
                try:
                    cleaned = await asyncio.wait_for(
                        process_response_tags(full_text, user_id=current_user_id),
                        timeout=15.0
                    )
                except asyncio.TimeoutError:
                    logger.warning("⏱️ process_response_tags timed out (15s) — skipping tag processing")
                    cleaned = full_text
                except Exception as e:
                    logger.warning(f"⚠️ process_response_tags error: {e}")
                    cleaned = full_text
                if not cleaned:
                    cleaned = full_text  # fallback: mantieni originale se la pulizia svuota tutto
                full_text = cleaned

            # Send final done message con testo pulito dai tag
            yield json.dumps({
                "model": body["model"],
                "created_at": datetime.now(UTC).isoformat() + "Z",
                "message": {"role": "assistant", "content": full_text},
                "done": True
            }).encode() + b"\n"

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
        current_user_id = options.get("user_id", "alfio_dev") if isinstance(options, dict) else "alfio_dev"
        
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
        
        # Processa i tag PRIMA di salvare in cache, per non memorizzare tag non processati
        try:
            cleaned = await asyncio.wait_for(
                process_response_tags(content, user_id=current_user_id),
                timeout=15.0
            )
        except asyncio.TimeoutError:
            logger.warning("⏱️ process_response_tags timed out (15s) on /api/generate non-streaming")
            cleaned = content
        except Exception as e:
            logger.warning(f"⚠️ process_response_tags error on /api/generate: {e}")
            cleaned = content
        asyncio.create_task(semantic_cache_store(prompt, cleaned))
        
        # Salva prompt utente in memoria (endpoint generate non usa build_omniscient_prompt)
        asyncio.create_task(save_to_memory(prompt, user_id=current_user_id))
        
        return JSONResponse(status_code=200, content={
            "model": body["model"],
            "created_at": datetime.now(UTC).isoformat() + "Z",
            "response": cleaned,
            "done": True
        })
    else:
        async def stream_gen():
            full_resp = []
            safe_stream = TagSafeStream()
            gen = await engine.generate_chat(messages, stream=True)
            async for chunk in gen:
                if "choices" in chunk and len(chunk["choices"]) > 0:
                    content = chunk["choices"][0].get("delta", {}).get("content", "")
                    full_resp.append(content)

                    # Strip XML action tags (MEMORY, SCHEDULE, etc.) BEFORE streaming
                    # Usa TagSafeStream per gestire tag spalmati su piu' chunk
                    cleaned_content = safe_stream.process(content) if content else ""

                    yield json.dumps({
                        "model": body["model"],
                        "created_at": datetime.now(UTC).isoformat() + "Z",
                        "response": cleaned_content,
                        "done": False
                    }).encode() + b"\n"

            final_content = "".join(full_resp)

            # Salva prompt utente + processa tag in background
            asyncio.create_task(save_to_memory(prompt, user_id=current_user_id))
            cleaned = final_content
            if final_content:
                try:
                    cleaned = await asyncio.wait_for(
                        process_response_tags(final_content, user_id=current_user_id),
                        timeout=15.0
                    )
                except asyncio.TimeoutError:
                    logger.warning("⏱️ process_response_tags timed out (15s) on /api/generate stream")
                    cleaned = final_content
                except Exception as e:
                    logger.warning(f"⚠️ process_response_tags error on /api/generate stream: {e}")
                    cleaned = final_content
                if not cleaned:
                    cleaned = final_content  # fallback: mantieni originale se la pulizia svuota
                asyncio.create_task(semantic_cache_store(prompt, cleaned))

            yield json.dumps({
                "model": body["model"],
                "created_at": datetime.now(UTC).isoformat() + "Z",
                "response": cleaned,
                "done": True
            }).encode() + b"\n"

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


@app.get("/api/version")
async def ollama_version():
    return {"version": "0.1.27"}

# ==============================================================================
# OpenAI-compatible endpoints via APIRouter
# ==============================================================================
app.include_router(openai_router)
@app.get("/api/tags")
async def ollama_tags():
    return {
        "models": [
            {
                "name": MODEL_ID,
                "model": MODEL_ID,
                "details": {"families": ["gemma"]}
            },
            {
                "name": "nomic-embed-text:latest",
                "model": "nomic-embed-text:latest",
                "details": {"families": ["nomic-embed-text"]}
            },
            {
                "name": "qwen3-embedding-0.6b:latest",
                "model": "qwen3-embedding-0.6b:latest",
                "details": {"families": ["qwen3"]}
            }
        ]
    }

@app.get("/api/ps")
async def ollama_ps():
    return {
        "models": [
            {
                "name": MODEL_ID,
                "model": MODEL_ID,
                "size": 2438740416,
                "size_vram": 2438740416,
                "details": {"families": ["gemma"]}
            },
            {
                "name": "nomic-embed-text:latest",
                "model": "nomic-embed-text:latest",
                "size": 84106624,
                "size_vram": 84106624,
                "details": {"families": ["nomic-embed-text"]}
            },
            {
                "name": "qwen3-embedding-0.6b:latest",
                "model": "qwen3-embedding-0.6b:latest",
                "size": 610000000,
                "size_vram": 610000000,
                "details": {"families": ["qwen3"]}
            }
        ]
    }

@app.post("/api/show")
async def ollama_show():
    return {
        "license": "",
        "modelfile": "",
        "parameters": "",
        "template": "",
        "details": {"families": ["qwen2", "nomic"]}
    }
