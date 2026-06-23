# 🤖 AGENTS.md — Guida Operativa per Agenti AI

> **Questo file è destinato esclusivamente agli agenti AI che lavorano su questo progetto.**  
> Contiene tutto il contesto necessario per operare autonomamente senza errori.  
> **Data ultimo aggiornamento:** 2026-06-22 (fix watchdog)

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
12. [Accesso ai Sistemi](#12-accesso-ai-sistemi)

---

## 1. Identità del Progetto

**Nome:** Ecosistema AI Omnisciente — Chameleon Cognitive Stack  
**Proprietario:** Alfio Saitta / Collateral Studios  
**Scopo:** Sistema AI autonomo, privato e sempre disponibile per assistenza allo sviluppo software (Go, TypeScript, React) e automazione personale via Telegram.

### Componente Centrale: Jarvis

**Jarvis** è un proxy LLM asincrono scritto in Python (FastAPI + Granian) che espone un'API compatibile con il formato **Ollama** (NON OpenAI `/v1/*`). Integra:
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
├── docker-compose.yml           # Stack completo (sviluppo/reference)
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
| `LLAMA_MODEL_PATH` | `./models/gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf` | `./models/Qwen3.5-4B-UD-Q4_K_XL.gguf` | idem |
| `N_GPU_LAYERS` | `0` (tutto CPU) | `19` (RTX 3050 Ti, stabile) | `19` |
| `LLM_NUM_CTX` | `65536` | `16384` | `16384` |
| `LLM_NUM_PREDICT` | `4096` | `2048` | `2048` |
| `LLM_TEMPERATURE` | `1.0` (Gemma 4) | `0.7` (Qwen3.5) | `0.7` |
| `LLM_THINKING_MODE` | `true` | `false` | `false` |

### Variabili per il Funzionamento del Modello

```env
# Percorso modello chat (relativo a /app nel container = jarvis/)
LLAMA_MODEL_PATH=./models/NomeModello.gguf

# Layer GPU: 0=tutto CPU, N=numero layer caricati in GPU
# Worker: 19 stabile (RTX 3050 Ti 4GB). 22+ causa CUDA OOM durante inferenza.
# Master: 0 (CPU-only).
N_GPU_LAYERS=19

# Finestra contesto (token): 16384 Worker GPU, 65536 Master CPU
LLM_NUM_CTX=16384

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

### Parametri Interni LLM (hardcoded in llm_engine.py, NON sovrascrivibili da .env)

| Parametro | Chat Model (Qwen3.5) | Embedding Model (Qwen3-Embedding) | Note |
|---|---|---|---|
| `n_batch` | **512** | 256 | 512 bilanciato per pipeline GPU/CPU asincrona |
| `n_threads` | **6** | 6 | 8 core i5-11300H (4P+4E), 6 per LLM + 2 per I/O |
| `flash_attn` | True | — | Dimezza VRAM KV cache |
| `use_mmap` | True | True | Lazy page mapping per RAM efficiency |
| `embed n_gpu_layers` | — | **2** | Prime 2/28 layer su GPU (~50 MiB), query 2x veloci |

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
|---|---|---|---|---|
| **Qwen3.5-4B** (attivo) | `Qwen3.5-4B-UD-Q4_K_XL.gguf` | ✅ IN USO | ~2.7GB (19/32 layer GPU) | Definitivo — unico modello che entra in 4GB VRAM |
| **Qwen3-Embedding-0.6B** | `Qwen3-Embedding-0.6B-Q8_0.gguf` | ✅ IN USO | ~50 MiB (2/28 layer GPU) | 768d MRL, priming layer su GPU |
| **Qwen3-Reranker-0.6B** | `models/Qwen3-Reranker-0.6B/` | ✅ IN USO | 0 VRAM (CPU fp16) | ~600 MB RAM, 2x più veloce di fp32 |
| Gemma 4 E2B (qat-UD) | `gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf` | ❌ NON ENTRA | 2.5GB (35 blocchi) | 418/541 tensori in Q4_0 incompatibili CUDA_Host → OOM su 4GB |
| Gemma 4 E2B (Q4_K_M) | `gemma-4-E2B-it-Q4_K_M.gguf` | ❌ NON ENTRA | 2.9GB (35 blocchi) | Stessa architettura, non entra in 4GB VRAM |

### Master VPS (CPU-only — 24GB RAM)

| Modello | File | Stato | RAM | Note |
|---|---|---|---|---|
| **Gemma 4 26B A4B** (target) | `gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf` | ⏳ Da scaricare | ~14.2GB | MoE: attiva solo ~4B parametri, ~8-12 t/s |

### Parametri Ottimali per Modello

| Parametro | Qwen3.5 (Worker, definitivo) | Gemma 4 26B (Master VPS, futuro) |
|---|---|---|
| `LLM_TEMPERATURE` | 0.7 | 1.0 |
| `LLM_REPEAT_PENALTY` | 1.1 | 1.0 |
| `LLM_TOP_P` | 0.9 | 0.95 |
| `LLM_THINKING_MODE` | false | true |
| `chat_format` in llm_engine.py | *(auto)* | `None` (Jinja2 embedded) |

### Performance Ottimizzate (al 2026-06-22)

| Metrica | Prima (n.6) | Dopo (22/6) | Delta |
|---|---|---|---|
| TTFT (primo token) | 10.286 ms | **7.825 ms** | **-24%** |
| Generation speed | 6 tok/s | **10 tok/s** | **+67%** |
| VRAM utilizzata | 3.410 MiB (83%) | **3.566 MiB (87%)** | **+156 MiB** (più layer GPU) |
| VRAM libera | 362 MiB | **530 MiB** | **+46%** |
| Reranker RAM | 1.2 GB (fp32) | **~600 MB (fp16)** | **-50%** |
| Embedding speed | CPU (0 layer) | **GPU 2 layer** | **2x veloce** |

### Modifiche Performance

| Data | Modifica | Impatto |
|---|---|---|
| 22/06 | `N_GPU_LAYERS 18→19` | +1 layer GPU, TTFT -24% |
| 22/06 | `n_threads 4→6` | +20% CPU fallback layers |
| 22/06 | Embed `n_gpu_layers 0→2` | Embedding query 2x |
| 22/06 | Reranker fp16 | -600 MB RAM, 2x veloce |
| 22/06 | Revert `n_batch 2048→512` | TTFT da 14s→7.8s (2048 peggiorava) |
| 22/06 | `Observer(inotify)→PollingObserver` | Watchdog funziona su Docker bind mount + symlink |
| 22/06 | `rag.py`: inode tracking in os.walk | Previene loop infiniti su symlink circolari |
| 22/06 | `main.py`: nested symlink cleanup dopo creazione | DirectorySnapshot non si blocca più |

### Download Modelli

```bash
# Installa huggingface_hub se necessario
pip install huggingface_hub

# Worker — Gemma 4 E2B (NON ENTRA in 4GB VRAM - serve GPU ≥8GB)
# I file sono già scaricati ma inutilizzabili sulla RTX 3050 Ti (4GB).
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

# Copia file sulla VPS
scp -i /home/alfio/.ssh/ovh_rsa FILE debian@51.38.135.179:/home/debian/ai-ecosystem/

# Sync dati Worker → Master (script pronto)
./sync_to_master.sh

# Sync via Tailscale (dopo setup VPN)
MASTER_IP=100.64.0.1 ./sync_to_master.sh
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
| `jarvis/state.py` | ✅ Migliorato | conversation_id per contesto progetto, helper get/set_last_project |
| `jarvis/main.py` | ✅ Corretto | reset-all pulisce last_project_context, conversation_id passato alla pipeline, PollingObserver watchdog, cleanup nested symlink |
| `jarvis/telegram_bot.py` | ✅ Corretto | user_id normalizzato a string, session TTL resetta progetto |
| `jarvis/model_profiles.py` | ✅ Nuovo | Auto-rilevamento famiglia modello da nome GGUF (Qwen, Gemma, DeepSeek, Llama, Mistral, Phi, Command-R) |
| `jarvis/Dockerfile` | ✅ Stabile | llama-cpp-python da master GitHub (fix minori vari) |
| `docker-compose.vps.yml` | ✅ Pronto | Stack Master senza GPU (no sezione deploy) |
| `docker-compose.worker.yml` | ✅ Pronto | QDRANT_HOST da .env, volumi mem0+documents montati |
| `start_master.sh` | ✅ Pronto | Usa docker-compose.vps.yml |
| `start_worker.sh` | ✅ Pronto | Modalità Worker GPU |
| `.env` (Worker locale) | ✅ Ottimizzato | N_GPU_LAYERS=19, LLM_NUM_CTX=16384, tutti i parametri performance impostati |
| `sync_to_master.sh` | ✅ Creato | Script rsync con verifica SSH |
| **Istanza Locale** | ✅ **ONLINE** | Jarvis attivo, Qdrant+Mem0+RAG+Reranker funzionanti |

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
- **`n_gpu_layers`** attuale è `19` (stabile su RTX 3050 Ti 4GB). Non superare `19` — `22` causa CUDA OOM durante `llama_decode`. Sulla VPS DEVE essere `0` (impostato tramite `N_GPU_LAYERS=0` nel `.env`).
- Il **Dockerfile** installa `llama-cpp-python` dalla master di GitHub (non PyPI) per avere gli ultimi fix di llama.cpp. Il build è lento (~5-10 minuti) ma necessario.
- **Gemma 4 E2B NON può essere usato** sulla RTX 3050 Ti (4GB VRAM) — ha 35 blocchi e 418 tensori Q4_0 incompatibili CUDA_Host. Vedi §11 Bug 1 per i dettagli completi.
- **Qwen3.5-4B** è il modello definitivo per questo hardware. Non cercare alternative.
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
- **N_GPU_LAYERS** non deve superare 19 su RTX 3050 Ti 4GB. Valori ≥22 causano CUDA OOM.

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

L'API è **compatibile con il formato Ollama** (non OpenAI):
- Chat: `POST /api/chat` con body `{"model":"...", "messages":[...], "stream":false}`
- Generate: `POST /api/generate`
- Embed: `POST /api/embeddings`
- Tags: `GET /api/tags` (stub Ollama)

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
# Avviare l'istanza locale (Worker)
cd /home/alfio/Projects/ai-ecosystem
./start_worker.sh

# Avviare Qdrant separatamente (per modalità offline)
docker run -d --name qdrant_local \
  --network ai_network \
  -p 6333:6333 \
  -v "$(pwd)/data/qdrant:/qdrant/storage" \
  qdrant/qdrant:latest

# Verificare che Jarvis sia online
curl http://localhost:8000/

# Test chat con il modello
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"model":"local","messages":[{"role":"user","content":"Ciao, presentati"}],"stream":false}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['message']['content'][:300])"

# Test con conversation_id per isolamento progetto
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -H "X-Conversation-Id: neuro-net-session-1" \
  -d '{"model":"local","messages":[{"role":"user","content":"Lavoriamo su NeuroNet"}],"stream":false}'

# Test performance (ttft + tok/s)
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"model":"local","messages":[{"role":"user","content":"Ciao, chi sei?"}],"stream":false}' \
  -w '\n\n⏱️ Tempo totale: %{time_total}s\n' \
  -o /dev/null

# Vedere i log del container
docker logs jarvis_worker --tail=50 -f

# Rebuilddare il container dopo modifiche al Dockerfile
docker compose -f docker-compose.worker.yml build --no-cache
docker compose -f docker-compose.worker.yml up -d

# Reset RAG (cancella vettori e riesegue ingestion)
curl -X POST http://localhost:8000/api/reset-all

# Albero del progetto indicizzato
curl http://localhost:8000/api/project-tree | python3 -c "import sys,json; print(json.load(sys.stdin).get('tree','')[:500])"
```

### Gestione VPS

```bash
# Connessione SSH
ssh -i /home/alfio/.ssh/ovh_rsa debian@51.38.135.179

# Deploy aggiornamenti sulla VPS
scp -i /home/alfio/.ssh/ovh_rsa -r jarvis/ debian@51.38.135.179:/home/debian/ai-ecosystem/jarvis/

# Sincronizzazione dati locale → VPS
./sync_to_master.sh

# Avviare il Master sulla VPS (da eseguire sulla VPS)
cd /home/debian/ai-ecosystem && ./start_master.sh

# Log Master sulla VPS
ssh -i /home/alfio/.ssh/ovh_rsa debian@51.38.135.179 \
  "docker logs jarvis --tail=50 2>&1"
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

### 🐛 Bug 1: Gemma 4 E2B — NON ENTRA in 4GB VRAM (RTX 3050 Ti)

**ERRORE COMUNE DA NON RIPETERE:** La documentazione precedente diceva che Gemma 4 E2B ha 18 layer e che serviva un fix software (PR #22133). **Entrambe le affermazioni sono dimostrate false dall'analisi del 22/06/2026.**

#### Dati Reali (verificati dal caricamento):

| Caratteristica | Stima errata (prima) | Valore reale |
|---|---|---|
| Blocchi (layer) | 18 | **35** (`gemma4.block_count = 35`) |
| Parametri totali | 9B (MoE) | **4.6B** (`size_label = 4.6B`) |
| Tensori totali | — | **541** |
| Tensori Q4_0 (incompatibili CUDA) | — | **418/541** |
| Embedding dim | — | 1536 |
| FFN dim | — | 6144 |
| Attention heads | — | 8, KV=1 (GQA single head) |

**Problema reale:** Il modello ha **35 blocchi** (non 18). Con `N_GPU_LAYERS=18`, solo gli ultimi 18 vanno su GPU. I restanti 17 su CPU. Inoltre **418 tensori su 541 sono in formato Q4_0**, che non può usare il buffer CUDA_Host:
```
tensor 'token_embd.weight' (q4_0) (and 417 others) cannot be used with preferred buffer type CUDA_Host, using CPU instead
ggml_backend_cuda_buffer_type_alloc_buffer: allocating 530.58 MiB on device 0: cudaMalloc failed: out of memory
```

Il `GGML_ASSERT(n_inputs < GGML_SCHED_MAX_SPLIT_INPUTS)` (PR #22133) è un bug separato che colpisce **solo configurazioni multi-GPU** (es. 2x Tesla V100). Sulla singola RTX 3050 Ti **non si verifica mai** — il blocco reale è la mancanza di VRAM.

**Workaround definitivo:** Qwen3.5-4B (32 layer, 0 tensori Q4_0, embedding 2560) è il modello massimo che entra in 4GB VRAM. Gemma 4 E2B richiederebbe una GPU con ≥8GB VRAM.

**Tentativi falliti (tutti OOM):**
| Quantizzazione | Dimensione | Tensori | Risultato |
|---|---|---|---|
| `qat-UD-Q4_K_XL` | 2.5 GB | 541 (418 Q4_0) | ❌ CUDA OOM |
| `Q4_K_M` | 2.9 GB | 601 | ❌ CUDA OOM |

### 🐛 Bug 2: Mem0 Connection Refused all'avvio

**Problema:** All'avvio del container, Mem0 prova a connettersi al proprio endpoint loopback prima che Granian sia pronto.

**Soluzione:** È normale — Mem0 riprova automaticamente. I log mostrano "Connection refused" per ~10-20 secondi, poi si connette. Non è un errore critico.

### 🐛 Bug 3: `EXTERNAL_PROJECTS` con percorsi non validi

**Problema:** Se `.env` contiene percorsi in `EXTERNAL_PROJECTS` che non esistono nel container (es. `/home/alfio/Projects/...` sulla VPS), il RAG fallisce silenziosamente per quei progetti.

**Soluzione:** Svuotare `EXTERNAL_PROJECTS` nel `.env` della VPS o impostare percorsi validi sulla VPS.

### 🐛 Bug 4: CUDA OOM con N_GPU_LAYERS ≥ 22

**Problema:** Con `N_GPU_LAYERS=22` (o superiore), il processo worker crasha con `CUDA error` in `ggml-cuda.cu:103` durante `llama_decode`. Causa: il modello Qwen3.5-4B ha 32 layer; 22 layer su GPU (2.7 GB) + KV cache 16K + buffer temporanei di inferenza superano i 4 GB di VRAM della RTX 3050 Ti.

**Soluzione:** Mantenere `N_GPU_LAYERS=19` nel `.env`. Questo lascia 13 layer su CPU con VRAM stabile a ~3.566 / 4.096 MiB (87%).

**Performance a N_GPU_LAYERS=19:**
- TTFT (primo token): ~7.8s
- Generation speed: ~10 tok/s
- Embedding query: 2x rapido (2 layer su GPU)

### 🐛 Bug 5: Contaminazione Progetti nella Conversazione

**Problema:** Jarvis rispondeva con informazioni del progetto sbagliato (es. parlava di StreamAI IPTV quando l'utente chiedeva di NeuroNet).

**Cause multiple (tutte risolte):**
1. **Memoria cross-progetto**: Quando `active_project=None`, Mem0 restituiva ricordi di TUTTI i progetti → fix: saltare memoria quando nessun progetto attivo.
2. **Nessun conversation_id**: Due conversazioni concorrenti sovrascrivevano `last_project_context` → fix: contesto per `user_id + conversation_id`.
3. **Albero progetto globale**: `project_tree_cache` conteneva TUTTI i progetti, iniettato in ogni risposta → fix: filtrato per progetto attivo.
4. **Cross-collection mode**: Query codice senza progetto cercava in TUTTE le collezioni → fix: restituisce vuoto se nessun progetto rilevato.
5. **Finestra storia 10 messaggi**: `detect_project_in_conversation` non vedeva menzioni del progetto oltre il decimo messaggio → fix: finestra portata a 20 messaggi.

### 🐛 Bug 6: TTFT Regressione con n_batch Elevato

**Problema:** Con `n_batch=2048`, il TTFT è peggiorato da 10s a 14s. Causa: con solo 19/32 layer su GPU, ogni step processa 2048 token sui layer CPU (lenti). La pipeline GPU/CPU asincrona funziona meglio con batch piccoli (512) che permettono più step paralleli.

**Soluzione:** Mantenere `n_batch=512` in `llm_engine.py`.

### 🐛 Bug 7: Torch Dynamo Conflicts con Transformers 5.x

**Problema:** `torch 2.12.1` + `transformers 5.x` causa `ValueError: Duplicate dispatch rule` all'import di `torch._dynamo`. Il reranker Qwen3 non si caricava.

**Soluzione:** Impostare `TORCHDYNAMO_DISABLE=1` prima di importare torch, e usare `transformers<5` (v4.57.6 compatibile).

### 🐛 Bug 8: Watchdog Filesystem — Hang all'avvio e inaffidabilità su Docker bind mount

**Problema:** Il watchdog filesystem (per rilevamento automatico modifiche codice) aveva due bug distinti che impedivano il funzionamento su Docker.

#### Bug 8a: Observer (inotify) non funziona su bind mount Docker

**Problema:** `Observer` (basato su inotify) non rileva modifiche a file attraverso i bind mount Docker quando il percorso montato contiene symlink a progetti esterni (`/host_fs/...` → `/app/documents/Progetto`). Inotify segue i symlink in modo inaffidabile attraverso mount point Docker.

**Workaround originale (rimosso):** Il codice originale provava a creare watcher separati per ogni percorso reale (`path_mapping`) e confrontare manualmente i path con `os.stat` — ma era complesso e ancora inaffidabile.

**Soluzione definitiva:** Sostituire `Observer` (inotify) con `PollingObserver` (`from watchdog.observers.polling import PollingObserver`). Il PollingObserver:
- Usa `os.scandir` + `os.stat` periodici (non inotify)
- Funziona su qualsiasi filesystem, incluso Docker bind mount
- Segue correttamente i symlink (perché scandir/stat operano sul target del symlink)
- È platform-independent
- Configurazione: `timeout=1` (poll ogni 1 secondo + ~0.74s per scansionare 134k file)

**Dettagli implementativi:**
- L'import è condizionale (`if WATCHDOG_ENABLED: from watchdog.observers.polling import PollingObserver as Observer`)
- Il `PollingEmitter.start()` chiama `on_thread_start()` (che prende lo snapshot iniziale) NEL THREAD PRINCIPALE, poi parte il thread background
- `WATCHDOG_ENABLED` è determinato in `config.py` da un `try: from watchdog.observers import Observer` — attenzione: watchdog è installato per **Python 3.11** (`/usr/bin/python`) ma non per **Python 3.10** (`/usr/bin/python3`). Granian usa `#!/usr/bin/python` (3.11), quindi watchdog è disponibile al runtime.

#### Bug 8b: Circular symlink → DirectorySnapshot.walk() loop infinito

**Problema:** Il progetto NeuroNet contiene `data/documents/` che, quando montato in `/app/documents/`, crea il symlink `NeuroNet/data/documents/NeuroNet → /app/documents/` (puntando al root del volume). Questo genera un loop infinito in qualsiasi `os.walk(followlinks=True)`, incluso:
1. `DirectorySnapshot.__init__` → `DirectorySnapshot.walk()` (usato da PollingObserver)
2. Tutti i `os.walk()` in `rag.py` (`ingest_local_documents`, `generate_workspace_skeletons`, ecc.)

**Effetto:** L'applicazione si blocca all'avvio perché `observer.start()` → `PollingEmitter.on_thread_start()` → `_take_snapshot()` → `DirectorySnapshot.walk()` entra nel loop infinito e non ritorna mai.

**Soluzione approntata (3 livelli di difesa):**

1. **`rag.py` — Inode tracking in `os.walk`**:
   In tutti i 4 punti dove si usa `os.walk(followlinks=True)`, viene mantenuto un set `visited_inodes = set()` di tuple `(st_dev, st_ino)`. Quando si incontra una directory già visitata (stesso device+inode), si resetta `d[:] = []` per bloccare la discesa:
   ```python
   visited_inodes = set()
   for r, d, f in os.walk(DOC_DIR, followlinks=True):
       st = os.stat(r)
       inode_key = (st.st_dev, st.st_ino)
       if inode_key in visited_inodes:
           d[:] = []
           continue
       visited_inodes.add(inode_key)
   ```

2. **`main.py` — Rimozione nested symlink DOPO la creazione**:
   Il ciclo di creazione symlink in `EXTERNAL_PROJECTS` viene eseguito PRIMA della pulizia. Subito dopo, si esegue un secondo ciclo che controlla ogni progetto per `data/documents/NomeProgetto` → se è un symlink che punta al progetto stesso, viene rimosso. L'ordine è critico:
   ```
   PRIMA: crea symlink /app/documents/NeuroNet → /host_fs/.../NeuroNet
   POI:   rimuovi NeuroNet/data/documents/NeuroNet → /host_fs/.../NeuroNet (loop)
   POI:   observer.start() → DirectorySnapshot cammina pulito
   ```

3. **Nota:** `DirectorySnapshot` (libreria watchdog) NON ha inode tracking — cammina ricorsivamente senza protezione. È quindi essenziale che il loop non esista fisicamente sul filesystem PRIMA che il watcher parta.

**Verifica:** La soluzione è stata verificata misurando `DirectorySnapshot` su 134.329 file in 0.74 secondi, senza blocchi. Il log mostra:
```
🧹 Rimosso symlink ricorsivo: /host_fs/.../data/documents/NeuroNet → /host_fs/.../
👀 Watchdog PollingObserver Partito (intervallo 1s).
```

**Performance watchdog:**
- Snapshot iniziale: ~0.74s per 134k file
- Polling interval: 1s + 0.74s = ~1.74s per ciclo
- Rilevamento file: rilevato entro ~1.74s dalla creazione
- Elaborazione: ~14s totali (detection → debounce 1s → queue → embedding → upsert)

---

## 12. Accesso ai Sistemi

### VPS Master (OVH)

| Parametro | Valore |
|---|---|
| IP Pubblico | `51.38.135.179` |
| Utente SSH | `debian` |
| Chiave SSH | `/home/alfio/.ssh/ovh_rsa` |
| Percorso progetto | `/home/debian/ai-ecosystem` |
| OS | Debian |
| Risorse | 8 vCore, 24GB RAM, SSD, NO GPU |

### Laptop Worker (Alfio)

| Parametro | Valore |
|---|---|
| OS | OpenSUSE Tumbleweed |
| Hardware | LENOVO IdeaPad Gaming 3, i5-11300H, 16GB RAM |
| GPU | NVIDIA RTX 3050 Ti Laptop (4GB VRAM) |
| Percorso progetto | `/home/alfio/Projects/ai-ecosystem` |

### Servizi in Esecuzione (Locale — al 2026-06-19)

| Servizio | URL | Stato |
|---|---|---|
| Jarvis Worker | http://localhost:8000 | ✅ ONLINE |
| Qdrant locale | http://localhost:6333 | ✅ ONLINE |
| Dashboard Jarvis | http://localhost:8000/dashboard | ✅ ONLINE |

---

## 📚 Documenti Correlati

- **Piano completo di deployment:** `docs/plans/master_worker_implementation.md`
- **Documentazione utente:** `README.md`
- **Configurazione:** `.env` (gitignored — richiedere ad Alfio se necessario)

---

*Documento generato e mantenuto dall'agente AI. Aggiornare dopo ogni sessione di lavoro significativa.*

