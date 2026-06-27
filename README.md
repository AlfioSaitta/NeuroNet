# NeuroNet — AI Cognitive Proxy

Questo repository ospita **NeuroNet**, un ecosistema di Intelligenza Artificiale locale di livello Enterprise. Il cuore del sistema è **Jarvis**, un **Cognitive Proxy** asincrono scritto in Python (FastAPI + Granian) che funge da gateway intelligente per l'inferenza AI.

A differenza di un semplice proxy API, Jarvis è un **middleware cognitivo** che arricchisce ogni interazione con il LLM integrando memoria episodica a lungo termine, RAG (Retrieval-Augmented Generation) AST-aware sul codice sorgente locale, web intelligence in tempo reale, un loop agentico autonomo con tool-calling, bot Telegram, multi-userbot e scheduling di task.

L'inferenza avviene **interamente in-process** tramite `llama-cpp-python` su modelli GGUF locali: il proxy carica i pesi direttamente in VRAM all'avvio e li mantiene caldi per tutta la durata del processo. **Nessun dato lascia mai la tua infrastruttura** (Zero-Data-Leak).

> 📖 **Per agenti AI:** leggere `docs/AGENTS.md` per il contesto operativo completo.
> 📋 **Piano deployment:** `docs/plans/master_worker_implementation.md`

---

## 🚦 Stato del Sistema (al 2026-06-28)

| Componente | Stato | Note |
|---|---|---|
| **Worker GPU (Locale)** | ✅ **ONLINE** | Qwen3.5-4B-UD Q4_K_XL, n_gpu_layers=15, flash_attn=true, CUDA 13.0 overlay |
| **VPS Master** | ⏳ Da deployare | Piano completo pronto per deploy |
| **Container CUDA** | ✅ **FUNZIONANTE** | CUDA 13.0 overlay su base 12.2 per driver NVIDIA 580.159.03 |
| **GPU Inferenza** | ✅ **OK** | Chat: ~1924MiB (47%), Embed: ~2320MiB (57%), 86°C peak |
| **Embedding** | ✅ **Qwen3-Embedding-0.6B** | 768d MRL, Q8_0, ~396MiB VRAM |
| **Reranker** | ✅ **Qwen3-Reranker-0.6B** | CPU fp16, multilingua, MTEB-Code 73.42 |
| **Memoria Mem0** | ✅ **ATTIVA** | Qdrant vector store, spaCy entity extraction |
| **RAG AST** | ✅ **ATTIVO** | Tree-sitter: Go, Python, JS/TS, C, C++, Java, Rust, SQL, YAML |
| **SearXNG** | ✅ **ATTIVO** | Metasearch anonimo su :8081 |
| **Crawl4AI** | ✅ **ATTIVO** | Scraper headless su :11235 |
| **Telegram Bot** | ✅ **PRONTO** | Da attivare su Master (disabilitato su Worker) |

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
    ├── main.py                      # Entry point FastAPI/Granian (1497 righe)
    ├── config.py                    # Configurazione centralizzata (325 righe)
    ├── state.py                     # Stato globale mutabile (72 righe)
    ├── llm_engine.py                # LlamaEngine + PriorityLock (571 righe)
    ├── rag.py                       # Pipeline RAG completa (1797 righe)
    ├── memory.py                    # Mem0 + helper memoria (187 righe)
    ├── memory_backup.py             # Export/import memoria JSON (68 righe)
    ├── prompt_builder.py            # Gatekeeper + super-prompt (479 righe)
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
    ├── tag_processor.py             # Elaborazione tag XML nelle risposte (1043 righe)
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
                    │  - Health monitor    │
                    │  - Re-embedding      │
                    │    automatico        │
                    └──────────────────────┘
```

**Feature evidenziate:**
- **AST Chunking semantico:** usa Tree-sitter per parsare il codice in nodi significativi (funzioni, classi, type declarations, import sections)
- **Reranker duale:** Qwen3-Reranker (primario, multilingua, MTEB-Code 73.42) → FlashRank (fallback ONNX)
- **Gitignore-aware:** rispetta .gitignore nei progetti monitorati tramite pathspec
- **Watchdog real-time:** PollingObserver per Docker compatibilità, ri-embedding automatico al salvataggio
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

**Tag d'Azione nella Risposta LLM:**
- `<MEMORY>testo</MEMORY>` — Salva in Mem0
- `<NOTIFY_ONCE>YYYY-MM-DD HH:MM|promemoria</NOTIFY_ONCE>` — Reminder singolo
- `<NOTIFY_IN>minuti|promemoria</NOTIFY_IN>` — Timer relativo
- `<SCHEDULE>cron_expr|promemoria</SCHEDULE>` — Task ricorrente
- `<SSH>server|comando</SSH>` — Esecuzione remota SSH
- `<TODO_ADD>desc|prio|scadenza|tipo</TODO_ADD>` — Nuovo task
- `<TODO_DONE>id</TODO_DONE>` — Task completato

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

## 🤖 Modelli LLM

Jarvis usa **esclusivamente `llama-cpp-python`** con file GGUF. Nessun processo Ollama.

### Worker Locale (RTX 3050 Ti — 4GB VRAM)

| Modello | Stato | VRAM | Note |
|---|---|---|---|
| `Qwen3.5-4B-UD-Q4_K_XL.gguf` | ✅ **IN USO** | 1924MiB (47%) | n_gpu_layers=15, flash_attn=true |
| `Qwen3-Embedding-0.6B-Q8_0.gguf` | ✅ IN USO | +396MiB (57% tot) | 768d MRL |
| `nomic-embed-text-v1.5.gguf` | ⏳ Sostituito | CPU | Rimpiazzato da Qwen3 |

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
| **OpenAI-compatibili** | | |
| `/v1/chat/completions` | POST | Chat completion (streaming SSE) |
| `/v1/completions` | POST | Text completion (streaming SSE) |
| `/v1/embeddings` | POST | Embeddings (float/base64) |
| `/v1/models` | GET | Lista modelli |
| `/v1/models/{model_name}` | GET | Dettaglio modello |
| `/v1/moderations` | POST | Moderazione contenuti |
| `/v1/audio/transcriptions` | POST | Trascrizione audio (whisper) |
| `/v1/audio/speech` | POST | Text-to-speech (gTTS) |

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
