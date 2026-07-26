# Analisi Completa del Codebase

## Struttura dei File

```
NeuroNet/
├── .env.example                     # Template configurazione
├── docker-compose.vps.yml           # Stack Master VPS (no GPU)
├── docker-compose.worker.yml        # Stack Worker GPU locale
├── .mcp.json                       # Config server MCP stdio per agenti AI esterni
├── start_master.sh / start_worker.sh
├── deploy_vps.sh / sync_to_master.sh
├── docs/
│   ├── AGENTS.md                    # Guida operativa per agenti AI
│   ├── ARCHITECTURE.md              # Topologia Master/Worker
│   ├── COMPONENTS.md                # ← QUESTO FILE
│   ├── PIPELINE.md                  # Flusso end-to-end
│   ├── API_REFERENCE.md             # Endpoint API
│   ├── TAGS_REFERENCE.md            # 21 tag XML d'azione
│   ├── SETUP.md                     # Installazione e configurazione
│   ├── STRATEGY.md                  # Provider esterni
│   ├── CHANGELOG.md                 # Storico versioni
│   ├── ROADMAP.md                   # Roadmap futura
│   └── plans/
├── data/                            # STATO PERSISTENTE (gitignored)
│   ├── qdrant/                      # Collezioni vettoriali
│   ├── jarvis_mem0/                 # Mem0 SQLite, cache HF, sessioni Userbot
│   ├── documents/                   # Progetti montati per RAG
│   └── searxng/                     # Configurazione SearXNG
├── AGENTS.md                        # (copia radice per visibilità)
└── jarvis/                          # CODICE SORGENTE
    ├── Dockerfile                   # Build CUDA 13.0 + llama-cpp-python
    ├── requirements.txt             # 33 dipendenze Python
    ├── models/                      # File GGUF (~8.7GB, gitignored)
    ├── main.py                      # Entry point FastAPI/Granian (1339 righe)
    ├── core/
    │   ├── config.py                # Configurazione centralizzata (508 righe)
    │   ├── state.py                 # Stato globale mutabile + ring buffer (249 righe)
    │   ├── llm_engine.py            # LlamaEngine + PriorityLock + _strip_thinking + decomp. helpers (875 righe)
    │   ├── model_profiles.py        # Auto-rilevamento famiglia modello GGUF (454 righe)
    │   ├── telemetry.py             # PipelineTracer + GatekeeperStats (577 righe)
    │   └── lifecycle.py             # Lifecycle manager (435 righe)
    ├── agent/
    │   ├── prompt.py                # Gatekeeper + build_omniscient_prompt + 6 helper (824 righe)
    │   ├── tags.py                  # TagSafeStream + process_all_tags + 21 handler (1179 righe)
    │   ├── tools.py                 # execute_tool_call + 18 dispatch + 5 built-in (1065 righe)
    │   ├── skills.py                # Skill dinamiche da YAML (526 righe)
    │   ├── classifier.py           # Classificatore intenti centralizzato (188 righe)
    │   └── confirmation.py         # ConfirmationManager con timeout 5 min (260 righe)
    ├── rag/
    │   ├── engine.py                # Pipeline RAG completa con AST chunking (1974 righe)
    │   ├── reranker.py              # Reranker modulare Qwen3 → FlashRank (80 righe)
    │   ├── cache.py                 # Cache semantica + Web Knowledge Qdrant (190 righe)
    │   └── web_search.py            # SearXNG + Crawl4AI (71 righe)
    ├── memory/
    │   ├── engine.py                # Mem0 + search/save helpers (419 righe)
    │   ├── backup.py                # Export/import memoria JSON (68 righe)
    │   └── reflection.py            # Self-reflection notturno (82 righe)
    ├── api/
    │   ├── auth/
    │   │   ├── auth.py              # JWT token + require_auth + login/logout/me (171 righe)
    │   │   └── user_manager.py      # UserManager SQLite + bcrypt + API key SHA256 (556 righe)
    │   └── mcp/
    │       ├── server.py            # MCP stdio (legacy, 510 righe)
    │       ├── server_v2.py         # MCP v2 Streamable HTTP — endpoint POST /api/mcp/v2 (570 righe)
    │       └── client.py            # Client MCP per tool esterni (634 righe)
    ├── routes/
    │   ├── profile.py               # Self-service API key/password/Telegram (135 righe)
    │   ├── users.py                 # Admin CRUD utenti (151 righe)
    │   └── projects.py              # Gestione progetti RAG + Synaptiq graph (391 righe)
    ├── admin/
    │   ├── dashboard.py             # SETTINGS_META (73 env var) + _persist_env (2406 righe)
    │   └── panel/
    │       ├── __init__.py          # Router FastAPI + mount static files (91 righe)
    │       ├── templates/
    │       │   ├── index.html       # Dashboard HTML (Chart.js, Sigma.js, cyberpunk)
    │       │   └── login.html       # Pagina di login standalone
    │       └── static/
    │           ├── css/style.css     # Tema chiaro/scuro, CSS custom properties (~500 righe)
    │           └── js/
    │               ├── main.js       # Init, cambio view, polling, auth
    │               ├── charts.js     # Chart.js (GPU, inference, RAG)
    │               ├── graph.js      # Sigma.js FA2 layout via Web Worker
    │               ├── chat.js       # Streaming SSE, drag-drop file
    │               ├── telemetry.js  # Page Visibility API, 10 funzioni polling
    │               ├── management.js # Settings (73 env), Synaptiq Graph, Users, Projects
    │               ├── logs.js       # Docker logs viewer
    │               └── utils.js      # fetchWithTimeout, showToast, escapeHtml
    ├── graph/
    │   ├── synaptiq_engine.py       # SynaptiqEngine + build_graph + hybrid search + graph viz (743 righe)
    │   └── synaptiq_bridge.py       # Bridge RAG+Synaptiq per hybrid search (224 righe)
    ├── tg_bot/
    │   ├── bot.py                   # Handler bot Telegram + comandi + auth (1287 righe)
    │   ├── format.py                # Utility formattazione Telegram Markdown (147 righe)
    │   ├── service.py               # Servizio Telegram (128 righe)
    │   └── userbot.py               # Multi-userbot Telethon via OTP (201 righe)
    ├── scheduler/
    │   ├── cron.py                  # APScheduler: CronTrigger, DateTrigger, timer (186 righe)
    │   └── tasks.py                 # ToDo persistenti con priorità/scadenze (73 righe)
    ├── session/
    │   └── store.py                 # ChatSessionStore SQLite persistente (404 righe)
    ├── openai_api/                  # Sotto-pacchetto OpenAI API (modulare)
    │   ├── __init__.py              # Factory init_openai_routes() con lazy import (38 righe)
    │   ├── state.py                 # OpenAIDatabase SQLite singleton + asyncio lock (715 righe)
    │   ├── models.py                # Pydantic models + /v1/models endpoint (151 righe)
    │   ├── chat.py                  # POST /v1/chat/completions (streaming, tool-calling) (268 righe)
    │   ├── completions.py           # POST /v1/completions (legacy) (95 righe)
    │   ├── embeddings.py            # POST /v1/embeddings (float/base64) (57 righe)
    │   ├── audio.py                 # POST /v1/audio/transcriptions, translations, speech (131 righe)
    │   ├── images.py                # POST /v1/images/* stub (400) (80 righe)
    │   ├── moderations.py           # POST /v1/moderations (71 righe)
    │   ├── files.py                 # POST /v1/files upload (140 righe)
    │   ├── uploads.py               # POST /v1/uploads large file (186 righe)
    │   ├── assistants.py            # CRUD Assistants API (123 righe)
    │   ├── threads.py               # CRUD Threads API (155 righe)
    │   ├── runs.py                  # POST /v1/threads/{id}/runs + submit_tool_outputs (270 righe)
    │   ├── run_engine.py            # Motore esecuzione Run (LLM + streaming) (347 righe)
    │   └── vector_stores.py         # CRUD Vector Store (349 righe)
    └── external/
        ├── infrastructure.py        # Registro server SSH per tag <SSH> (45 righe)
        └── providers.py             # Provider cloud esterni (361 righe)
```

---

### 1. 🏭 LlamaEngine (`jarvis/core/llm_engine.py`) — Motore di Inferenza

**Singleton** che carica i modelli GGUF all'avvio e li mantiene caldi in VRAM. Cuore pulsante del sistema. Rifattorizzato in 3 helper per `load_models()` e 3 helper statici per `generate_chat()`.

```
┌─────────────────────────────────────────────────────────────┐
│  LlamaEngine (Singleton)                                    │
│                                                             │
│  ThreadPoolExecutor (8 workers) ─── operazioni CPU-bound    │
│                                                             │
│  ┌────────────────────────┐  ┌────────────────────────┐    │
│  │ chat_model (GGUF)      │  │ embed_model (GGUF)     │    │
│  │ Gemma 4 E2B QAT        │  │ Qwen3-Embedding-0.6B   │    │
│  │ n_gpu_layers=15        │  │ n_gpu_layers=2         │    │
│  │ flash_attn=true        │  │ n_ctx=8192, pooling=2  │    │
│  │                        │  │ MRL: 1024→768 dims     │    │
│  └────────┬───────────────┘  └────────┬────────────────┘    │
│           │                           │                     │
│  PriorityLock(0)              PriorityLock(10)              │
│  (chat: priorità alta)        (embed: priorità bassa)       │
└──────────┼──────────────────────────────────────────────────┘
           │
           ▼
    External GPU Offloading
    ─ Se EXTERNAL_GPU_URL configurato:
      Ping Worker (1.5s timeout)
      ├── OK → HTTP POST con skip_rag=true
      └── FAIL → fallback CPU locale
```

**Decomposizione `load_models()`** (875 righe → 25 righe orchestrazione + 3 helper):
- `_load_single_model(path, params)` — carica un singolo GGUF con parametri
- `_load_embedding_model()` — wrapper per embedding GGUF
- `_verify_gpu_layers(model_name, n_gpu_layers)` — verifica layer GPU allocati

**Decomposizione `generate_chat()`** (~110 righe orchestrazione + 3 helper statici):
- `_prepare_generation_kwargs(...)` — prepara kwargs per llama_cpp
- `_handle_streaming(stream, ...)` — gestisce streaming output
- `_handle_non_streaming(result, ...)` — gestisce risposta completa

**Thinking Mode:** Supporto nativo per modelli con `<|think|>` (Gemma, DeepSeek, QwQ). Inietta automaticamente il tag nel system prompt.

**Feature evidenziate:**
- PriorityLock con coda prioritaria (chat priority 0 > embedding priority 10)
- Flash Attention riduce VRAM del 30-50%
- Offloading GPU con failover automatico (1.5s ping)
- Warmup CUDA JIT per evitare delay di 30s+ sulla prima richiesta
- MRL embedding troncamento (1024→768) per retrocompatibilità
- `_strip_thinking()` — rimuove tag `<think>`, analisi strutturate e meta-ragionamenti dalle risposte LLM
- `compress_prompt()` — compressione caveman con Qwen3.5 0.8B (CPU/GPU via GATEKEEPER_N_GPU_LAYERS), raw fallback se ratio negativo
- `classify_intent_with_gemma()` — classificazione intenti con Gemma 4 esistente (0 VRAM extra, 1-5 token output)
- `Gatekeeper N_GPU_LAYERS` — supporto offload GPU opzionale per il Gatekeeper LLM

---

### 2. 📚 Pipeline RAG (`jarvis/rag/engine.py`) — 1974 righe

Il componente più complesso. Pipeline completa di Retrieval-Augmented Generation con chunking semantico del codice.

```
                    ┌──────────────────────┐
                    │  Ingestione Documenti │
                    │  - os.walk ricorsivo  │
                    │  - GitignoreFilter    │
                    │  - pathspec support   │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │  AST Chunking        │
                    │  (Tree-sitter)       │
                    │                      │
                    │  Linguaggi:          │
                    │  Go, Python, JS/TS,  │
                    │  C, C++, Java,       │
                    │  Rust, SQL, YAML     │
                    │                      │
                    │  Strategie:          │
                    │  - function/class    │
                    │  - type declaration  │
                    │  - import section    │
                    │  - fallback riga     │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │  Embedding + Storage │
                    │  - Qwen3-Embedding   │
                    │  - Qdrant vector DB  │
                    │  - Batch processing  │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │  Ricerca + Reranking │
                    │                      │
                    │  ┌─────────────────┐ │
                    │  │ Qwen3-Reranker  │ │ ← Primario (CPU fp16)
                    │  │ (0.6B, 100+     │ │
                    │  │  lingue)        │ │
                    │  └─────────────────┘ │
                    │  ┌─────────────────┐ │
                    │  │ FlashRank       │ │ ← Fallback (ONNX)
                    │  │ (MiniLM-L6-v2)  │ │
                    │  └─────────────────┘ │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                     │  Watchdog Filesystem │
                     │  - PollingObserver   │
                     │  - Timeout/Modalità  │
                     │    configurabili     │ ← .env: WATCHDOG_TIMEOUT, WATCHDOG_WATCH_MODE
                     │  - Health monitor    │
                     │  - Re-embedding      │
                     │    automatico        │
                     └──────────────────────┘
```

**Feature evidenziate:**
- **AST Chunking semantico:** usa Tree-sitter per parsare il codice in nodi significativi (funzioni, classi, type declarations, import sections)
- **Reranker duale:** Qwen3-Reranker (primario, multilingua, MTEB-Code 73.42) → FlashRank (fallback ONNX) — modulare in `jarvis/rag/reranker.py`
- **Gitignore-aware:** rispetta .gitignore nei progetti monitorati tramite pathspec
- **Watchdog real-time:** PollingObserver per Docker compatibilità, ri-embedding automatico al salvataggio. Timeout e modalità watch configurabili via `.env` per bilanciare CPU/latenza
- **Semantic Cache:** cache risposte per query simili (soglia cosine 0.88) — modulare in `jarvis/rag/cache.py`
- **Cross-collection fallback:** se il progetto specifico non ha risultati, cerca in tutte le collezioni
- **Web Knowledge Cache:** memorizza risultati di ricerche web per reuse
- **Web Search integrato:** SearXNG + Crawl4AI in `jarvis/rag/web_search.py`

---

### 3. 🧠 Memoria Episodica (`jarvis/memory/engine.py`, `jarvis/memory/backup.py`)

Sistema di memoria a lungo termine basato su **Mem0** + **Qdrant**.

```
Conversazione utente
       │
       ▼
┌─────────────────┐
│  save_to_memory │──► Mem0.add() ──► Qdrant (vettori)
│  (infer=false)  │    + spaCy entità
└─────────────────┘    + metadati progetto
       │
       ▼
┌─────────────────┐
│  search_memory   │──► Mem0.search() ──► Qdrant (ricerca)
│  (con filtri)    │    + BM25 + cross-encoder
└─────────────────┘
       │
       ▼
┌─────────────────┐
│  extract_memories│──► Testo leggibile
│  (lista/dict)    │    per super-prompt
└─────────────────┘
```

**Feature evidenziate:**
- Salvataggio automatico di ogni interazione utente con metadati progetto
- Ricerca filtrata per `user_id` e `project` (isolamento contestuale)
- Warmup automatico spaCy/BM25 all'avvio (evita 10-30s di delay)
- Tag `<MEMORY>` nella risposta LLM per salvataggio esplicito
- Backup/export memoria in JSON per disaster recovery
- Consolidamento notturno (`jarvis/memory/reflection.py`) — riduce memoria episodica in sintesi profilo

---

### 4. 🧩 Prompt Builder (`jarvis/agent/prompt.py`) — Costruttore Super-Prompt

Pipeline di arricchimento che costruisce un super-prompt omnisciente con tag XML contestuali. Rifattorizzato in 6 helper: 3 helper per web search, 3 helper per memoria.

```
Messaggio utente
       │
       ▼
┌─────────────────────┐
│  LLM Gatekeeper     │──► Classifica intento
│  (keyword + regex    │    True = progetto/codice
│   + LLM grammar)    │    False = conversazione
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Web Intelligence   │──► /web prefix → SearXNG + Crawl4AI
│  (se /web o auto)   │    Auto-discovery se no RAG results
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Progetto Attivo    │──► detect_project_in_conversation()
│  (rilevamento +     │    Persist per conversazione
│   isolamento)       │    Reset per conversazioni generiche
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Mem0 Ricerca       │──► Ricerca filtrata per user+project
│  (se progetto attivo)│    Limite 5 risultati
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  RAG Documentale    │──► Qdrant search + reranking
│  (se gatekeeper True)│    + file matching nel prompt
│                      │    + Synaptiq hybrid search
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Budget Allocator   │──► Distribuzione dinamica contesto
│                     │    55% RAG, 20% web, 10% mem, 15% tree
│                     │    Max 15000 caratteri (≈11k tokens)
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Super-prompt XML   │──► <user_memory>
│                     │    <todo_list>
│                     │    <project_tree>
│                     │    <retrieved_code>
│                     │    <web_data>
│                     │    <active_project>
│                     │    <system_instructions>
└─────────────────────┘
```

**Formattazione Output:** Il system prompt include regole esplicite per l'uso di tabelle Markdown, code block, elenchi puntati e grassetto. Ogni risposta DEVE chiudersi con una sezione `---` contenente **Riepilogo:** (2-3 bullet) e **Attenzione:** (warnings/note). Il `finalize_trace` parameter opzionale permette al chiamante di decidere se chiudere il PipelineTracer.

**Tag d'Azione nella Risposta LLM — 21 tag XML:** Vedi [`docs/TAGS_REFERENCE.md`](TAGS_REFERENCE.md) per la tabella completa.

---

### 5. 🔧 Loop Agentico (`jarvis/agent/tools.py`, `jarvis/agent/skills.py`)

Tool-calling nativo integrato nel flusso di chat. `execute_tool_call()` usa una dispatch table con 18 handler.

```
Risposta LLM con tool_calls
       │
       ▼
┌─────────────────────┐
│  execute_tool_call()│──► 5 built-in tools:
│  (dispatch table)   │     write_file, delete_file
│                     │     read_file, replace_in_file
│                     │     run_shell_command
│                     │
│                     │    + skill_* (dinamici da YAML)
├─────────────────────┤
│  Richiesta          │──► Telegram/HTTP conferma utente
│  Conferma Utente    │     (ConfirmationManager, timeout 5 min)
│  (jarvis/agent/     │
│   confirmation.py)  │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Ricorsione LLM     │──► Risultato tool → nuovo giro
│  (risposta finale)  │    → risposta all'utente
└─────────────────────┘
```

**Tool Built-in:**
| Tool | Descrizione | Conferma |
|---|---|---|
| `write_file` | Scrive/sovrascrive file | ✅ |
| `delete_file` | Elimina file | ✅ |
| `read_file` | Legge file (max 8K caratteri) | ❌ |
| `replace_in_file` | Patch mirata (SEARCH/REPLACE) | ✅ |
| `run_shell_command` | Bash nel container (timeout 60s) | ✅ |

**Dynamic Skills:** skill YAML in `jarvis/agent/skills.py` vengono caricate a runtime e registrate come tool aggiuntivi con prefisso `skill_`.

**ConfirmationManager** (`jarvis/agent/confirmation.py`): gestione conferme utente con token univoci, timeout 5 min, callback per notifica risultati.

---

### 6. 🤖 Telegram Bot (`jarvis/tg_bot/bot.py`)

Interfaccia utente principale con menu a bottoni, whitelist, esplorazione file e admin panel.

```
Messaggio Telegram
       │
       ▼
┌─────────────────────┐
│  auth_middleware    │──► Blocca utenti non autorizzati
│  (gruppo -1)        │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Gestione Comandi   │──► /start → main menu
│  + CallbackQuery    │    Bottoni inline:
│                     │      📁 Esplora Progetti
│                     │      📋 Task, ToDo & Notifiche
│                     │      🌐 Info Ricerca Web
│                     │      ❓ Aiuto / Guida
│                     │      🤖 Mio Userbot
│                     │      ⚙️ Admin (admin)
│                     │      🖥️ Infrastruttura (admin)
├─────────────────────┤
│  Messaggi Vocali    │──► faster-whisper trascrizione
│  + Documenti        │    + gTTS risposta vocale
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  handle_telegram_   │──► build_omniscient_prompt
│  message            │    → LlamaEngine.generate_chat
│                     │    → process_all_tags()
│                     │    → telegram_safe_format()
└─────────────────────┘
```

**Multi-Userbot** (`jarvis/tg_bot/userbot.py`):
- Ogni utente autorizzato può attivare il proprio clone Telethon via OTP
- Risponde in chat private per conto dell'utente
- Whitelist per mittenti autorizzati
- NO RAG (sicurezza: nessun dato progetto leakato)
- Sessioni persistenti su disco

**Formattazione** (`jarvis/tg_bot/format.py`): `telegram_safe_format()` con escape MarkdownV2/Markdown.

---

### 7. 🕐 Scheduler APScheduler (`jarvis/scheduler/cron.py`)

Sistema di promemoria e task ricorrenti persistente.

```
┌──────────────────────────────────┐
│  AsyncIOScheduler                │
│                                  │
│  Job Types:                      │
│  ├── CronTrigger (es. 0 9 * * *)│
│  ├── DateTrigger (es. 2026-07-01│
│  │             15:00)            │
│  └── Relative (tra N minuti)     │
│                                  │
│  Default Jobs:                   │
│  ├── sys_reflection              │
│  │   (0 3 * * * → memoria        │
│  │    notturna)                  │
│  └── sys_morning_recap           │
│      (0 9 * * * → task pendenti  │
│       via Telegram)              │
└──────────────────────────────────┘
```

---

### 8. ☕ Task Manager (`jarvis/scheduler/tasks.py`)

Sistema di task persistenti con priorità e scadenze.

```
add_todo(desc, priority, deadline, task_type, user_id)
  ├── "personale" → owner = user_id
  └── "progetto"  → owner = "global" (visibile a tutti)

mark_done(tid, user_id) → solo owner
remove_todo(tid, user_id) → solo owner

get_open_tasks(user_id) → filtra per owner
```

---

### 9. 🌙 Reflection Agent (`jarvis/memory/reflection.py`)

Job notturno che consolida la memoria episodica del giorno in un profilo utente sintetico.

```
Ogni notte alle 3:00 UTC:
  1. Recupera tutte le memorie del giorno (min 5)
  2. LLM le condensa in fatti essenziali
  3. Elimina memorie episodiche vecchie
  4. Salva sintesi come nuovo profilo utente
```

---

### 10. 🔌 Infrastructure Manager (`jarvis/external/infrastructure.py`)

Registro server SSH per esecuzione comandi remoti via tag `<SSH>`.

```json
{
  "vps-ovh": {
    "ip": "51.xx.xx.xx",
    "user": "root",
    "key_path": "/root/.ssh/id_ed25519"
  }
}
```

Triggerato dal tag `<SSH>` nella risposta LLM → esecuzione async via asyncssh.

---

### 11. 🏛️ Sistema di Autenticazione (`jarvis/api/auth/`, `jarvis/routes/`)

Sistema JWT per proteggere la dashboard admin e le API sensibili.

| Modulo | File | Funzione |
|---|---|---|
| **UserManager** | `jarvis/api/auth/user_manager.py` (556 righe) | SQLite singleton: bcrypt password, CRUD utenti, API key SHA256. `ensure_admin_exists()` safety net |
| **JWT Auth** | `jarvis/api/auth/auth.py` (171 righe) | PyJWT token create/verify, FastAPI deps (`require_auth`, `require_admin`, `get_current_user`). Cookie `access_token` o header Bearer |
| **Profile API** | `jarvis/routes/profile.py` (135 righe) | Self-service: change password, API key list/generate/rotate/revoke, link Telegram ID. Cache `_RECENT_KEYS` 5 min |
| **Users API** | `jarvis/routes/users.py` (151 righe) | Admin CRUD: create/list/update/delete/activate/deactivate |
| **Projects API** | `jarvis/routes/projects.py` (391 righe) | Project management: register, reindex, delete collection, synaptiq/graph |

**Flusso Auth:**
```
Login (/api/auth/login) → JWT cookie access_token
  → protected endpoints (require_auth/require_admin)
  → header API key (Bearer sk-jarvis-*) per API OpenAI-compatibili

Admin safety net:
  ensure_admin_exists() → se nessun admin in DB, crea admin/neuronet
```

---

### 12. 📊 Dashboard Web (`jarvis/admin/dashboard.py` + `jarvis/admin/panel/`)

Pannello di controllo web con grafici Chart.js in tempo reale, accessibile su `/admin/` (primario; `/dashboard` redirect 301).

**Viste Dashboard:**
- **Monitor:** GPU metrics, modelli, health services (Qdrant/SearXNG/Crawl4AI), statistiche inferenza, sistema, errori
- **Code Graph:** Visualizzazione Sigma.js collezioni Qdrant. Re-index e delete collection
- **Management → Projects:** Progetti RAG, pulsante 🧬 Graph per grafo Synaptiq per-progetto
- **Chat:** Streaming SSE in-browser, drag-drop file, shortcut `/`
- **Management → Settings:** 73 env var categorizzate in 12 gruppi. Simple/Advanced Mode. Badge ⚡ restart. Persistenza su `.env` con `_persist_env()`
- **Management → Users:** User management CRUD (solo admin)
- **Management → Models:** Lista GGUF, switch runtime
- **Management → Tasks, Cron, Analytics**
- **Profile:** Self-service password, API key, Telegram
- **Logs:** Docker logs viewer con filtro servizio

**Settings API** (`jarvis/admin/dashboard.py`):
- `SETTINGS_META`: 73 env var con metadati (type, editable, category, restart_required, sensitive, basic)
- `_persist_env()`: scrittura atomica su `.env`
- `update_settings()`: type coercion e restart detection

---

### 13. 🗺️ Model Profiles (`jarvis/core/model_profiles.py`)

Rilevamento automatico della famiglia modello dal nome file GGUF:

| Famiglia | Thinking | Unsloth | Max CTX | Note |
|---|---|---|---|---|
| Qwen | ❌ | ✅ | 131072 | Embedding + backup chat |
| Gemma | ✅ | ✅ | 32768 | **Gemma 4 E2B QAT attivo** |
| DeepSeek | ✅ | ❌ | 16384 | DeepSeek Coder V2 |
| QwQ | ✅ | ❌ | 32768 | QwQ-32B-Preview |
| Llama | ❌ | ✅ | 131072 | Llama 3.x |
| Mistral/Mixtral | ❌ | ✅ | 32768 | Mistral / Mixtral MoE |
| Phi | ❌ | ❌ | 32768 | Phi-3/4 |

---

### 14. 🧬 Synaptiq Engine (`jarvis/graph/synaptiq_engine.py`) — Structural Code Graph

Motore di analisi strutturale del codice che converte il repository in un grafo diretto (file → dipendenze → funzioni/classi) e offre 4 modalità di ricerca avanzata complementari alla RAG vettoriale.

Attivato automaticamente dal Watchdog alla ricezione di file event: debounce 30s per-project, esegue `analyze()` come background task dopo RAG ingest.

```
File evento (Watchdog)
       │
       ▼
┌──────────────────────────┐
│  notify_file_event()     │──► Debounce 30s per-project
│  (in main.py)            │    Reset timer su nuovi eventi
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│  SynaptiqEngine.analyze()│──► Background task asincrono
│  (initialization auto)   │    Chiama build_graph per progetto
└──────────┬───────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────┐
│  SynaptiqEngine (jarvis/graph/synaptiq_engine.py)           │
│                                                             │
│  ┌──────────────────────┐   ┌──────────────────────────┐   │
│  │ BUILD GRAPH          │   │ RICERCA STRUTTURALE       │   │
│  │                      │   │                           │   │
│  │ build_graph(repo)    │   │ hybrid_search(query,      │   │
│  │  ├── os.walk +      │   │   repo, top_k=10)         │   │
│  │  │   linguist        │   │  ├── vettori (Qdrant)     │   │
│  │  ├── tree-sitter     │   │  ├── grafo (PageRank)     │   │
│  │  │   parsing per     │   │  └── Fusione pesata       │   │
│  │  │   file sorgente   │   │      α=0.6 vettori,       │   │
│  │  ├── dependency      │   │      β=0.4 grafo          │   │
│  │  │   resolution      │   ├── dead_code_analysis()    │   │
│  │  │   (import/include)│   │  ├── impact_analysis()    │   │
│  │  └── community       │   │  └── community_detect()   │   │
│  │      detection       │   └──────────────────────────┘   │
│  └──────────────────────┘                                   │
│                                                             │
│  ┌────────────────────────────────────────────────────────┐│
│  │ GRAPH VISUALIZATION (Sigma.js)                         ││
│  │                                                         ││
│  │ get_graph_data(project_path)                            ││
│  │  ├── KnowledgeGraph API → nodi + relazioni             ││
│  │  ├── Counter-based project_name inference              ││
│  │  ├── _last_project_path tracking                       ││
│  │  └── endpoint: GET /api/projects/{name}/synaptiq/graph ││
│  └────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

**4 Modalità di Ricerca:**

| Modalità | Metodo | Input | Output | Caso d'Uso |
|---|---|---|---|---|
| **Hybrid Search** | `hybrid_search()` | query testo | Top-10 nodi (vettori + grafo) | Ricerca semantica + strutturale |
| **Dead Code** | `dead_code_analysis()` | file_path | Variabili/funzioni non referenziate | Refactoring, pulizia codice |
| **Impact Analysis** | `impact_analysis()` | file_path | Dipendenti diretti/indiretti | Valutazione rischio modifiche |
| **Community Detection** | `community_detect()` | repo | Cluster di moduli correlati | Architettura, modularizzazione |

**Grafo Strutturale:**

```
Nodi:
  ├── File          (path, language, size)
  ├── Function      (name, start_line, end_line, params)
  └── Class         (name, start_line, end_line, methods)

Archi:
  ├── imports       (file → file, via import/include)
  ├── calls         (function → function)
  ├── inherits      (class → class)
  └── contains      (directory → file)

Metriche:
  ├── PageRank      (centralità nel grafo)
  ├── degree        (connessioni entranti/uscenti)
  └── community     (Louvain clustering)
```

**File:**
- `jarvis/graph/synaptiq_engine.py` — Engine principale (743 righe): SynaptiqEngine, build_graph, hybrid_search, dead_code, impact, community detection, graph visualization
- `jarvis/graph/synaptiq_bridge.py` — Bridge RAG+Synaptiq per hybrid search (224 righe)
- `jarvis/main.py` — Hook notify_file_event() + initial_analysis() background task
- `jarvis/rag/engine.py` — notify_file_event() chiamato in rag_queue_worker()
- `jarvis/core/config.py` — `SYNAPTIQ_ENABLED`, `SYNAPTIQ_STORAGE_PATH`, `parse_external_projects()`

---

### 15. 🔍 Pipeline Telemetry & MCP Server (`jarvis/core/telemetry.py`, `jarvis/api/mcp/`)

Sistema di tracciamento strutturato che registra ogni richiesta utente attraverso i 4 step della pipeline (keyword bypass, gatekeeper LLM, context gathering, generazione LLM). I dati sono esposti tramite API REST HTTP e server MCP v2.

```
Richiesta utente
       │
       ▼
┌─────────────────────┐
│  PipelineTracer     │──► start_step("keyword_bypass")
│  (per-request)      │    ├── ok/skipped/error
│                      │    └── duration_ms
├─────────────────────┤
│  GatekeeperStats    │──► record(intent, confidence, bypassed)
│  (cumulativo)       │    ├── by_intent distribution
│                      │    └── avg_confidence
├─────────────────────┤
│  PipelineTrace      │──► steps[] + llm_calls[] + gatekeeper
│  (completato)       │    + system_prompt + rag_context + user_content + compressed_text + llm_response
│                      │    └── insert in state.pipeline_traces (ring buffer 500)
└─────────┬───────────┘
          │
          ▼
┌─────────────────────────────────────────────────────┐
│  Canali di accesso                                    │
│                                                       │
│  ┌──────────────────┐  ┌──────────────────────────┐  │
│  │ MCP stdio        │  │ MCP v2 Streamable HTTP   │  │
│  │ jarvis/api/mcp/  │  │ POST /api/mcp/v2         │  │
│  │ server.py        │  │ (FastAPI endpoint)       │  │
│  └───────┬──────────┘  └───────┬──────────────────┘  │
│          │                     │                      │
│  ┌───────▼─────────────────────▼──────────┐           │
│  │ HTTP REST API                          │           │
│  │ /api/telemetry/traces                  │           │
│  │ /api/telemetry/gatekeeper              │           │
│  │ /api/telemetry/errors                  │           │
│  │ /api/telemetry/status                  │           │
│  │ /api/telemetry/model                   │           │
│  │ /api/telemetry/pending_ops             │           │
│  └──────────────────────────────────────────────────┘
```

**PipelineTracer** — per-request tracker:
- Timeline step-by-step con misure di durata in millisecondi
- Registrazione di tutte le chiamate LLM con token prompt/completion
- Risultato del Gatekeeper (intento, confidence, bypass)
- Conteggio tool calls
- Campi prompt: system_prompt, rag_context, user_content, compressed_text, llm_response
- Errore finale se presente
- Ogni trace completato finisce in `state.pipeline_traces` (ring buffer circolare, ultimi 500)

**GatekeeperStats** — statistiche cumulative:
- `total_classified`, `bypassed`, `llm_called`
- `by_intent`: distribuzione degli intenti classificati
- `avg_confidence`: confidenza media del Gatekeeper
- `by_intent_with_bypass`: bypass rate per intento

**File:**
- `jarvis/core/telemetry.py` — Classi core (PipelineTracer, GatekeeperStats, LlmCallRecord, StepRecord, PipelineTrace) (577 righe)
- `jarvis/api/mcp/server.py` — Server MCP stdio legacy (510 righe)
- `jarvis/api/mcp/server_v2.py` — Server MCP v2 Streamable HTTP — 8 tool + 7 resources (570 righe)
- `jarvis/api/mcp/client.py` — Client MCP per tool esterni (634 righe)
- `jarvis/core/state.py` — Ring buffer `pipeline_traces`, `gatekeeper_stats`, `error_counters`
- `.mcp.json` — Config per Claude Code/Cursor

---

### 16. 🗄️ Session Store (`jarvis/session/store.py`)

ChatSessionStore SQLite persistente per salvare/caricare sessioni chat.

**Feature:**
- `save_session()` / `load_session()` — persistenza sessione
- `list_sessions()` / `delete_session()` — gestione
- Usato da dashboard (Chat view) e main.py

---

### 17. 🧠 Intent Classifier (`jarvis/agent/classifier.py`)

Classificatore intenti centralizzato con pattern matching:

| Funzione | Descrizione |
|---|---|
| `classify(message)` | Classifica intento in base a keyword + regex |
| `is_project_query(msg)` | True se messaggio riguarda codice/progetto |
| `is_greeting(msg)` | True se saluto/chiacchiera |
| `is_web_query(msg)` | True se richiede ricerca web |
| `is_internal_query(msg)` | True se comando interno |

Costanti `Intent.*` e `PROJECT_KEYWORDS` per matching rapido.
