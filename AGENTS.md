# 🤖 AGENTS.md — Guida Operativa per Agenti AI

> **Questo file è destinato esclusivamente agli agenti AI che lavorano su questo progetto.**  
> Contiene tutto il contesto necessario per operare autonomamente senza errori.  
> **Data ultimo aggiornamento:** 2026-07-27 (Hardware profile auto-detection per cambio modello sicuro)

---

## 📋 Indice Rapido

1. [Identità del Progetto](#1-identità-del-progetto)
2. [Architettura del Sistema](#2-architettura-del-sistema)
3. [Struttura File e Responsabilità](#3-struttura-file-e-responsabilità)
4. [Configurazione e Variabili d'Ambiente](#4-configurazione-e-variabili-dambiente)
5. [Modelli LLM in Uso](#5-modelli-llm-in-uso)
6. [Hardware Profile Auto-Detection](#6-hardware-profile-auto-detection)
7. [Topologia di Rete Master/Worker](#7-topologia-di-rete-masterworker)
8. [Stato Attuale dell'Implementazione](#8-stato-attuale-dellimplementazione)
9. [Regole Operative per gli Agenti](#9-regole-operative-per-gli-agenti)
10. [Pattern di Codice e Convenzioni](#10-pattern-di-codice-e-convenzioni)
11. [Bug Noti e Workaround](#11-bug-noti-e-workaround)

---

## 1. Identità del Progetto

**Nome:** Ecosistema AI Omnisciente — Chameleon Cognitive Stack  
**Proprietario:** Alfio Saitta / Collateral Studios  
**Scopo:** Sistema AI autonomo, privato e sempre disponibile per assistenza allo sviluppo software (Go, TypeScript, React) e automazione personale via Telegram.

### Componente Centrale: Jarvis

**Jarvis** è un proxy LLM asincrono scritto in Python (FastAPI + Granian) che espone API in formato **Ollama** e **OpenAI** (`/v1/*`). Integra:
- Inferenza LLM locale via `llama-cpp-python` (file GGUF, nessun Ollama installato)
- Memoria episodica a lungo termine (Mem0 + Qdrant)
- RAG documentale AST-aware con Tree-sitter
- Bot Telegram con multi-userbot
- Loop agentico con tool-calling (scrittura file, shell, skills dinamiche)
- Web intelligence (SearXNG + Crawl4AI)

---

## 2. Architettura del Sistema

### Topologia Fisica

Nodo **Worker GPU** (attivo, laptop): FastAPI + LlamaEngine su RTX 3050 Ti 4GB.
Nodo **Master VPS** (futuro): CPU-only, 24GB RAM, delegherà inferenza GPU al Worker via Tailscale.

```
┌─────────────────────────────────────────────────┐
│  LAPTOP (OpenSUSE Tumbleweed)                   │
│  i5-11300H, 16GB RAM, RTX 3050 Ti (4GB VRAM)    │
│                                                  │
│  jarvis:8000 (FastAPI + LlamaEngine GPU)         │
│  qdrant:6333 (locale Docker)                     │
│  searxng:8081, crawl4ai:11235                    │
│  Bot Telegram (locale)                           │
└─────────────────────────────────────────────────┘
```

### Stack Docker

| Servizio | Porta | Note |
|---|---|---|
| `jarvis` | 8000 | Esecuzione HOST diretta (non containerizzata) |
| `qdrant` | 6333 | Database vettoriale |
| `searxng` | 8081 | Metasearch anonimo |
| `crawl4ai` | 11235 | Scraper headless |

### Flusso Inferenza

```
Client (API HTTP) → main.py → LlamaEngine.load_models()
  ├── Chat Model: Qwen3.5-4B su GPU (main brain)
  ├── Embedding: FastEmbed ONNX CPU (BAAI/bge-base-en-v1.5)
  ├── Gatekeeper: Qwen3.5-0.8B su CPU (compressione)
  └── RAG: Qdrant + Synaptiq → super-prompt → LLM
```

---

## 3. Struttura File e Responsabilità

### Moduli Core

| File | Responsabilità | Dipendenze Chiave |
|---|---|---|
| `core/config.py` | **Unica fonte di verità per tutte le costanti.** Legge `.env` con `os.getenv()`. | `os`, `logging` |
| `core/state.py` | Stato globale mutabile (singleton). Ring buffer `pipeline_traces` (500), gatekeeper stats. | — |
| `core/llm_engine.py` | Carica modelli GGUF, inferenza, thinking mode, **FastEmbed (ONNX CPU)**. `_load_chat_model()` rileva famiglia modello PRIMA del caricamento e applica default hardware per famiglia. | `llama_cpp`, `fastembed`, `config`, `model_profiles` |
| `core/model_profiles.py` | Auto-rilevamento famiglia modello GGUF via header binario. `_family_ctx_defaults()` + `_family_hardware_defaults()` per parametri temperatura e GPU per famiglia. | `config` |
| `core/lifecycle.py` | Lifecycle manager: avvio componenti, shutdown graceful, RAG ingestion saltabile. | `config`, `state` |
| `core/telemetry.py` | PipelineTracer per-request + GatekeeperStats. Tracciamento step, LLM calls, tool calls. | `state` |
| `main.py` | Entry point FastAPI, lifespan, endpoint HTTP. `conversation_id` generato e restituito. | Tutti i moduli |

### Moduli Agente

| File | Responsabilità |
|---|---|
| `agent/prompt.py` | Costruisce super-prompt omnisciente con tag XML. Gatekeeper + keyword bypass. |
| `agent/tags.py` | 21 tag XML d'azione (MEMORY, SCHEDULE, SSH, EXEC, ecc.). `TagSafeStream` per streaming. |
| `agent/tools.py` | TOOLS_SCHEMA + dispatch table per tool-calling (file/shell/skills). |
| `agent/classifier.py` | Classificatore intenti centralizzato. |
| `agent/confirmation.py` | ConfirmationManager per tool calls con timeout 5 min. |

### Moduli RAG e Memoria

| File | Responsabilità |
|---|---|
| `rag/engine.py` | Pipeline RAG: AST chunking (Tree-sitter), embedding FastEmbed, Qdrant. Watchdog PollingObserver. |
| `rag/reranker.py` | Reranker modulare: Qwen3-Reranker + FlashRank fallback. |
| `rag/cache.py` | Cache semantica Qdrant + Web Knowledge persistence. |
| `rag/web_search.py` | SearXNG + Crawl4AI parallelo. |
| `memory/engine.py` | Mem0: salvataggio/recupero ricordi filtrati per user+project. |

### API e Auth

| File | Responsabilità |
|---|---|
| `auth.py` | JWT auth (PyJWT): login/logout/me, require_auth/require_admin. |
| `user_manager.py` | UserManager SQLite: bcrypt password, API key SHA256. |
| `routes/profile.py` | Self-service: API key, change password, link Telegram. |
| `routes/users.py` | Admin CRUD utenti. |
| `routes/projects.py` | Project management: reindex, synaptiq/graph. |
| `api/mcp/server_v2.py` | MCP v2 Streamable HTTP (8 tool + 7 resources). |
| `session/store.py` | ChatSessionStore SQLite persistente. |

### OpenAI Compatible API (`jarvis/openai/`)

17 moduli: Chat, Completions, Embeddings, Audio, Images, Assistants API, Threads, Runs, Vector Stores.

### Dashboard (`jarvis/admin_panel/`)

Router FastAPI + 8 moduli JS (main, charts, graph, chat, telemetry, management, logs, utils) + CSS tematico. 73 env var categorizzate in Settings.

### Altri

| File | Ruolo |
|---|---|
| `graph/synaptiq_engine.py` | Synaptiq v2.0.5: grafo strutturale, hybrid search, dead code. |
| `graph/synaptiq_bridge.py` | Bridge RAG+Synaptiq per hybrid code search. |
| `scheduler/cron.py` | APScheduler: promemoria, timer, task ricorrenti. |
| `tg_bot/bot.py` | Bot Telegram ufficiale (comandi, whitelist, vocali). |
| `external/providers.py` | Provider cloud esterni (Gemini). |

---

## 4. Configurazione e Variabili d'Ambiente

### ⚠️ Regola Fondamentale
Il file `.env` è la **singola fonte di configurazione**. Non hardcodare mai valori nei file Python.

### Variabili Attive (Worker Locale)

| Variabile | Valore | Note |
|---|---|---|
| `LLAMA_MODEL_PATH` | `./models/Qwen3.5-4B-UD-Q4_K_XL.gguf` | **Unica da cambiare per switchare modello** |
| `LLAMA_EMBED_MODEL_PATH` | `./models/Qwen3-Embedding-0.6B-Q8_0.gguf` | Embedding (CPU) |
| `GATEKEEPER_MODEL_PATH` | `./models/Qwen3.5-0.8B-Instruct-Q4_K_M.gguf` | Compressione (CPU) |
| `GATEKEEPER_N_GPU_LAYERS` | `0` | Gatekeeper su CPU |
| `GATEKEEPER_N_CTX` | `4096` | Contesto gatekeeper |
| `LLM_NUM_CTX` | `12288` | Contesto chat |
| `LLM_BATCH_SIZE` | `512` | Batch prompt processing |
| `LLM_NUM_PREDICT` | `2048` | Token max output |

### Parametri AUTO-DETECTATI (non servono in `.env`)

| Parametro | Qwen3.5-4B | Gemma4 E2B | DeepSeek | Llama |
|---|---|---|---|---|
| `N_GPU_LAYERS` | -1 (full GPU) | 15 | -1 | -1 |
| `LLM_FLASH_ATTN` | true | false | true | true |
| `LLM_UBATCH_SIZE` | 128 | 512 | 128 | 128 |
| `LLM_TEMPERATURE` | 0.7 | 0.6 | 0.5 | 0.6 |
| `LLM_REPEAT_PENALTY` | 1.1 | 1.0 | 1.05 | 1.1 |
| `LLM_TOP_P` | 0.9 | 0.85 | 0.85 | 0.9 |
| `LLM_THINKING_MODE` | false | true | true | false |

Se un parametro è impostato esplicitamente in `.env`, ha la massima priorità. Altrimenti viene usato il default per famiglia (da `_family_hardware_defaults()` in `model_profiles.py`).

**CRITICO:** `N_GPU_LAYERS=-1` su Gemma4 causa segfault. `LLM_FLASH_ATTN=true` su Gemma4 causa crash. Entrambi sono prevenuti dall'auto-detection.

### Variabili RAG

```env
MAIN_PROJECT_PATH=/home/alfio/Projects/NeuroNet
EMBEDDING_DIMS=768
VECTOR_DB_VERSION=v3
EMBEDDING_MODEL=qwen3-embedding-0.6b
FLASHRANK_MODEL=ms-marco-TinyBERT-L-2-v2
RERANKER_DEVICE=cuda
SEMANTIC_CACHE_THRESHOLD=0.96
MEM0_STARTUP_DELAY=4.0
```

### Watchdog

```env
WATCHDOG_ENABLED=false
WATCHDOG_TIMEOUT=5
WATCHDOG_WATCH_MODE=per_project
WATCHDOG_BATCH_DELAY=1.0
```

### JWT Authentication

- `JWT_SECRET`: generato automaticamente da `config.py` (SHA256 di hostname + urandom).
- Token in cookie httpOnly `access_token`. Admin default: `admin`/`neuronet`.
- API key format: `sk-jarvis-<base64random>` (SHA256 hash nel DB).
- Nessuna self-registration. Nessun pass-through per localhost.

---

## 5. Modelli LLM in Uso

### Worker Locale (RTX 3050 Ti — 4GB VRAM)

| Modello | File | VRAM | Ruolo |
|---|---|---|---|
| **Qwen3.5-4B (attivo)** | `Qwen3.5-4B-UD-Q4_K_XL.gguf` | ~3334 MiB (full GPU) | Chat model primario |
| **Gemma 4 E2B QAT (backup)** | `gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf` | 1036 MiB (15/35 layer) | Backup, richiede N_GPU_LAYERS=15 |
| **FastEmbed (ONNX CPU)** | `BAAI/bge-base-en-v1.5` | 0 VRAM | Embedding via FastEmbed |
| **Qwen3.5-0.8B Gatekeeper** | `Qwen3.5-0.8B-Instruct-Q4_K_M.gguf` | 0 VRAM (CPU) | Compressione caveman |

### Parametri Ottimali per Modello

| Parametro | Qwen3.5-4B | Gemma4 E2B | Gemma4 26B (VPS) |
|---|---|---|---|
| `N_GPU_LAYERS` | **-1** | **15** | **0** |
| `LLM_FLASH_ATTN` | **true** | **false** | **true** |
| `LLM_UBATCH_SIZE` | **128** | **512** | **128** |
| `LLM_NUM_CTX` | 12288 | 12288 | 32768 |
| `LLM_TEMPERATURE` | 0.7 | 0.7 | 1.0 |
| `LLM_REPEAT_PENALTY` | 1.1 | 1.1 | 1.0 |
| `LLM_TOP_P` | 0.9 | 0.9 | 0.95 |
| `LLM_THINKING_MODE` | false | false | true |

NOTA: `N_GPU_LAYERS` e `LLM_FLASH_ATTN` sono auto-detectati. La tabella sopra è puramente informativa.

### Benchmark (2026-07-27)

Test su RTX 3050 Ti 4GB, i5-11300H, CPU governor performance.

| Configurazione | Tok/s | Note |
|---|---|---|
| **Qwen3.5-4B full GPU** (-1 layer, flash=true) | **~35-40** | Tensor core INT4 — attuale primario |
| **Gemma4 E2B CPU** (0 layer) | **19.7** | AVX512 |
| **Gemma4 E2B GPU** (15 layer, flash=false) | **5.7** | Overhead PCIe |
| **Gemma4 E2B CPU** (powersave) | **9.7** | CPU throttlata |

### Download Modelli

```bash
# Gemma 4 26B per Master VPS
huggingface-cli download unsloth/gemma-4-26B-A4B-it-GGUF \
  gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf \
  --local-dir /home/debian/ai-ecosystem/jarvis/models/
```

---

## 6. Hardware Profile Auto-Detection

### Come funziona

Il sistema rileva automaticamente la famiglia del modello GGUF **prima di caricarlo**, leggendo solo l'header binario (`general.architecture` dal GGUF). In base alla famiglia, applica i default hardware ottimali.

### Flusso decisionale (`_load_chat_model()` in `llm_engine.py`)

```
1. detect_model_family(path) → legge header GGUF → famiglia (qwen/gemma/deepseek/llama/...)
2. _family_hardware_defaults(family) → default per famiglia
3. Per OGNI parametro (n_gpu_layers, flash_attn, n_ubatch):
   - Se esplicitamente in .env → usa .env
   - Altrimenti → usa default per famiglia
4. Carica Llama() con parametri risolti
```

### Mappa default per famiglia

| Famiglia | `n_gpu_layers` | `flash_attn` | `n_ubatch` |
|---|---|---|---|
| `qwen` | -1 | true | 128 |
| `gemma` | 15 | false | 512 |
| `deepseek` | -1 | true | 128 |
| `llama` | -1 | true | 128 |
| `mistral` / `mixtral` | -1 | true | 128 |
| `phi` | -1 | true | 128 |
| `command-r` | -1 | true | 128 |
| `qwq` | 0 | false | 512 |
| `unknown` | 0 | false | 512 |

### Per switchare modello

Basta cambiare `LLAMA_MODEL_PATH` nel `.env`. **Non serve più** modificare `N_GPU_LAYERS`, `LLM_FLASH_ATTN`, o `LLM_UBATCH_SIZE`.

---

## 7. Topologia di Rete Master/Worker

### VPN Mesh (Tailscale)

Sistema in locale standalone (laptop). Futura integrazione VPS via Tailscale.

| Nodo | Ruolo | IP Tailscale |
|---|---|---|
| Laptop (Worker) | GPU inference, Qdrant, Telegram | Locale |
| VPS OVH (Master) | CPU-only, futuro | 100.64.0.1 |

### Rete Docker Interna

Tutti i container in `ai_network`: `qdrant`, `searxng`, `crawl4ai`. Jarvis gira direttamente sull'host.

---

## 8. Stato Attuale dell'Implementazione

### ✅ Completato

| Componente | Stato | Dettaglio |
|---|---|---|
| `core/llm_engine.py` | ✅ **OTTIMIZZATO** | FastEmbed ONNX CPU (BAAI/bge-base-en-v1.5). Hardware profile auto-detection per cambio modello sicuro. Classificazione intenti via main model (0 VRAM extra). Compressione gatekeeper su CPU. |
| `core/model_profiles.py` | ✅ **ESTESO** | `_family_hardware_defaults()` per default GPU per famiglia (N_GPU_LAYERS, flash_attn, n_ubatch). Rilevamento via header GGUF. |
| `core/lifecycle.py` | ✅ **MIGLIORATO** | RAG ingestion iniziale saltata se `WATCHDOG_ENABLED=false`. |
| `main.py` | ✅ **MIGLIORATO** | `conversation_id` generato e restituito in ogni risposta (UUID se non fornito). Multi-turn funzionante tra richieste separate. |
| `core/telemetry.py` | ✅ Nuovo | PipelineTracer per-request + GatekeeperStats. |
| `agent/prompt.py` | ✅ **MIGLIORATO** | 3-tier gatekeeper: keyword bypass + Gemma4 classification + Qwen3.5 compression. |
| `admin/dashboard.py` | ✅ **ESTESO** | SETTINGS_META (73 env var). `_persist_env()` atomic write. |
| `admin_panel/` | ✅ **OTTIMIZZATO** | Sigma.js FA2 via Web Worker. Telemetry splittato in 10 funzioni. CSS tematico. |
| `auth.py` + `user_manager.py` | ✅ **NUOVO** | JWT auth + UserManager SQLite + API key SHA256. |
| `graph/synaptiq_engine.py` | ✅ **ATTIVO** | Synaptiq v2.0.5: grafo, hybrid search, dead code, graph visualization. |
| `api/mcp/server_v2.py` | ✅ **ATTIVO** | MCP v2 Streamable HTTP. |
| `openai/` (pacchetto) | ✅ Nuovo | 17 moduli: Chat, Audio, Assistants, Threads, Runs. |
| `session/store.py` | ✅ Nuovo | ChatSessionStore SQLite persistente. |

### ⏳ Da Completare (Operazioni Manuali sulla VPS)

| Step | Azione |
|---|---|
| 1 | Copiare progetto su VPS |
| 2 | Installare Tailscale su VPS e Laptop |
| 3 | Scaricare Gemma 4 26B sulla VPS |
| 4 | Avviare Master sulla VPS |
| 5 | Collegamento Worker→Master via Tailscale |

### Cronologia Modifiche Recenti

| Data | Modifica | Impatto |
|---|---|---|
| **27/07** | **Hardware Profile Auto-Detection** | `model_profiles.py`: nuova `_family_hardware_defaults()` mappa ogni famiglia ai parametri GPU corretti. `llm_engine.py`: `_load_chat_model()` rileva famiglia PRIMA del caricamento e applica default per famiglia. Switch modello basta cambiare `LLAMA_MODEL_PATH` — `N_GPU_LAYERS`/`flash_attn`/`n_ubatch` auto-detectati. |
| **27/07** | **FastEmbed integration** | `llm_engine.py`: sostituito subprocess `sentence-transformers` con FastEmbed nativo (`BAAI/bge-base-en-v1.5`). Nessun `fused_gated_delta_net` crash. |
| **27/07** | **conversation_id fix (T4b)** | `main.py`: se `conversation_id` non fornito, genera UUID. Restituito in tutte le risposte (non-stream, streaming, timeout, confirm). Multi-turn ora funzionante tra richieste separate. |
| **27/07** | **AGENTS.md riscritto** | Documentazione allineata a stato attuale. Nuova sezione §6 Hardware Profile. .env pulito. |
| 26/07 | **Gatekeeper OpB — Gemma 4 classification** | `classify_intent_with_gemma()` per gatekeeper via main model (0 VRAM extra). |
| 26/07 | **Gatekeeper OpA — Qwen3.5 0.8B 4096ctx** | `GATEKEEPER_N_CTX=4096`. 6 few-shot esempi. Compressione pass-through se ratio negativo. |
| 26/07 | **Phantom Request Loop Fix (C7)** | `is_internal_query()` bypass in `/v1/chat/completions` — rompe loop Mem0→API→Mem0. |
| 26/07 | **Compress ValueError Fix (C6)** | `_GK_MAX_CHARS=1500` guard per gatekeeper 2048-ctx overflow. |
| 21/07 | **Synaptiq Graph Visualization** | Endpoint `GET /api/projects/{name}/synaptiq/graph`. Sigma.js integrato. |
| 20/07 | **User Management & ACL** | JWT auth, admin panel, API keys, profile self-service. |
| 19/07 | **Settings Dashboard** | 73 env var categorizzate con persistenza atomica. |
| 18/07 | **Admin Panel modulare** | Refactor dashboard in sub-package con JS/CSS separati. |
| 16/07 | **MCP v2 Streamable HTTP** | Nuovo endpoint `/api/mcp/v2`. |
| 29/06 | **OpenAI API pacchetto** | 17 moduli con Assistants/Threads/Runs. |
| 29/06 | **Reranker modulare** | Qwen3-Reranker + FlashRank fallback. |
| 23/06 | **Gemma 4 E2B QAT** | Primo caricamento modello QAT. |

---

## 9. Regole Operative per gli Agenti

### 🔴 NON FARE MAI

1. **Non committare `.env`** — contiene token Telegram, API key, segreti.
2. **Non avviare Ollama** — Jarvis usa solo `llama-cpp-python` con file GGUF.
3. **Non hardcodare path o IP** — usare variabili d'ambiente in `config.py`.
4. **Non modificare `data/`** senza backup — contiene Qdrant, Mem0, sessioni.
5. **Non riavviare Jarvis autonomamente** — chiedere SEMPRE ad Alfio.
6. **Non usare `as any`, `@ts-ignore`, `@ts-expect-error`** — mai.
7. **Non impostare `N_GPU_LAYERS` manualmente nel `.env`** — tranne per override esplicito. L'auto-detection per famiglia è più sicura.

### 🟡 ATTENZIONE

- **DUE Python nel container**: `python3` (3.10, senza watchdog) e `python` (3.11, con watchdog). Granian usa `python` (3.11).
- **`EXTERNAL_PROJECTS`** contiene percorsi validi SOLO sul laptop. Sulla VPS va svuotato.
- **Gemma4 E2B QAT limit**: `N_GPU_LAYERS` max 15 su RTX 3050 Ti (segfault a 18+). L'auto-detection lo imposta a 15. Non forzare oltre.
- **Qwen3.5-4B full GPU**: `N_GPU_LAYERS=-1` è sicuro (32 layer tutti su GPU, nessun Q4_0).
- **ISOLAMENTO PROGETTO**: `conversation_id` obbligatorio per sessioni multiple. Ora generato automaticamente se non fornito.
- **SYNAPTIQ IMPORT SAFETY**: tutti gli import di `synaptiq_engine` DEVONO essere lazy (dentro funzioni, try/except).

### 🟢 APPROCCIO CORRETTO

- Le costanti di configurazione vanno in `config.py`, lette da `.env`.
- Lo stato globale condiviso va in `state.py`.
- Le modifiche al modello LLM si effettuano SOLO cambiando `LLAMA_MODEL_PATH` nel `.env`.
- `N_GPU_LAYERS`, `LLM_FLASH_ATTN`, `LLM_UBATCH_SIZE` sono ora auto-detectati — **non modificarli** a meno di override esplicito richiesto.
- Usare il server MCP v2 (`/api/mcp/v2`) per diagnostica: `get_recent_traces`, `get_trace_by_id`, `get_status`.

---

## 10. Pattern di Codice e Convenzioni

### Stile Python

- Python 3.11 (asincrono con `asyncio`)
- `logger = logging.getLogger(__name__)` in ogni modulo
- Errori con `try/except` + `logger.warning/error` (NO `except: pass`)
- `ThreadPoolExecutor` per operazioni CPU-bound (inferenza LLM)

### Synaptiq — Import Safety Pattern

```python
# ✅ CORRETTO — import lazy dentro funzione + try/except
def my_function():
    if SYNAPTIQ_ENABLED:
        try:
            from synaptiq_engine import synaptiq_engine
            await synaptiq_engine.analyze(path)
        except Exception:
            pass
```

### Hardware Profile Pattern (nuovo)

```python
# In _load_chat_model():
# Prio 1: .env esplicito → os.environ
# Prio 2: default per famiglia → _family_hardware_defaults()
# Prio 3: fallback globale → dict dentro la funzione

_env_val = os.environ.get("N_GPU_LAYERS", "")
if _env_val.strip():
    n_gpu_layers = int(_env_val)  # override da .env
else:
    n_gpu_layers = _hw_def["n_gpu_layers"]  # default per famiglia
```

### conversation_id Pattern

```python
# In main.py /api/chat endpoint:
conversation_id = body.get("conversation_id") or request.headers.get("X-Conversation-Id")
if not conversation_id:
    conversation_id = str(uuid.uuid4())  # generato se non fornito

# In ogni response:
chat_resp = {
    ...
    "conversation_id": str(conversation_id),  # sempre presente
}
```

### Endpoint API

**Formato Jarvis nativo (`/api/chat`):**
- Richiede `model`, `messages[]`. Opzionale: `stream`, `conversation_id`, `options`.
- `conversation_id` viene generato e restituito se non fornito.
- Streaming: `application/x-ndjson`.

**Formato OpenAI (`/v1/*`):**
- `POST /v1/chat/completions` — streaming SSE + tool-calling.
- `POST /v1/embeddings` — 768-dim via FastEmbed (non più subprocess).
- `POST /v1/completions` — legacy.
- `POST /v1/audio/transcriptions` — faster-whisper.
- `GET /v1/models` — lista modelli.

### Tag XML d'Azione (21 tag)

| Tag | Visibilità | Descrizione |
|---|---|---|
| `MEMORY` | hidden | Salva fatto in memoria episodica |
| `SCHEDULE` | action | Promemoria cron |
| `NOTIFY_ONCE` / `NOTIFY_IN` | action | Timer singolo / relativo |
| `SSH` | action | Comando SSH su server remoto |
| `TODO_ADD` / `TODO_DONE` | action | Task management |
| `WEB` | action | Ricerca web |
| `FILE` | action | Lettura file |
| `EXEC` | action | Comando shell (whitelist) |
| `COMMIT` | action | Git commit |
| `BRANCH` | action | Git branch switch |
| `RAG` | action | Forza RAG su progetto |
| `ASK` | action | Reverse interaction |
| `THINK_DEEP` | hidden | Attiva reasoning approfondito |
| `CACHE_CLEAR` | action | Resetta cache semantica |

---

## 11. Bug Noti e Workaround

### 🐛 Bug 1: Gemma4 E2B QAT — Limite N_GPU_LAYERS=15

**Stato: MITIGATO dall'hardware profile auto-detection.**

Gemma4 E2B QAT ha 35 blocchi, di cui 418 tensori su 541 in formato Q4_0 (incompatibile CUDA_Host). Oltre 15 layer GPU → segfault per buffer split fatale.

| Dato | Valore |
|---|---|
| Blocchi totali | 35 |
| Tensori Q4_0 | 418/541 |
| N_GPU_LAYERS max | **15** (segfault a 18+) |
| VRAM a 15 layer | 1036 MiB (25%) |

**Soluzione:** `_family_hardware_defaults("gemma")` restituisce `n_gpu_layers=15`. Se si forza `N_GPU_LAYERS=-1` in `.env`, crash garantito.

### 🐛 Bug 2: Mem0 Connection Refused all'avvio

**Normale.** Mem0 riprova automaticamente per ~10-20 secondi, poi si connette.

### 🐛 Bug 3: CUDA OOM con N_GPU_LAYERS ≥ 22 su Qwen3.5-4B

Qwen3.5-4B ha 32 layer. 22+ layer su GPU (2.7 GB) + KV cache 16K + buffer temporanei > 4 GB VRAM.

**Soluzione:** Auto-detection imposta `N_GPU_LAYERS=-1` per qwen (full GPU, 32 layer, ~3334 MiB, safe).

### 🐛 Bug 4: CUDA 12.2 Incompatibile con Driver 580.x

Container basato su CUDA 12.2 crasha su host con driver NVIDIA ≥ 580.x.

**Soluzione:** Overlay CUDA 13.0 su base 12.2 (già implementato nel Dockerfile).

### 🐛 Bug 5: Synaptiq Module-Level Import Crash

Import a livello modulo di `synaptiq_engine` senza try/except → Jarvis non parte.

**Soluzione:** Tutti gli import Synaptiq DEVONO essere lazy + try/except.

### 🐛 Bug 6: FA2 Sincrono — Browser Freeze su Grafi Grandi

`window.__fa2.assign()` sincrono bloccava il browser per minuti (5659 nodi × 21711 relazioni).

**Soluzione:** Rimosso assign sincrono. FA2Worker asincrono via Web Worker.

### 🐛 Bug 7: Watchdog CPU 88% in idle

`PollingObserver(timeout=1)` eseguiva stat() su 335K file ogni secondo su `/home/alfio/Projects`.

**Soluzione:** Watchdog in modalità `per_project` con timeout=5s (~150x riduzione stat/sec).

### 🐛 Bug 8: Phantom Request Loop Mem0→API→Mem0

Mem0 chiamava `/v1/chat/completions` per entity extraction → finiva nella pipeline completa → creava memorie → loop infinito.

**Soluzione:** `is_internal_query()` bypass in `/v1/chat/completions` con skip di `build_omniscient_prompt()` e `process_response_tags()`.

---

## 📚 Documenti Correlati

- **README.md** — Documentazione utente
- **docs/ARCHITECTURE.md** — Topologia Master/Worker
- **docs/COMPONENTS.md** — Analisi 14 componenti
- **docs/PIPELINE.md** — Flusso end-to-end
- **docs/SETUP.md** — Installazione e configurazione
- **docs/API_REFERENCE.md** — Endpoint API completi

---

*Documento generato e mantenuto dall'agente AI. Aggiornare dopo ogni sessione di lavoro significativa.*
