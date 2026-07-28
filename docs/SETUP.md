# Setup e Installazione

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

## 🚀 Avvio Rapido

### Worker Locale (Sviluppo — Modalità Offline)

**Prerequisiti:**
- Docker + NVIDIA Container Toolkit
- GPU NVIDIA con driver ≥ 580.x (CUDA 13.0)
- Modelli GGUF in `jarvis/models/`

```bash
cd ~/NeuroNet

# 1. Avviare servizi Docker (Qdrant, SearXNG, Crawl4AI)
docker compose -f docker-compose.worker.yml up -d qdrant searxng crawl4ai

# 2. Build immagine (solo prima volta, ~5-10 min per llama-cpp-python CUDA)
docker compose -f docker-compose.worker.yml build jarvis_worker

# 3. Avviare Jarvis Worker (esecuzione HOST diretta, non Docker)
./start_worker.sh
```

> ⚠️ Build lento (~5-10 min): compila `llama-cpp-python` da sorgente con CUDA.

### Verifica GPU

```bash
docker logs jarvis_worker | grep -i "vram\|n_gpu_layers"
# Output: 🎯 [VRAM] Dopo caricamento ... MiB / 4096MiB
# Output: ⚙️ n_gpu_layers=-1  (full GPU per Qwen3.5-4B)
```

### Test Rapido

```bash
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"local","messages":[{"role":"user","content":"Ciao, presentati"}],"max_tokens":100}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['choices'][0]['message']['content'])"
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
```

---

## 📦 Stack Docker

| Servizio | Container | Porte | Descrizione |
|---|---|---|---|
| `jarvis_worker` (solo build) | `jarvis_worker` | — | Build CUDA per llama-cpp-python |
| `qdrant` | `qdrant_db` | 6333, 6334 | Database vettoriale |
| `searxng` | `searxng` | 8081 | Metasearch anonimo |
| `crawl4ai` | `crawl4ai_server` | 11235 | Web scraper headless |

Jarvis (FastAPI) gira direttamente sull'host, non containerizzato.

---

## 🤖 Modelli LLM

Jarvis usa **esclusivamente `llama-cpp-python`** con file GGUF. Nessun processo Ollama.

### Worker Locale (RTX 3050 Ti — 4GB VRAM)

| Modello | Stato | VRAM | Note |
|---|---|---|---|
| `Qwen3.5-4B-UD-Q4_K_XL.gguf` | ✅ **IN USO** | ~3334MiB (81%) | Chat model primario, n_gpu_layers=-1 (full GPU), flash_attn=true, ~35-40 tok/s |
| `Qwen3.5-0.8B-Instruct-Q4_K_M.gguf` | ✅ **IN USO** | **0 VRAM (CPU)** | Gatekeeper compression, GATEKEEPER_N_GPU_LAYERS=0, 4096 ctx, 6 few-shot |
| FastEmbed ONNX (BAAI/bge-base-en-v1.5) | ✅ **IN USO** | **0 VRAM (CPU)** | Embedding vettoriale, 768 dims |
| `Qwen3-Reranker-0.6B-Q8_0.gguf` | ⏳ Inutilizzato | — | Sostituito da FastEmbed CPU |
| `gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf` | ⏳ Backup | 1036MiB (25%) | Backup, richiede n_gpu_layers=15, ~5.7 tok/s |

### Master VPS (CPU-only — 24GB RAM, futuro)

| Modello | Stato | RAM | Note |
|---|---|---|---|
| `gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf` | ⏳ Da scaricare | ~14.2GB | MoE: ~4B attivi, 8-12 t/s |

---

## 🔧 Configurazione

Tutte le variabili in `.env`. Copiare da `.env.example`.

### Variabili Essenziali

```env
# === ARCHITETTURA ===
QDRANT_HOST=localhost            # localhost in offline, IP Tailscale in futuro
EXTERNAL_GPU_URL=                # (vuoto per ora — VPS non ancora deployato)

# === MODELLO LLM ===
LLAMA_MODEL_PATH=./models/Qwen3.5-4B-UD-Q4_K_XL.gguf
# N_GPU_LAYERS, LLM_FLASH_ATTN, LLM_UBATCH_SIZE sono AUTO-DETECTATI
# dal profilo famiglia GGUF. Non impostarli manualmente.

# === RAG ===
MAIN_PROJECT_PATH=/home/alfio/Projects/NeuroNet
EMBEDDING_DIMS=768

# === WATCHDOG FILESYSTEM ===
WATCHDOG_ENABLED=false            # true/false
WATCHDOG_TIMEOUT=5                # secondi tra polling (default: 5)
WATCHDOG_WATCH_MODE=per_project   # "full" o "per_project"
```

---

## 📚 Documenti Correlati

- **AGENTS.md** — Guida operativa per agenti AI
- **docs/ARCHITECTURE.md** — Topologia e flusso
- **docs/COMPONENTS.md** — Analisi componenti
- **docs/PIPELINE.md** — Flusso end-to-end
