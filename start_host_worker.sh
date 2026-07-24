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
#        pip install --force-reinstall jarvis/.llama-cpp-src/
#      (Il clone persistente in .llama-cpp-src/ evita l'editable install in /tmp/ che si perde al reboot)
#   3. Modelli GGUF in jarvis/models/
#   4. File .env configurato (copia da .env.example)
#
# Uso:
#   ./start_host_worker.sh              # avvio normale (auto-rileva se ricompilare)
#   ./start_host_worker.sh --build      # forza ricompilazione llama-cpp-python
# ==============================================================================

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LLAMA_CPP_SRC="$SCRIPT_DIR/jarvis/.llama-cpp-src"

# ── Helper: clona/aggiorna llama-cpp-python in path persistente ──
clone_llama_cpp_src() {
    if [ ! -d "$LLAMA_CPP_SRC" ]; then
        echo -e "${YELLOW}📦 Clonazione llama-cpp-python in $LLAMA_CPP_SRC...${NC}"
        git clone --depth 1 https://github.com/abetlen/llama-cpp-python.git "$LLAMA_CPP_SRC"
    fi
}

echo -e "${CYAN}====================================================${NC}"
echo -e "${CYAN}  Avvio JARVIS - GPU WORKER (HOST)                  ${NC}"
echo -e "${CYAN}====================================================${NC}"

# ── 1. Verifica venv ──────────────────────────────────────────────
# Priorità: jarvis/venv/ (ha CUDA già compilato) > venv/ (root)
if [ -f "$SCRIPT_DIR/jarvis/venv/bin/activate" ]; then
    VENV_DIR="$SCRIPT_DIR/jarvis/venv"
elif [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
    VENV_DIR="$SCRIPT_DIR/venv"
else
    echo -e "${RED}❌ Nessun ambiente virtuale trovato (cercato jarvis/venv/ e venv/)${NC}"
    echo -e "${YELLOW}   Crealo con: python3 -m venv venv && source venv/bin/activate && pip install -r jarvis/requirements.txt${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Venv trovato: $VENV_DIR${NC}"

# ── 2. Re-build opzionale llama-cpp-python ────────────────────────
if [ "$1" = "--build" ]; then
    clone_llama_cpp_src
    echo -e "${YELLOW}🔧 Re-compilazione llama-cpp-python con CUDA (da $LLAMA_CPP_SRC)...${NC}"
    source "$VENV_DIR/bin/activate"
    CUDACXX=/usr/local/cuda/bin/nvcc \
    CMAKE_ARGS="-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=86" \
        FORCE_CMAKE=1 \
        pip install --no-cache-dir --force-reinstall "$LLAMA_CPP_SRC"
    echo -e "${GREEN}✅ Compilazione completata.${NC}"
fi

# ── 3. Verifica file .env ─────────────────────────────────────────
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo -e "${YELLOW}⚠️  .env non trovato. Copia da .env.example e configura.${NC}"
    echo -e "${YELLOW}   cp .env.example .env${NC}"
    exit 1
fi

# ── 4. Avvio servizi Docker ───────────────────────────────────────
echo -e "${YELLOW}📦 Rimozione vecchi container (se presenti)...${NC}"
for c in searxng qdrant_db crawl4ai_server; do
    if docker ps -a --format '{{.Names}}' | grep -q "^$c$"; then
        docker rm -f "$c" 2>/dev/null && echo -e "  🗑️  Rimosso $c"
    fi
done
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

# ── 7. Verifica import llama_cpp (dopo reboot il modulo editable sparisce da /tmp) ──
echo -e "${YELLOW}🔍 Verifica modulo llama_cpp...${NC}"
source "$VENV_DIR/bin/activate"
SITE_PKG=$(python3 -c "import site; print(site.getsitepackages()[0])")
if ! python3 -c "import llama_cpp" 2>/dev/null; then
    echo -e "${RED}❌ Modulo llama_cpp non trovato (editable install in /tmp/ perso dopo reboot).${NC}"
    # Rimuove i resti dell'editable hook scikit-build
    rm -f "$SITE_PKG/_llama_cpp_python_editable.pth"
    rm -f "$SITE_PKG/_llama_cpp_python_editable.py"

    # Se il clone persistente esiste, copia i sorgenti Python in site-packages
    if [ -d "$LLAMA_CPP_SRC/llama_cpp" ]; then
        echo -e "${YELLOW}   Ripristino sorgenti Python da $LLAMA_CPP_SRC...${NC}"
        cp -a "$LLAMA_CPP_SRC/llama_cpp/"*.py "$SITE_PKG/llama_cpp/"
        cp -a "$LLAMA_CPP_SRC/llama_cpp/py.typed" "$SITE_PKG/llama_cpp/"
        cp -a "$LLAMA_CPP_SRC/llama_cpp/server" "$SITE_PKG/llama_cpp/"
        echo -e "${GREEN}✅ Sorgenti Python ripristinate (editable hook rimosso).${NC}"
    else
        echo -e "${YELLOW}   Clone persistente non trovato, avvio compilazione CUDA...${NC}"
        echo -e "${YELLOW}   (Nota: può richiedere 5-10 minuti)${NC}"
        clone_llama_cpp_src
        CUDACXX=/usr/local/cuda/bin/nvcc \
        CMAKE_ARGS="-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=86" \
            FORCE_CMAKE=1 \
            pip install --no-cache-dir --force-reinstall "$LLAMA_CPP_SRC"
        echo -e "${GREEN}✅ Compilazione completata.${NC}"
    fi
fi
echo -e "${GREEN}✅ Modulo llama_cpp verificato.${NC}"

# ── 8. Attiva venv e avvia Jarvis ─────────────────────────────────
echo -e "${YELLOW}🚀 Avvio Jarvis su HOST (porta 8000)...${NC}"
cd "$SCRIPT_DIR/jarvis" || exit 1

exec granian --interface asgi --host 0.0.0.0 --port 8000 main:app
