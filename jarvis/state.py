"""
Stato mutabile globale — Singleton condiviso fra tutti i moduli.
Inizializzato nel lifespan di main.py.
"""

import asyncio
from collections import deque

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
    """Imposta il progetto attivo per una conversazione."""
    convs = last_project_context.setdefault(user_id, {})
    if project is None:
        convs.pop(conversation_id, None)
    else:
        convs[conversation_id] = project
