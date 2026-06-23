FROM nvidia/cuda:12.2.2-devel-ubuntu22.04

# Prevenire i prompt interattivi durante l'installazione
ENV DEBIAN_FRONTEND=noninteractive

# Installazione CUDA 13.0 toolkit (per compatibilità con driver host 580.159.03)
# La CUDA 12.2 di base serve per i driver di sistema; installiamo la 13.0 per nvcc/librerie.
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    && rm -rf /var/lib/apt/lists/* && \
    wget -q https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb && \
    dpkg -i cuda-keyring_1.1-1_all.deb && \
    rm cuda-keyring_1.1-1_all.deb

RUN apt-get update && apt-get install -y \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    ffmpeg git curl build-essential \
    cuda-compiler-13-0 \
    cuda-cudart-dev-13-0 \
    libcublas-dev-13-0 \
    && rm -rf /var/lib/apt/lists/*

# Alias per far sì che i comandi `python` puntino a python3.11
RUN ln -s /usr/bin/python3.11 /usr/bin/python || true

# CUDA 13.0 toolkit path per la compilazione
ENV CUDA_HOME=/usr/local/cuda-13.0
ENV PATH=${CUDA_HOME}/bin:${PATH}
ENV LD_LIBRARY_PATH=${CUDA_HOME}/lib64:${LD_LIBRARY_PATH}

WORKDIR /app

# Upgrade pip base
RUN python -m pip install --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m spacy download en_core_web_sm || echo "⚠️ Spacy model download non riuscito (verrà scaricato al runtime se necessario)"

# Installazione di llama-cpp-python dalla master di GitHub con il fix Gemma 4
# Fix PR #22133 (GGML_SCHED_MAX_SPLIT_INPUTS) non ancora in PyPI — richiede build da sorgente
# ✅ CUDA 13.0 abilitato: toolkit installato a fianco della CUDA 12.2 di sistema.
# RTX 3050 Ti (compute capability 8.6) — compilazione ottimizzata.
ENV CMAKE_ARGS="-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=86"
ENV FORCE_CMAKE=1
RUN pip install --no-cache-dir "llama-cpp-python[server] @ git+https://github.com/abetlen/llama-cpp-python.git@main"

# Installazione del server Rust ad alte prestazioni
RUN pip install --no-cache-dir granian

COPY . .

EXPOSE 8000

# Avvio tramite Granian ASGI engine
CMD ["granian", "--interface", "asgi", "--host", "0.0.0.0", "--port", "8000", "main:app"]