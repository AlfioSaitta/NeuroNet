#!/bin/bash

# ==============================================================================
# Script di avvio per Jarvis su HOST (modalità Worker GPU)
# ==============================================================================
# Avvia i servizi Docker (Qdrant, SearXNG, Crawl4AI) e Jarvis direttamente
# sull'host tramite granian + venv Python.
#
# Prerequisiti:
#   1. python3 -m venv venv && source venv/bin/activate && pip install -r jarvis/requirements.txt
#   2. CUDACXX=/usr/local/cuda/bin/nvcc CMAKE_ARGS="-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=86" FORCE_CMAKE=1 \
#        pip install "llama-cpp-python[server] @ git+https://github.com/abetlen/llama-cpp-python.git@main"
#   3. Modelli GGUF in jarvis/models/
#   4. File .env configurato (copia da .env.example)
#
# Uso:
#   ./start_host_worker.sh              # avvio normale
#   ./start_host_worker.sh --build      # reinstalla llama-cpp-python prima di avviare
# ==============================================================================

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo -e "${CYAN}====================================================${NC}"
echo -e "${CYAN}  Avvio JARVIS - GPU WORKER (HOST)                  ${NC}"
echo -e "${CYAN}====================================================${NC}"

# ── 1. Verifica venv ──────────────────────────────────────────────
if [ ! -f "$SCRIPT_DIR/venv/bin/activate" ]; then
    echo -e "${RED}❌ Nessun ambiente virtuale trovato in venv/${NC}"
    echo -e "${YELLOW}   Crealo con: python3 -m venv venv && source venv/bin/activate && pip install -r jarvis/requirements.txt${NC}"
    exit 1
fi

# ── 2. Re-build opzionale llama-cpp-python ────────────────────────
if [ "$1" = "--build" ]; then
    echo -e "${YELLOW}🔧 Re-compilazione llama-cpp-python con CUDA...${NC}"
    source "$SCRIPT_DIR/venv/bin/activate"
    CUDACXX=/usr/local/cuda/bin/nvcc \
    CMAKE_ARGS="-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=86" \
        FORCE_CMAKE=1 \
        pip install --no-cache-dir --force-reinstall \
            "llama-cpp-python[server] @ git+https://github.com/abetlen/llama-cpp-python.git@main"
    echo -e "${GREEN}✅ Compilazione completata.${NC}"
fi

# ── 3. Verifica file .env ─────────────────────────────────────────
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo -e "${YELLOW}⚠️  .env non trovato. Copia da .env.example e configura.${NC}"
    echo -e "${YELLOW}   cp .env.example .env${NC}"
    exit 1
fi

# ── 4. Avvio servizi Docker ───────────────────────────────────────
echo -e "${YELLOW}📦 Avvio servizi Docker (Qdrant, SearXNG, Crawl4AI)...${NC}"
docker compose -f "$SCRIPT_DIR/docker-compose.services.yml" up -d
echo -e "${GREEN}✅ Servizi Docker avviati.${NC}"

# ── 5. Attesa Qdrant ──────────────────────────────────────────────
echo -e "${YELLOW}⏳ Attesa Qdrant sulla porta 6333...${NC}"
for i in $(seq 1 15); do
    if curl -s -o /dev/null -w "%{http_code}" http://localhost:6333/healthz 2>/dev/null | grep -q 200; then
        echo -e "${GREEN}✅ Qdrant pronto.${NC}"
        break
    fi
    if [ "$i" -eq 15 ]; then
        echo -e "${RED}❌ Qdrant non risponde dopo 15s. Controlla i log: docker logs qdrant_db${NC}"
    fi
    sleep 1
done

# ── 6. Carica .env nell'ambiente ─────────────────────────────────
echo -e "${YELLOW}📝 Caricamento .env...${NC}"
set -a
source "$SCRIPT_DIR/.env"
set +a
echo -e "${GREEN}✅ .env caricato (DATA_DIR=${DATA_DIR:-non impostato}).${NC}"

# ── 7. Attiva venv e avvia Jarvis ─────────────────────────────────
echo -e "${YELLOW}🚀 Avvio Jarvis su HOST (porta 8000)...${NC}"
source "$SCRIPT_DIR/venv/bin/activate"
cd "$SCRIPT_DIR/jarvis" || exit 1

exec granian --interface asgi --host 0.0.0.0 --port 8000 main:app
