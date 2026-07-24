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

## 📦 Stack Docker

| Servizio | Container | Porte | Descrizione |
|---|---|---|---|
| `jarvis` | `jarvis` | 8000 | Nodo Master (CPU) |
| `jarvis_worker` | `jarvis_worker` | 8000 | Nodo Worker (GPU) |
| `qdrant` | `qdrant_db` | 6333, 6334 | Database vettoriale |
| `searxng` | `searxng` | 8081 | Metasearch anonimo |
| `crawl4ai` | `crawl4ai_server` | 11235 | Web scraper headless |

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
