"""
Gestore del ciclo di vita dell'applicazione (Lifespan Startup & Shutdown).
Separa la logica di bootstrap e tearing-down da main.py.
"""

import asyncio
import os
import re
import traceback
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import VectorParams, Distance

from core.config import (
    logger,
    QDRANT_HOST,
    DOC_DIR,
    WATCHDOG_ENABLED,
    WATCHDOG_TIMEOUT,
    WATCHDOG_WATCH_MODE,
    VECTOR_DB_VERSION,
    EMBEDDING_DIMS,
    WORKSPACE_DIR,
    WORKSPACE_PROJECTS,
    MCP_ENABLED,
    MCP_AUTO_INIT,
    SYNAPTIQ_ENABLED,
    SYNAPTIQ_STORAGE_PATH,
    SYNAPTIQ_EMBEDDING_TIER,
    DATA_DIR,
    parse_external_projects,
)
import core.state as state
from rag.engine import (
    ingest_local_documents,
    rag_queue_worker,
)
from memory.engine import init_mem0_delayed
from core.llm_engine import engine
from tg_bot.service import init_telegram, start_userbots, stop_telegram

if WATCHDOG_ENABLED:
    from watchdog.observers.polling import PollingObserver as Observer
    from rag.engine import DynamicRagEventHandler

observer = None


async def cleanup_old_collections() -> None:
    """Rimuove automaticamente le vecchie collezioni Qdrant non più utilizzate (migrazioni precedenti e legacy)."""
    try:
        cols_response = await state.qdrant.get_collections()
        col_names = [c.name for c in cols_response.collections]
        current_v = VECTOR_DB_VERSION.replace("v", "")

        legacy_exact = [
            "collateral_documents",
            "collateral_memories",
            "collateral_memories_entities",
            "semantic_cache",
        ]

        for name in col_names:
            delete_it = False

            if name in legacy_exact:
                delete_it = True
            elif (
                name.startswith("collateral_docs_")
                or name.startswith("collateral_memories_")
                or name.startswith("semantic_cache_")
            ):
                match = re.search(r"_v(\d+)(_entities)?$", name)
                if match:
                    version = match.group(1)
                    if version != current_v:
                        delete_it = True
                else:
                    if name != "collateral_memories_entities":
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

    # ────────────────────────────────────────────────────────────────────
    # Blocchi async indipendenti: Qdrant, Telegram, MCP in parallelo
    # ────────────────────────────────────────────────────────────────────

    async def _init_qdrant():
        """Inizializza Qdrant, crea collezioni, ripristina contesti."""
        if QDRANT_HOST == "local":
            state.qdrant = AsyncQdrantClient(path="./data/qdrant_local")
            logger.info("[SYSTEM] Qdrant inizializzato in modalità LOCALE (in-process).")
        else:
            state.qdrant = AsyncQdrantClient(host=QDRANT_HOST, port=6333)
            logger.info(f"[SYSTEM] Qdrant inizializzato in modalità HTTP (host: {QDRANT_HOST}).")

        await cleanup_old_collections()

        try:
            await state.qdrant.create_collection(
                collection_name=f"semantic_cache_{VECTOR_DB_VERSION}",
                vectors_config=VectorParams(size=EMBEDDING_DIMS, distance=Distance.COSINE),
            )
            logger.info(f"[SYSTEM] Collezione semantic_cache_{VECTOR_DB_VERSION} creata con successo.")
        except Exception as e:
            if "already exists" not in str(e).lower() and "409" not in str(e):
                logger.warning(f"Errore silenziato in create_collection: {e}")

        try:
            await state.qdrant.create_collection(
                collection_name=state.APP_CONTEXT_COLLECTION,
                vectors_config=VectorParams(size=EMBEDDING_DIMS, distance=Distance.COSINE),
            )
            logger.info(f"[SYSTEM] Collezione {state.APP_CONTEXT_COLLECTION} creata con successo.")
        except Exception as e:
            if "already exists" not in str(e).lower() and "409" not in str(e):
                logger.warning(f"Errore silenziato in create_collection app_context: {e}")

        try:
            await state.restore_project_contexts_from_qdrant()
        except Exception as e:
            logger.warning(f"Errore restore contesti progetto: {e}")

        # Pulizia symlink DOC_DIR
        if os.path.exists(DOC_DIR):
            for item in os.listdir(DOC_DIR):
                item_path = os.path.join(DOC_DIR, item)
                if os.path.islink(item_path):
                    os.remove(item_path)

    async def _init_mcp():
        """Inizializza server MCP (solo se configurato)."""
        if not (MCP_ENABLED and MCP_AUTO_INIT):
            return
        try:
            from api.mcp.client import init_mcp_from_config, get_mcp_manager

            total = await init_mcp_from_config()
            if total > 0:
                logger.info(f"🔌 MCP: {total} servers initialized from core.config files")

                if MCP_ENABLED:
                    try:
                        from agent.skills import register_skill_mcp_servers

                        reg_count = register_skill_mcp_servers()
                        if reg_count > 0:
                            logger.info(f"🔌 MCP: {reg_count} skill-embedded servers registered")
                            await get_mcp_manager().initialize_all()
                    except ImportError:
                        pass

                from agent.tools import refresh_mcp_tools_async

                mcp_count = await refresh_mcp_tools_async()
                if mcp_count > 0:
                    logger.info(f"🔌 MCP: {mcp_count} tools injected into TOOLS_SCHEMA")

                mgr = get_mcp_manager()
                for srv_name in mgr.list_servers():
                    logger.info(f"  ├─ MCP Server: {srv_name}")

        except ImportError as e:
            logger.debug(f"MCP client not available (non-critical): {e}")
        except Exception as e:
            logger.warning(f"MCP initialization: {e}")

    await asyncio.gather(
        _init_qdrant(),
        init_telegram(),
        _init_mcp(),
        return_exceptions=True,
    )

    # ── User Manager ──────────────────────────────────────────────────
    from api.auth.user_manager import init_user_manager

    db_path = os.path.join(DATA_DIR, "users.db")
    logger.info("👤 Initializing User Manager at %s", db_path)
    um = await init_user_manager(db_path)

    try:
        admins = await um.list_users(role="admin")
        if not admins:
            logger.warning("⚠️ No admin found — creating default admin...")
            user, api_key = await um.create_user(
                username="admin",
                password="neuronet",
                role="admin",
                display_name="Default Admin",
                allowed_projects=["*"],
            )
            logger.info("✅ Default admin created: username='admin', password='neuronet'")
            logger.info("🔑 Initial API key: %s", api_key)
            logger.warning("⚠️ CHANGE THE DEFAULT PASSWORD ON FIRST LOGIN!")
    except Exception as exc:
        logger.error("❌ Error seeding default admin: %s", exc)

    # Mem0 + Ingestion
    task_mem0 = asyncio.create_task(init_mem0_delayed())
    state.background_tasks.add(task_mem0)
    task_mem0.add_done_callback(state.background_tasks.discard)

    async def _ingest_after_mem0():
        await task_mem0
        if not WATCHDOG_ENABLED:
            logger.info("⏭️ RAG ingestion iniziale saltata (WATCHDOG_ENABLED=false). Usa Re-index dalla dashboard o API.")
            return
        await ingest_local_documents()

    task_ingest = asyncio.create_task(_ingest_after_mem0())
    state.background_tasks.add(task_ingest)
    task_ingest.add_done_callback(state.background_tasks.discard)

    # Watchdog filesystem
    if WATCHDOG_ENABLED:
        worker_task = asyncio.create_task(rag_queue_worker())
        state.background_tasks.add(worker_task)
        observer = Observer(timeout=WATCHDOG_TIMEOUT)

        if os.path.isdir(DOC_DIR):
            handler_doc = DynamicRagEventHandler(
                asyncio.get_running_loop(), state.file_event_queue, DOC_DIR
            )
            observer.schedule(handler_doc, DOC_DIR, recursive=True)
            logger.info(f"👀 Watchdog DOC_DIR: {DOC_DIR}")
        else:
            logger.warning(f"⚠️ DOC_DIR non trovato ({DOC_DIR}), watchdog su DOC_DIR saltato.")

        if WORKSPACE_DIR and os.path.isdir(WORKSPACE_DIR):
            if WATCHDOG_WATCH_MODE == "per_project":
                for proj_dir in WORKSPACE_PROJECTS:
                    if os.path.isdir(proj_dir):
                        proj_handler = DynamicRagEventHandler(
                            asyncio.get_running_loop(), state.file_event_queue, proj_dir
                        )
                        observer.schedule(proj_handler, proj_dir, recursive=True)
                        proj_name = os.path.basename(proj_dir)
                        logger.info(f"👀 Watchdog progetto: {proj_name} ({proj_dir})")
            else:
                handler_ws = DynamicRagEventHandler(
                    asyncio.get_running_loop(), state.file_event_queue, WORKSPACE_DIR
                )
                observer.schedule(handler_ws, WORKSPACE_DIR, recursive=True)
                logger.info(f"👀 Watchdog WORKSPACE_DIR: {WORKSPACE_DIR}")

        observer.start()
        logger.info(
            f"👀 Watchdog PollingObserver Partito (timeout={WATCHDOG_TIMEOUT}s, mode={WATCHDOG_WATCH_MODE})."
        )

        async def watchdog_health():
            global observer
            while True:
                await asyncio.sleep(60)
                try:
                    emitters = getattr(observer, "_emitters", [])
                    emitter_alive = any(e.is_alive() for e in emitters)
                    dispatch_alive = observer.is_alive()
                    qsize = state.file_event_queue.qsize()
                    if not emitter_alive or not dispatch_alive:
                        logger.warning(
                            f"Watchdog: emitter={emitter_alive} dispatch={dispatch_alive} coda={qsize} — riavvio..."
                        )
                        observer.stop()
                        loop = asyncio.get_running_loop()
                        await loop.run_in_executor(None, lambda: observer.join(timeout=5))
                        observer = Observer(timeout=WATCHDOG_TIMEOUT)
                        if os.path.isdir(DOC_DIR):
                            new_handler_doc = DynamicRagEventHandler(
                                asyncio.get_running_loop(), state.file_event_queue, DOC_DIR
                            )
                            observer.schedule(new_handler_doc, DOC_DIR, recursive=True)
                        if WORKSPACE_DIR and os.path.isdir(WORKSPACE_DIR):
                            if WATCHDOG_WATCH_MODE == "per_project":
                                for proj_dir in WORKSPACE_PROJECTS:
                                    if os.path.isdir(proj_dir):
                                        proj_handler = DynamicRagEventHandler(
                                            asyncio.get_running_loop(),
                                            state.file_event_queue,
                                            proj_dir,
                                        )
                                        observer.schedule(
                                            proj_handler, proj_dir, recursive=True
                                        )
                            else:
                                new_handler_ws = DynamicRagEventHandler(
                                    asyncio.get_running_loop(),
                                    state.file_event_queue,
                                    WORKSPACE_DIR,
                                )
                                observer.schedule(
                                    new_handler_ws, WORKSPACE_DIR, recursive=True
                                )
                        observer.start()
                        logger.info("Watchdog: nuovo Observer avviato dopo crash.")
                    elif qsize > 100:
                        logger.warning(f"Watchdog: coda eventi {qsize}, possibile blocco worker")
                except Exception as e:
                    logger.error(f"Watchdog health check error: {e}", exc_info=True)

        health_task = asyncio.create_task(watchdog_health())
        state.background_tasks.add(health_task)
        health_task.add_done_callback(state.background_tasks.discard)

    # Multi-Userbot MTProto
    task_userbots = asyncio.create_task(start_userbots())
    state.background_tasks.add(task_userbots)
    task_userbots.add_done_callback(state.background_tasks.discard)

    # Scheduler
    try:
        from scheduler.cron import init_scheduler

        init_scheduler()
    except Exception as e:
        logger.error(f"Errore inizializzazione cron scheduler: {e}\n{traceback.format_exc()}")

    # Telemetria
    try:
        from admin.dashboard import start_telemetry_collector

        start_telemetry_collector(app)
    except Exception as e:
        logger.warning(f"Telemetry collector non avviato: {e}")

    # Synaptiq Engine
    if SYNAPTIQ_ENABLED:
        try:
            from graph.synaptiq_engine import synaptiq_engine

            synaptiq_engine.storage_path = SYNAPTIQ_STORAGE_PATH
            synaptiq_engine.embedding_tier = SYNAPTIQ_EMBEDDING_TIER
            await synaptiq_engine.initialize()
            logger.info(f"🧬 Synaptiq Engine avviato (storage={SYNAPTIQ_STORAGE_PATH})")

            async def _synaptiq_initial_after_ingest():
                try:
                    await task_ingest
                except Exception as e:
                    logger.warning("RAG ingest fallito, Synaptiq initial analysis saltata: %s", e)
                    return
                projects = list(WORKSPACE_PROJECTS) + parse_external_projects()
                await synaptiq_engine.run_initial_analysis(projects)

            task_synaptiq = asyncio.create_task(_synaptiq_initial_after_ingest())
            state.background_tasks.add(task_synaptiq)
            task_synaptiq.add_done_callback(state.background_tasks.discard)
        except Exception as e:
            logger.warning(f"Synaptiq Engine non avviato: {e}")

    yield

    # Shutdown
    if SYNAPTIQ_ENABLED:
        try:
            from graph.synaptiq_engine import synaptiq_engine

            await synaptiq_engine.close()
            logger.info("Synaptiq Engine fermato.")
        except Exception as e:
            logger.warning(f"Synaptiq Engine stop error: {e}")

    if observer:
        observer.stop()
        with suppress(Exception):
            observer.join(timeout=5)

    tasks_to_stop = list(state.background_tasks)
    for t in tasks_to_stop:
        t.cancel()
    if tasks_to_stop:
        await asyncio.gather(*tasks_to_stop, return_exceptions=True)

    await stop_telegram()

    # MCP shutdown
    if MCP_ENABLED:
        try:
            from api.mcp.client import get_mcp_manager

            mgr = get_mcp_manager()
            await mgr.close_all()
            logger.info("🔌 MCP: all servers shut down")
        except Exception as e:
            logger.warning(f"MCP shutdown error: {e}")

    # Persist session store
    try:
        if state.chat_session_store:
            state.chat_session_store.persist("./data/sessions.json")
    except Exception as e:
        logger.warning(f"SessionStore persist error during shutdown: {e}")

    # Close User Manager
    try:
        from api.auth.user_manager import close_user_manager

        await close_user_manager()
    except Exception as e:
        logger.warning(f"User manager close error: {e}")

    # Embed worker deprecato: il chat model (embedding=True) fa anche embedding.
    # Non c'è più un subprocess separato da fermare.
    await asyncio.gather(
        state.qdrant.close(),
        state.http_client.aclose(),
        return_exceptions=True,
    )
    logger.info("🎉 Shutdown completato con successo.")
