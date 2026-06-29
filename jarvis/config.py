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
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:3b-instruct-q5_K_M")
QDRANT_HOST = os.getenv("QDRANT_HOST", "local")
SEARXNG_HOST = os.getenv("SEARXNG_HOST", "http://searxng:8080").rstrip('/')
CRAWL4AI_HOST = os.getenv("CRAWL4AI_HOST", "http://crawl4ai:11235").rstrip('/')
BOT_NAME = os.getenv("BOT_NAME", "Jarvis")
EXTERNAL_GPU_URL = os.getenv("EXTERNAL_GPU_URL", "")
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
# ==============================================================================
# PERCORSI MODELLI GGUF
# ==============================================================================
LLAMA_MODEL_PATH = os.getenv("LLAMA_MODEL_PATH", "./models/qwen2.5-coder-3b.gguf")
LLAMA_EMBED_MODEL_PATH = os.getenv("LLAMA_EMBED_MODEL_PATH", "./models/Qwen3-Embedding-0.6B-Q8_0.gguf")

# ==============================================================================
# CONFIGURAZIONE INFERENZA LLM
# ==============================================================================
N_GPU_LAYERS = int(os.getenv("N_GPU_LAYERS", "20"))
LLM_NUM_CTX = _llm_num_ctx
LLM_BATCH_SIZE = int(os.getenv("LLM_BATCH_SIZE", "128"))
LLM_UBATCH_SIZE = int(os.getenv("LLM_UBATCH_SIZE", "128"))
LLM_FLASH_ATTN = os.getenv("LLM_FLASH_ATTN", "false").lower() == "true"
LLM_CHAT_FORMAT = os.getenv("LLM_CHAT_FORMAT") or None
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2048"))
# Raw env var flag per runtime checks (es. metadata override)
LLM_THINKING_MODE_RAW = os.getenv("LLM_THINKING_MODE", "")

# Rilevamento automatico famiglia modello dal GGUF.
# Usato per thinking mode, temperatura, contesto massimo e compatibilità RAG.
from model_profiles import detect_model_family
MODEL_PROFILE = detect_model_family(LLAMA_MODEL_PATH)
logger.info(f"🤖 Modello rilevato: {MODEL_PROFILE.family} ({MODEL_PROFILE.description}) | "
            f"thinking={'✅' if MODEL_PROFILE.thinking_support else '❌'} | "
            f"unsloth={'✅' if MODEL_PROFILE.unsloth_optimized else '❌'} | "
            f"chat_format={MODEL_PROFILE.chat_format} | "
            f"ctx max={MODEL_PROFILE.max_ctx}")

# Thinking Mode: default basato sul modello caricato, sovrascrivibile via .env
_THINKING_ENV = os.getenv("LLM_THINKING_MODE", "")
if _THINKING_ENV:
    LLM_THINKING_MODE: bool = _THINKING_ENV.lower() == "true"
else:
    LLM_THINKING_MODE: bool = MODEL_PROFILE.thinking_support

# ==============================================================================
# PERCORSI CENTRALIZZATI
# ==============================================================================
# DATA_DIR:       dati persistenti (stato RAG, cache, backup Mem0)
# MODELS_DIR:     modelli GGUF, reranker, embedding
# DOCUMENTS_DIR:  documenti monitorati per RAG (symlink esterni)
# HOST_FS_PREFIX: prefisso mount host in Docker (vuoto per esecuzione diretta su host)
DATA_DIR = os.getenv("DATA_DIR", "/app/mem0_data_v3")
MODELS_DIR = os.getenv("MODELS_DIR", "/app/models")
DOCUMENTS_DIR = os.getenv("DOCUMENTS_DIR", "/app/documents")
HOST_FS_PREFIX = os.getenv("HOST_FS_PREFIX", "/host_fs")

WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "")

# Auto-discover projects from WORKSPACE_DIR
WORKSPACE_PROJECTS: list[str] = []
if WORKSPACE_DIR and os.path.isdir(WORKSPACE_DIR):
    try:
        WORKSPACE_PROJECTS = sorted([
            os.path.join(WORKSPACE_DIR, d)
            for d in os.listdir(WORKSPACE_DIR)
            if os.path.isdir(os.path.join(WORKSPACE_DIR, d))
            and not d.startswith('.')
        ])
        logger.info(f"📂 Workspace auto-discovered {len(WORKSPACE_PROJECTS)} projects from {WORKSPACE_DIR}")
    except OSError as e:
        logger.warning(f"⚠️ Error scanning WORKSPACE_DIR ({WORKSPACE_DIR}): {e}")

VECTOR_DB_VERSION = os.getenv("VECTOR_DB_VERSION", "v1")
EXTERNAL_PROJECTS = os.getenv("EXTERNAL_PROJECTS", "")
STATE_FILE = os.getenv("STATE_FILE", os.path.join(DATA_DIR, f"rag_state_{VECTOR_DB_VERSION}.json"))
CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "512"))
CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "0"))
MAX_CONCURRENT_EMBEDDINGS = int(os.getenv("RAG_EMBEDDING_BATCH_SIZE", "8"))
DOC_DIR = DOCUMENTS_DIR
DOC_COLLECTION = f"collateral_documents_{VECTOR_DB_VERSION}"
INFRA_FILE = os.getenv("INFRA_FILE", os.path.join(DATA_DIR, "infrastructure.json"))
MEMORY_BACKUP_FILE = os.getenv("MEMORY_BACKUP_FILE", os.path.join(DATA_DIR, "memory_backup.json"))

# Cache HuggingFace / FastEmbed / tiktoken
os.environ["HF_HOME"] = os.path.join(DATA_DIR, "hf_cache")
os.environ["FASTEMBED_CACHE_PATH"] = os.path.join(DATA_DIR, "fastembed_cache")
os.environ["TIKTOKEN_CACHE_DIR"] = os.path.join(DATA_DIR, "tiktoken_cache")

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
_env_users_str = [x.strip() for x in _allowed_users_env.split(",") if x.strip()]
# Manteniamo ADMIN_USERS come lista di stringhe per compatibilità con str(update.effective_user.id) nel bot
ADMIN_USERS = _env_users_str

ALLOWED_USERS = set(_env_users_str)
USERS_FILE = os.path.join(os.path.dirname(__file__), "allowed_users.json")
if os.path.exists(USERS_FILE):
    import json
    try:
        with open(USERS_FILE, "r") as f:
            ALLOWED_USERS.update(str(u) for u in json.load(f))
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
# MCP CONFIG
# ==============================================================================
MCP_ENABLED = os.getenv("MCP_ENABLED", "true").lower() in ("1", "true", "yes")
MCP_CONFIG_PATHS_JSON = os.getenv("MCP_CONFIG_PATHS", "")  # comma-separated extra paths
MCP_AUTO_INIT = os.getenv("MCP_AUTO_INIT", "true").lower() in ("1", "true", "yes")
MCP_SKILL_EMBEDDED = os.getenv("MCP_SKILL_EMBEDDED", "true").lower() in ("1", "true", "yes")

# ==============================================================================
# IMPOSTAZIONI DI SISTEMA (Hardcoded Estratti)
# ==============================================================================
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "qwen3-embedding-0.6b")
EMBEDDING_DIMS = int(os.getenv("EMBEDDING_DIMS", "768"))  # 768 via MRL (Qwen3 nativo 1024)
FLASHRANK_MODEL = os.getenv("FLASHRANK_MODEL", "ms-marco-MiniLM-L-6-v2")
Qwen3_RERANKER_MODEL = os.getenv("Qwen3_RERANKER_MODEL", os.path.join(MODELS_DIR, "Qwen3-Reranker-0.6B"))
QENABLED_QWEN3_RERANKER = os.getenv("QENABLED_QWEN3_RERANKER", "").lower() in ("1", "true", "yes")
RERANKER_DEVICE = os.getenv("RERANKER_DEVICE", "cpu")  # cpu per non rubare VRAM alla chat

# Sicurezza Webhook
GIT_WEBHOOK_SECRET = os.getenv("GIT_WEBHOOK_SECRET", "")

API_RATE_LIMIT_DEFAULT = os.getenv("API_RATE_LIMIT_DEFAULT", "60/minute")
API_RATE_LIMIT_HEAVY = os.getenv("API_RATE_LIMIT_HEAVY", "5/minute")
API_RATE_LIMIT_EMBED = os.getenv("API_RATE_LIMIT_EMBED", "600/minute")

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

# Watchdog filesystem: auto-detected da presenza libreria, sovrascrivibile via .env
_WATCHDOG_LIB_AVAILABLE = False
try:
    from watchdog.observers import Observer  # noqa: F401
    from watchdog.events import FileSystemEventHandler  # noqa: F401
    _WATCHDOG_LIB_AVAILABLE = True
except ImportError:
    pass

_WATCHDOG_ENV = os.getenv("WATCHDOG_ENABLED", "").lower()
if _WATCHDOG_ENV == "true":
    WATCHDOG_ENABLED = True
elif _WATCHDOG_ENV == "false":
    WATCHDOG_ENABLED = False
else:
    WATCHDOG_ENABLED = _WATCHDOG_LIB_AVAILABLE

# PollingObserver intervallo in secondi (default 5s — riduce CPU vs 1s)
WATCHDOG_TIMEOUT = int(os.getenv("WATCHDOG_TIMEOUT", "5"))
# Modalità watch: "per_project" (solo directory progetto) o "full" (intero WORKSPACE_DIR)
WATCHDOG_WATCH_MODE = os.getenv("WATCHDOG_WATCH_MODE", "per_project").lower()

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

# ==============================================================================
# PROVIDER ESTERNI (Gemini, Claude, ecc.)
# ==============================================================================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro-exp-03-25")
EXTERNAL_PROVIDER_STRATEGY = os.getenv("EXTERNAL_PROVIDER_STRATEGY", "fallback_only")

PROVIDER_CONFIG = {
    "strategy": EXTERNAL_PROVIDER_STRATEGY,
    "gemini_api_key": GEMINI_API_KEY,
    "gemini_model": GEMINI_MODEL,
}

USERBOT_API_ID = os.getenv("TELEGRAM_API_ID", "")
USERBOT_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
USERBOT_PHONE = os.getenv("TELEGRAM_PHONE", "")
_allowed_chats_env = os.getenv("ALLOWED_PRIVATE_CHATS", "")
USERBOT_ALLOWED_CHATS = [int(x.strip()) if x.strip().lstrip('-').isdigit() else x.strip() for x in _allowed_chats_env.split(",") if x.strip()]


# ==============================================================================
# RIMOSSO: Legacy constants sovrascrivevano le variabili d'ambiente.
# I valori ora vengono letti esclusivamente dalle env (righe 71-91).
# ==============================================================================
