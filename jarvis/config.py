"""
Configurazione centralizzata — Costanti, variabili d'ambiente, hyperparameters e setup AST/Watchdog.
"""

import os
import logging

# ==============================================================================
# LOGGING
# ==============================================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
numeric_level = getattr(logging, LOG_LEVEL, logging.INFO)

class TagFormatter(logging.Formatter):
    def format(self, record):
        if record.name == "chameleon":
            mod = record.module.lower()
            if mod == "rag": tag = "RAG"
            elif "telegram_bot" in mod: tag = "TELEGRAM"
            elif "userbot" in mod: tag = "USERBOT"
            elif "prompt_builder" in mod: tag = "PROMPT_B"
            elif "memory" in mod: tag = "MEM0"
            elif "agent" in mod or "tools" in mod: tag = "AGENT"
            elif mod in ("main", "config", "state"): tag = "SYSTEM"
            elif mod in ("cron_agent", "task_manager"): tag = "CRON"
            else: tag = mod.upper()[:8]
        else:
            name = record.name.lower()
            if "telethon" in name: tag = "USERBOT"
            elif "uvicorn" in name or "fastapi" in name: tag = "API"
            elif "httpx" in name or "httpcore" in name: tag = "HTTP"
            elif "watchdog" in name: tag = "FS"
            else: tag = name.split('.')[0].upper()[:8]
            
        record.tag = tag
        return super().format(record)

logging.getLogger().handlers.clear()
handler = logging.StreamHandler()
handler.setFormatter(TagFormatter("%(asctime)s - %(levelname)s - [%(tag)s] %(message)s"))
logging.basicConfig(level=numeric_level, handlers=[handler], force=True)

logger = logging.getLogger("chameleon")

class UvicornAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.args and len(record.args) >= 5:
            status = record.args[4]
            if status in [200, 201, 202, 204, 304]:
                return False
        return True

class TelegramNetworkErrorFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info and "NetworkError" in str(record.exc_info[0]):
            logger.warning("⏳ Timeout di rete con Telegram (NetworkError). Riconnessione automatica in corso...")
            return False
        if "Exception happened while polling for updates" in str(record.msg):
            return False
        return True

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("watchdog").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").addFilter(UvicornAccessFilter())
logging.getLogger("telegram.ext.Updater").addFilter(TelegramNetworkErrorFilter())

# ==============================================================================
# URL DEI SERVIZI E OLLAMA
# ==============================================================================
OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:3b-instruct-q5_K_M")
QDRANT_HOST = os.getenv("QDRANT_HOST", "local")
SEARXNG_HOST = os.getenv("SEARXNG_HOST", "http://searxng:8080").rstrip('/')
CRAWL4AI_HOST = os.getenv("CRAWL4AI_HOST", "http://crawl4ai:11235").rstrip('/')
BOT_NAME = os.getenv("BOT_NAME", "Jarvis")
EXTERNAL_GPU_URL = os.getenv("EXTERNAL_GPU_URL", "")
GLOBAL_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "15m")
# LLM Options supporta parametri personalizzati da .env
# Nota: LLM_NUM_CTX è il nome standard nel .env; LLM_CTX_SIZE è l'alias legacy
_llm_num_ctx = int(os.getenv("LLM_NUM_CTX") or os.getenv("LLM_CTX_SIZE") or "32768")
LLM_OPTIONS = {
    "num_ctx": _llm_num_ctx,
    "temperature": float(os.getenv("LLM_TEMPERATURE", "1.0")),
    "num_predict": int(os.getenv("LLM_NUM_PREDICT", "2048")),
    "presence_penalty": float(os.getenv("LLM_PRESENCE_PENALTY", "0.0")),
    "frequency_penalty": float(os.getenv("LLM_FREQUENCY_PENALTY", "0.0")),
    "repeat_penalty": float(os.getenv("LLM_REPEAT_PENALTY", "1.0")),
    "top_p": float(os.getenv("LLM_TOP_P", "0.95")),
    "top_k": int(os.getenv("LLM_TOP_K", "40"))
}
# Rilevamento automatico famiglia modello dal GGUF.
# Usato per thinking mode, temperatura, contesto massimo e compatibilità RAG.
from model_profiles import detect_model_family, supports_thinking as _profile_supports_thinking
MODEL_PROFILE = detect_model_family()
logger.info(f"🤖 Modello rilevato: {MODEL_PROFILE.family} ({MODEL_PROFILE.description}) | "
            f"thinking={'✅' if MODEL_PROFILE.thinking_support else '❌'} | "
            f"unsloth={'✅' if MODEL_PROFILE.unsloth_optimized else '❌'} | "
            f"ctx max={MODEL_PROFILE.max_ctx}")

# Thinking Mode: default basato sul modello caricato, sovrascrivibile via .env
_THINKING_ENV = os.getenv("LLM_THINKING_MODE", "")
if _THINKING_ENV:
    LLM_THINKING_MODE: bool = _THINKING_ENV.lower() == "true"
else:
    LLM_THINKING_MODE: bool = MODEL_PROFILE.thinking_support

# ==============================================================================
# PERCORSI E CHUNKING
# ==============================================================================
VECTOR_DB_VERSION = os.getenv("VECTOR_DB_VERSION", "v1")
EXTERNAL_PROJECTS = os.getenv("EXTERNAL_PROJECTS", "")
STATE_FILE = f"/app/mem0_data_v3/rag_state_{VECTOR_DB_VERSION}.json"
CHUNK_SIZE = 4000
CHUNK_OVERLAP = 400
MAX_CONCURRENT_EMBEDDINGS = 3
DOC_DIR = "/app/documents"
DOC_COLLECTION = f"collateral_documents_{VECTOR_DB_VERSION}"

# Cache HuggingFace / FastEmbed
os.environ["HF_HOME"] = "/app/mem0_data_v3/hf_cache"
os.environ["FASTEMBED_CACHE_PATH"] = "/app/mem0_data_v3/fastembed_cache"

# ==============================================================================
# 🎛️ PANNELLO DI CONTROLLO CENTRALIZZATO (HYPERPARAMETERS & RAG)
# ==============================================================================

RAG_CONFIG = {
    "score_threshold_code": float(os.getenv("RAG_SCORE_THRESHOLD_CODE", "0.40")),
    "score_threshold_docs": float(os.getenv("RAG_SCORE_THRESHOLD_DOCS", "0.55")),
    "top_k_code": int(os.getenv("RAG_TOP_K_CODE", "5")),
    "top_k_docs": int(os.getenv("RAG_TOP_K_DOCS", "3"))
}

# ==============================================================================

# ==============================================================================
# TELEGRAM
# ==============================================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
_allowed_users_env = os.getenv("ALLOWED_USERS", "")
_env_users = [int(x.strip()) for x in _allowed_users_env.split(",") if x.strip()]
ADMIN_USERS = _env_users

ALLOWED_USERS = set(_env_users)
USERS_FILE = os.path.join(os.path.dirname(__file__), "allowed_users.json")
if os.path.exists(USERS_FILE):
    import json
    try:
        with open(USERS_FILE, "r") as f:
            ALLOWED_USERS.update(json.load(f))
    except Exception as e:
        logger.error(f"Error loading allowed_users.json: {e}")
ALLOWED_USERS = list(ALLOWED_USERS)

def save_allowed_users():
    import json, os
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(list(ALLOWED_USERS), f)
    os.replace(tmp, USERS_FILE)


# ==============================================================================
# IMPOSTAZIONI DI SISTEMA (Hardcoded Estratti)
# ==============================================================================
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "qwen3-embedding-0.6b")
EMBEDDING_DIMS = int(os.getenv("EMBEDDING_DIMS", "768"))  # 768 via MRL (Qwen3 nativo 1024)
FLASHRANK_MODEL = os.getenv("FLASHRANK_MODEL", "ms-marco-MiniLM-L-6-v2")
Qwen3_RERANKER_MODEL = os.getenv("Qwen3_RERANKER_MODEL", "/root/models/Qwen3-Reranker-0.6B")
RERANKER_DEVICE = os.getenv("RERANKER_DEVICE", "cpu")  # cpu per non rubare VRAM alla chat

# Sicurezza Webhook
GIT_WEBHOOK_SECRET = os.getenv("GIT_WEBHOOK_SECRET", "")

API_RATE_LIMIT_DEFAULT = os.getenv("API_RATE_LIMIT_DEFAULT", "60/minute")
API_RATE_LIMIT_HEAVY = os.getenv("API_RATE_LIMIT_HEAVY", "5/minute")

WATCHDOG_BATCH_DELAY = float(os.getenv("WATCHDOG_BATCH_DELAY", "1.0"))
SEMANTIC_CACHE_THRESHOLD = float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.88"))
MEM0_STARTUP_DELAY = float(os.getenv("MEM0_STARTUP_DELAY", "4.0"))

# ==============================================================================
# CONFIGURAZIONE MEM0 E PRIVACY (Fix Telemetry PostHog)
# ==============================================================================
os.environ["MEM0_TELEMETRY"] = "false"  # mem0 2.x = variabile corretta
os.environ["MEM0_ENABLE_TELEMETRY"] = "false"  # legacy
os.environ["POSTHOG_DISABLED"] = "true"

MEM0_CONFIG = {
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "path": "./data/qdrant_local" if QDRANT_HOST == "local" else None,
            "host": QDRANT_HOST if QDRANT_HOST != "local" else None,
            "port": 6333 if QDRANT_HOST != "local" else None,
            "collection_name": f"collateral_memories_{VECTOR_DB_VERSION}",
            "embedding_model_dims": EMBEDDING_DIMS
        }
    },
    "llm": {
        "provider": "ollama",
        "config": {"model": OLLAMA_MODEL, "temperature": 0.0, "ollama_base_url": "http://127.0.0.1:8000"}
    },
    "embedder": {
        "provider": "ollama",
        "config": {"model": EMBEDDING_MODEL, "ollama_base_url": "http://127.0.0.1:8000"}
    }
}


# ==============================================================================
# MOTORE AST (Tree-sitter)
# ==============================================================================
AST_ENABLED = False
GO = PY = JS = TSX = C = CPP = JAVA = RUST = SQL = YAML = None
try:
    from tree_sitter import Language, Parser  # noqa: F401
    import tree_sitter_go, tree_sitter_python, tree_sitter_javascript, tree_sitter_typescript
    import tree_sitter_c, tree_sitter_cpp, tree_sitter_java, tree_sitter_rust, tree_sitter_sql, tree_sitter_yaml

    GO = Language(tree_sitter_go.language())
    PY = Language(tree_sitter_python.language())
    JS = Language(tree_sitter_javascript.language())
    TSX = Language(tree_sitter_typescript.language_tsx())
    C = Language(tree_sitter_c.language())
    CPP = Language(tree_sitter_cpp.language())
    JAVA = Language(tree_sitter_java.language())
    RUST = Language(tree_sitter_rust.language())
    SQL = Language(tree_sitter_sql.language())
    YAML = Language(tree_sitter_yaml.language())
    AST_ENABLED = True
    logger.info("🌳 Motore AST ATTIVO.")
except ImportError:
    logger.warning("⚠️ AST mancante.")

# ==============================================================================
# LIBRERIE OPZIONALI (Pathspec, Watchdog, Telegram)
# ==============================================================================
PATHSPEC_ENABLED = False
try:
    import pathspec  # noqa: F401
    PATHSPEC_ENABLED = True
    logger.info("🛡️ Libreria 'pathspec' ATTIVA.")
except ImportError:
    pass

WATCHDOG_ENABLED = False
try:
    from watchdog.observers import Observer  # noqa: F401
    from watchdog.events import FileSystemEventHandler  # noqa: F401
    WATCHDOG_ENABLED = True
except ImportError:
    pass

TELEGRAM_ENABLED_ENV = os.getenv("TELEGRAM_ENABLED", "auto").lower()
TELEGRAM_ENABLED = False

if TELEGRAM_ENABLED_ENV == "true":
    TELEGRAM_ENABLED = True
elif TELEGRAM_ENABLED_ENV == "false":
    TELEGRAM_ENABLED = False
else:
    # Auto-detection
    try:
        from telegram import Update  # noqa: F401
        from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes  # noqa: F401
        TELEGRAM_ENABLED = True
        logging.getLogger("telegram").setLevel(logging.WARNING)
    except ImportError:
        TELEGRAM_ENABLED = False

USERBOT_ENABLED = False
try:
    import telethon  # noqa: F401
    USERBOT_ENABLED = True
except ImportError:
    pass

USERBOT_API_ID = os.getenv("TELEGRAM_API_ID", "")
USERBOT_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
USERBOT_PHONE = os.getenv("TELEGRAM_PHONE", "")
_allowed_chats_env = os.getenv("ALLOWED_PRIVATE_CHATS", "")
USERBOT_ALLOWED_CHATS = [int(x.strip()) if x.strip().lstrip('-').isdigit() else x.strip() for x in _allowed_chats_env.split(",") if x.strip()]


# ==============================================================================
# RIMOSSO: Legacy constants sovrascrivevano le variabili d'ambiente.
# I valori ora vengono letti esclusivamente dalle env (righe 71-91).
# ==============================================================================
