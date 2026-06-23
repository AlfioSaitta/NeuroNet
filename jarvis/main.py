"""
Collateral Studios Agent v8.6.7 — Entry point dell'applicazione.
App FastAPI, lifespan e tutti gli endpoint HTTP.
"""

import os
import json
import time
import asyncio
import uuid
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
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import VectorParams, Distance

from config import (
    logger, OLLAMA_MODEL, QDRANT_HOST,
    DOC_COLLECTION,
    TELEGRAM_TOKEN, ALLOWED_USERS, MEM0_CONFIG, STATE_FILE,
    TELEGRAM_ENABLED, WATCHDOG_ENABLED, VECTOR_DB_VERSION,
    API_RATE_LIMIT_DEFAULT, API_RATE_LIMIT_HEAVY, EMBEDDING_DIMS, EXTERNAL_PROJECTS
)
import state
from rag import ingest_local_documents, rag_queue_worker, generate_project_tree, search_documents, semantic_cache_search, semantic_cache_store
from memory import init_mem0_delayed, extract_memories, save_to_memory
from prompt_builder import build_omniscient_prompt
from llm_engine import engine

if WATCHDOG_ENABLED:
    from watchdog.observers.polling import PollingObserver as Observer
    from rag import DynamicRagEventHandler

if TELEGRAM_ENABLED:
    from telegram import BotCommand, Update
    from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler, TypeHandler
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

    from llm_engine import engine
    logger.info("Avvio caricamento modelli Llama-cpp (Qwen + Nomic)...")
    await asyncio.to_thread(engine.load_models)
    logger.info("Modelli Llama caricati in locale (No Ollama).")

    state.http_client = httpx.AsyncClient(timeout=300.0)
    if QDRANT_HOST == "local":
        state.qdrant = AsyncQdrantClient(path="./data/qdrant_local")
        logger.info("[SYSTEM] Qdrant inizializzato in modalità LOCALE (in-process).")
    else:
        state.qdrant = AsyncQdrantClient(host=QDRANT_HOST, port=6333)
        logger.info(f"[SYSTEM] Qdrant inizializzato in modalità HTTP (host: {QDRANT_HOST}).")
    
    # Pulizia automatica delle vecchie migrazioni Qdrant
    await cleanup_old_collections()

    from qdrant_client.models import VectorParams, Distance
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
    # MOUNT DINAMICI EXTERNAL PROJECTS
    # ==========================================================================
    path_mapping = {}
    
    # Rimuovi vecchi symlink dinamici da /app/documents/ (evita loop ricorsivi)
    if os.path.exists("/app/documents"):
        for item in os.listdir("/app/documents"):
            item_path = os.path.join("/app/documents", item)
            if os.path.islink(item_path):
                os.remove(item_path)
    
    if EXTERNAL_PROJECTS.strip():
        for pair in EXTERNAL_PROJECTS.split(','):
            pair = pair.strip()
            if ':' in pair:
                host_path, folder_name = pair.split(':', 1)
                host_path = host_path.strip()
                folder_name = folder_name.strip()
                
                # Traduci il path host nel path montato nel container (/host_fs)
                container_path = os.path.join("/host_fs", host_path.lstrip('/'))
                symlink_path = os.path.join("/app/documents", folder_name)
                
                if os.path.exists(container_path):
                    try:
                        os.symlink(container_path, symlink_path)
                        path_mapping[container_path] = symlink_path
                        logger.info(f"🔗 Mount dinamico RAG creato: {folder_name} -> {host_path}")
                    except Exception as e:
                        logger.warning(f"Impossibile creare mount per {folder_name}: {e}")
                else:
                    logger.warning(f"Mount ignorato: il percorso host '{host_path}' non esiste sul filesystem root.")
        
        # Dopo aver creato i symlink, rimuovi eventuali loop ricorsivi nidificati
        # (es. NeuroNet/data/documents/NeuroNet → /app/documents == data/documents/
        #  crea loop infinito in os.walk e DirectorySnapshot)
        # NOTA: per il progetto NeuroNet (ai-ecosystem), data/documents/ è un Docker
        # volume montato a /app/documents/. Il symlink /app/documents/NeuroNet ha lo
        # STESSO inode di NeuroNet/data/documents/NeuroNet (stesso file). Rimuoviamo
        # TUTTI i symlink ricorsivi per evitare loop in DirectorySnapshot (watchdog).
        # NeuroNet verrà indicizzato in ingest_local_documents tramite percorso diretto.
        for pair in EXTERNAL_PROJECTS.split(','):
            pair = pair.strip()
            if ':' in pair:
                host_path, _ = pair.split(':', 1)
                host_path = host_path.strip()
                container_path = os.path.join("/host_fs", host_path.lstrip('/'))
                nested_doc_dir = os.path.join(container_path, "data", "documents")
                if os.path.isdir(nested_doc_dir):
                    for nested_item in os.listdir(nested_doc_dir):
                        nested_path = os.path.join(nested_doc_dir, nested_item)
                        if os.path.islink(nested_path):
                            target = os.readlink(nested_path)
                            if target == container_path:
                                os.remove(nested_path)
                                logger.info(f"🧹 Rimosso symlink ricorsivo: {nested_path} -> {target}")
    # ==========================================================================

    # Avvio asincrono di Mem0 (con ritardo per il loopback proxy)
    task_mem0 = asyncio.create_task(init_mem0_delayed())
    state.background_tasks.add(task_mem0)
    task_mem0.add_done_callback(state.background_tasks.discard)

    # Ingestion iniziale documenti
    task_ingest = asyncio.create_task(ingest_local_documents())
    state.background_tasks.add(task_ingest)
    task_ingest.add_done_callback(state.background_tasks.discard)

    # Watchdog filesystem (PollingObserver per compatibilità Docker bind mount / symlink)
    # Nota: usiamo PollingObserver (non Observer/inotify) perché inotify:
    #   - Non segue i symlink dentro /app/documents/
    #   - Non si propaga in modo affidabile attraverso i bind mount Docker (/host_fs)
    # PollingObserver periodicamente esegue os.stat() sui file — funziona sempre.
    if WATCHDOG_ENABLED:
        worker_task = asyncio.create_task(rag_queue_worker())
        state.background_tasks.add(worker_task)
        observer = Observer(timeout=1)
        handler = DynamicRagEventHandler(asyncio.get_running_loop(), state.file_event_queue, "/app/documents")
        
        # Unico watch su /app/documents (i symlink ai progetti sono seguiti da os.scandir)
        observer.schedule(handler, "/app/documents", recursive=True)
                
        observer.start()
        logger.info("👀 Watchdog PollingObserver Partito (intervallo 1s).")

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
                        observer = Observer(timeout=1)
                        new_handler = DynamicRagEventHandler(asyncio.get_running_loop(), state.file_event_queue, "/app/documents")
                        observer.schedule(new_handler, "/app/documents", recursive=True)
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
            state.telegram_app = (
                ApplicationBuilder()
                .token(TELEGRAM_TOKEN)
                .read_timeout(120.0)
                .write_timeout(120.0)
                .connect_timeout(60.0)
                .pool_timeout(60.0)
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
        import traceback
        logger.error(f"Errore inizializzazione cron scheduler: {e}\n{traceback.format_exc()}")

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


@app.post("/api/webhook/git")
async def git_webhook(request: Request):
    """Gestisce i webhook da GitHub/Gitea/GitLab per triggerare l'aggiornamento RAG via git pull."""
    from config import GIT_WEBHOOK_SECRET, DOC_DIR
    
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
    from agent_tools import TOOLS_SCHEMA, execute_tool_call
    from datetime import datetime
    
    body = payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()
    body["model"] = OLLAMA_MODEL
    
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
                if txt.startswith("## Summary") or "Extract entities" in txt or txt.strip().startswith("ADD_MEMORY") or txt.strip().startswith("UPDATE_MEMORY") or "deduce the facts" in txt:
                    is_internal = True
                    break
                
    current_user_id = body.get("user_id") or (options.get("user_id") if isinstance(options, dict) else None) or "alfio_dev"
    conversation_id = body.get("conversation_id") or request.headers.get("X-Conversation-Id", "default")
    if not is_internal:
        body["messages"] = await build_omniscient_prompt(raw_messages, user_id=current_user_id, conversation_id=str(conversation_id))
    
    is_stream = body.get("stream", True)
    
    if not is_stream:
        # Non-stream
        response = await engine.generate_chat(body["messages"], tools=body.get("tools"), options=body.get("options"), stream=False)
        if "error" in response:
            return JSONResponse(status_code=500, content={"error": response["error"]})
        
        state.total_prompt_tokens += response.get("usage", {}).get("prompt_tokens", 0)
        state.total_completion_tokens += response.get("usage", {}).get("completion_tokens", 0)
        
        # Mappa formato OpenAI a Ollama
        choice = response["choices"][0]["message"]
        ollama_resp = {
            "model": body["model"],
            "created_at": datetime.utcnow().isoformat() + "Z",
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
            body["messages"].append(ollama_resp["message"])
            for tc in tool_calls:
                tool_res = await execute_tool_call(tc)
                body["messages"].append({"role": "tool", "content": tool_res, "name": tc.get("function", {}).get("name", "unknown")})
            
            # Ricorsione simulata per far generare la risposta finale dopo il tool
            response = await engine.generate_chat(body["messages"], tools=body.get("tools"), options=body.get("options"), stream=False)
            choice = response["choices"][0]["message"]
            ollama_resp["message"] = {"role": choice.get("role", "assistant"), "content": choice.get("content", "")}
        from memory import process_response_tags
        content = ollama_resp["message"].get("content", "")
        cleaned = await process_response_tags(content, user_id=current_user_id)
        ollama_resp["message"]["content"] = cleaned
        return JSONResponse(status_code=200, content=ollama_resp)
        
    else:
        # Streaming
        async def stream_gen():
            gen = await engine.generate_chat(body["messages"], tools=body.get("tools"), options=body.get("options"), stream=True)
            if isinstance(gen, dict) and "error" in gen:
                yield json.dumps({"error": gen["error"]}).encode() + b"\n"
                return
                
            full_chunks = []
            async for chunk in gen:
                if "choices" in chunk and len(chunk["choices"]) > 0:
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    full_chunks.append(content)
                    
                    ollama_chunk = {
                        "model": body["model"],
                        "created_at": datetime.utcnow().isoformat() + "Z",
                        "message": {
                            "role": "assistant",
                            "content": content
                        },
                        "done": False
                    }
                    yield json.dumps(ollama_chunk).encode() + b"\n"
            
            # Processa eventuali tag <MEMORY> in background dalla risposta completa
            full_text = "".join(full_chunks)
            if full_text:
                from memory import process_response_tags
                asyncio.create_task(process_response_tags(full_text, user_id=current_user_id))
            
            # Send final done message
            yield json.dumps({
                "model": body["model"],
                "created_at": datetime.utcnow().isoformat() + "Z",
                "message": {"role": "assistant", "content": ""},
                "done": True
            }).encode() + b"\n"

        return StreamingResponse(stream_gen(), media_type="application/x-ndjson")

@app.post("/api/generate")
@limiter.limit(API_RATE_LIMIT_DEFAULT)
async def ollama_generate(payload: GenerateRequest, request: Request):
    state.total_requests += 1
    """Endpoint generate Ollama simulato con iniezione RAG."""
    from datetime import datetime
    body = payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()
    body["model"] = OLLAMA_MODEL
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
        
        content = response["choices"][0]["message"].get("content", "")
        
        # Processa i tag PRIMA di salvare in cache, per non memorizzare tag non processati
        from memory import process_response_tags
        cleaned = await process_response_tags(content, user_id=current_user_id)
        asyncio.create_task(semantic_cache_store(prompt, cleaned))
        
        # Salva prompt utente in memoria (endpoint generate non usa build_omniscient_prompt)
        asyncio.create_task(save_to_memory(prompt, user_id=current_user_id))
        
        return JSONResponse(status_code=200, content={
            "model": body["model"],
            "created_at": datetime.utcnow().isoformat() + "Z",
            "response": cleaned,
            "done": True
        })
    else:
        async def stream_gen():
            full_resp = []
            gen = await engine.generate_chat(messages, stream=True)
            async for chunk in gen:
                if "choices" in chunk and len(chunk["choices"]) > 0:
                    content = chunk["choices"][0].get("delta", {}).get("content", "")
                    full_resp.append(content)
                    
                    yield json.dumps({
                        "model": body["model"],
                        "created_at": datetime.utcnow().isoformat() + "Z",
                        "response": content,
                        "done": False
                    }).encode() + b"\n"
                    
            final_content = "".join(full_resp)
            
            # Salva prompt utente + processa tag in background
            asyncio.create_task(save_to_memory(prompt, user_id=current_user_id))
            if final_content:
                from memory import process_response_tags
                cleaned = await process_response_tags(final_content, user_id=current_user_id)
                asyncio.create_task(semantic_cache_store(prompt, cleaned))
            
            yield json.dumps({
                "model": body["model"],
                "created_at": datetime.utcnow().isoformat() + "Z",
                "response": "",
                "done": True
            }).encode() + b"\n"

        return StreamingResponse(stream_gen(), media_type="application/x-ndjson")

@app.post("/api/embeddings")
@limiter.limit(API_RATE_LIMIT_DEFAULT)
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
@limiter.limit(API_RATE_LIMIT_DEFAULT)
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
# OPENAI-COMPATIBLE ENDPOINTS (per OpenCode e altri tool)
# ==============================================================================

class OpenAIMessage(BaseModel):
    role: str
    content: str

class ChatCompletionRequestOpenAI(BaseModel):
    model: str
    messages: List[OpenAIMessage]
    stream: Optional[bool] = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    stop: Optional[List[str]] = None

@app.get("/v1/models")
async def openai_models():
    return {
        "object": "list",
        "data": [
            {
                "id": "qwen2.5-coder:latest",
                "object": "model",
                "created": 1710000000,
                "owned_by": "ollama"
            },
            {
                "id": "nomic-embed-text:latest",
                "object": "model",
                "created": 1710000000,
                "owned_by": "ollama"
            }
        ]
    }

@app.post("/v1/chat/completions")
@limiter.limit(API_RATE_LIMIT_DEFAULT)
async def openai_chat_completions(payload: ChatCompletionRequestOpenAI, request: Request):
    state.total_requests += 1
    from datetime import datetime
    import uuid

    body = payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()
    is_stream = body.get("stream", False)
    raw_messages = body.get("messages", [])

    options = {}
    if body.get("temperature") is not None:
        options["temperature"] = body["temperature"]
    if body.get("max_tokens") is not None:
        options["num_predict"] = body["max_tokens"]
    if body.get("top_p") is not None:
        options["top_p"] = body["top_p"]

    ollama_messages = [{"role": m["role"], "content": m["content"]} for m in raw_messages]

    current_user_id = body.get("user_id") or "alfio_dev"
    conversation_id = body.get("conversation_id") or request.headers.get("X-Conversation-Id", "default")
    enriched = await build_omniscient_prompt(ollama_messages, user_id=current_user_id, conversation_id=str(conversation_id))
    chat_body = {"model": OLLAMA_MODEL, "messages": enriched, "stream": is_stream, "options": options}
    if not is_stream:
        response = await engine.generate_chat(chat_body["messages"], options=options, stream=False)
        if "error" in response:
            return JSONResponse(status_code=500, content={"error": response["error"]})

        state.total_prompt_tokens += response.get("usage", {}).get("prompt_tokens", 0)
        state.total_completion_tokens += response.get("usage", {}).get("completion_tokens", 0)

        choice = response["choices"][0]["message"]
        content = choice.get("content", "")
        from memory import process_response_tags
        cleaned = await process_response_tags(content, user_id=current_user_id)
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(datetime.utcnow().timestamp()),
            "model": body["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": choice.get("role", "assistant"),
                        "content": cleaned
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": response.get("usage", {})
        }
    else:
        async def openai_stream_gen():
            gen = await engine.generate_chat(chat_body["messages"], options=options, stream=True)
            if isinstance(gen, dict) and "error" in gen:
                yield f"data: {json.dumps({'error': gen['error']})}\n\n"
                return

            full_chunks = []
            async for chunk in gen:
                if "choices" in chunk and len(chunk["choices"]) > 0:
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if not content:
                        continue
                    full_chunks.append(content)
                    openai_chunk = {
                        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                        "object": "chat.completion.chunk",
                        "created": int(datetime.utcnow().timestamp()),
                        "model": body["model"],
                        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]
                    }
                    yield f"data: {json.dumps(openai_chunk)}\n\n"

            full_text = "".join(full_chunks)
            if full_text:
                from memory import process_response_tags
                asyncio.create_task(process_response_tags(full_text, user_id=current_user_id))

            yield f"data: {json.dumps({'id': f'chatcmpl-{uuid.uuid4().hex[:12]}', 'object': 'chat.completion.chunk', 'created': int(datetime.utcnow().timestamp()), 'model': body['model'], 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(openai_stream_gen(), media_type="text/event-stream")

@app.get("/api/tags")
async def ollama_tags():
    return {
        "models": [
            {
                "name": "qwen2.5-coder:latest",
                "model": "qwen2.5-coder:latest",
                "details": {"families": ["qwen2"]}
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
                "name": "qwen2.5-coder:latest",
                "model": "qwen2.5-coder:latest",
                "size": 2438740416,
                "size_vram": 2438740416,
                "details": {"families": ["qwen2"]}
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
