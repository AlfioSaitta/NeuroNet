"""
Stato mutabile globale — Singleton condiviso fra tutti i moduli.
Inizializzato nel lifespan di main.py.
"""

import asyncio

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

# Concurrency limits (Fix 9.2)
llm_semaphore = asyncio.Semaphore(1)

from concurrent.futures import ThreadPoolExecutor
mem0_executor = ThreadPoolExecutor(max_workers=4)

# Inferenza
total_requests = 0
total_prompt_tokens = 0
total_completion_tokens = 0

# GPU metrics history for time-series charts (max 300 entries ~15 min at 3s interval)
gpu_history: list[dict] = []
sys_history: list[dict] = []
inference_history: list[dict] = []
MAX_GPU_HISTORY = 300
MAX_SYS_HISTORY = 300
MAX_INF_HISTORY = 300

# Previous CPU stats for delta calculation
cpu_prev_idle: float = 0
cpu_prev_total: float = 0

# Contesto progetto attivo per utente e conversazione (persiste tra turni)
# Mappa: user_id -> conversation_id -> nome_progetto
# Previene contaminazione tra conversazioni concorrenti dello stesso utente.
last_project_context: dict[str, dict[str, str]] = {}

# Flag per prevenire watchdog events durante re-indexing
is_reindexing: bool = False


def get_last_project(user_id: str, conversation_id: str = "default") -> str | None:
    """Restituisce l'ultimo progetto attivo per una conversazione."""
    convs = last_project_context.get(user_id)
    if convs:
        return convs.get(conversation_id)
    return None


def set_last_project(user_id: str, conversation_id: str, project: str | None) -> None:
    """Imposta il progetto attivo per una conversazione."""
    convs = last_project_context.setdefault(user_id, {})
    if project is None:
        convs.pop(conversation_id, None)
    else:
        convs[conversation_id] = project
