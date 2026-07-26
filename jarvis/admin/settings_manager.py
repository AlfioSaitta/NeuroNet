"""
settings_manager.py — SETTINGS_META (73 env var config), _persist_env, SETTINGS_OVERRIDES
===========================================================
Estratto da admin/dashboard.py per modularizzazione (H7).
"""

import os
import logging

logger = logging.getLogger(__name__)

# ── Path .env ──

_ENV_FILE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


# ── Persistenza atomica ──

def _persist_env(key: str, value) -> bool:
    """Write a single key=value to .env file atomically.

    Reads .env line by line, finds the line with KEY=... (not commented),
    replaces the value inline, or appends at end if not found.
    Uses os.replace() for atomic write.
    """
    if not os.path.exists(_ENV_FILE_PATH):
        logger.warning(f".env not found at {_ENV_FILE_PATH}")
        return False

    # Format the value for .env
    if isinstance(value, bool):
        val_str = "true" if value else "false"
    elif value is None:
        val_str = ""
    else:
        val_str = str(value)

    try:
        with open(_ENV_FILE_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()

        key_prefix = f"{key}="
        found = False
        new_lines = []
        for line in lines:
            stripped = line.strip()
            # Only match lines that start with KEY= (not comments, not empty)
            if stripped.startswith(key_prefix) and not stripped.startswith("#"):
                new_lines.append(f"{key}={val_str}\n")
                found = True
            else:
                new_lines.append(line)

        if not found:
            # Append at end
            new_lines.append(f"\n# Added by dashboard settings\n{key}={val_str}\n")

        # Atomic write
        tmp_path = _ENV_FILE_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        os.replace(tmp_path, _ENV_FILE_PATH)
        logger.info(f"Persisted {key}={val_str} to .env")
        return True
    except Exception as e:
        logger.error(f"Failed to persist {key} to .env: {e}")
        return False


# ── Settings Metadata ──

SETTINGS_META: dict[str, dict] = {
    # ═══════════════════════════════════════════
    # 🧠 Inferenza
    # ═══════════════════════════════════════════
    "LLAMA_MODEL_PATH": {
        "type": "text", "editable": True,
        "label": "LLM Model Path", "category": "🧠 Inferenza",
        "description": "Percorso del file GGUF del modello di chat principale",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": False,
        "basic": True,
    },
    "LLAMA_EMBED_MODEL_PATH": {
        "type": "text", "editable": True,
        "label": "Embedding Model Path", "category": "📚 Embedding & Reranker",
        "description": "Percorso del file GGUF per embedding (es. Qwen3-Embedding)",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": False,
        "basic": True,
    },
    "LLM_NUM_CTX": {
        "type": "number", "editable": True,
        "label": "Context Size (n_ctx)", "category": "🧠 Inferenza",
        "description": "Dimensione massima del contesto in token",
        "options": None, "unit": "token", "min": 512, "max": 131072, "step": 1024,
        "restart_required": True, "sensitive": False,
        "basic": True,
    },
    "LLM_FLASH_ATTN": {
        "type": "bool", "editable": True,
        "label": "Flash Attention", "category": "🧠 Inferenza",
        "description": "Abilita Flash Attention per ridurre VRAM del 30-50%",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": False,
        "basic": True,
    },
    "LLM_BATCH_SIZE": {
        "type": "number", "editable": True,
        "label": "Batch Size", "category": "🧠 Inferenza",
        "description": "Token paralleli nel prompt processing (più alto = più VRAM)",
        "options": None, "unit": "token", "min": 64, "max": 2048, "step": 64,
        "restart_required": True, "sensitive": False,
        "basic": False,
    },
    "LLM_UBATCH_SIZE": {
        "type": "number", "editable": True,
        "label": "Micro-Batch Size", "category": "🧠 Inferenza",
        "description": "Micro-batch per CUDA graph interno (deve essere ≤ Batch Size)",
        "options": None, "unit": "token", "min": 64, "max": 2048, "step": 64,
        "restart_required": True, "sensitive": False,
        "basic": False,
    },
    "LLM_TEMPERATURE": {
        "type": "float", "editable": True,
        "label": "Temperature", "category": "🧠 Inferenza",
        "description": "Creatività del modello (0=deterministico, 1=creativo, 2=caotico)",
        "options": None, "unit": None, "min": 0.0, "max": 2.0, "step": 0.1,
        "restart_required": False, "sensitive": False,
        "basic": True,
    },
    "LLM_REPEAT_PENALTY": {
        "type": "float", "editable": True,
        "label": "Repeat Penalty", "category": "🧠 Inferenza",
        "description": "Penalità per evitare ripetizioni di token",
        "options": None, "unit": None, "min": 1.0, "max": 2.0, "step": 0.05,
        "restart_required": False, "sensitive": False,
        "basic": False,
    },
    "LLM_TOP_P": {
        "type": "float", "editable": True,
        "label": "Top-P (Nucleus)", "category": "🧠 Inferenza",
        "description": "Cumulative probability cutoff per nucleus sampling",
        "options": None, "unit": None, "min": 0.0, "max": 1.0, "step": 0.05,
        "restart_required": False, "sensitive": False,
        "basic": False,
    },
    "LLM_TOP_K": {
        "type": "number", "editable": True,
        "label": "Top-K", "category": "🧠 Inferenza",
        "description": "Numero di token candidati al sampling",
        "options": None, "unit": None, "min": 1, "max": 100, "step": 1,
        "restart_required": False, "sensitive": False,
        "basic": False,
    },
    "LLM_NUM_PREDICT": {
        "type": "number", "editable": True,
        "label": "Max Predict", "category": "🧠 Inferenza",
        "description": "Limite massimo token output per risposta",
        "options": None, "unit": "token", "min": 64, "max": 32768, "step": 256,
        "restart_required": False, "sensitive": False,
        "basic": False,
    },
    "LLM_MAX_TOKENS": {
        "type": "number", "editable": True,
        "label": "Max Tokens", "category": "🧠 Inferenza",
        "description": "Limite massimo token totale (alias per LLM_NUM_PREDICT)",
        "options": None, "unit": "token", "min": 64, "max": 32768, "step": 256,
        "restart_required": False, "sensitive": False,
        "basic": False,
    },
    "LLM_THINKING_MODE": {
        "type": "bool", "editable": True,
        "label": "Thinking Mode", "category": "🧠 Inferenza",
        "description": "Abilita tag <|think|> per modelli che lo supportano (Gemma 4, DeepSeek)",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": False,
        "basic": True,
    },
    "LLM_PRESENCE_PENALTY": {
        "type": "float", "editable": True,
        "label": "Presence Penalty", "category": "🧠 Inferenza",
        "description": "Penalità per argomenti/topic già menzionati",
        "options": None, "unit": None, "min": 0.0, "max": 2.0, "step": 0.1,
        "restart_required": False, "sensitive": False,
        "basic": False,
    },
    "LLM_FREQUENCY_PENALTY": {
        "type": "float", "editable": True,
        "label": "Frequency Penalty", "category": "🧠 Inferenza",
        "description": "Penalità per token già usati di frequente",
        "options": None, "unit": None, "min": 0.0, "max": 2.0, "step": 0.1,
        "restart_required": False, "sensitive": False,
        "basic": False,
    },
    "GATEKEEPER_MODEL_PATH": {
        "type": "text", "editable": True,
        "label": "Gatekeeper Model", "category": "🧠 Gatekeeper",
        "description": "Percorso del modello GGUF per Gatekeeper (vuoto = disabilitato)",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": False,
        "basic": True,
    },
    "GATEKEEPER_N_CTX": {
        "type": "number", "editable": True,
        "label": "Gatekeeper Context", "category": "🧠 Gatekeeper",
        "description": "Contesto massimo per Gatekeeper",
        "options": None, "unit": "token", "min": 512, "max": 32768, "step": 512,
        "restart_required": True, "sensitive": False,
        "basic": False,
    },
    "GATEKEEPER_N_GPU_LAYERS": {
        "type": "number", "editable": True,
        "label": "Gatekeeper GPU Layers", "category": "🧠 Gatekeeper",
        "description": "Layer Gatekeeper su GPU (-1 = tutti, 0 = CPU)",
        "options": None, "unit": None, "min": -1, "max": 99, "step": 1,
        "restart_required": True, "sensitive": False,
        "basic": False,
    },
    "GATEKEEPER_N_THREADS": {
        "type": "number", "editable": True,
        "label": "Gatekeeper Threads", "category": "🧠 Gatekeeper",
        "description": "Thread CPU per Gatekeeper",
        "options": None, "unit": None, "min": 1, "max": 32, "step": 1,
        "restart_required": True, "sensitive": False,
        "basic": False,
    },

    # ═══════════════════════════════════════════
    # 📚 RAG & Retrieval
    # ═══════════════════════════════════════════
    "EMBEDDING_DIMS": {
        "type": "number", "editable": True,
        "label": "Embedding Dimensions", "category": "📚 Embedding & Reranker",
        "description": "Dimensionalità delle embedding (richiede re-ingestione)",
        "options": None, "unit": "dim", "min": 128, "max": 4096, "step": 64,
        "restart_required": True, "sensitive": False,
        "basic": True,
    },
    "EMBEDDING_MODEL": {
        "type": "text", "editable": True,
        "label": "Embedding Model", "category": "📚 Embedding & Reranker",
        "description": "Modello per embedding dei documenti (es. qwen3-embedding-0.6b)",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": False,
        "basic": True,
    },
    "FLASHRANK_MODEL": {
        "type": "text", "editable": True,
        "label": "FlashRank Model", "category": "📚 Embedding & Reranker",
        "description": "Modello di re-ranking fallback ONNX",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": False,
        "basic": False,
    },
    "RERANKER_DEVICE": {
        "type": "select", "editable": True,
        "label": "Reranker Device", "category": "📚 Embedding & Reranker",
        "description": "Device per Qwen3-Reranker (cpu = non ruba VRAM, cuda = più veloce)",
        "options": ["cpu", "cuda"], "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": False,
        "basic": False,
    },
    "RAG_SCORE_THRESHOLD_CODE": {
        "type": "float", "editable": True,
        "label": "Score Threshold (Code)", "category": "📚 RAG",
        "description": "Soglia minima similarità per chunk di codice",
        "options": None, "unit": None, "min": 0.0, "max": 1.0, "step": 0.05,
        "restart_required": False, "sensitive": False,
        "basic": False,
    },
    "RAG_SCORE_THRESHOLD_DOCS": {
        "type": "float", "editable": True,
        "label": "Score Threshold (Docs)", "category": "📚 RAG",
        "description": "Soglia minima similarità per chunk di documentazione",
        "options": None, "unit": None, "min": 0.0, "max": 1.0, "step": 0.05,
        "restart_required": False, "sensitive": False,
        "basic": False,
    },
    "RAG_TOP_K_CODE": {
        "type": "number", "editable": True,
        "label": "Top-K Code", "category": "📚 RAG",
        "description": "Numero di chunk di codice da passare al LLM",
        "options": None, "unit": "chunks", "min": 1, "max": 20, "step": 1,
        "restart_required": False, "sensitive": False,
        "basic": False,
    },
    "RAG_TOP_K_DOCS": {
        "type": "number", "editable": True,
        "label": "Top-K Docs", "category": "📚 RAG",
        "description": "Numero di chunk di documenti da passare al LLM",
        "options": None, "unit": "chunks", "min": 1, "max": 20, "step": 1,
        "restart_required": False, "sensitive": False,
        "basic": False,
    },
    "RAG_CHUNK_SIZE": {
        "type": "number", "editable": True,
        "label": "Chunk Size", "category": "📚 RAG",
        "description": "Dimensione in caratteri di ogni chunk per RAG",
        "options": None, "unit": "chars", "min": 128, "max": 4096, "step": 128,
        "restart_required": True, "sensitive": False,
        "basic": False,
    },
    "RAG_CHUNK_OVERLAP": {
        "type": "number", "editable": True,
        "label": "Chunk Overlap", "category": "📚 RAG",
        "description": "Sovrapposizione tra chunk consecutivi",
        "options": None, "unit": "chars", "min": 0, "max": 512, "step": 32,
        "restart_required": True, "sensitive": False,
        "basic": False,
    },
    "RAG_EMBEDDING_BATCH_SIZE": {
        "type": "number", "editable": True,
        "label": "Embedding Batch Size", "category": "📚 RAG",
        "description": "Numero massimo di embedding concorrenti",
        "options": None, "unit": None, "min": 1, "max": 64, "step": 1,
        "restart_required": False, "sensitive": False,
        "basic": False,
    },
    "SEMANTIC_CACHE_THRESHOLD": {
        "type": "float", "editable": True,
        "label": "Semantic Cache Threshold", "category": "📚 RAG",
        "description": "Soglia similarità cosine per cache semantica (0-1)",
        "options": None, "unit": None, "min": 0.0, "max": 1.0, "step": 0.02,
        "restart_required": False, "sensitive": False,
        "basic": False,
    },
    "MEM0_STARTUP_DELAY": {
        "type": "float", "editable": True,
        "label": "Mem0 Startup Delay", "category": "📚 RAG",
        "description": "Secondi di attesa all'avvio prima di inizializzare Mem0",
        "options": None, "unit": "s", "min": 0.0, "max": 30.0, "step": 0.5,
        "restart_required": True, "sensitive": False, "basic": False,
    },

    # ═══════════════════════════════════════════
    # 🔌 Servizi Esterni
    # ═══════════════════════════════════════════
    "QDRANT_HOST": {
        "type": "text", "editable": True,
        "label": "Qdrant Host", "category": "🔌 Servizi",
        "description": "Host del database vettoriale Qdrant ('local' = embedded)",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": False,
        "basic": True,
    },
    "SEARXNG_HOST": {
        "type": "text", "editable": True,
        "label": "SearXNG URL", "category": "🔌 Servizi",
        "description": "URL del motore di ricerca SearXNG",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": False, "sensitive": False,
        "basic": True,
    },
    "CRAWL4AI_HOST": {
        "type": "text", "editable": True,
        "label": "Crawl4AI URL", "category": "🔌 Servizi",
        "description": "URL del crawler web Crawl4AI",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": False, "sensitive": False, "basic": True,
    },
    "CRAWL4AI_API_TOKEN": {
        "type": "secret", "editable": True,
        "label": "Crawl4AI API Token", "category": "🔌 Servizi",
        "description": "Token per autenticazione API Crawl4AI",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": False, "sensitive": True, "basic": False,
    },
    "EXTERNAL_GPU_URL": {
        "type": "text", "editable": True,
        "label": "External GPU URL", "category": "🔌 Servizi",
        "description": "URL del nodo GPU esterno per offloading inferenza (vuoto = locale)",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": False,
        "basic": True,
    },
    "GIT_WEBHOOK_SECRET": {
        "type": "secret", "editable": True,
        "label": "Git Webhook Secret", "category": "🔌 Servizi",
        "description": "Segreto per validare webhook Git",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": False, "sensitive": True,
        "basic": False,
    },

    # ═══════════════════════════════════════════
    # ⚙️ Sistema
    # ═══════════════════════════════════════════
    "WATCHDOG_ENABLED": {
        "type": "bool", "editable": True,
        "label": "Watchdog Filesystem", "category": "⏱️ Watchdog",
        "description": "Abilita il monitoraggio real-time del filesystem per RAG",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": False,
        "basic": True,
    },
    "WATCHDOG_TIMEOUT": {
        "type": "number", "editable": True,
        "label": "Watchdog Interval", "category": "⏱️ Watchdog",
        "description": "Secondi tra scansioni watchdog",
        "options": None, "unit": "s", "min": 1, "max": 300, "step": 1,
        "restart_required": False, "sensitive": False,
        "basic": True,
    },
    "WATCHDOG_WATCH_MODE": {
        "type": "select", "editable": True,
        "label": "Watchdog Watch Mode", "category": "⏱️ Watchdog",
        "description": "Directory da monitorare (per_project = solo indicizzate, full = tutta WORKSPACE_DIR)",
        "options": ["per_project", "full"], "unit": None, "min": None, "max": None, "step": None,
        "restart_required": False, "sensitive": False,
        "basic": False,
    },
    "WATCHDOG_BATCH_DELAY": {
        "type": "float", "editable": True,
        "label": "Watchdog Batch Delay", "category": "⏱️ Watchdog",
        "description": "Debounce in secondi per accorpare modifiche rapide ai file",
        "options": None, "unit": "s", "min": 0.0, "max": 10.0, "step": 0.5,
        "restart_required": False, "sensitive": False,
        "basic": False,
    },
    "API_RATE_LIMIT_DEFAULT": {
        "type": "text", "editable": True,
        "label": "Rate Limit (Default)", "category": "🚦 Rate Limit",
        "description": "Limite generale per /api/chat, /api/generate (es. 60/minute)",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": False, "sensitive": False,
        "basic": True,
    },
    "API_RATE_LIMIT_HEAVY": {
        "type": "text", "editable": True,
        "label": "Rate Limit (Heavy)", "category": "🚦 Rate Limit",
        "description": "Limite per endpoint pesanti che causano inferenza (es. 5/minute)",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": False, "sensitive": False,
        "basic": False,
    },
    "API_RATE_LIMIT_EMBED": {
        "type": "text", "editable": True,
        "label": "Rate Limit (Embedding)", "category": "🚦 Rate Limit",
        "description": "Limite per endpoint di embedding leggeri (es. 600/minute)",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": False, "sensitive": False,
        "basic": False,
    },
    "WORKSPACE_DIR": {
        "type": "text", "editable": True,
        "label": "Workspace Directory", "category": "⚙️ Sistema",
        "description": "Directory principale per auto-scoperta progetti RAG",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": False,
        "basic": True,
    },
    "BOT_NAME": {
        "type": "text", "editable": True,
        "label": "Bot Name", "category": "⚙️ Sistema",
        "description": "Nome visualizzato del bot (usato nei log e risposte)",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": False, "sensitive": False,
        "basic": True,
    },
    "HOST_FS_PREFIX": {
        "type": "text", "editable": False,
        "label": "", "category": "",
        "description": "",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": False, "sensitive": False,
        "basic": False,
    },
    "EXTERNAL_PROJECTS": {
        "type": "text", "editable": False,
        "label": "", "category": "",
        "description": "",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": False, "sensitive": False,
        "basic": False,
    },

    # ═══════════════════════════════════════════
    # 🤖 Telegram
    # ═══════════════════════════════════════════
    "TELEGRAM_ENABLED": {
        "type": "bool", "editable": True,
        "label": "Telegram Enabled", "category": "🤖 Telegram",
        "description": "Abilita/disabilita il bot Telegram",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": False,
        "basic": True,
    },
    "TELEGRAM_TOKEN": {
        "type": "secret", "editable": True,
        "label": "Telegram Bot Token", "category": "🤖 Telegram",
        "description": "Token del bot rilasciato da @BotFather",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": True,
        "basic": True,
    },
    "ALLOWED_USERS": {
        "type": "text", "editable": True,
        "label": "Allowed Users", "category": "🤖 Telegram",
        "description": "ID Telegram degli utenti autorizzati (separati da virgola)",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": False,
        "basic": True,
    },
    "ALLOWED_PRIVATE_CHATS": {
        "type": "text", "editable": True,
        "label": "Allowed Private Chats", "category": "🤖 Telegram",
        "description": "Lista di ID chat private consentite per userbot (separati da virgola)",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": False,
        "basic": False,
    },
    "TELEGRAM_API_ID": {
        "type": "number", "editable": True,
        "label": "Telegram API ID", "category": "🤖 Telegram",
        "description": "App API ID da my.telegram.org",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": True,
        "basic": False,
    },
    "TELEGRAM_API_HASH": {
        "type": "secret", "editable": True,
        "label": "Telegram API Hash", "category": "🤖 Telegram",
        "description": "App API Hash da my.telegram.org",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": True,
        "basic": False,
    },
    "TELEGRAM_PHONE": {
        "type": "text", "editable": True,
        "label": "Telegram Phone", "category": "🤖 Telegram",
        "description": "Numero di telefono in formato internazionale (+39...) per client Telethon",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": True,
        "basic": False,
    },

    # ═══════════════════════════════════════════
    # 🔗 MCP (Model Context Protocol)
    # ═══════════════════════════════════════════
    "MCP_ENABLED": {
        "type": "bool", "editable": True,
        "label": "MCP Enabled", "category": "🔗 MCP",
        "description": "Abilita/disabilita il sistema MCP (tool esterni)",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": False,
        "basic": True,
    },
    "MCP_AUTO_INIT": {
        "type": "bool", "editable": True,
        "label": "MCP Auto Init", "category": "🔗 MCP",
        "description": "Inizializza automaticamente i server MCP all'avvio",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": False,
        "basic": False,
    },
    "MCP_SKILL_EMBEDDED": {
        "type": "bool", "editable": True,
        "label": "MCP Skill Embedded", "category": "🔗 MCP",
        "description": "Carica gli MCP embedded nelle skill al startup",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": False,
        "basic": False,
    },
    "MCP_CONFIG_PATHS": {
        "type": "text", "editable": True,
        "label": "MCP Config Paths", "category": "🔗 MCP",
        "description": "Percorsi aggiuntivi per file di configurazione MCP (separati da virgola)",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": False, "sensitive": False,
        "basic": False,
    },

    # ═══════════════════════════════════════════
    # ☁️ Provider Cloud
    # ═══════════════════════════════════════════
    "GEMINI_API_KEY": {
        "type": "secret", "editable": True,
        "label": "Gemini API Key", "category": "☁️ Provider",
        "description": "API key per Google Gemini (aistudio.google.com/apikey)",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": False, "sensitive": True,
        "basic": True,
    },
    "GEMINI_MODEL": {
        "type": "text", "editable": True,
        "label": "Gemini Model", "category": "☁️ Provider",
        "description": "Modello Gemini da utilizzare (es. gemini-2.5-pro-exp-03-25)",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": False, "sensitive": False,
        "basic": True,
    },
    "EXTERNAL_PROVIDER_STRATEGY": {
        "type": "select", "editable": True,
        "label": "Provider Strategy", "category": "☁️ Provider",
        "description": "Strategia di routing per provider esterni (disabled = solo locale)",
        "options": ["disabled", "fallback_only", "selective", "parallel", "multimodal"],
        "unit": None, "min": None, "max": None, "step": None,
        "restart_required": False, "sensitive": False,
        "basic": True,
    },

    # ═══════════════════════════════════════════
    # 🧬 Synaptiq
    # ═══════════════════════════════════════════
    "SYNAPTIQ_STORAGE_PATH": {
        "type": "text", "editable": True,
        "label": "Synaptiq Storage Path", "category": "🧬 Synaptiq",
        "description": "Percorso del database locale Synaptiq (.lb file)",
        "options": None, "unit": None, "min": None, "max": None, "step": None,
        "restart_required": True, "sensitive": False,
        "basic": False,
    },
    "SYNAPTIQ_EMBEDDING_TIER": {
        "type": "select", "editable": True,
        "label": "Synaptiq Embedding Tier", "category": "🧬 Synaptiq",
        "description": "Qualità embedding per Synaptiq (quality = migliore, speed = veloce)",
        "options": ["quality", "speed", "balanced"],
        "unit": None, "min": None, "max": None, "step": None,
        "restart_required": False, "sensitive": False,
        "basic": False,
    },

    # ═══════════════════════════════════════════
    # Hidden (non mostrati in UI — label vuota)
    # ═══════════════════════════════════════════
    "N_GPU_LAYERS":         {"type": "number", "editable": False, "label": "", "category": "", "description": "", "options": None, "unit": None, "min": None, "max": None, "step": None, "restart_required": False, "sensitive": False, "basic": False},
    "VECTOR_DB_VERSION":    {"type": "text",   "editable": False, "label": "", "category": "", "description": "", "options": None, "unit": None, "min": None, "max": None, "step": None, "restart_required": False, "sensitive": False, "basic": False},
    "SYNAPTIQ_ENABLED":     {"type": "bool",   "editable": False, "label": "", "category": "", "description": "", "options": None, "unit": None, "min": None, "max": None, "step": None, "restart_required": False, "sensitive": False, "basic": False},
    "DATA_DIR":             {"type": "text",   "editable": False, "label": "", "category": "", "description": "", "options": None, "unit": None, "min": None, "max": None, "step": None, "restart_required": False, "sensitive": False, "basic": False},
    "MODELS_DIR":           {"type": "text",   "editable": False, "label": "", "category": "", "description": "", "options": None, "unit": None, "min": None, "max": None, "step": None, "restart_required": False, "sensitive": False, "basic": False},
    "DOCUMENTS_DIR":        {"type": "text",   "editable": False, "label": "", "category": "", "description": "", "options": None, "unit": None, "min": None, "max": None, "step": None, "restart_required": False, "sensitive": False, "basic": False},
    "MODEL_ID":             {"type": "text",   "editable": False, "label": "", "category": "", "description": "", "options": None, "unit": None, "min": None, "max": None, "step": None, "restart_required": False, "sensitive": False, "basic": False},
    "STATE_FILE":           {"type": "text",   "editable": False, "label": "", "category": "", "description": "", "options": None, "unit": None, "min": None, "max": None, "step": None, "restart_required": False, "sensitive": False, "basic": False},
}

SETTINGS_OVERRIDES: dict = {}
