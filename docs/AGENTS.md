# 🤖 AGENTS.md — Guida Operativa per Agenti AI

> **Questo file è destinato esclusivamente agli agenti AI che lavorano su questo progetto.**  
> Contiene tutto il contesto necessario per operare autonomamente senza errori.  
> **Data ultimo aggiornamento:** 2026-06-28 (OpenAI API completa + codebase cleanup)

---

## 📋 Indice Rapido

1. [Identità del Progetto](#1-identità-del-progetto)
2. [Architettura del Sistema](#2-architettura-del-sistema)
3. [Struttura File e Responsabilità](#3-struttura-file-e-responsabilità)
4. [Configurazione e Variabili d'Ambiente](#4-configurazione-e-variabili-dambiente)
5. [Modelli LLM in Uso](#5-modelli-llm-in-uso)
6. [Topologia di Rete Master/Worker](#6-topologia-di-rete-masterworker)
7. [Stato Attuale dell'Implementazione](#7-stato-attuale-dellimplementazione)
8. [Regole Operative per gli Agenti](#8-regole-operative-per-gli-agenti)
9. [Pattern di Codice e Convenzioni](#9-pattern-di-codice-e-convenzioni)
10. [Comandi Utili](#10-comandi-utili)
11. [Bug Noti e Workaround](#11-bug-noti-e-workaround)

---

## 1. Identità del Progetto

**Nome:** Ecosistema AI Omnisciente — Chameleon Cognitive Stack  
**Proprietario:** Alfio Saitta / Collateral Studios  
**Scopo:** Sistema AI autonomo, privato e sempre disponibile per assistenza allo sviluppo software (Go, TypeScript, React) e automazione personale via Telegram.

### ⚠️ Compatibilità CUDA — Overlay 13.0

Il container usa `nvidia/cuda:12.2.2-devel-ubuntu22.04` come base con overlay dei pacchetti **CUDA 13.0** dal repository NVIDIA. Motivazione: il driver host (NVIDIA 580.159.03) supporta CUDA 13.0. Il runtime 12.2 del container base è incompatibile e causa crash GPU. I pacchetti installati:

```
cuda-keyring (repo NVIDIA)
cuda-compiler-13-0       # nvcc + toolchain CUDA 13.0
cuda-cudart-dev-13-0     # CUDA Runtime 13.0
libcublas-dev-13-0       # cuBLAS 13.0 (840MB, necessario per CUDA::cublas in cmake)
```

llama-cpp-python (v0.3.31) è buildato da GitHub main con `-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=86`. Model file GGUF esclusi dal build context via `.dockerignore`. Container rebuild: `docker compose -f docker-compose.worker.yml build jarvis_worker`.

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

```
┌─────────────────────────────────────────────────────────────┐
│  VPS Debian (OVH) — 51.38.135.179                           │
│  8 vCore, 24GB RAM, NO GPU                                  │
│                                                             │
│  Nodo MASTER:                                               │
│  ├── jarvis:8000    (FastAPI + LlamaEngine CPU-only)        │
│  ├── qdrant:6333    (database vettoriale — memoria unica)   │
│  ├── searxng:8081   (metasearch anonimo)                    │
│  ├── crawl4ai:11235 (scraper headless)                      │
│  └── Bot Telegram + Userbots (TELEGRAM_ENABLED=true)        │
└──────────────────────┬──────────────────────────────────────┘
                       │ Tailscale VPN (WireGuard)
                       │ EXTERNAL_GPU_URL=http://100.64.0.2:8000
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  Laptop LENOVO IdeaPad Gaming 3 (OpenSUSE Tumbleweed)       │
│  i5-11300H, 16GB RAM, RTX 3050 Ti (4GB VRAM)               │
│                                                             │
│  Nodo WORKER (Online):                                      │
│  └── jarvis_worker:8000 (FastAPI + LlamaEngine GPU)         │
│      QDRANT_HOST=100.64.0.1 (punta al Master via VPN)      │
│      TELEGRAM_ENABLED=false                                 │
│                                                             │
│  Nodo WORKER (Offline — modalità standalone):               │
│  └── jarvis_worker:8000 (FastAPI + LlamaEngine GPU)         │
│      QDRANT_HOST=qdrant_local (Qdrant locale Docker)        │
│      TELEGRAM_ENABLED=false                                 │
└─────────────────────────────────────────────────────────────┘
```

### Flusso Inferenza

```
Client (Cherry Studio / Jan / Continue / Cursor)
  │
  ▼
Master jarvis:8000
  ├── EXTERNAL_GPU_URL valorizzato?
  │     ├── SÌ → ping Worker (timeout 1.5s)
  │     │         ├── Worker online → offload inferenza al Worker (HTTP POST)
  │     │         └── Worker offline → fallback CPU locale (Gemma 4 26B su CPU)
  │     └── NO → inferenza locale CPU
  │
  ├── RAG: recupera chunk codice da Qdrant (collezioni vettoriali)
  ├── Memoria: recupera ricordi da Mem0 (via Qdrant)
  ├── Web: SearXNG + Crawl4AI (solo se messaggio inizia con /web)
  └── Costruisce super-prompt → risposta LLM → tool-calling loop
```

### Stack Docker

| Servizio | Porta | File Compose | Nodo |
|---|---|---|---|
| `jarvis` | 8000 | `docker-compose.vps.yml` | Master |
| `qdrant` | 6333 | `docker-compose.vps.yml` | Master |
| `searxng` | 8081 | `docker-compose.vps.yml` | Master |
| `crawl4ai` | 11235 | `docker-compose.vps.yml` | Master |
| `jarvis_worker` | 8000 | `docker-compose.worker.yml` | Worker |
| `qdrant_local` | 6333 | manuale (Docker run) | Worker Offline |

---

## 3. Struttura File e Responsabilità

### Root del Progetto

```
/home/alfio/Projects/ai-ecosystem/
├── .env                         # Segreti e configurazione (gitignored, NON committare mai)
├── docker-compose.vps.yml       # Stack Master VPS (NO sezione deploy GPU)
├── docker-compose.worker.yml    # Stack Worker GPU locale
├── start_master.sh              # Avvia il Master sulla VPS
├── start_worker.sh              # Avvia il Worker locale
├── sync_to_master.sh            # Sincronizza dati locale→VPS via rsync
├── deploy_vps.sh                # Script di deploy iniziale su VPS
├── docs/
│   ├── AGENTS.md                # ← QUESTO FILE
│   └── plans/
│       └── master_worker_implementation.md  # Piano dettagliato deployment
├── jarvis/                      # Codice sorgente Jarvis
│   ├── models/                  # File GGUF modelli LLM
│   └── skills/                  # Skill dinamiche caricabili a runtime
└── data/                        # Stato persistente (gitignored)
    ├── qdrant/                  # Dati Qdrant
    ├── jarvis_mem0/             # Cache Mem0, sessioni Userbot Telegram
    ├── documents/               # Repository indicizzati dal RAG
    └── searxng/                 # Config SearXNG
```

### Moduli Jarvis (`jarvis/*.py`)

| File | Responsabilità | Dipendenze Chiave |
|---|---|---|
| `config.py` | **Unica fonte di verità per tutte le costanti.** Legge `.env` con `os.getenv()`. NON modificare valori hardcoded qui — usare `.env`. | `os`, `logging` |
| `state.py` | Stato globale mutabile condiviso tra moduli (singleton). Popolato nel `lifespan` di main.py. | — |
| `llm_engine.py` | Carica i modelli GGUF, gestisce inferenza, thinking mode, offloading al Worker. | `llama_cpp`, `httpx`, `config`, `state` |
| `main.py` | Entry point FastAPI, lifespan, tutti gli endpoint HTTP, integrazione componenti. | Tutti i moduli |
| `rag.py` | Pipeline RAG: AST chunking, embedding, Qdrant, watchdog filesystem. | `config`, `state`, `llm_engine` |
| `memory.py` | Mem0: inizializzazione, salvataggio e recupero ricordi. | `config`, `state` |
| `prompt_builder.py` | Costruisce il super-prompt omnisciente con tag XML. LLM Gatekeeper. | `rag`, `memory`, `web_search` |
| `agent_tools.py` | TOOLS_SCHEMA per tool-calling, esecuzione tool (file/shell/skills). | `config`, `state` |
| `telegram_bot.py` | Handler bot Telegram ufficiale (comandi, dashboard inline, whitelist). | `llm_engine`, `prompt_builder` |
| `telegram_userbot_manager.py` | Multi-userbot Telethon (MTProto), autenticazione OTP. | `config`, `state` |
| `web_search.py` | SearXNG metasearch + Crawl4AI scraping parallelo. | `config`, `state` |
| `cron_agent.py` | APScheduler: promemoria, task ricorrenti, timer relativi. | `config`, `state` |
| `dashboard.py` | Pannello web di controllo Jarvis. | `state` |
| `skills_manager.py` | Carica skill dinamiche da `jarvis/skills/` a runtime. | — |
| `reflection_agent.py` | Job notturno di consolidamento memoria. | `memory`, `llm_engine` |

---

## 4. Configurazione e Variabili d'Ambiente

### ⚠️ Regola Fondamentale
Il file `.env` è la **singola fonte di configurazione**. Non hardcodare mai valori nei file Python o YAML dei compose.

### Variabili Critiche per l'Architettura

| Variabile | Nodo Master | Nodo Worker (Online) | Nodo Worker (Offline) |
|---|---|---|---|
| `TELEGRAM_ENABLED` | `true` | `false` | `false` |
| `QDRANT_HOST` | `qdrant` (nome container Docker) | `100.64.0.1` (IP Tailscale VPS) | `qdrant_local` (container locale) |
| `EXTERNAL_GPU_URL` | `http://100.64.0.2:8000` (IP Tailscale Worker) | *(vuoto)* | *(vuoto)* |
| `LLAMA_MODEL_PATH` | `./models/gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf` | `./models/gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf` | idem |
| `N_GPU_LAYERS` | `0` (CPU) | `15` (massimo stabile) | `15` |
| `LLM_NUM_CTX` | `65536` | `12288` | `12288` |
| `LLM_NUM_PREDICT` | `4096` | `2048` | `2048` |
| `LLM_TEMPERATURE` | `1.0` (Gemma 4) | `0.7` (Qwen3.5) | `0.7` |
| `LLM_THINKING_MODE` | `true` | `false` | `false` |

### Variabili per il Funzionamento del Modello

```env
# Percorso modello chat (relativo a /app nel container = jarvis/)
LLAMA_MODEL_PATH=./models/NomeModello.gguf

# Layer GPU: 0=tutto CPU, N=numero layer caricati in GPU
# Worker: 15 stabile (Gemma 4 E2B QAT). Oltre 15 causa segfault (35 blocchi, Q4_0).
# Su Qwen3.5 (32 layer) si può arrivare a 19. 22+ causa CUDA OOM su RTX 3050 Ti 4GB.
# Master: 0 (CPU-only).
N_GPU_LAYERS=15

# Finestra contesto (token): 12288 Worker GPU (RTX 3050 Ti 4GB), 32768 Master CPU
LLM_NUM_CTX=12288

# Token max output
LLM_NUM_PREDICT=2048

# Temperature: 1.0 per Gemma 4, 0.7 per Qwen3.5
LLM_TEMPERATURE=0.7

# Penalità ripetizioni: 1.0 per Gemma 4, 1.1 per Qwen3.5
LLM_REPEAT_PENALTY=1.1

# Top-p sampling: 0.95 per Gemma 4, 0.9 per Qwen3.5
LLM_TOP_P=0.9

# Thinking mode (solo Gemma 4): inietta <|think|> nel system prompt
LLM_THINKING_MODE=false

# Etichetta testuale nel payload HTTP di offloading (NON avvia Ollama)
OLLAMA_MODEL=nome-identificativo-worker
```

### Parametri Interni LLM (hardcoded in llm_engine.py)

| Parametro | Chat Model (Gemma 4 / Qwen3.5) | Embedding (Qwen3-Embedding) | Note |
|---|---|---|---|
| `n_batch` | **512** | 256 | 512 è il sweet spot per TTFT |
| `n_threads` | **6** | 6 | i5-11300H: 6 per LLM + 2 per I/O |
| `flash_attn` | True | — | Dimezza VRAM KV cache |
| `use_mmap` | True | True | Lazy page mapping |
| `embed n_gpu_layers` | — | **2** | Prime 2/28 layer su GPU |

### Variabili RAG

```env
# Progetto principale (percorso host, montato in /app/documents/)
MAIN_PROJECT_PATH=/percorso/al/tuo/progetto

# Progetti aggiuntivi (separati da virgola, percorsi host)
# ATTENZIONE: questi path sono validi SOLO sul laptop — svuotare sulla VPS
EXTERNAL_PROJECTS=/home/alfio/Projects/ProgettoA,/home/alfio/Projects/ProgettoB

# Versione DB vettoriale (incrementare per forzare migrazione)
VECTOR_DB_VERSION=v3

# Dimensioni embedding (768 per Qwen3-Embedding MRL)
EMBEDDING_DIMS=768

# Reranker: Qwen3-Reranker-0.6B su CPU (caricato in fp16, ~600MB RAM)
# Fallback automatico a FlashRank (ms-marco-MiniLM-L-6-v2) se transformers/torch mancanti
Qwen3_RERANKER_MODEL=/root/models/Qwen3-Reranker-0.6B
RERANKER_DEVICE=cpu

# FlashRank fallback model
FLASHRANK_MODEL=ms-marco-MiniLM-L-6-v2
```

---

## 5. Modelli LLM in Uso

### Worker Locale (RTX 3050 Ti — 4GB VRAM)

| Modello | File | Stato | VRAM | Note |
|---|---|---|---|---|---|
| **Gemma 4 E2B (qat-UD)** (attivo) | `gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf` | ✅ IN USO | 1036MiB (15/35 layer) | Modello primario — 2.1B param, QAT |
| **Qwen3-Embedding-0.6B** | `Qwen3-Embedding-0.6B-Q8_0.gguf` | ✅ IN USO | ~400 MiB (2/28 layer) | 768d MRL, 2 layer su GPU |
| **Qwen3-Reranker-0.6B** | `models/Qwen3-Reranker-0.6B/` | ✅ IN USO | 0 VRAM (CPU fp16) | ~600 MB RAM, fallback FlashRank |
| **Qwen3.5-4B** (backup) | `Qwen3.5-4B-UD-Q4_K_XL.gguf` | ⏳ BACKUP | 1924MiB (15/32 layer) | Sostituito da Gemma 4 (86% VRAM in più) |
| Gemma 4 E2B (Q4_K_M) | `gemma-4-E2B-it-Q4_K_M.gguf` | ➖ SCONSIGLIATO | 1118MiB (15/35 layer) | +8% VRAM, -38% tok/s vs QAT |

### Master VPS (CPU-only — 24GB RAM)

| Modello | File | Stato | RAM | Note |
|---|---|---|---|---|---|
| **Gemma 4 26B A4B** (target) | `gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf` | ⏳ Da scaricare | ~14.2 GB | MoE: ~4B attivi, ~8-12 tok/s attesi |

### Parametri Ottimali per Modello

| Parametro | Qwen3.5 (Worker, backup) | Gemma 4 E2B (Worker, attivo) | Gemma 4 26B (Master VPS, futuro) |
|---|---|---|---|---|
| `LLM_TEMPERATURE` | 0.7 | 0.7 | 1.0 |
| `LLM_REPEAT_PENALTY` | 1.1 | 1.1 | 1.0 |
| `LLM_TOP_P` | 0.9 | 0.9 | 0.95 |
| `LLM_NUM_CTX` | 12288 | 12288 | 32768 |
| `LLM_BATCH_SIZE` | 512 | 512 | 512 |
| `LLM_UBATCH_SIZE` | 128 | 128 | 128 |
| `LLM_FLASH_ATTN` | true | true | true |
| `LLM_THINKING_MODE` | false | false | true |
| `N_GPU_LAYERS` | 15 | 15 | 0 |
| `chat_format` in llm_engine.py | `None` (Jinja2 embedded) | `None` (Jinja2 embedded) | `None` (Jinja2 embedded) |

### Benchmark Modelli — Performance Misurate (2026-06-23)

Test su RTX 3050 Ti 4GB, N_GPU_LAYERS=15, flash_attn=true, n_ctx=12288.  
Misurazioni dirette da `llama_cpp.create_chat_completion()` nel container, prompt singolo "Spiega in 5 righe cos'è una rete neurale."

| Modello | File | VRAM chat | Prompt tok | Completion tok | Wall time (s) | Tok/s | Note |
|---|---|---|---|---|---|---|---|
| **Gemma 4 E2B QAT** (attivo) | `qat-UD-Q4_K_XL` (2.5 GB) | 1036 MiB (25%) | 24 | 118 | 17.15 | **6.88** | Modello primario, 2.1B param, 35 blocchi |
| **Qwen3.5-4B** (backup) | `UD-Q4_K_XL` (2.8 GB) | 1924 MiB (47%) | 26 | ~119 | 21.34 | **~5.58** | +86% VRAM, -19% tok/s vs Gemma 4 |
| **Gemma 4 E2B Q4_K_M** | `Q4_K_M` (2.9 GB) | 1118 MiB (27%) | 24 | 86 | 20.05 | **4.29** | +82 MiB VRAM, -38% tok/s vs QAT |

Benchmark aggiuntivo (prompt "Differenze ML/DL/AI"):

| Modello | Prompt tok | Completion tok | Wall time (s) | Tok/s |
|---|---|---|---|---|
| Gemma 4 E2B QAT | 32 | 136 | 22.76 | **5.98** |
| Qwen3.5-4B | 32 | ~129 | 18.70 | **~6.90** |
| Gemma 4 E2B Q4_K_M | 32 | 125 | 24.09 | **5.19** |

**Conclusione:** Il Q4_K_M è **peggiore** del QAT su tutti i fronti: VRAM superiore (1118 vs 1036 MiB), tok/s inferiore (4.29 vs 6.88), e crasha ugualmente a N_GPU_LAYERS=18 (stesso segfault del QAT). Qwen3.5 è comparabile in velocità (~6.24 tok/s media vs ~6.43 del QAT) ma usa il 86% più VRAM. **Il QAT rimane la scelta ottimale** per RTX 3050 Ti 4GB: miglior rapporto qualità/VRAM/velocità.

### Cronologia Modifiche Recenti

| Data | Modifica | Impatto |
|---|---|---|---|
| 24/06 | **Dashboard log viewer + restart container/ingestion** | Docker logs con combo box, auto-refresh, restart container/ingestion |
| 24/06 | **tiktoken caching offline** | `o200k_base` pre-scaricato in build, lazy init con fallback chain, niente crash su DNS assente |
| 24/06 | **Docker API via Unix socket** | Sostituito httpx http+unix:// con http.client + AF_UNIX per compatibilità |
| 24/06 | Test B RAG: 100% hit (6/6), media 59s | Nessun crash durante sessione, stabilità migliorata |
| 24/06 | **PLAN.md v2: sezione Ottimizzazioni Baseline** | Aggiunta sezione 10 con ricerca web: KV q8_0, n_batch=2048, CUDA Graphs, Hybrid search Qdrant, Parent-Child chunking, Docker best practices. Tabella impatto cumulativo + Sprint 5 |
| 24/06 | **RAG 4.1: tree-sitter per estrazione dipendenze** | Riscritta `extract_dependencies()` con AST tree-sitter per Go, Python, JS/TS. 12/12 test PASS. Eliminati falsi positivi da stringhe/commenti. Cattura CommonJS `require()`. Tabella comparativa in RAG_IMPROVEMENT_PLAN.md |
| 24/06 | **RAG 4.2: dependency graph traversal** | Migliorata `search_documents()`: limit=20 per collezione (era 5 tot), parent-child reconstruction su risultati dipendenze, dedup via set `seen_filenames`, `asyncio.as_completed` per parallelismo reattivo |
| 24/06 | **RAG 4.3: File-level co-embedding** | `_mean_vector()`, `ensure_file_profile_collection()`, `search_file_profiles()`. Media aritmetica chunk embedding upsertata in collezione `file_profiles_v3`. Cleanup su delete file. Test PASS: vettore 768 dim, search funziona. |
| 23/06 | **Gemma 4 E2B QAT attivo** | VRAM 1036MiB (25%), -46% vs Qwen3.5 |
| 23/06 | **Benchmark completi (3 modelli)** | QAT: 6.88 tok/s, Qwen3.5: ~5.58, Q4_K_M: 4.29 |
| 23/06 | N_GPU_LAYERS=18 → crash confermato su QAT e Q4_K_M | Segfault oltre 15 layer |
| 23/06 | Dashboard GPU charts + counter tracking | Chart.js 3 grafici, inferenza counters |
| 23/06 | Overlay CUDA 13.0 su base 12.2 | GPU inference stabile su driver 580.x |
| 22/06 | PollingObserver + inode tracking | Watchdog funziona su Docker bind mount |
| 22/06 | Reranker fp16, n_batch=512, n_threads=6 | TTFT -24%, RAM -50% |

### Download Modelli

```bash
# Installa huggingface_hub se necessario
pip install huggingface_hub

# Worker — Gemma 4 E2B QAT (attivo, 15 layer GPU, ~1036 MiB VRAM)
# I file sono già scaricati in jarvis/models/.
# huggingface-cli download unsloth/gemma-4-E2B-it-GGUF \
#   gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf \
#   --local-dir /home/alfio/Projects/ai-ecosystem/jarvis/models/

# Master VPS — Gemma 4 26B A4B
huggingface-cli download unsloth/gemma-4-26B-A4B-it-GGUF \
  gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf \
  --local-dir /home/debian/ai-ecosystem/jarvis/models/

# Reranker — Qwen3-Reranker-0.6B (per container Docker)
mkdir -p /home/alfio/Projects/ai-ecosystem/jarvis/models/Qwen3-Reranker-0.6B
python3 -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='Qwen/Qwen3-Reranker-0.6B', local_dir='/home/alfio/Projects/ai-ecosystem/jarvis/models/Qwen3-Reranker-0.6B')"
```

---

## 6. Topologia di Rete Master/Worker

### VPN Mesh (Tailscale — WireGuard)

**IMPORTANTE:** Il sistema NON usa Ngrok. Usa **Tailscale** per la connettività sicura tra VPS e laptop.

| Nodo | IP Pubblico | IP Tailscale |
|---|---|---|
| Master (VPS) | 51.38.135.179 | 100.64.0.1 *(stimato — verificare con `tailscale ip`)* |
| Worker (Laptop) | dinamico | 100.64.0.2 *(stimato — verificare con `tailscale ip`)* |

**Installazione Tailscale:**
```bash
# VPS (Debian)
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up

# Laptop (OpenSUSE)
sudo zypper install tailscale
sudo tailscale up
```

### Rete Docker Interna

Tutti i container comunicano nella rete Docker `ai_network`. I nomi dei container sono i service name definiti nel compose:
- `qdrant` (Master), `qdrant_local` (Worker offline)
- `jarvis`, `jarvis_worker`
- `searxng`, `crawl4ai`

### Accesso SSH alla VPS

```bash
# Connessione diretta
ssh -i /home/alfio/.ssh/ovh_rsa debian@51.38.135.179
```

---

## 7. Stato Attuale dell'Implementazione

### ✅ Completato (Codice Locale — Ottimizzato e Stabile)

| Componente | Stato | Dettaglio |
|---|---|---|
| `jarvis/config.py` | ✅ Pronto | LLM_THINKING_MODE, LLM_NUM_CTX, parametri Gemma 4/Qwen3.5, Reranker config |
| `jarvis/llm_engine.py` | ✅ Ottimizzato | n_batch=512, n_threads=6, embed n_gpu_layers=2, chat_format=None, thinking mode, offloading+failover |
| `jarvis/rag.py` | ✅ Ottimizzato | Reranker Qwen3 fp16 su CPU, project_id nei payload, substring matching multi-word, inode tracking in os.walk per evitare loop symlink |
| `jarvis/prompt_builder.py` | ✅ Corretto | Isolamento progetto per conversazione, memoria filtrata per progetto, history 20 msg, finestra progetto attivo |
| `jarvis/state.py` | ✅ Migliorato | conversation_id per contesto progetto, helper get/set_last_project; counter inferenza (total_requests, total_prompt_tokens, total_completion_tokens) |
| `jarvis/main.py` | ✅ Corretto | reset-all pulisce last_project_context, conversation_id passato alla pipeline, PollingObserver watchdog, cleanup nested symlink |
| `jarvis/telegram_bot.py` | ✅ Corretto | user_id normalizzato a string, session TTL resetta progetto |
| `jarvis/model_profiles.py` | ✅ Nuovo | Auto-rilevamento famiglia modello da nome GGUF (Qwen, Gemma, DeepSeek, Llama, Mistral, Phi, Command-R) |
| `jarvis/dashboard.py` | ✅ Riscritto | GPU monitor real-time con time-series charts (Chart.js), inference counters, modelli, Qdrant collections |
| `jarvis/Dockerfile` | ✅ CUDA 13.0 | overlay cuda-compiler-13-0 + cudart-dev + cublas-dev su base 12.2; llama-cpp-python buildato con GGML_CUDA=on |
| `docker-compose.vps.yml` | ✅ Pronto | Stack Master senza GPU (no sezione deploy) |
| `docker-compose.worker.yml` | ✅ Pronto | QDRANT_HOST da .env, volumi mem0+documents montati |
| `start_master.sh` | ✅ Pronto | Usa docker-compose.vps.yml |
| `start_worker.sh` | ✅ Pronto | Modalità Worker GPU |
| `.env` (Worker locale) | ✅ Ottimizzato | N_GPU_LAYERS=15, LLM_NUM_CTX=12288, flash_attn=true |
| `sync_to_master.sh` | ✅ Creato | Script rsync con verifica SSH |
| **Istanza Locale** | ✅ **ONLINE** | Gemma 4 E2B QAT GPU inference (15 layer, 1036MiB/25% VRAM chart, 1432MiB/35% totale), CUDA 13.0 overlay, dashboard con GPU charts |

### ⏳ Da Completare (Operazioni Manuali sulla VPS)

| Step | Azione |
|---|---|
| 1 | Copia progetto sulla VPS: `rsync` o `git clone` |
| 2 | Installare Tailscale su VPS e Laptop |
| 3 | Creare `.env` Master sulla VPS (template in `docs/plans/master_worker_implementation.md` §8.6) |
| 4 | Copiare sessioni Userbot Telegram: `data/jarvis_mem0/userbots/` dal laptop alla VPS |
| 5 | Download `gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf` sulla VPS (~14.2GB) |
| 6 | Avviare Master: `./start_master.sh` sulla VPS |
| 7 | Avviare Worker: `./start_worker.sh` sul laptop |
| 8 | Prima sincronizzazione dati: `./sync_to_master.sh` |

---

## 8. Regole Operative per gli Agenti

### 🔴 NON FARE MAI

1. **Non committare mai `.env`** — contiene token Telegram, credenziali API, chiavi private.
2. **Non avviare Ollama** — Jarvis usa esclusivamente `llama-cpp-python` con file GGUF. Nessun processo Ollama deve essere installato o avviato.
3. **Non usare Ngrok** — rimosso completamente dal progetto. Usare Tailscale.
4. **Non hardcodare path o IP** nei file Python o YAML — usare sempre variabili d'ambiente lette da `config.py`.
5. **Non abilitare `TELEGRAM_ENABLED=true` sul Worker** — il bot Telegram gira SOLO sul Master.
6. **Non impostare `EXTERNAL_GPU_URL` sul Worker** — il Worker è il target, non il delegante.
7. **Non modificare `data/`** senza backup — contiene i dati persistenti (Qdrant, Mem0, sessioni Telegram).
8. **Non cambiare `VECTOR_DB_VERSION`** senza una buona ragione — causa migrazione automatica delle collezioni Qdrant e ri-ingestion completa del RAG.

### 🟡 ATTENZIONE

- **Python version split**: Il container ha DUE Python: `python3` (3.10.12, senza watchdog) e `python` (3.11, con watchdog). Granian usa `#!/usr/bin/python` (3.11). Testare watchdog con `/usr/bin/python`, NON con `python3`. Questo è un'eredità del Dockerfile multi-stage; non merge le due installazioni.
- **`EXTERNAL_PROJECTS`** nel `.env` contiene percorsi assoluti validi SOLO sul laptop di Alfio. Sulla VPS questo campo deve essere vuoto o contenere percorsi validi sulla VPS.
- **`chat_format=None`** in `llm_engine.py` funziona con Qwen3.5 e Gemma 4 (usa il template Jinja2 embedded nel GGUF). Non cambiare.
- **`n_gpu_layers`** attuale è `15` su RTX 3050 Ti 4GB. `N_GPU_LAYERS=15` con `flash_attn=true` lascia VRAM headroom per KV cache 12K e buffer computazionali. **Non superare `15` su Gemma 4 E2B QAT** — `18+` causa segfault per via dei 418 tensori Q4_0 incompatibili CUDA_Host (35 blocchi totali). Su Qwen3.5 (32 layer, nessun Q4_0) si può arrivare a `19`, ma `22+` causa CUDA OOM. Sulla VPS DEVE essere `0`.
- Il **Dockerfile** installa `llama-cpp-python` dalla master di GitHub (non PyPI) per avere gli ultimi fix di llama.cpp. Il build è lento (~5-10 minuti) ma necessario.
- **Gemma 4 E2B QAT** è ora il modello attivo con N_GPU_LAYERS=15 (1036 MiB VRAM, 25%). Qwen3.5-4B è in backup (1924 MiB VRAM, 47%). Vedi §11 Bug 1 per i dettagli sui limiti.
- **Qwen3.5-4B** è ora in backup — usare se Gemma 4 E2B dovesse presentare problemi.
- Le **collezioni Qdrant** sono versionate con il suffisso definito in `VECTOR_DB_VERSION`. Le versioni obsolete vengono eliminate automaticamente all'avvio.
- **Isolamento progetto**: il sistema ora supporta `conversation_id` per separare il contesto tra conversazioni concorrenti. Il Telegram usa `chat_id`, l'HTTP API accetta header `X-Conversation-Id` o body `conversation_id`.
- **Memoria filtrata per progetto**: quando un progetto è attivo, Mem0 cerca solo ricordi di quel progetto. Per conversazione generica (saluti), la memoria non viene iniettata per evitare contaminazione.
- **RAG cross-collection**: per query di codice senza progetto specificato, il RAG restituisce vuoto invece di cercare in tutte le collezioni, prevenendo contaminazione tra progetti.

### 🟢 APPROCCIO CORRETTO

- Quando aggiungi una nuova funzionalità, il punto di ingresso è sempre `main.py` (lifespan o endpoint).
- Le costanti di configurazione vanno in `config.py`, lette da `.env`.
- Lo stato globale condiviso va in `state.py`.
- Le modifiche al modello LLM (parametri, path) si effettuano SOLO nel `.env`.
- Per testare Jarvis localmente: `curl -X POST http://localhost:8000/api/chat -H "Content-Type: application/json" -d '{"model":"local","messages":[{"role":"user","content":"..."}],"stream":false}'`
- **Per isolamento progetto**: passare sempre `conversation_id` nelle richieste API (header `X-Conversation-Id` o body `conversation_id`). Il Telegram lo fa automaticamente con `chat_id`.
- **N_GPU_LAYERS** non deve superare 15 su Gemma 4 E2B QAT (segfault a 18+). Su Qwen3.5 max 19 (OOM a 22+). Sulla VPS sempre 0.

---

## 9. Pattern di Codice e Convenzioni

### Stile Python

- **Python 3.11** (runtime, via `/usr/bin/python`), asincrono con `asyncio` e `await`
  - NOTA: nel container esiste anche Python 3.10 (`/usr/bin/python3`) SENZA watchdog. Non usarlo per test watchdog.
- Moduli separati per responsabilità (Single Responsibility Principle)
- `logger = logging.getLogger(__name__)` in ogni modulo
- Errori gestiti con `try/except` + `logger.warning/error` (NO `except: pass` silenzioso)
- Context manager (`async with`) per risorse condivise
- `ThreadPoolExecutor` per operazioni CPU-bound bloccanti (inferenza LLM)

### Endpoint API

L'API supporta **sia il formato Ollama** che il **formato OpenAI** (`/v1/*`):

**Formato Ollama:**
- `POST /api/chat` — Chat con memoria, RAG, tool-calling
- `POST /api/generate` — Generate + cache semantica
- `POST /api/embeddings` — Embeddings (legacy)
- `GET /api/tags`, `GET /api/ps`, `GET /api/show`, `GET /api/version` — Stub Ollama

**Formato OpenAI (`/v1/*`):**
- `POST /v1/chat/completions` — Chat completion (streaming SSE)
- `POST /v1/completions` — Text completion (streaming SSE)
- `POST /v1/embeddings` — Embeddings (float/base64 encoding)
- `GET /v1/models` — Lista modelli
- `GET /v1/models/{model_name}` — Dettaglio modello
- `POST /v1/moderations` — Moderazione contenuti
- `POST /v1/audio/transcriptions` — Trascrizione audio (faster-whisper)
- `POST /v1/audio/speech` — Text-to-speech (gTTS)

### Tool Calling

Lo schema dei tool è definito in `agent_tools.py`. Per aggiungere un nuovo tool:
1. Aggiungere la entry in `TOOLS_SCHEMA` (formato JSON Schema)
2. Aggiungere il case in `execute_tool_call()`
3. Documentare il tool nel system prompt (in `prompt_builder.py`)

### Skills Dinamiche

Le skill in `jarvis/skills/` vengono caricate automaticamente a runtime da `skills_manager.py`. Ogni skill è un file Python con una classe che implementa l'interfaccia skill.

---

## 10. Comandi Utili

### Sviluppo Locale

```bash
# Avviare/Riavviare il Worker
cd /home/alfio/Projects/ai-ecosystem
./start_worker.sh
docker compose -f docker-compose.worker.yml up -d jarvis_worker

# Test rapido modello
curl -s -X POST http://localhost:8000/api/chat -H "Content-Type: application/json" \
  -d '{"model":"local","messages":[{"role":"user","content":"Ciao, presentati"}],"stream":false}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['message']['content'][:300])"

# Log worker
docker logs jarvis_worker --tail=50 -f

# Rebuild full
docker compose -f docker-compose.worker.yml build --no-cache && docker compose -f docker-compose.worker.yml up -d
```

### Gestione VPS (Master)

```bash
# Connessione SSH
ssh -i /home/alfio/.ssh/ovh_rsa debian@51.38.135.179

# Sync dati locale → VPS
./sync_to_master.sh
```

### Qdrant — Gestione Collezioni

```bash
# Lista collezioni
curl http://localhost:6333/collections | python3 -c "import sys,json; [print(c['name']) for c in json.load(sys.stdin)['result']['collections']]"

# Info su una collezione specifica
curl http://localhost:6333/collections/collateral_docs_Jarvis_v3 | python3 -m json.tool

# Eliminare una collezione (ATTENZIONE: irreversibile senza backup)
curl -X DELETE http://localhost:6333/collections/NOME_COLLEZIONE
```

### Backup e Restore

```bash
# Backup completo (dati + segreti)
tar -cvzf backup_ai_$(date +%Y%m%d).tar.gz ./data .env

# Restore
tar -xvzf backup_ai_YYYYMMDD.tar.gz
```

---

## 11. Bug Noti e Workaround

### 🐛 Bug 1: Gemma 4 E2B QAT — Limite N_GPU_LAYERS=15 (segfault oltre)

**AGGIORNAMENTO 23/06/2026:** Gemma 4 E2B QAT ora FUNZIONA con N_GPU_LAYERS=15 (1036 MiB VRAM, 25% della VRAM totale). Sei mesi fa si pensava che il modello non entrasse in 4GB — in realtà **entra benissimo** con la variante QAT UD-Q4_K_XL (2.62 GB su disco, 1036 MiB in VRAM a 15 layer GPU).

#### Dati Reali (verificati dal caricamento):

| Caratteristica | Valore reale |
|---|---|
| Blocchi (layer) totali | **35** (`gemma4.block_count = 35`) |
| Parametri totali | **2.1B** (4.6B size_label) |
| Tensori totali | **541** |
| Tensori Q4_0 (incompatibili CUDA_Host) | **418/541** |
| Embedding dim | 1536 |
| FFN dim | 6144 |
| Attention heads | 8, KV=1 (GQA single head) |

#### Il Problema: N_GPU_LAYERS > 15

Con `N_GPU_LAYERS=18`, il processo worker crasha con **segfault** (`[ERROR] Unexpected exit from worker-1`). Causa: 418 tensori su 541 sono in formato **Q4_0** che non può usare il buffer CUDA_Host. Oltre un certo numero di layer, il backend CUDA di llama.cpp non riesce a gestire la frammentazione tra buffer compatibili e incompatibili.

```
tensor 'token_embd.weight' (q4_0) (and 417 others) cannot be used with preferred buffer type CUDA_Host, using CPU instead
ggml_backend_cuda_buffer_type_alloc_buffer: allocating 530.58 MiB on device 0: cudaMalloc failed: out of memory
```

**N_GPU_LAYERS=15** evita il crash perché lascia sufficienti layer su CPU da evitare il buffer split fatale. Con 15 layer su GPU:
- Chat model VRAM: **1036 MiB** (25%)
- Embed model VRAM: **+400 MiB** (totale 1432 MiB / 35%)
- Headroom: **2.6 GB** liberi per KV cache + buffer computazionali — 7.4x rispetto a Qwen3.5

**Workaround:** Mantenere `N_GPU_LAYERS=15`. Non aumentare oltre 15 su Gemma 4 E2B QAT.

#### Performance a N_GPU_LAYERS=15 con Gemma 4 E2B (QAT):
- TTFT (primo token): ~8s
- Generation speed: **6.88 tok/s** (misurato, prompt 24 tok → 118 tok out)
- GPU temp peak: 86°C (sotto 89°C)
- VRAM: 1036MiB (25%) chat + ~400MiB embed = 1432MiB (35%) totale

#### Modelli disponibili in `jarvis/models/`:
| Quantizzazione | Dimensione | Tensori | VRAM (15 layer) | Tok/s | Stato |
|---|---|---|---|---|---|
| `qat-UD-Q4_K_XL` | 2.62 GB | 541 (418 Q4_0) | 1036 MiB (25%) | **6.88** | ✅ FUNZIONA (N_GPU_LAYERS=15) |
| `Q4_K_M` | 2.9 GB | 601 | 1118 MiB (27%) | 4.29 | ⚠️ TESTATO — +8% VRAM, -38% tok/s vs QAT, crash uguale a 18 layer |
| `Qwen3.5-4B-UD-Q4_K_XL` | 2.5 GB | ? | 1924 MiB (47%) | ~10 | ⏳ Backup (2x VRAM, più veloce) |

### 🐛 Bug 2: Mem0 Connection Refused all'avvio

**Problema:** All'avvio del container, Mem0 prova a connettersi al proprio endpoint loopback prima che Granian sia pronto.

**Soluzione:** È normale — Mem0 riprova automaticamente. I log mostrano "Connection refused" per ~10-20 secondi, poi si connette. Non è un errore critico.

### 🐛 Bug 3: `EXTERNAL_PROJECTS` con percorsi non validi

**Problema:** Se `.env` contiene percorsi in `EXTERNAL_PROJECTS` che non esistono nel container (es. `/home/alfio/Projects/...` sulla VPS), il RAG fallisce silenziosamente per quei progetti.

**Soluzione:** Svuotare `EXTERNAL_PROJECTS` nel `.env` della VPS o impostare percorsi validi sulla VPS.

### 🐛 Bug 4: CUDA OOM con N_GPU_LAYERS ≥ 22

**Problema:** Con `N_GPU_LAYERS=22` (o superiore), il processo worker crasha con `CUDA error` in `ggml-cuda.cu:103` durante `llama_decode`. Causa: il modello Qwen3.5-4B ha 32 layer; 22 layer su GPU (2.7 GB) + KV cache 16K + buffer temporanei di inferenza superano i 4 GB di VRAM della RTX 3050 Ti.

**Soluzione:** Mantenere `N_GPU_LAYERS=19` nel `.env`. Questo lascia 13 layer su CPU con VRAM stabile a ~3.566 / 4.096 MiB (87%).

### 🐛 Bug 9: CUDA 12.2 Incompatibile con Driver 580.x — GPU Crash

**Problema:** Il container basato su `nvidia/cuda:12.2.2-devel-ubuntu22.04` crasha con `CUDA error` in `ggml-cuda.cu:103` su host con driver NVIDIA ≥ 580.x. Causa: CUDA 12.2 runtime è incompatibile con CUDA 13.0 driver (non è forward-compatible per GPU kernel).

**Diagnosi rapida:**
```bash
nvidia-smi  # Se mostra CUDA Version: 13.0 → serve rebuild con overlay
docker logs jarvis_worker | grep -i "ggml_cuda_can_mul_mat\|cuda error"
```

**Soluzione:** Overlay CUDA 13.0 su base 12.2. Aggiungere al Dockerfile:
```dockerfile
RUN wget -q https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb \
    && dpkg -i cuda-keyring_1.1-1_all.deb \
    && apt-get update \
    && apt-get install -y cuda-compiler-13-0 cuda-cudart-dev-13-0 libcublas-dev-13-0
```

**llama-cpp-python** va buildato da sorgente contro CUDA 13.0:
```bash
CMAKE_ARGS="-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=86" pip install llama-cpp-python --no-binary llama-cpp-python
```

**Note:**
- `libcublas-dev-13-0` (840MB) è necessario quando llama.cpp cmake richiede il target `CUDA::cublas`. Senza, il link fallisce con `cannot find -lcublas`.
- Il `.dockerignore` deve escludere `jarvis/models/` per evitare di copiare 8.7GB di modelli nel build context.
- Il build richiede ~5-10 minuti su connessione lenta.

### 🐛 Bug 5: Contaminazione Progetti nella Conversazione

*(Risolto — mantenere conversation_id per isolamento, vedere §8 🟢 APPROCCIO CORRETTO)*

### 🐛 Bug 8: Watchdog Filesystem

*(Risolto — PollingObserver + inode tracking + nested symlink cleanup)*

**Problema originale:** `Observer` (inotify) non funziona su bind mount Docker con symlink. Inoltre NeuroNet crea un symlink circolare (`data/documents/NeuroNet → /app/documents/`) che blocca `DirectorySnapshot.walk()` in loop infinito.

**Soluzione (3 livelli originali):**
1. `Observer(inotify)` → `PollingObserver` (scandir ogni 1s, funziona su Docker)
2. `rag.py`: `visited_inodes` set in tutti gli `os.walk(followlinks=True)` per rilevare e bloccare loop
3. `main.py`: dopo creazione symlink, rimuove `data/documents/NeuroNet → /app/documents/` auto-referenziale

**Performance (rilascio originale):** snapshot 134k file in ~0.74s, rilevamento entro ~1.74s, elaborazione ~14s.

---

### 🐛 Bug 8b (2026-06-28): CPU 88% in idle — Watchdog ottimizzato

**Problema:** `PollingObserver(timeout=1)` eseguiva `stat()` su 335.479 file in `/home/alfio/Projects` ogni 1 secondo, causando 88% CPU su un thread sempre in stato `R (RUNNING)`.

**Causa:** `WORKSPACE_DIR=/home/alfio/Projects` contiene 335K file (compresi `.git/`, `node_modules/`, `__pycache__/`). Il watch ricorsivo sull'intero workspace esplorava ogni directory, inclusi artefatti di build.

**Soluzione (3 env var configurabili):**

| Variabile | Default | Descrizione | Impatto |
|---|---|---|---|
| `WATCHDOG_ENABLED` | auto-detect | Sovrascrive auto-detect legacy | Disattivabile via `.env` |
| `WATCHDOG_TIMEOUT` | `5` | Secondi tra polling (era hardcoded 1) | **5x meno polling** |
| `WATCHDOG_WATCH_MODE` | `per_project` | `"full"`=WORKSPACE_DIR, `"per_project"`=solo WORKSPACE_PROJECTS | **~30x meno file** |

**Impatto combinato:** timeout 5s × modalità per-progetto (10K file invece di 335K) = **~150x riduzione stat()/sec**, CPU stimata da 88% a **<1%**.

**File modificati:**
- `jarvis/config.py` (righe 285-304): `WATCHDOG_TIMEOUT`, `WATCHDOG_WATCH_MODE` letti da `.env` con default
- `jarvis/main.py` (lifespan + watchdog_health): parametri passati a `PollingObserver()` e path watch basati su `WATCHDOG_WATCH_MODE`
- `.env`: sezione `WATCHDOG FILESYSTEM` con valori ottimizzati

---

## 📚 Documenti Correlati

- **Piano completo di deployment:** `docs/plans/master_worker_implementation.md`
- **Documentazione utente:** `README.md`
- **Configurazione:** `.env` (gitignored — richiedere ad Alfio se necessario)

---

*Documento generato e mantenuto dall'agente AI. Aggiornare dopo ogni sessione di lavoro significativa.*

