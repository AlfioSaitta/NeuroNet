---

# 🚀 Ecosistema AI Omnisciente — Chameleon Cognitive Stack

Questo repository ospita l'architettura di un ecosistema di Intelligenza Artificiale locale di livello Enterprise sviluppato per **Collateral Studios**. Il cuore del sistema è **Jarvis** (Collateral Studios Agent), un **Cognitive Proxy** asincrono che unisce la memoria episodica a lungo termine, il RAG (Retrieval-Augmented Generation) documentale AST-aware sul codice sorgente locale, un bot Telegram integrato, un loop agentico autonomo con tool-calling e capacità di esplorazione web automatizzata in tempo reale.

L'inferenza gira **in-process** tramite `llama-cpp-python` su modelli GGUF locali (**nessun container Ollama** — mai): il proxy carica i pesi direttamente in VRAM all'avvio e li mantiene caldi per tutta la durata del processo. L'intera infrastruttura garantisce la totale privacy dei dati (Zero-Data-Leak), latenze minime e portabilità assoluta.

L'architettura supporta una topologia **Master/Worker Edge-Cloud** connessa tramite **VPN Mesh Tailscale (WireGuard)**: un nodo *Master* sulla VPS ospita memoria, RAG, database vettoriale e il Bot Telegram (sempre disponibile), mentre un nodo *Worker* locale (dotato di GPU) riceve in offloading le inferenze pesanti.

> 📖 **Per agenti AI:** leggere `docs/AGENTS.md` per il contesto operativo completo.  
> 📋 **Piano deployment:** `docs/plans/master_worker_implementation.md`

---

## 🚦 Stato del Sistema (al 2026-06-22)

| Componente | Stato | Note |
|---|---|---|
| **Istanza Locale (Worker)** | ✅ **ONLINE** | Qwen3.5-4B su GPU (19/32 layer, ~3.5GB VRAM), Qdrant+Mem0+RAG attivi |
| **VPS Master** | ⏳ Da deployare | Deployment in preparazione — piano completo pronto |
| Modello Worker (Qwen3.5-4B) | ✅ **Definitivo** | Unico modello che entra in 4GB VRAM; Gemma 4 E2B incompatibile (418 tensori Q4_0) |
| Modello Master (Gemma 4 26B A4B) | ⏳ Da scaricare | `gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf` (~14.2GB) |
| Watchdog filesystem | ✅ **FIXATO** | PollingObserver + inode tracking os.walk + cleanup nested symlink |

---

## 🏗️ Architettura del Sistema

### Topologia Master/Worker

```
┌─────────────────────────────────────────────────────────────┐
│  VPS Debian (OVH) — 51.38.135.179                           │
│  8 vCore, 24GB RAM, NO GPU                                  │
│                                                             │
│  Nodo MASTER (sempre online):                               │
│  ├── jarvis:8000    (FastAPI + Granian + LlamaEngine CPU)   │
│  ├── qdrant:6333    (database vettoriale centralizzato)     │
│  ├── searxng:8081   (metasearch anonimo)                    │
│  ├── crawl4ai:11235 (scraper headless)                      │
│  └── Bot Telegram + Userbots (TELEGRAM_ENABLED=true)        │
└──────────────────────┬──────────────────────────────────────┘
                       │ Tailscale VPN (WireGuard)
                       │ EXTERNAL_GPU_URL=http://100.64.0.2:8000
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  Laptop LENOVO IdeaPad Gaming 3 (OpenSUSE Tumbleweed)       │
│  i5-11300H, 16GB RAM, NVIDIA RTX 3050 Ti (4GB VRAM)        │
│                                                             │
│  Nodo WORKER GPU (Online — punta al Master):                │
│  └── jarvis_worker:8000  QDRANT_HOST=100.64.0.1            │
│                          TELEGRAM_ENABLED=false             │
│                                                             │
│  Nodo WORKER GPU (Offline — standalone):                    │
│  └── jarvis_worker:8000  QDRANT_HOST=qdrant_local           │
└─────────────────────────────────────────────────────────────┘
```

### 🔐 Gestione Esclusiva del Bot Telegram

Il bot Telegram è centralizzato sul nodo **Master (VPS)** per garantire disponibilità 24/7 anche quando il laptop è spento.

- **Master (VPS):** `TELEGRAM_ENABLED=true` — gestisce Bot ufficiale e tutti gli Userbot.
- **Worker (Laptop):** `TELEGRAM_ENABLED=false` — mai abilitare qui; causa conflitti di sessione.
- **Migrazione Userbot:** per spostare sessioni esistenti: copiare `data/jarvis_mem0/userbots/` dal laptop alla VPS.

### Flusso Inferenza e Failover

```
Client (Cherry Studio / Jan / Continue / Cursor / Telegram)
  │
  ▼
Master jarvis:8000
  ├── [EXTERNAL_GPU_URL valorizzato?]
  │     ├── SÌ: ping Worker (timeout 1.5s)
  │     │       ├── Worker ONLINE  → offload al Worker GPU (HTTP POST)
  │     │       └── Worker OFFLINE → fallback CPU locale (Gemma 4 26B)
  │     └── NO: inferenza locale CPU
  │
  ├── RAG: chunk codice da Qdrant (AST-aware, Tree-sitter)
  ├── Memoria: ricordi da Mem0 (Qdrant)
  ├── Web: SearXNG + Crawl4AI (prefisso /web)
  └── super-prompt XML → risposta LLM → loop tool-calling
```

---

## 🧩 Struttura del Codice Sorgente

```text
jarvis/
├── Dockerfile                    # nvidia/cuda + Python 3.11 + llama-cpp-python (CUBLAS) + Granian
├── requirements.txt              # Dipendenze Python
├── config.py                     # ⚙️ Configurazione centralizzata — UNICA fonte di verità per costanti
├── state.py                      # Stato mutabile globale (singleton, popolato nel lifespan)
├── llm_engine.py                 # LlamaEngine (GGUF in-process), PriorityLock, offloading GPU
├── main.py                       # Entry point FastAPI, lifespan, endpoint HTTP (API Ollama-compat)
├── rag.py                        # Pipeline RAG: AST chunking, ingestion, ricerca, watchdog
├── memory.py                     # Mem0: inizializzazione e helper ricordi
├── memory_backup.py              # Export/import memoria episodica in JSON
├── reflection_agent.py           # Job notturno di self-reflection e consolidamento memoria
├── web_search.py                 # Web intelligence: SearXNG + Crawl4AI
├── prompt_builder.py             # LLM Gatekeeper + super-prompt omnisciente (tag XML)
├── agent_tools.py                # Loop agentico: TOOLS_SCHEMA + execute_tool_call
├── skills_manager.py             # Skill dinamiche da jarvis/skills/
├── cron_agent.py                 # Scheduler APScheduler (promemoria, task ricorrenti)
├── task_manager.py               # Gestione ToDo/task persistenti
├── telegram_bot.py               # Handler bot Telegram: comandi, dashboard inline, whitelist
├── telegram_userbot_manager.py   # Multi-userbot Telethon (MTProto) con autenticazione OTP
├── dashboard.py                  # Pannello web di controllo
└── infrastructure.py             # Registro infrastruttura
```

### Grafo delle Dipendenze

```
config.py / state.py ← (moduli foglia; state popolato nel lifespan di main.py)
llm_engine.py        ← main.py, telegram_bot.py
rag.py / memory.py   ← prompt_builder.py, main.py
web_search.py        ← prompt_builder.py
prompt_builder.py    ← telegram_bot.py, main.py
agent_tools.py       ← main.py, telegram_bot.py (skills_manager ← agent_tools)
dashboard / cron / *userbot* ← main.py
```

---

## ⚙️ Struttura Dati & Portabilità

```text
~/ai-ecosystem/
├── .env                         # 🔒 Segreti (gitignored — NON committare MAI)
├── docker-compose.yml           # Stack completo (reference)
├── docker-compose.vps.yml       # Stack Master VPS (no GPU)
├── docker-compose.worker.yml    # Stack Worker GPU locale
├── start_master.sh              # Avvio nodo Master
├── start_worker.sh              # Avvio nodo Worker
├── sync_to_master.sh            # Sync dati Worker→Master via rsync
├── deploy_vps.sh                # Deploy iniziale su VPS
├── docs/
│   ├── AGENTS.md                # Guida per agenti AI
│   └── plans/
│       └── master_worker_implementation.md
├── jarvis/
│   └── models/                  # File GGUF (Qwen3.5-4B, Gemma 4, Nomic Embed)
└── data/                        # 📂 STATO PERSISTENTE (gitignored)
    ├── qdrant/                  # Collezioni vettoriali
    ├── jarvis_mem0/             # Mem0 SQLite, cache HF, sessioni Userbot Telegram
    ├── documents/               # Progetti montati per RAG
    └── searxng/                 # Config SearXNG
```

> 💡 `jarvis/` è montato come volume (`./jarvis:/app`): le modifiche al codice sono immediatamente visibili nel container senza ribuilddare.

---

## 🤖 Modelli LLM

Jarvis usa **esclusivamente `llama-cpp-python`** con file GGUF. Non è presente né richiesto alcun processo Ollama.

### Worker Locale (RTX 3050 Ti — 4GB VRAM)

| Modello | Stato | VRAM | Note |
|---|---|---|---|
| `Qwen3.5-4B-UD-Q4_K_XL.gguf` | ✅ **IN USO** | ~2.5GB | Temporaneo — ottimo per coding Go/TS/React |
| `gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf` | ⏳ In attesa fix | ~2.5GB | Target — bloccato da bug llama-cpp-python ≤0.3.30 |
| `nomic-embed-text-v1.5.gguf` | ✅ IN USO | CPU | Embedding 768d |

### Master VPS (CPU-only — 24GB RAM)

| Modello | Stato | RAM | Note |
|---|---|---|---|
| `gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf` | ⏳ Da scaricare | ~14.2GB | MoE: attiva ~4B param, ~8-12 t/s |

---

## ✨ Funzionalità Principali

### 1. Core IA & Inferenza (`jarvis/llm_engine.py`)

- **LlamaEngine** (singleton): carica i pesi GGUF all'avvio nel `lifespan` FastAPI. Mantiene il modello caldo in VRAM.
- **PriorityLock**: serializza le chiamate GPU. Chat utente (priorità `0`) > batch embedding RAG (priorità `10`).
- **Thinking Mode** (Gemma 4): se `LLM_THINKING_MODE=true`, inietta `<|think|>` nel system prompt per il reasoning esplicito.
- **Offloading GPU**: se `EXTERNAL_GPU_URL` è valorizzato, pinga il Worker (timeout 1.5s) e delega l'inferenza; failover automatico su CPU locale se offline.

### 2. Memoria Intelligente (`rag.py` + `memory.py` + `qdrant`)

- **Memoria Episodica (Mem0 + Qdrant):** estrae concetti chiave da ogni conversazione tramite spaCy, li vettorizza e li archivia. Recupera i ricordi pertinenti per ogni query.
- **RAG AST-Aware (Tree-sitter):** chunking semantico del codice (funzioni, classi, type declarations) per Go, Python, TypeScript/JavaScript, C, C++, Java, Rust, SQL, YAML.
- **Watchdog filesystem:** re-embedding automatico al salvataggio dei file sorgente.
- **Semantic Cache:** cache delle risposte per query simili (soglia cosine configurabile).
- **Collezioni Qdrant versionate:** `collateral_docs_*_vX`, `collateral_memories_vX`, `semantic_cache_vX`.

### 3. Web Intelligence (`web_search.py`)

Attivabile con il prefisso `/web` nel messaggio:
- **SearXNG:** metasearch anonimizzato, top 3 risultati.
- **Crawl4AI:** scraping parallelo dei risultati, output `fit_markdown` iniettato nel prompt.

### 4. Bot Telegram & Multi-Userbot

- **Bot Ufficiale:** whitelist utenti, dashboard inline con bottoni, formattazione Markdown, ToDo list.
- **Userbot (Telethon):** ogni utente può attivare il proprio clone via OTP dal bot, permettendo all'LLM di interagire nei gruppi privati.
- **Scheduler APScheduler:** promemoria singoli e ricorrenti con timer relativi (`NOTIFY_IN`).

### 5. Loop Agentico (Tool Calling)

Tool disponibili:
- `write_file`, `read_file`, `delete_file`, `replace_in_file`: manipolazione file nel progetto.
- `run_shell_command`: esecuzione bash nel container (con conferma Telegram per operazioni distruttive).
- `skill_*`: tool dinamici da `jarvis/skills/`.

### Endpoint Esposti

| Endpoint | Metodo | Funzione |
|---|---|---|
| `/api/chat` | POST | Chat Ollama-nativa + memoria + RAG + tool-calling |
| `/api/generate` | POST | Generate + cache semantica |
| `/api/embed` / `/api/embeddings` | POST | Embeddings (Nomic v1.5, 768d) |
| `/api/tags`, `/api/ps`, `/api/show`, `/api/version` | GET/POST | Stub compatibilità Ollama |
| `/api/project-tree` | GET | Albero del progetto indicizzato |
| `/api/reset-all` | GET/POST | Reset RAG + cache + re-ingestion |
| `/api/webhook/git` | POST | Git webhook → pull → re-ingestion |
| `/`, `/dashboard` | GET | Pannello di controllo web |

> ⚠️ L'API è nel **formato Ollama** (non OpenAI `/v1/*`). Nei client IDE usare il provider Ollama puntando a `http://localhost:8000`.

---

## 🚀 Avvio Rapido

### Worker Locale (Sviluppo — Modalità Offline)

```bash
cd ~/ai-ecosystem

# 1. Avviare Qdrant locale
docker run -d --name qdrant_local \
  --network ai_network \
  -p 6333:6333 \
  -v "$(pwd)/data/qdrant:/qdrant/storage" \
  qdrant/qdrant:latest

# 2. Avviare Jarvis Worker
./start_worker.sh

# 3. Verificare che sia online
curl http://localhost:8000/
# → risposta HTML della dashboard
```

### Primo Avvio (Build Immagine)

```bash
docker compose -f docker-compose.worker.yml build --no-cache
docker compose -f docker-compose.worker.yml up -d
```

> ⚠️ Il build è lento (~5-10 minuti) perché compila `llama-cpp-python` da sorgente con supporto CUDA.

### Test Rapido

```bash
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"model":"local","messages":[{"role":"user","content":"Ciao, presentati"}],"stream":false}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['message']['content'][:400])"
```

---

## 🔧 Configurazione

Tutti i parametri sono controllati dal file `.env` nella root del progetto.

### Variabili Essenziali

```env
# === TELEGRAM (solo Master/VPS) ===
TELEGRAM_ENABLED=true          # false sul Worker!
TELEGRAM_TOKEN=...             # Token bot @BotFather
TELEGRAM_API_ID=...            # my.telegram.org
TELEGRAM_API_HASH=...

# === ARCHITETTURA ===
QDRANT_HOST=qdrant             # Nome container Master. Offline: qdrant_local
EXTERNAL_GPU_URL=              # Master: http://100.64.0.2:8000 | Worker: vuoto

# === MODELLO LLM ===
LLAMA_MODEL_PATH=./models/Qwen3.5-4B-UD-Q4_K_XL.gguf
N_GPU_LAYERS=20                # 0 sulla VPS (CPU-only)
LLM_NUM_CTX=16384              # 65536 sulla VPS
LLM_TEMPERATURE=0.7            # 1.0 per Gemma 4
LLM_REPEAT_PENALTY=1.1         # 1.0 per Gemma 4
LLM_TOP_P=0.9                  # 0.95 per Gemma 4
LLM_THINKING_MODE=false        # true per Gemma 4

# === RAG ===
MAIN_PROJECT_PATH=/percorso/al/tuo/progetto
EXTERNAL_PROJECTS=             # Percorsi aggiuntivi (vuoto sulla VPS)
VECTOR_DB_VERSION=v3
EMBEDDING_DIMS=768
```

---

## 🛠️ Comandi di Manutenzione

```bash
# Log in tempo reale
docker logs jarvis_worker --tail=50 -f

# Reset completo RAG (cancella vettori e riesegue ingestion)
curl -X POST http://localhost:8000/api/reset-all

# Albero del progetto indicizzato
curl http://localhost:8000/api/project-tree | python3 -c "import sys,json; print(json.load(sys.stdin).get('tree','')[:500])"

# Lista collezioni Qdrant
curl http://localhost:6333/collections | python3 -c "import sys,json; [print(c['name']) for c in json.load(sys.stdin)['result']['collections']]"

# Backup completo
tar -cvzf backup_ai_$(date +%Y%m%d).tar.gz ./data .env

# Arresto completo
docker compose -f docker-compose.worker.yml down --remove-orphans
docker stop qdrant_local && docker rm qdrant_local

# Sync dati Worker → VPS Master
./sync_to_master.sh
```

---

## 📝 Changelog

### v9.0.0 (2026-06-19) — Architettura Master/Worker
- **Architettura:** Migrazione da topologia single-node a **Master/Worker** con VPN Tailscale.
- **Networking:** Rimosso completamente Ngrok. Connettività tramite Tailscale WireGuard.
- **Telegram:** Centralizzato sul Master (VPS) — `TELEGRAM_ENABLED=false` obbligatorio sul Worker.
- **Modelli:** Aggiunto supporto `LLM_THINKING_MODE` (Gemma 4), parametri LLM configurabili via `.env`.
- **llm_engine.py:** `chat_format=None` (Gemma 4 GGUF Jinja2), `n_gpu_layers` e `n_ctx` da `.env`.
- **docker-compose.worker.yml:** Rimosso `QDRANT_HOST` hardcoded; aggiunti volumi Mem0+documents.
- **Dockerfile:** Build `llama-cpp-python` dalla master GitHub per supporto Gemma 4 (fix PR #22133).
- **Modello attuale Worker:** `Qwen3.5-4B-UD-Q4_K_XL.gguf` (temporaneo — Gemma 4 in attesa fix).

### v8.6.9
- **RAG:** Numerazione righe nei chunk AST (`RIGHE X-Y:`), full-file bypass, skeleton auto-generation.
- **RAG:** Project-specific guidelines (`.ai-rules.md`, `.cursorrules`, `AGENT.md`).
- **Logging:** Filtri middleware per silenziare log rumorosi.
- **DB:** Migrazione automatica Qdrant con `EMBEDDING_DIMS` dinamico e versionamento.
- **Prompt:** Diffing formatter `SEARCH/REPLACE` forzato nel System Prompt.
- **Scheduler:** `DateTrigger` APScheduler, tag `<NOTIFY_IN>` per timer relativi.

### v8.6.8
- **Sicurezza:** Fix file handle leak e race condition TOCTOU in `rag.py`.
- **Performance:** I/O e hashing fuori dal lock globale, debouncing watchdog, batch embedding.
- **RAG:** Esteso Tree-sitter a C, C++, Java, Rust, SQL, YAML; chunk "Preambolo".
- **Telegram:** Buffer sessione (10 msg, TTL 10min), dashboard InlineKeyboard, file manager `/ls`.
- **API:** Conformità OpenAI `/v1/chat/completions` con UUID, `finish_reason`, `usage`.

---

🌐 **Collateral Studios** — *Infrastruttura di Intelligenza Artificiale Locale Riservata.*
