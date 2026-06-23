---

# NeuroNet — AI Cognitive Proxy

Questo repository ospita l'architettura di un ecosistema di Intelligenza Artificiale locale di livello Enterprise. Il cuore del sistema è **Jarvis**, un **Cognitive Proxy** asincrono che unisce memoria episodica a lungo termine, RAG (Retrieval-Augmented Generation) AST-aware sul codice sorgente locale, un bot Telegram integrato, un loop agentico autonomo con tool-calling e capacità di esplorazione web automatizzata in tempo reale.

L'inferenza gira **in-process** tramite `llama-cpp-python` su modelli GGUF locali (**nessun container Ollama**): il proxy carica i pesi direttamente in VRAM all'avvio e li mantiene caldi per tutta la durata del processo. L'intera infrastruttura garantisce la totale privacy dei dati (Zero-Data-Leak), latenze minime e portabilità assoluta.

L'architettura supporta una topologia **Master/Worker Edge-Cloud** connessa tramite **VPN Mesh Tailscale (WireGuard)**: un nodo *Master* sulla VPS ospita memoria, RAG, database vettoriale e il Bot Telegram (sempre disponibile), mentre un nodo *Worker* locale (dotato di GPU) riceve in offloading le inferenze pesanti.

> 📖 **Per agenti AI:** leggere `docs/AGENTS.md` per il contesto operativo completo.  
> 📋 **Piano deployment:** `docs/plans/master_worker_implementation.md`

---

## 🚦 Stato del Sistema (al 2026-06-23)

| Componente | Stato | Note |
|---|---|---|
| **Worker GPU (Locale)** | ✅ **ONLINE** | Qwen3.5-4B-UD Q4_K_XL, n_gpu_layers=15, flash_attn=true, CUDA 13.0 overlay |
| **VPS Master** | ⏳ Da deployare | Deployment in preparazione — piano completo pronto |
| **Container CUDA** | ✅ **FUNZIONANTE** | Overlay CUDA 13.0 su base 12.2 per compatibilità driver NVIDIA 580.159.03 |
| **GPU** | ✅ **Inferenza OK** | Chat: 47% VRAM (1924MiB), Embed: 57% VRAM (2320MiB), 86°C peak |

---

## ⚠️ Compatibilità CUDA — Nota Critica

Il container usa `nvidia/cuda:12.2.2-devel-ubuntu22.04` come base con overlay dei pacchetti **CUDA 13.0** da repository NVIDIA:

```dockerfile
RUN apt-get install -y cuda-compiler-13-0 cuda-cudart-dev-13-0 libcublas-dev-13-0
```

**Perché?** Il driver host (NVIDIA 580.159.03) supporta CUDA 13.0. Il runtime CUDA 12.2 del container base è incompatibile e causa crash GPU `ggml_cuda_can_mul_mat`. L'overlay CUDA 13.0 risolve il problema permettendo a `llama-cpp-python` di linkare correttamente le librerie CUDA 13.0 durante la compilazione con `-DGGML_CUDA=on`.

**Se il container non si avvia o crasha all'inferenza**, verificare:
```bash
nvidia-smi           # CUDA Version deve corrispondere ai pacchetti overlay
docker logs jarvis_worker | grep -i cuda
```

---

## 🏗️ Architettura del Sistema

### Topologia Master/Worker

```
┌─────────────────────────────────────────────────────────────┐
│  VPS Debian (OVH)                                            │
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
│  └── jarvis_worker:8000  QDRANT_HOST=localhost              │
└─────────────────────────────────────────────────────────────┘
```

### 🔐 Gestione Esclusiva del Bot Telegram

Il bot Telegram è centralizzato sul nodo **Master (VPS)** per garantire disponibilità 24/7 anche quando il laptop è spento.

- **Master (VPS):** `TELEGRAM_ENABLED=true` — gestisce Bot ufficiale e tutti gli Userbot.
- **Worker (Laptop):** `TELEGRAM_ENABLED=false` — mai abilitare qui; causa conflitti di sessione.

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
├── Dockerfile                    # nvidia/cuda:12.2.2 + CUDA 13.0 overlay + llama-cpp-python (GGML_CUDA=on)
├── requirements.txt              # Dipendenze Python
├── config.py                     # ⚙️ Configurazione centralizzata
├── state.py                      # Stato mutabile globale (singleton, popolato nel lifespan)
├── llm_engine.py                 # LlamaEngine (GGUF in-process), PriorityLock, offloading GPU
├── main.py                       # Entry point FastAPI/Granian, endpoint HTTP (API Ollama + OpenAI /v1/)
├── rag.py                        # Pipeline RAG: AST chunking (Tree-sitter), ingestion, ricerca, watchdog
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

---

## ⚙️ Struttura Dati & Portabilità

```text
~/ai-ecosystem/
├── .env                         # 🔒 Segreti (gitignored — NON committare MAI)
├── .dockerignore                # Esclude models/ e data/ dal build context
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
│   ├── Dockerfile               # Build immagine CUDA 13.0 + llama-cpp-python
│   ├── models/                  # File GGUF (gitignored — scaricare manualmente)
│   └── ... sorgenti Python
└── data/                        # 📂 STATO PERSISTENTE (gitignored)
    ├── qdrant/                  # Collezioni vettoriali
    ├── jarvis_mem0/             # Mem0 SQLite, cache HF, sessioni Userbot Telegram
    ├── documents/               # Progetti montati per RAG
    └── searxng/                 # Config SearXNG
```

> 💡 `jarvis/` è montato come volume (`./jarvis:/app`): le modifiche al codice sono immediatamente visibili nel container senza rebuild.

---

## 🤖 Modelli LLM

Jarvis usa **esclusivamente `llama-cpp-python`** con file GGUF. Non è presente né richiesto alcun processo Ollama.

### Worker Locale (RTX 3050 Ti — 4GB VRAM)

| Modello | Stato | VRAM | Note |
|---|---|---|---|
| `Qwen3.5-4B-UD-Q4_K_XL.gguf` | ✅ **IN USO** | 1924MiB (47%) | n_gpu_layers=15, flash_attn=true |
| `Qwen3-Embedding-0.6B-Q8_0.gguf` | ✅ IN USO | +396MiB (57% tot) | Embedding locale |
| `nomic-embed-text-v1.5.gguf` | ⏳ Sostituito | CPU | Sostituito da Qwen3-Embedding |

### Master VPS (CPU-only — 24GB RAM)

| Modello | Stato | RAM | Note |
|---|---|---|---|
| `gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf` | ⏳ Da scaricare | ~14.2GB | MoE: attiva ~4B param, ~8-12 t/s |

---

## ✨ Funzionalità Principali

### 1. Core IA & Inferenza (`jarvis/llm_engine.py`)

- **LlamaEngine** (singleton): carica i pesi GGUF all'avvio nel `lifespan` FastAPI. Mantiene il modello caldo in VRAM.
- **PriorityLock**: serializza le chiamate GPU. Chat utente (priorità `0`) > batch embedding RAG (priorità `10`).
- **Offloading GPU**: se `EXTERNAL_GPU_URL` è valorizzato, pinga il Worker (timeout 1.5s) e delega l'inferenza; failover automatico su CPU locale se offline.

### 2. Memoria Intelligente (`rag.py` + `memory.py` + `qdrant`)

- **Memoria Episodica (Mem0 + Qdrant):** estrae concetti chiave da ogni conversazione tramite spaCy, li vettorizza e li archivia. Recupera i ricordi pertinenti per ogni query.
- **RAG AST-Aware (Tree-sitter):** chunking semantico del codice (funzioni, classi, type declarations) per Go, Python, TypeScript/JavaScript, C, C++, Java, Rust, SQL, YAML.
- **Watchdog filesystem:** re-embedding automatico al salvataggio dei file sorgente.
- **Semantic Cache:** cache delle risposte per query simili (soglia cosine configurabile).

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
| `/api/chat` | POST | Chat con memoria + RAG + tool-calling |
| `/api/generate` | POST | Generate + cache semantica |
| `/v1/chat/completions` | POST | API compatibile OpenAI |
| `/api/embed` / `/api/embeddings` | POST | Embeddings |
| `/api/tags`, `/api/ps`, `/api/show`, `/api/version` | GET/POST | Stub compatibilità Ollama |
| `/v1/models` | GET | Lista modelli (API OpenAI) |
| `/api/project-tree` | GET | Albero del progetto indicizzato |
| `/api/webhook/git` | POST | Git webhook → pull → re-ingestion |
| `/docs` | GET | Swagger UI |

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

> ⚠️ Il build è lento (~5-10 minuti) perché compila `llama-cpp-python` da sorgente con supporto CUDA.

### Verifica GPU

```bash
# Controllare che la GPU sia attiva
docker logs jarvis_worker | grep -i "vram\|n_gpu_layers"
# Output atteso: 🎯 [VRAM] Dopo caricamento ... MiB / 4096MiB
# Output atteso: ⚙️ n_gpu_layers=15
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

Tutti i parametri sono controllati dal file `.env` nella root del progetto. Copiare da `.env.example` se presente.

### Variabili Essenziali

```env
# === ARCHITETTURA ===
QDRANT_HOST=localhost            # Nome container. Offline: localhost
EXTERNAL_GPU_URL=                # Master: http://100.64.0.2:8000 | Worker: vuoto

# === MODELLO LLM ===
LLAMA_MODEL_PATH=./models/Qwen3.5-4B-UD-Q4_K_XL.gguf
N_GPU_LAYERS=15                  # 0 sulla VPS (CPU-only). RTX 3050 Ti 4GB max 15
LLM_FLASH_ATTN=true              # Flash attention per ridurre VRAM

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

# Reset RAG
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

### v9.1.0 (2026-06-23) — CUDA 13.0 Overlay + GPU Inference stabile
- **CUDA 13.0 overlay:** Aggiunti `cuda-compiler-13-0`, `cuda-cudart-dev-13-0`, `libcublas-dev-13-0` su base CUDA 12.2 per compatibilità driver NVIDIA 580.159.03.
- **llama-cpp-python:** Build da GitHub main con `GGML_CUDA=on`, `CMAKE_CUDA_ARCHITECTURES=86`.
- **GPU:** Inferenza funzionante con `n_gpu_layers=15`, `flash_attn=true`.
- **.dockerignore:** Esclusi modelli (8.7GB) dal build context — immagine finale 19.6GB.
- **Modello Worker:** `Qwen3.5-4B-UD-Q4_K_XL.gguf` (UD = Unified Debug), embedding con `Qwen3-Embedding-0.6B-Q8_0`.

### v9.0.0 (2026-06-19) — Architettura Master/Worker
- **Architettura:** Migrazione da single-node a **Master/Worker** con VPN Tailscale.
- **Networking:** Rimosso Ngrok. Connettività tramite Tailscale WireGuard.
- **Telegram:** Centralizzato sul Master (VPS) — `TELEGRAM_ENABLED=false` sul Worker.
- **llm_engine.py:** `chat_format=None` (Gemma 4 GGUF Jinja2), `n_gpu_layers` e `n_ctx` da `.env`.
- **Dockerfile:** Build `llama-cpp-python` dalla master GitHub per supporto Gemma 4 (fix PR #22133).

---

🌐 **NeuroNet** — *Infrastruttura di Intelligenza Artificiale Locale Riservata.*
