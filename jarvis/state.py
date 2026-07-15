"""
Stato mutabile globale — Singleton condiviso fra tutti i moduli.
Inizializzato nel lifespan di main.py.
"""

import asyncio
import hashlib
import logging
import random
import struct
import time
from collections import deque

# ── Import differito di config (sotto-modulo, nessun circolare) ──
try:
    from config import VECTOR_DB_VERSION, logger
except ImportError:
    VECTOR_DB_VERSION = "v3"
    logger = logging.getLogger(__name__)

APP_CONTEXT_COLLECTION = f"app_context_{VECTOR_DB_VERSION}"


# ── Helper: persistenza progetto attivo su Qdrant con vettore deterministico ──
# Usa una collection dedicata (app_context_v3) con vettori 768d generati
# deterministicamente dall'hash della chiave. La collection non serve a
# semantic search ma solo a key-value persistente — il _type payload field
# discrimina le entries. Questo permette a last_project_context di
# sopravvivere ai restart di Jarvis.

def _project_context_point_id(user_id: str, conversation_id: str) -> str:
    """ID punto deterministico per (utente, conversazione)."""
    raw = f"project_ctx:{user_id}:{conversation_id}"
    return hashlib.md5(raw.encode()).hexdigest()


def _deterministic_vector(seed_key: str, dims: int = 768) -> list[float]:
    """Vettore deterministico 768d per storage non-semantico in Qdrant."""
    h = hashlib.sha256(seed_key.encode()).digest()
    seed = struct.unpack('<I', h[:4])[0]
    rng = random.Random(seed)
    return [rng.random() for _ in range(dims)]


async def persist_project_context_to_qdrant(
    user_id: str, conversation_id: str, project: str | None
) -> None:
    """Upsert/delete una entry progetto attivo su Qdrant.

    Chiamata in background da set_last_project(). Non blocca il caller.
    """
    module_qdrant = globals().get("qdrant")
    if module_qdrant is None:
        return  # Qdrant non ancora inizializzato (prima del lifespan)

    try:
        from qdrant_client import models as qdrant_models

        point_id = _project_context_point_id(user_id, conversation_id)
        if project is None:
            await module_qdrant.delete(
                collection_name=APP_CONTEXT_COLLECTION,
                points_selector=qdrant_models.PointIdsList(points=[point_id]),
            )
        else:
            vector = _deterministic_vector(f"project_ctx:{user_id}:{conversation_id}")
            await module_qdrant.upsert(
                collection_name=APP_CONTEXT_COLLECTION,
                points=[
                    qdrant_models.PointStruct(
                        id=point_id,
                        vector=vector,
                        payload={
                            "user_id": user_id,
                            "conversation_id": conversation_id,
                            "project": project,
                            "updated_at": time.time(),
                        },
                    )
                ],
            )
    except Exception:
        logger.warning(
            "Qdrant persist project context fallito (non critico)", exc_info=True
        )


async def restore_project_contexts_from_qdrant() -> int:
    """Carica tutti i contesti progetto da Qdrant → last_project_context.

    Chiamata nel lifespan di main.py dopo init Qdrant.
    Restituisce il numero di contesti ripristinati.
    """
    module_qdrant = globals().get("qdrant")
    if module_qdrant is None:
        logger.warning("Qdrant non disponibile, skip restore contesti progetto")
        return 0

    try:
        collections = await module_qdrant.get_collections()
        col_names = [c.name for c in collections.collections]
        if APP_CONTEXT_COLLECTION not in col_names:
            logger.info(
                "Collection %s non trovata (primo avvio o migrazione)",
                APP_CONTEXT_COLLECTION,
            )
            return 0

        from qdrant_client import models as qdrant_models

        all_points: list = []
        next_offset = None
        while True:
            points, next_offset = await module_qdrant.scroll(
                collection_name=APP_CONTEXT_COLLECTION,
                limit=100,
                offset=next_offset,
                with_payload=True,
            )
            all_points.extend(points)
            if next_offset is None:
                break

        count = 0
        for point in all_points:
            p = point.payload or {}
            uid = p.get("user_id")
            cid = p.get("conversation_id")
            proj = p.get("project")
            if uid and cid and proj:
                convs = last_project_context.setdefault(uid, {})
                convs[cid] = proj
                count += 1

        if count:
            logger.info(
                "♻️ Ripristinati %d contesti progetto da %s", count, APP_CONTEXT_COLLECTION
            )
        return count
    except Exception as e:
        logger.warning("Impossibile ripristinare contesti progetto: %s", e)
        return 0


# Client e connessioni (inizializzati in main.py lifespan)
qdrant = None           # AsyncQdrantClient
http_client = None      # httpx.AsyncClient
memory = None           # Mem0 Memory instance
telegram_app = None     # Telegram Application

# Stato RAG
rag_state = {}
state_lock = asyncio.Lock()
created_collections = set()
project_tree_cache = ""  # Fix 9.4: Caching per event loop non bloccato

# Task management
background_tasks = set()
file_event_queue = asyncio.Queue()

from concurrent.futures import ThreadPoolExecutor
mem0_executor = ThreadPoolExecutor(max_workers=4)

# Inferenza
total_requests = 0
total_prompt_tokens = 0
total_completion_tokens = 0

# GPU metrics history for time-series charts (max 300 entries ~15 min at 3s interval)
gpu_history: deque[dict] = deque(maxlen=300)
sys_history: deque[dict] = deque(maxlen=300)
inference_history: deque[dict] = deque(maxlen=300)

# Previous CPU stats for delta calculation
cpu_prev_idle: float = 0
cpu_prev_total: float = 0

# Contesto progetto attivo per utente e conversazione (persiste tra turni)
# Mappa: user_id -> conversation_id -> nome_progetto
# Previene contaminazione tra conversazioni concorrenti dello stesso utente.
last_project_context: dict[str, dict[str, str]] = {}

# Flag per prevenire watchdog events durante re-indexing
is_reindexing: bool = False

# — Tag Processor state (gestito da tag_processor.py) —
last_emotion: str = ""              # Ultima emozione impostata da <EMOTION>
deepthink_mode: bool = False         # Modalità ragionamento approfondito (<THINK_DEEP/>)
last_confidence: float = 0.0         # Ultimo punteggio confidenza (<CONFIDENCE>)
pending_questions: list[str] = []    # Domande in attesa dal LLM (<ASK>)
forced_rag_project: str | None = None  # Progetto RAG forzato (<RAG>)


def get_last_project(user_id: str, conversation_id: str = "default") -> str | None:
    """Restituisce l'ultimo progetto attivo per una conversazione."""
    convs = last_project_context.get(user_id)
    if convs:
        return convs.get(conversation_id)
    return None


def set_last_project(user_id: str, conversation_id: str, project: str | None) -> None:
    """Imposta il progetto attivo per una conversazione e persiste su Qdrant.

    La scrittura su Qdrant è fire-and-forget (non blocca il chiamante).
    """
    convs = last_project_context.setdefault(user_id, {})
    if project is None:
        convs.pop(conversation_id, None)
    else:
        convs[conversation_id] = project

    # Persistenza asincrona su Qdrant (fire-and-forget)
    _bg_persist_project_context(user_id, conversation_id, project)


def _bg_persist_project_context(
    user_id: str, conversation_id: str, project: str | None
) -> None:
    """Fire-and-forget wrapper per persist_project_context_to_qdrant."""
    try:
        task = asyncio.create_task(
            persist_project_context_to_qdrant(user_id, conversation_id, project)
        )
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
    except RuntimeError:
        pass  # Nessun event loop disponibile (es. test sincroni)


# ════════════════════════════════════════════════════════════════
# Pipeline Telemetry — ring buffer di trace esposto via MCP
# ════════════════════════════════════════════════════════════════

# Ring buffer degli ultimi 500 pipeline trace completati.
# Popolato da PipelineTracer.finish() in telemetry.py.
# Letto dal server MCP e dal dashboard.
from collections import deque as _deque
pipeline_traces: "_deque" = _deque(maxlen=500)

# Statistiche cumulative del Gatekeeper.
# Aggiornato da prompt_builder.py dopo ogni classificazione.
gatekeeper_stats: dict | None = None  # Verrà inizializzato come GatekeeperStats()

# Contatori di errore per diagnostica MCP
error_counters: dict[str, int] = {}
