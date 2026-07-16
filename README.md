# NeuroNet — AI Cognitive Proxy

Questo repository ospita **NeuroNet**, un ecosistema di Intelligenza Artificiale locale di livello Enterprise. Il cuore del sistema è **Jarvis**, un **Cognitive Proxy** asincrono scritto in Python (FastAPI + Granian) che funge da gateway intelligente per l'inferenza AI.

A differenza di un semplice proxy API, Jarvis è un **middleware cognitivo** che arricchisce ogni interazione con il LLM integrando memoria episodica a lungo termine, RAG (Retrieval-Augmented Generation) AST-aware sul codice sorgente locale, web intelligence in tempo reale, un loop agentico autonomo con tool-calling, bot Telegram, multi-userbot e scheduling di task.

L'inferenza avviene **interamente in-process** tramite `llama-cpp-python` su modelli GGUF locali: il proxy carica i pesi direttamente in VRAM all'avvio e li mantiene caldi per tutta la durata del processo. **Nessun dato lascia mai la tua infrastruttura** (Zero-Data-Leak).

> 📖 **Per agenti AI:** leggere `docs/AGENTS.md` per il contesto operativo completo.
> 📋 **Piano deployment:** `docs/plans/master_worker_implementation.md`

---

## 🚦 Stato del Sistema (al 2026-07-16)

| Componente | Stato | Note |
|---|---|---|
| **Worker GPU (Locale)** | ✅ **ONLINE** | Gemma 4 E2B QAT, n_gpu_layers=15, flash_attn=true, CUDA 13.0 overlay |
| **VPS Master** | ⏳ Da deployare | Piano completo pronto per deploy |
| **Container CUDA** | ✅ **FUNZIONANTE** | CUDA 13.0 overlay su base 12.2 per driver NVIDIA 580.159.03 |
| **GPU Inferenza** | ✅ **OK** | Chat: ~1036MiB (25%), Embed: ~400MiB (+10%), 86°C peak |
| **OpenAI API** | ✅ **COMPLETA** | 25 endpoint `/v1/*` — Chat, Audio, Assistants/Threads/Runs, DB race fix |
| **Embedding** | ✅ **Qwen3-Embedding-0.6B** | 768d MRL, Q8_0, ~396MiB VRAM |
| **Reranker** | ✅ **Qwen3-Reranker-0.6B** | CPU fp16, multilingua, MTEB-Code 73.42 |
| **Memoria Mem0** | ✅ **ATTIVA** | Qdrant vector store, spaCy entity extraction |
| **RAG AST** | ✅ **ATTIVO** | Tree-sitter: Go, Python, JS/TS, C, C++, Java, Rust, SQL, YAML |
| **SearXNG** | ✅ **ATTIVO** | Metasearch anonimo su :8081 |
| **Crawl4AI** | ✅ **ATTIVO** | Scraper headless su :11235 |
| **Telegram Bot** | ✅ **PRONTO** | Da attivare su Master (disabilitato su Worker) |
| **Pipeline Telemetry** | ✅ **ATTIVA** | Tracciamento richieste: step, LLM calls, gatekeeper, tool calls |
| **MCP Server v2 (Streamable HTTP)** | ✅ **ATTIVO** | `/api/mcp/v2` — 8 tool + 7 resources per diagnostica AI esterna |
| **MCP Server (stdio)** | ✅ **LEGACY** | Sostituito da MCP v2, mantiene compatibilità stdio |
| **Caveman Compression** | ✅ **OTTIMIZZATA** | `_strip_thinking()` rimuove metacognizione Qwen3.5; compressor prompt con esempio concreto |
| **Prompt Formatting** | ✅ **ATTIVA** | Regole formato tabelle/code block/bold + sezione finale `---` con Riepilogo/Attenzione |

---

## ⚠️ CUDA 13.0 Overlay — Nota Critica

Il container usa `nvidia/cuda:12.2.2-devel-ubuntu22.04` come base con overlay dei pacchetti **CUDA 13.0** dal repository NVIDIA:

```dockerfile
RUN apt-get install -y cuda-compiler-13-0 cuda-cudart-dev-13-0 libcublas-dev-13-0
```

**Perché?** Il driver host (NVIDIA 580.159.03) supporta CUDA 13.0 ma il runtime CUDA 12.2 del container base è incompatibile, causando crash GPU `ggml_cuda_can_mul_mat`. L'overlay CUDA 13.0 risolve il problema permettendo a `llama-cpp-python` di linkare correttamente le librerie CUDA 13.0 durante la compilazione con `-DGGML_CUDA=on`.

**Se il container non si avvia o crasha:**
```bash
nvidia-smi           # CUDA Version deve corrispondere
docker logs jarvis_worker | grep -i cuda
```

---

## 🏗️ Architettura del Sistema

### Topologia Master/Worker Edge-Cloud

```
┌──────────────────────────────────────────────────────────────────┐
│  VPS Debian (OVH)                                                │
│  8 vCore, 24GB RAM, NO GPU                                      │
│                                                                  │
│  Nodo MASTER (sempre online):                                    │
│  ├── jarvis:8000      (FastAPI + Granian + LlamaEngine CPU)     │
│  ├── qdrant:6333      (database vettoriale centralizzato)       │
│  ├── searxng:8081     (metasearch anonimo)                      │
│  ├── crawl4ai:11235   (scraper headless)                        │
│  ├── Bot Telegram + Userbots (TELEGRAM_ENABLED=true)            │
│  └── Modello: gemma-4-26B-A4B-it (CPU, ~14.2GB RAM)            │
└──────────────────────┬───────────────────────────────────────────┘
                       │ Tailscale VPN (WireGuard)
                       │ EXTERNAL_GPU_URL=http://100.64.0.2:8000
                       │
┌──────────────────────▼───────────────────────────────────────────┐
│  Laptop LENOVO IdeaPad Gaming 3 (OpenSUSE Tumbleweed)            │
│  i5-11300H, 16GB RAM, NVIDIA RTX 3050 Ti (4GB VRAM)            │
│                                                                  │
│  Nodo WORKER GPU (Online):                                       │
│  ├── jarvis_worker:8000   QDRANT_HOST=100.64.0.1                │
│  ├── Modello: Qwen3.5-4B-UD-Q4_K_XL.gguf (GPU)                 │
│  └── TELEGRAM_ENABLED=false (centralizzato sul Master)          │
└──────────────────────────────────────────────────────────────────┘
```

### Flusso di Inferenza e Failover

```
Client (Cherry Studio / Jan / Continue / Cursor / Telegram)
  │
  ▼
Master jarvis:8000
  ├── [EXTERNAL_GPU_URL valorizzato?]
  │     ├── SÌ: ping Worker (timeout 1.5s)
  │     │       ├── Worker ONLINE  → offload GPU via HTTP POST
  │     │       └── Worker OFFLINE → fallback CPU locale
  │     └── NO: inferenza locale CPU
  │
  ├── RAG: chunk codice da Qdrant (AST-aware, Tree-sitter)
  ├── Memoria: ricordi da Mem0 (Qdrant)
  ├── Web: SearXNG + Crawl4AI (prefisso /web o auto-discovery)
  └── Super-prompt XML → risposta LLM → loop tool-calling
```

### Gestione Esclusiva del Bot Telegram

Il bot Telegram è centralizzato sul nodo **Master (VPS)** per disponibilità 24/7:
- **Master:** `TELEGRAM_ENABLED=true` — Bot ufficiale + tutti gli Userbot
- **Worker:** `TELEGRAM_ENABLED=false` — mai abilitare (causa conflitti di sessione)

---

## 📦 Stack Docker

| Servizio | Container | Porte | Descrizione |
|---|---|---|---|
| `jarvis` | `jarvis` | 8000 | Nodo Master (CPU) |
| `jarvis_worker` | `jarvis_worker` | 8000 | Nodo Worker (GPU) |
| `qdrant` | `qdrant_db` | 6333, 6334 | Database vettoriale |
| `searxng` | `searxng` | 8081 | Metasearch anonimo |
| `crawl4ai` | `crawl4ai_server` | 11235 | Web scraper headless |

---

## 🧠 Analisi Completa del Codebase

### Struttura dei File

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

**Tag d'Azione nella Risposta LLM — Registro Completo (21 tag):**

I tag XML vengono intercettati dalla risposta del LLM e processati da `tag_processor.py` prima che il testo pulito arrivi all'utente. La visibilità determina se il tag e il suo contenuto vengono rimossi (`hidden`/`action`) o lasciati nel testo (`kept`). I tag `action` generano feedback visibile all'utente.

| Tag | Formato | Visibilità | Self-Closing | Descrizione |
|---|---|---|---|---|
| `MEMORY` | `<MEMORY>testo</MEMORY>` | hidden | ❌ | Salva un fatto in memoria episodica (Mem0) |
| `SCHEDULE` | `<SCHEDULE>cron_expr\|promemoria</SCHEDULE>` | action | ❌ | Crea un promemoria schedulato (cron) |
| `NOTIFY_ONCE` | `<NOTIFY_ONCE>YYYY-MM-DD HH:MM\|testo</NOTIFY_ONCE>` | action | ❌ | Promemoria singolo a data fissa |
| `NOTIFYONCE` | `<NOTIFYONCE>...</NOTIFYONCE>` | action | ❌ | Alias per NOTIFY_ONCE (senza underscore) |
| `NOTIFY_IN` | `<NOTIFY_IN>minuti\|testo</NOTIFY_IN>` | action | ❌ | Timer relativo tra N minuti |
| `NOTIFYIN` | `<NOTIFYIN>...</NOTIFYIN>` | action | ❌ | Alias per NOTIFY_IN (senza underscore) |
| `SSH` | `<SSH>server\|comando</SSH>` | action | ❌ | Esecuzione comando SSH su server remoto |
| `TODO_ADD` | `<TODO_ADD>desc\|prio\|scad\|tipo</TODO_ADD>` | action | ❌ | Aggiunge un task alla todo list |
| `TODO_DONE` | `<TODO_DONE>id</TODO_DONE>` | action | ❌ | Segna un task come completato |
| `WEB` | `<WEB>query</WEB>` | action | ❌ | Esegue una ricerca web e include i risultati |
| `FILE` | `<FILE>path/file</FILE>` | action | ❌ | Legge e include contenuto di un file |
| `EMOTION` | `<EMOTION>stato</EMOTION>` | hidden | ❌ | Imposta stato emotivo per l'interfaccia UI |
| `THINK_DEEP` | `<THINK_DEEP/>` | hidden | ✅ | Attiva modalità ragionamento approfondito |
| `CACHE_CLEAR` | `<CACHE_CLEAR/>` | action | ✅ | Resetta la cache semantica |
| `CONFIDENCE` | `<CONFIDENCE>0.95</CONFIDENCE>` | hidden | ❌ | Autovalutazione confidenza della risposta |
| `ASK` | `<ASK>domanda</ASK>` | action | ❌ | Il LLM fa una domanda all'utente (reverse interaction) |
| `RAG` | `<RAG>project_name</RAG>` | action | ❌ | Forza RAG su un progetto specifico |
| `SUMMARY` | `<SUMMARY target="user_id">testo</SUMMARY>` | action | ❌ | Salva un riepilogo nella memoria di un altro utente |
| `BRANCH` | `<BRANCH>project\|branch</BRANCH>` | action | ❌ | Cambia branch git in un progetto |
| `COMMIT` | `<COMMIT>message</COMMIT>` | action | ❌ | Crea un commit git con i cambiamenti locali |
| `EXEC` | `<EXEC>timeout\|comando</EXEC>` | action | ❌ | Esegue un comando shell readonly (whitelist) |

> **Nota Streaming:** Nello streaming, i tag che si estendono su più chunk vengono gestiti da `TagSafeStream` (stato `_in_tag`/`_sc_pending`), che trattiene il contenuto in buffer fino al completamento del tag. A fine stream, `process_response_tags()` elabora il testo completo (con tag) per gli effetti collaterali (memoria, scheduling, notifiche).

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

### 13. 🔍 Pipeline Telemetry & MCP Server (`telemetry.py`, `mcp_server.py`)

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

---

## 🤖 Modelli LLM

Jarvis usa **esclusivamente `llama-cpp-python`** con file GGUF. Nessun processo Ollama.

### Worker Locale (RTX 3050 Ti — 4GB VRAM)

| Modello | Stato | VRAM | Note |
|---|---|---|---|
| `gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf` | ✅ **IN USO** | 1036MiB (25%) | n_gpu_layers=15, flash_attn=true, 2.1B param QAT |
| `Qwen3-Embedding-0.6B-Q8_0.gguf` | ✅ IN USO | +400MiB (35% tot) | 768d MRL |
| `Qwen3.5-4B-UD-Q4_K_XL.gguf` | ⏳ Backup | 1924MiB (47%) | Sostituito da Gemma 4 (86% meno VRAM) |
| `nomic-embed-text-v1.5.gguf` | ❌ Rimosso | CPU | Rimpiazzato da Qwen3 |

### Master VPS (CPU-only — 24GB RAM)

| Modello | Stato | RAM | Note |
|---|---|---|---|
| `gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf` | ⏳ Da scaricare | ~14.2GB | MoE: ~4B attivi, 8-12 t/s |

---

## ✨ Funzionalità Principali

### Core IA & Inferenza
- **LlamaEngine singleton**: modelli caldi in VRAM per tutta la durata del processo
- **PriorityLock**: serializzazione GPU con priorità (chat > embedding)
- **Offloading GPU**: Worker remoto con failover automatico via ping 1.5s
- **Thinking Mode**: supporto nativo `<|think|>` per Gemma/DeepSeek/QwQ
- **Flash Attention**: riduzione VRAM 30-50%
- **Warmup CUDA JIT**: evita delay di 30s+ sulla prima richiesta

### Memoria Intelligente
- **Memoria Episodica (Mem0 + Qdrant)**: estrazione concetti chiave via spaCy, archiviazione vettoriale
- **RAG AST-aware (Tree-sitter)**: chunking semantico per 9 linguaggi di programmazione
- **Reranker duale**: Qwen3-Reranker (primario) + FlashRank (fallback)
- **Watchdog filesystem**: re-embedding automatico al salvataggio file
- **Semantic Cache**: cache risposte con soglia cosine configurabile
- **Consolidamento notturno**: riduzione automatica della memoria episodica

### Web Intelligence
- **SearXNG**: metasearch anonimizzato, top 3 risultati
- **Crawl4AI**: scraping headless parallelo, output fit_markdown
- **Auto Web Discovery**: ricerca automatica se RAG non trova risultati
- **Web Knowledge Cache**: memorizzazione risultati per reuse

### Bot Telegram & Multi-Userbot
- **Bot Ufficiale**: whitelist utenti, dashboard inline, Markdown, ToDo list
- **Userbot (Telethon)**: clone per ogni utente via OTP, interazione in chat private
- **Messaggi vocali**: trascrizione faster-whisper, risposta gTTS
- **Scheduler APScheduler**: promemoria singoli/ricorrenti con timer relativi

### Loop Agentico (Tool Calling)
- 5 tool built-in: write_file, read_file, delete_file, replace_in_file, run_shell_command
- Skill dinamiche da YAML in `jarvis/skills/`
- Conferma Telegram per operazioni distruttive (timeout 5 min)
- Integrazione SSH remota via `<SSH>` tag

### Elaborazione Tag d'Azione XML
- **21 tag XML** processati da `tag_processor.py` nella risposta del LLM: memoria (`<MEMORY>`), scheduling (`<SCHEDULE>`/`<NOTIFY_ONCE>`/`<NOTIFY_IN>`), esecuzione (`<SSH>`/`<EXEC>`), web (`<WEB>`), task (`<TODO_ADD>`/`<TODO_DONE>`), ragionamento (`<THINK_DEEP/>`), RAG (`<RAG>`), git (`<BRANCH>`/`<COMMIT>`), e altri
- **TagSafeStream**: state machine anti-leak per streaming — impedisce la fuga di tag XML incompleti quando il LLM genera token uno alla volta, mantenendo lo stato `_in_tag`/`_sc_pending` tra chunk successivi
- **process_response_tags()**: post-processo asincrono a fine stream che esegue gli handler dei tag (salvataggio memoria, scheduling notifiche, esecuzione comandi) sul testo completo con chiusura tag orfani
- **Estendibilità**: nuovi tag registrabili a runtime via `register_tag(TagDef)`

### Endpoint API

| Endpoint | Metodo | Funzione |
|---|---|---|---|
| `/api/chat` | POST | Chat con memoria + RAG + tool-calling |
| `/api/generate` | POST | Generate + cache semantica |
| `/api/embed` / `/api/embeddings` | POST | Embeddings (legacy) |
| `/api/tags`, `/api/ps`, `/api/show`, `/api/version` | GET/POST | Stub compatibilità Ollama |
| `/api/project-tree` | GET | Albero del progetto indicizzato |
| `/api/webhook/git` | POST | Git webhook → pull → re-ingestion |
| `/api/reset-all` | GET/POST | Reset RAG + Mem0 |
| `/docs` | GET | Swagger UI |
| **Pipeline Telemetry** | | |
| `/api/telemetry/traces` | GET | Ultimi N pipeline trace completati |
| `/api/telemetry/traces/active` | GET | Trace correntemente in esecuzione |
| `/api/telemetry/traces/{request_id}` | GET | Cerca trace per request_id |
| `/api/telemetry/gatekeeper` | GET | Statistiche cumulative Gatekeeper |
| `/api/telemetry/errors` | GET | Contatori di errore |
| `/api/telemetry/status` | GET | Uptime, richieste, token, stato sistema |
| `/api/telemetry/model` | GET | Informazioni modello LLM (family, GPU layers) |
| `/api/telemetry/pending_ops` | GET | Background tasks, coda watchdog |
| **MCP Server (SSE Transport)** | | |
| `/api/mcp/sse` | GET | Connessione SSE persistente per MCP |
| `/api/mcp/message` | POST | Invio messaggio JSON-RPC MCP |
| **OpenAI-compatibili** | | |
| `/v1/chat/completions` | POST | Chat completion (streaming SSE, tool-calling, confirmation tokens) |
| `/v1/completions` | POST | Text completion legacy (streaming SSE, echo) |
| `/v1/embeddings` | POST | Embeddings (float/base64 encoding) |
| `/v1/models` | GET | Lista modelli |
| `/v1/models/{model_name}` | GET | Dettaglio modello |
| `/v1/moderations` | POST | Moderazione contenuti (LLM-based + keyword fallback) |
| `/v1/audio/transcriptions` | POST | Trascrizione audio (faster-whisper) |
| `/v1/audio/translations` | POST | Traduzione audio → inglese (faster-whisper) |
| `/v1/audio/speech` | POST | Text-to-speech (gTTS) |
| `/v1/images/generations` | POST | Stub 400 (model not available) |
| `/v1/images/edits` | POST | Stub 400 (model not available) |
| `/v1/images/variations` | POST | Stub 400 (model not available) |
| `/v1/assistants` | GET | Lista Assistenti |
| `/v1/assistants` | POST | Crea Assistente |
| `/v1/assistants/{id}` | GET | Dettaglio Assistente |
| `/v1/assistants/{id}` | POST | Modifica Assistente |
| `/v1/assistants/{id}` | DELETE | Cancella Assistente |
| `/v1/threads` | POST | Crea Thread |
| `/v1/threads/{id}` | GET | Dettaglio Thread |
| `/v1/threads/{id}/runs` | POST | Esegui Run su Thread |
| `/v1/threads/{id}/runs/{run_id}/submit_tool_outputs` | POST | Tool output per Run |
| `/v1/vector_stores` | GET/POST | Lista/Crea Vector Store |
| `/v1/files` | POST | Upload file |
| `/v1/uploads` | POST | Upload large file in parti |

---

### Pipeline Telemetry & MCP per Diagnostica AI
- **PipelineTracer**: tracciamento per-request con step timing, LLM calls, gatekeeper decisioni, tool calls
- **GatekeeperStats**: statistiche cumulative di classificazione (bypass rate, confidence media, by_intent)
- **Ring buffer 500 trace**: ultimi 500 trace completati sempre disponibili in memoria
- **HTTP REST**: 8 endpoint `/api/telemetry/*` per query diretta
- **MCP stdio**: server esterno per Claude Code / Cursor via `.mcp.json`
- **MCP SSE**: endpoint in-app `/api/mcp/sse` per connessioni persistenti

## 🔌 Connessione al Server MCP di Jarvis

Jarvis espone due modalità di accesso MCP per permettere ad agenti AI esterni
(Claude Code, Cursor, Continue, ecc.) di ispezionare lo stato interno del sistema
a fini di diagnostica e debug.

### Modalità 1: Server MCP stdio (per agenti esterni)

Configura il tuo agente AI per lanciare il server MCP come subprocesso.
Jarvis include già il file `.mcp.json` nella root del progetto:

```json
{
  "mcpServers": {
    "jarvis-telemetry": {
      "command": "python",
      "args": ["-m", "jarvis.mcp_server"],
      "env": {
        "JARVIS_URL": "http://localhost:8000"
      },
      "description": "Jarvis telemetry — espone pipeline trace, gatekeeper stats, error counters e stato del sistema per diagnostica AI."
    }
  }
}
```

**Claude Code / Cursor** rilevano automaticamente `.mcp.json` nella root del progetto.
L'agente può quindi usare i tool MCP per ispezionare Jarvis.

**Uso standalone** (per test):
```bash
# Collega il server MCP a un'istanza Jarvis in esecuzione
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python -m jarvis.mcp_server
```

Se Jarvis è su un host diverso:
```bash
JARVIS_URL=http://192.168.1.100:8000 python -m jarvis.mcp_server
```

### Modalità 2: Endpoint MCP SSE (in-app)

Jarvis espone un endpoint SSE direttamente via FastAPI. Utile per agenti
che supportano il trasporto SSE persistente.

1. Connessione SSE:
   ```
   GET /api/mcp/sse
   ```
   Il server risponde con un evento `endpoint` che specifica l'URL per i messaggi.

2. Invio comandi JSON-RPC:
   ```
   POST /api/mcp/message?session_id=<id>
   Content-Type: application/json

   {"jsonrpc":"2.0","id":1,"method":"tools/list"}
   ```

### Elenco Completo Tool MCP

| Tool | Descrizione | Parametri |
|---|---|---|
| `get_recent_traces` | Ultimi N pipeline trace completati | `limit` (int, default 10) |
| `get_active_traces` | Trace correntemente in esecuzione | nessuno |
| `get_trace_by_id` | Cerca un trace completato per request_id | `request_id` (stringa, required) |
| `get_gatekeeper_stats` | Statistiche cumulative Gatekeeper | nessuno |
| `get_errors` | Contatori di errore per diagnostica | nessuno |
| `get_status` | Stato sistema: uptime, richieste, token | nessuno |
| `get_model_info` | Info modello LLM: family, GPU layers | nessuno |
| `get_pending_ops` | Operazioni pendenti: background tasks, coda watchdog | nessuno |
| `get_llm_call_breakdown` | Analisi aggregata chiamate LLM da ultimi 100 trace | nessuno |

### Risorse MCP (resources)

| URI | Descrizione |
|---|---|
| `jarvis://traces/recent` | Ultimi 10 pipeline trace |
| `jarvis://traces/active` | Trace attualmente in esecuzione |
| `jarvis://gatekeeper/stats` | Statistiche cumulative Gatekeeper |
| `jarvis://errors/counters` | Contatori di errore |
| `jarvis://system/status` | Uptime, richieste, token |
| `jarvis://model/info` | Informazioni modello LLM |
| `jarvis://system/pending_ops` | Operazioni pendenti |

### Esempio di Utilizzo

**Debug di una richiesta lenta:**
1. Chiama `get_recent_traces(limit=5)` per vedere gli ultimi trace
2. Identifica il `request_id` del trace più lento
3. Chiama `get_trace_by_id(request_id="abc123")` per vedere i dettagli
4. Analizza gli step: `build_omniscient_prompt`, `gemma_generation`, `tool_execution`

**Verifica dello stato del Gatekeeper:**
1. Chiama `get_gatekeeper_stats()`
2. Controlla `bypassed` vs `llm_called` — se il bypass rate è basso, il Gatekeeper sta funzionando correttamente
3. Controlla `avg_confidence` — se < 0.7, il modello Gatekeeper potrebbe avere problemi

**Diagnostica errori:**
1. Chiama `get_errors()` per vedere i contatori errori
2. Chiama `get_status()` per verificare uptime e richieste totali
3. Se `total_requests` è alto ma non ci sono trace recenti, potrebbe esserci un problema di inizializzazione del tracer

---

## 🚀 Avvio Rapido

### Worker Locale (Sviluppo — Modalità Offline)

**Prerequisiti:**
- Docker + NVIDIA Container Toolkit
- GPU NVIDIA con driver ≥ 580.x (CUDA 13.0)
- Modelli GGUF in `jarvis/models/`

```bash
cd ~/ai-ecosystem

# 1. Avviare Qdrant locale
docker run -d --name qdrant_local \
  --network ai_network \
  -p 6333:6333 \
  -v "$(pwd)/data/qdrant:/qdrant/storage" \
  qdrant/qdrant:latest

# 2. Build immagine (CUDA 13.0 overlay + llama-cpp-python)
docker compose -f docker-compose.worker.yml build jarvis_worker

# 3. Avviare Jarvis Worker
./start_worker.sh
```

> ⚠️ Build lento (~5-10 min): compila `llama-cpp-python` da sorgente con CUDA.

### Verifica GPU

```bash
docker logs jarvis_worker | grep -i "vram\|n_gpu_layers"
# Output: 🎯 [VRAM] Dopo caricamento ... MiB / 4096MiB
# Output: ⚙️ n_gpu_layers=15
```

### Test Rapido

```bash
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen","messages":[{"role":"user","content":"Ciao, presentati"}],"max_tokens":100}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['choices'][0]['message']['content'])"
```

---

## 🔧 Configurazione

Tutte le variabili in `.env`. Copiare da `.env.example`.

### Variabili Essenziali

```env
# === ARCHITETTURA ===
QDRANT_HOST=localhost            # localhost in offline, IP Tailscale in online
EXTERNAL_GPU_URL=                # Master: http://100.64.0.2:8000 | Worker: vuoto

# === MODELLO LLM ===
LLAMA_MODEL_PATH=./models/Qwen3.5-4B-UD-Q4_K_XL.gguf
N_GPU_LAYERS=15                  # 0 su VPS (CPU-only), max 15 su RTX 3050 Ti 4GB
LLM_FLASH_ATTN=true

# === RAG ===
MAIN_PROJECT_PATH=/host_fs/home/alfio/Projects
EMBEDDING_DIMS=768

# === WATCHDOG FILESYSTEM ===
WATCHDOG_ENABLED=true             # true/false (sovrascrive auto-detect)
WATCHDOG_TIMEOUT=5                # secondi tra polling (default: 5)
WATCHDOG_WATCH_MODE=per_project   # "full" o "per_project"
```

---

## 🛠️ Comandi di Manutenzione

```bash
# Log in tempo reale
docker logs jarvis_worker --tail=50 -f

# Shell nel container
docker exec -it jarvis_worker /bin/bash

# Reset RAG (cancella collezioni Qdrant e re-ingerisce)
curl -X POST http://localhost:8000/api/reset-all

# Stato GPU
nvidia-smi

# Backup dati
tar -cvzf backup_ai_$(date +%Y%m%d).tar.gz ./data .env

# Sync Worker → Master
./sync_to_master.sh
```

---

## 📝 Changelog

### v9.6.0 (2026-07-16) — MCP Server v2 + compressione ottimizzata + prompt format rules
- **MCP Server v2 Streamable HTTP**: nuovo endpoint `/api/mcp/v2` conforme MCP Streamable HTTP (RFC 2025-11-25). 8 tool + 7 resources. Rimossi vecchi endpoint SSE (`/api/mcp/sse`, `/api/mcp/message`).
- **Model info rewrite**: `get_telemetry_model()` ora legge da `config.py` invece che dal motore. Sync in `_mcp_handlers.py`. `GATEKEEPER_N_GPU_LAYERS` per offload GPU opzionale.
- **`_strip_thinking()`**: nuova funzione in `llm_engine.py` che rimuove tag `<think>`, analisi strutturate numerate e meta-ragionamenti dalle risposte del Gatekeeper Qwen3.5. Applicata in `extract_content()`, `compress_prompt()` e su ogni risposta LLM.
- **Compressor prompt riscritto**: `CAVEMAN_COMPRESSOR_SYSTEM_PROMPT` ora include esempio concreto INPUT/OUTPUT per guidare Qwen3.5 verso compressione reale invece di analisi.
- **Prompt format rules**: system prompt aggiornato con regole esplicite per tabelle Markdown, code block, grassetto. Sezione finale `---` con Riepilogo/Attenzione richiesta in ogni risposta.
- **Telemetry prompt tracing**: `PipelineTrace` ora include campi `system_prompt`, `rag_context`, `user_content`, `compressed_text`, `llm_response` per debug completo della pipeline.
- **`finalize_trace` parameter**: `build_omniscient_prompt()` supporta `finalize_trace=False` per uso esterno (MCP chat_send).
- **fix: options=None**: bug in `ollama_chat()` che causava errore quando `options` era nullo.
- **AGENTS.md**: regola n.9 (non riavviare Jarvis autonomamente), nota MCP diagnostic per agenti DEVs.

### v9.5.0 (2026-06-30) — TagSafeStream: fix leak tag XML nello streaming + documentazione completa
- **TagSafeStream introdotto**: nuova classe state machine in `tag_processor.py` che previene la fuga di tag XML incompleti (`<NOTIFY_ONCE>`, `<CONFIDENCE>`, ecc.) quando il LLM genera token uno alla volta. Mantiene stato `_in_tag`/`_sc_pending` tra chunk successivi e yielda solo contenuto safe
- **3 endpoint streaming aggiornati**: `openai_router.py`, `openai/chat.py`, `main.py` (entrambi `/api/chat` e `/api/generate`) ora usano `TagSafeStream.process()` invece di `strip_action_tags()` per ogni chunk
- **Side effects preservati**: `process_response_tags(full_text)` a fine stream continua a ricevere il testo completo con tag per salvataggio memoria, scheduling notifiche, esecuzione comandi
- **Documentazione espansa**: README.md e AGENTS.md aggiornati con tabella completa dei 21 tag XML (formato, visibilità, self-closing, descrizione), lista endpoint OpenAI completa (25 endpoint Assistants/Threads/Runs), e nota tecnica sul funzionamento dello streaming

### v9.4.0 (2026-06-29) — Refactor OpenAI in sottopacchetto + DB race fix
- **Refactor OpenAI:** `openai_router.py` → pacchetto `openai/` con 17 moduli. Lazy import tramite `init_openai_routes()`, init ritardato nell'lifespan
- **Assistants API:** Nuovi endpoint per Assistants, Threads, Runs, Vector Stores, Files, Uploads
- **DB race condition fix:** `asyncio.Lock` + double-check in `get_db()` di `openai/state.py` — risolve `RuntimeError: OpenAIDatabase not initialised` su richieste concorrenti
- **Audio API:** Aggiunto endpoint `/v1/audio/translations` (forced en); `/v1/audio/speech` migliorato
- **Images API:** Stub `/v1/images/*` (generations, edits, variations) con errore 400 standard OpenAI
- **Reranker modulare:** Estratto `rag_reranker.py` da `rag.py`: Qwen3-Reranker (transformers fp16 CPU) + fallback FlashRank ONNX
- **Cache semantica:** Estratto `rag_cache.py` da `rag.py`: `semantic_cache_search/store/clear`, `save_web_knowledge`, `search_web_knowledge`
- **Telegram formatting:** Estratto `telegram_format.py` da `tag_processor.py`: `telegram_safe_format()` con escape MarkdownV2/Markdown
- **Dashboard template:** Estratto `dashboard_template.py` da `dashboard.py`: template HTML/CSS/JS con Chart.js, Sigma.js, stile cyberpunk
- **Documentazione:** AGENTS.md e README.md aggiornati con nuovo pacchetto e fix

### v9.3.0 (2026-06-28) — OpenAI API completa + codebase cleanup
- **OpenAI API:** Implementati 6 nuovi endpoint: `/v1/completions`, `/v1/embeddings`, `/v1/audio/transcriptions`, `/v1/audio/speech`, `/v1/models/{model_name}`, `/v1/moderations`
- **main.py:** Da 967 a 1497 righe (+55%) — nuovi Pydantic models, streaming SSE, faster-whisper, gTTS
- **Codebase cleanup:** Rimossi `scratch/` (script orfani), `__pycache__/` dalla sorgente, symlink rotti in `documents/`
- **Documentazione:** README e AGENTS.md aggiornati con nuovi endpoint e struttura file attuale
- **docker-compose.yml:** Rimosso (superseduto dalla split vps.yml + worker.yml); deploy_vps.sh aggiornato a vps.yml

### v9.2.0 (2026-06-24) — Analisi completa + Architettura Provider
- **README:** Analisi completa e approfondita di tutti i 14 moduli Jarvis
- **Architettura:** Documentati componenti, flussi e dipendenze
- **Provider Esterni:** Valutata e pianificata integrazione provider cloud (Gemini)

### v9.1.0 (2026-06-23) — CUDA 13.0 Overlay + GPU Inference stabile
- **CUDA 13.0 overlay:** Pacchetti overlay su base 12.2 per driver 580.159.03
- **llama-cpp-python:** Build da GitHub main con GGML_CUDA=on, CMAKE_CUDA_ARCHITECTURES=86
- **GPU:** Inferenza stabile con n_gpu_layers=15, flash_attn=true
- **.dockerignore:** Esclusi modelli (8.7GB) dal build context
- **Modello:** Qwen3.5-4B-UD-Q4_K_XL.gguf, Qwen3-Embedding-0.6B-Q8_0

### v9.0.0 (2026-06-19) — Architettura Master/Worker
- **Architettura:** Migrazione da single-node a Master/Worker con VPN Tailscale
- **Networking:** Rimosso Ngrok, connettività via Tailscale WireGuard
- **Telegram:** Centralizzato sul Master — TELEGRAM_ENABLED=false sul Worker
- **llm_engine.py:** chat_format=None, n_gpu_layers e n_ctx da .env
- **Dockerfile:** Build llama-cpp-python da master GitHub per Gemma 4

---

## 🌐 Strategia Integrazione Provider Esterni

### Stato Attuale
Jarvis è **100% locale** — nessuna dipendenza da provider cloud. L'unica eccezione è l'offloading GPU verso un Worker sulla VPN Tailscale.

### Perché Integrare Provider Esterni?

| Scenario | Problema Locale | Soluzione Esterna |
|---|---|---|
| **Conoscenza enciclopedica** | Modello 4B non sa tutto | Gemini 2.5 Pro ha conoscenza aggiornata |
| **Multimodalità** | Nessun supporto immagini | Gemini accetta immagini, audio, video |
| **Contesto lunghissimo** | Max 32K token (locale) | Gemini 1M+ token |
| **Code review incrociata** | Unico punto di vista | Confronto con modello diverso |
| **Fallback disponibilità** | Worker GPU offline = CPU lenta | Cloud sempre disponibile |
| **Traduzioni multilingua** | Qualità variabile | Gemini eccelle in multilingua |

### Architettura Proposta: Provider Router

```
┌─────────────────────────────────────────────────────────────┐
│  ProviderRouter                                              │
│                                                              │
│  Strategy:                                                   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  1. Locale (llama-cpp-python) ← PRIORITARIO          │   │
│  │     - Sempre disponibile, zero latenza di rete        │   │
│  │     - Privacy assoluta                                │   │
│  │                                                       │   │
│  │  2. Worker GPU (EXTERNAL_GPU_URL)                     │   │
│  │     - Accelerazione GPU remota                        │   │
│  │     - Failover automatico (1.5s ping)                 │   │
│  │                                                       │   │
│  │  3. Gemini API (cloud)                                │   │
│  │     - Fallback per conoscenza mancante                │   │
│  │     - Richieste multimodali (immagini)                │   │
│  │     - Contesto lunghissimo (>32K)                     │   │
│  │     - Routing selettivo per specifiche task            │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### Implementazione Suggerita

**Nuovi file:**
- `jarvis/external_providers.py` — Classe base astratta `BaseProvider` + `ProviderRouter`
- `jarvis/gemini_provider.py` — Implementazione Google Gemini via `google-generativeai`

**Modifiche a file esistenti:**
- `jarvis/config.py` — Variabili `GEMINI_API_KEY`, `GEMINI_MODEL`, `EXTERNAL_PROVIDER_STRATEGY`
- `jarvis/llm_engine.py` — Integrazione `ProviderRouter` nel flusso `generate_chat`
- `jarvis/prompt_builder.py` — Routing selettivo per web knowledge / classificazione
- `.env.example` — Aggiunta variabili Gemini

**Strategie di Routing:**
1. `fallback_only` — Usa provider esterno solo se locale fallisce
2. `selective` — Routing basato su tipo richiesta (es. web knowledge → Gemini)
3. `parallel` — Chiama entrambi, sceglie il meglio (lento, alta qualità)
4. `multimodal` — Solo per richieste con immagini/allegati

**Considerazioni Privacy:**
- Mai inviare codice proprietario a provider cloud
- Solo richieste di conoscenza generale / web research
- Opzione `PRIVACY_MODE=strict` per bloccare routing esterno su codice

### Piano di Integrazione

| Fase | Task | Priorità |
|---|---|---|
| **1** | Aggiungere `GEMINI_API_KEY` a `.env.example` e `config.py` | Alta |
| **2** | Creare `external_providers.py` con `BaseProvider` + `ProviderRouter` | Alta |
| **3** | Creare `gemini_provider.py` con wrapper Google Generative AI | Alta |
| **4** | Integrare `ProviderRouter` in `llm_engine.py` (fallback + selective) | Alta |
| **5** | Aggiungere routing selettivo in `prompt_builder.py` (web knowledge) | Media |
| **6** | Aggiungere supporto multimodale (immagini in input) | Media |
| **7** | Documentare strategia e privacy nella configurazione | Bassa |

---

🌐 **NeuroNet** — *Infrastruttura di Intelligenza Artificiale Locale Riservata.*
