# Analisi Completa del Codebase

## Struttura dei File

```
ai-ecosystem/
├── .env.example                     # Template configurazione
├── docker-compose.vps.yml           # Stack Master VPS (no GPU)
├── docker-compose.worker.yml        # Stack Worker GPU locale
├── .mcp.json                       # Config server MCP per agenti AI esterni
├── start_master.sh / start_worker.sh
├── deploy_vps.sh / sync_to_master.sh
├── docs/
│   ├── AGENTS.md                    # Guida operativa per agenti AI
│   └── plans/PLAN.md               # Piano di implementazione
├── data/                            # STATO PERSISTENTE (gitignored)
│   ├── qdrant/                      # Collezioni vettoriali
│   ├── jarvis_mem0/                 # Mem0 SQLite, cache HF, sessioni Userbot
│   ├── documents/                   # Progetti montati per RAG
│   └── searxng/                     # Configurazione SearXNG
└── jarvis/                          # CODICE SORGENTE
    ├── Dockerfile                   # Build CUDA 13.0 + llama-cpp-python
    ├── requirements.txt             # 33 dipendenze Python
    ├── models/                      # File GGUF (~8.7GB, gitignored)
    ├── main.py                      # Entry point FastAPI/Granian (1360 righe)
    ├── config.py                    # Configurazione centralizzata (327 righe)
    ├── state.py                     # Stato globale mutabile (72 righe)
    ├── llm_engine.py                # LlamaEngine + PriorityLock + _strip_thinking (635 righe)
    ├── rag.py                       # Pipeline RAG completa (1797 righe)
    ├── rag_reranker.py              # Reranker modulare (Qwen3 + FlashRank) (80 righe)
    ├── rag_cache.py                 # Cache semantica + Web Knowledge Qdrant (190 righe)
    ├── memory.py                    # Mem0 + helper memoria (187 righe)
    ├── memory_backup.py             # Export/import memoria JSON (68 righe)
    ├── prompt_builder.py            # Gatekeeper + super-prompt + format rules (530 righe)
    ├── agent_tools.py               # Tool-calling agentico (1008 righe)
    ├── skills_manager.py            # Skill dinamiche da YAML (526 righe)
    ├── skills/                      # Skill dinamiche (vuota)
    ├── web_search.py                # SearXNG + Crawl4AI (67 righe)
    ├── telegram_bot.py              # Handler bot Telegram (1176 righe)
    ├── telegram_userbot_manager.py  # Multi-userbot Telethon (201 righe)
    ├── cron_agent.py                # Scheduler APScheduler (186 righe)
    ├── task_manager.py              # ToDo persistenti (73 righe)
    ├── reflection_agent.py          # Self-reflection notturno (82 righe)
    ├── dashboard.py                 # Pannello web Chart.js (1983 righe)
    ├── infrastructure.py            # Registro server SSH (45 righe)
    ├── model_profiles.py            # Auto-rilevamento famiglia modello (292 righe)
    ├── external_providers.py        # Provider cloud esterni (356 righe)
    ├── mcp_client.py                # Client MCP per tool esterni (634 righe)
    ├── mcp_server.py                # Server MCP stdio per diagnostica AI (legacy) (474 righe)
    ├── mcp_server_v2.py             # Server MCP v2 Streamable HTTP — endpoint POST /api/mcp/v2 (455 righe)
    ├── _mcp_handlers.py             # (Deprecato) Handler MCP condivisi — sostituito da mcp_server_v2.py (250 righe)
    ├── telemetry.py                 # PipelineTracer + GatekeeperStats + prompt fields (442 righe)
    ├── tag_processor.py             # Elaborazione tag XML nelle risposte (1043 righe)
    ├── telegram_format.py            # Utility formattazione Telegram Markdown (147 righe)
    ├── dashboard_template.py         # Template HTML/CSS/JS dashboard (cyberpunk, Chart.js, Sigma.js)
    ├── openai_router.py              # (Legacy) Router OpenAI /v1/* (545 righe)
    ├── openai/                       # Sotto-pacchetto OpenAI API (modulare)
    │   ├── __init__.py               # Factory init_openai_routes() con lazy import
    │   ├── state.py                  # OpenAIDatabase SQLite singleton + lock asyncio
    │   ├── models.py                 # Pydantic models + /v1/models endpoint
    │   ├── chat.py                   # POST /v1/chat/completions (streaming, tool-calling)
    │   ├── completions.py            # POST /v1/completions (legacy)
    │   ├── embeddings.py             # POST /v1/embeddings (float/base64)
    │   ├── audio.py                  # POST /v1/audio/transcriptions, translations, speech
    │   ├── images.py                 # POST /v1/images/* stub (400)
    │   ├── moderations.py            # POST /v1/moderations
    │   ├── files.py                  # POST /v1/files
    │   ├── uploads.py                # POST /v1/uploads (large file)
    │   ├── assistants.py             # CRUD Assistants API
    │   ├── threads.py                # CRUD Threads API
    │   ├── runs.py                   # POST /v1/threads/{id}/runs
    │   ├── run_engine.py             # Motore esecuzione Run (LLM + streaming)
    │   └── vector_stores.py          # CRUD Vector Store
    ├── agents/                      # Def. agenti specializzati
    │   └── code-reviewer.agent.md
    ├── cron_jobs.json               # Job schedulati persistenti
    └── tasks.json                   # Task persistenti
```

---

### 1. 🏭 LlamaEngine (`llm_engine.py`) — Motore di Inferenza

**Singleton** che carica i modelli GGUF all'avvio e li mantiene caldi in VRAM. Cuore pulsante del sistema.

```
┌─────────────────────────────────────────────────────────────┐
│  LlamaEngine (Singleton)                                    │
│                                                             │
│  ThreadPoolExecutor (8 workers) ─── operazioni CPU-bound    │
│                                                             │
│  ┌────────────────────────┐  ┌────────────────────────┐    │
│  │ chat_model (GGUF)      │  │ embed_model (GGUF)     │    │
│  │ Qwen3.5-4B-UD          │  │ Qwen3-Embedding-0.6B   │    │
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

**Thinking Mode:** Supporto nativo per modelli con `<|think|>` (Gemma, DeepSeek, QwQ). Inietta automaticamente il tag nel system prompt.

**Feature evidenziate:**
- PriorityLock con coda prioritaria (chat priority 0 > embedding priority 10)
- Flash Attention riduce VRAM del 30-50%
- Offloading GPU con failover automatico (1.5s ping)
- Warmup CUDA JIT per evitare delay di 30s+ sulla prima richiesta
- MRL embedding troncamento (1024→768) per retrocompatibilità
- `_strip_thinking()` — rimuove tag `<think>`, analisi strutturate e meta-ragionamenti dalle risposte LLM
- `compress_prompt()` — compressione caveman con Qwen3.5 (CPU), raw fallback se ratio negativo
- `Gatekeeper N_GPU_LAYERS` — supporto offload GPU opzionale per il Gatekeeper LLM

---

### 2. 📚 Pipeline RAG (`rag.py`) — 1763 righe

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
- **Reranker duale:** Qwen3-Reranker (primario, multilingua, MTEB-Code 73.42) → FlashRank (fallback ONNX)
- **Gitignore-aware:** rispetta .gitignore nei progetti monitorati tramite pathspec
- **Watchdog real-time:** PollingObserver per Docker compatibilità, ri-embedding automatico al salvataggio. Timeout e modalità watch configurabili via `.env` per bilanciare CPU/latenza
- **Semantic Cache:** cache risposte per query simili (soglia cosine 0.88)
- **Cross-collection fallback:** se il progetto specifico non ha risultati, cerca in tutte le collezioni
- **Web Knowledge Cache:** memorizza risultati di ricerche web per reuse

---

### 3. 🧠 Memoria Episodica (`memory.py`, `memory_backup.py`)

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
- Consolidamento notturno (riduce memoria episodica in sintesi profilo)

---

### 4. 🧩 Prompt Builder (`prompt_builder.py`) — Costruttore Super-Prompt

Pipeline di arricchimento che costruisce un super-prompt omnisciente con tag XML contestuali.

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

### 5. 🔧 Loop Agentico (`agent_tools.py`, `skills_manager.py`)

Tool-calling nativo integrato nel flusso di chat.

```
Risposta LLM con tool_calls
       │
       ▼
┌─────────────────────┐
│  execute_tool_call()│──► 5 built-in tools:
│                     │     write_file, delete_file
│                     │     read_file, replace_in_file
│                     │     run_shell_command
│                     │
│                     │    + skill_* (dinamici da YAML)
├─────────────────────┤
│  Richiesta          │──► Telegram conferma utente
│  Conferma Utente    │     (timeout 5 min)
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

**Dynamic Skills:** skill YAML in `jarvis/skills/` vengono caricate a runtime e registrate come tool aggiuntivi con prefisso `skill_`.

---

### 6. 🤖 Telegram Bot (`telegram_bot.py`)

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
│                     │    → process_response_tags
└─────────────────────┘
```

**Multi-Userbot** (`telegram_userbot_manager.py`):
- Ogni utente autorizzato può attivare il proprio clone Telethon via OTP
- Risponde in chat private per conto dell'utente
- Whitelist per mittenti autorizzati
- NO RAG (sicurezza: nessun dato progetto leakato)
- Sessioni persistenti su disco

---

### 7. 🕐 Scheduler APScheduler (`cron_agent.py`)

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

### 8. ☕ Task Manager (`task_manager.py`)

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

### 9. 🌙 Reflection Agent (`reflection_agent.py`)

Job notturno che consolida la memoria episodica del giorno in un profilo utente sintetico.

```
Ogni notte alle 3:00 UTC:
  1. Recupera tutte le memorie del giorno (min 5)
  2. LLM le condensa in fatti essenziali
  3. Elimina memorie episodiche vecchie
  4. Salva sintesi come nuovo profilo utente
```

---

### 10. 🔌 Infrastructure Manager (`infrastructure.py`)

Registro server SSH per esecuzione comandi remoti.

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

### 11. 📊 Dashboard Web (`dashboard.py`)

Pannello di controllo web con grafici Chart.js in tempo reale:

- **GPU Metrics:** VRAM used/total, temperature, utilization %
- **System Metrics:** CPU%, RAM, disk, network I/O
- **Inference History:** tokens/s, request latency, model used
- **RAG Stats:** documents indexed, collection sizes
- **Agent Logs:** real-time log streaming
- **ToDo List:** visualizzazione task
- **Settings Panel:** 73 env var categorizzate in 12 gruppi, Simple/Advanced Mode, persistenza su `.env` con `_persist_env()`
- **Code Graph:** Visualizzazione interattiva collezioni Qdrant (Sigma.js)
- **Chat:** Streaming SSE in-browser
- **Telemetry:** Tempo reale GPU, modelli, health checks

---

### 12. 🗺️ Model Profiles (`model_profiles.py`)

Rilevamento automatico della famiglia modello dal nome file GGUF:

| Famiglia | Thinking | Unsloth | Max CTX | Note |
|---|---|---|---|---|
| Qwen | ❌ | ✅ | 131072 | Qwen3.5-4B-UD attuale |
| Gemma | ✅ | ✅ | 32768 | Gemma 4 E2B / 26B |
| DeepSeek | ✅ | ❌ | 16384 | DeepSeek Coder V2 |
| QwQ | ✅ | ❌ | 32768 | QwQ-32B-Preview |
| Llama | ❌ | ✅ | 131072 | Llama 3.x |
| Mistral/Mixtral | ❌ | ✅ | 32768 | Mistral / Mixtral MoE |
| Phi | ❌ | ❌ | 32768 | Phi-3/4 |

---

### 13. 🧬 Synaptiq Engine (`synaptiq_engine.py`) — Structural Code Graph

Motore di analisi strutturale del codice che converte il repository in un grafo diretto (file → dipendenze → funzioni/classi) e offre 4 modalità di ricerca avanzata complementari alla RAG vettoriale.

Attivato automaticamente dal Watchdog alla ricezione di file event: debounce 30s per-project, esegue initial_analysis() come background task dopo RAG ingest.

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
│  initial_analysis()      │──► Background task asincrono
│  (in main.py)            │    Chiama synaptiq per progetto
└──────────┬───────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────┐
│  SynaptiqEngine (synaptiq_engine.py)                        │
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
- `jarvis/synaptiq_engine.py` — Engine principale: SynaptiqEngine, build_graph, hybrid_search, dead_code, impact, community detection
- `jarvis/main.py` — Hook notify_file_event() + initial_analysis() background task
- `jarvis/rag.py` — notify_file_event() chiamato in rag_queue_worker()
- `jarvis/config.py` — parse_external_projects() helper per watchdog

---

### 14. 🔍 Pipeline Telemetry & MCP Server (`telemetry.py`, `mcp_server.py`)

Sistema di tracciamento strutturato che registra ogni richiesta utente attraverso i 4 step della pipeline (keyword bypass, gatekeeper LLM, context gathering, generazione LLM). I dati sono esposti tramite API REST HTTP, server MCP stdio, e endpoint MCP SSE.

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
│  (completato)       │    └── insert in state.pipeline_traces (ring buffer 500)
└─────────┬───────────┘
          │
          ▼
┌─────────────────────────────────────────────────────┐
│  Canali di accesso                                   │
│                                                      │
│  ┌──────────────────┐  ┌──────────────────┐         │
│  │ MCP stdio        │  │ MCP SSE (in-app) │         │
│  │ jarvis/mcp_server│  │ /api/mcp/sse     │         │
│  │ (subprocess)     │  │ (persistent conn)│         │
│  └───────┬──────────┘  └───────┬──────────┘         │
│          │                     │                     │
│  ┌───────▼─────────────────────▼──────────┐          │
│  │ HTTP REST API                          │          │
│  │ /api/telemetry/traces                  │          │
│  │ /api/telemetry/gatekeeper              │          │
│  │ /api/telemetry/errors                  │          │
│  │ /api/telemetry/status                  │          │
│  │ /api/telemetry/model                   │          │
│  │ /api/telemetry/pending_ops             │          │
│  └──────────────────────────────────────────────────┘
```

**PipelineTracer** — per-request tracker:
- Timeline step-by-step con misure di durata in millisecondi
- Registrazione di tutte le chiamate LLM con token prompt/completion
- Risultato del Gatekeeper (intento, confidence, bypass)
- Conteggio tool calls
- Errore finale se presente
- Ogni trace completato finisce in `state.pipeline_traces` (ring buffer circolare, ultimi 500)

**GatekeeperStats** — statistiche cumulative:
- `total_classified`, `bypassed`, `llm_called`
- `by_intent`: distribuzione degli intenti classificati
- `avg_confidence`: confidenza media del Gatekeeper
- `by_intent_with_bypass`: bypass rate per intento

**File:**
- `jarvis/telemetry.py` — Classi core (PipelineTracer, GatekeeperStats, LlmCallRecord, StepRecord, PipelineTrace)
- `jarvis/mcp_server.py` — Server MCP stdio per agenti AI esterni
- `jarvis/_mcp_handlers.py` — Handler MCP condivisi (usati da SSE e stdio)
- `jarvis/state.py` — Ring buffer `pipeline_traces`, `gatekeeper_stats`, `error_counters`
- `.mcp.json` — Config per Claude Code/Cursor
