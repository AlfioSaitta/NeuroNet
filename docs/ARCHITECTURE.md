# Architettura del Sistema

## Topologia Attuale — Locale Standalone

> ⚠️ **Nota:** L'architettura Master/Worker via Tailscale non è ancora stata deployata.
> Il sistema opera attualmente in **modalità locale standalone** sul laptop Worker GPU.
> La documentazione VPS Master è mantenuta come blueprint futuro.

```
┌──────────────────────────────────────────────────────────────────┐
│  LAPTOP LENOVO IdeaPad Gaming 3 (OpenSUSE Tumbleweed)            │
│  i5-11300H, 16GB RAM, NVIDIA RTX 3050 Ti (4GB VRAM)            │
│                                                                  │
│  NODO UNICO (Worker GPU + tutto il resto):                      │
│  ├── jarvis:8000        (FastAPI + Granian + LlamaEngine GPU)   │
│  ├── qdrant:6333        (database vettoriale, Docker locale)     │
│  ├── searxng:8081       (metasearch anonimo, Docker)             │
│  ├── crawl4ai:11235     (scraper headless, Docker)               │
│  ├── Bot Telegram + Userbot (locale, TELEGRAM_ENABLED)           │
│  └── Modello: Qwen3.5-4B (GPU, ~3334 MiB VRAM, full GPU)        │
└──────────────────────────────────────────────────────────────────┘
```

## Topologia Futura — Master/Worker via Tailscale

```
┌──────────────────────────────────────────────────────────────────┐
│  VPS Debian (OVH)                                                │
│  8 vCore, 24GB RAM, NO GPU                                      │
│                                                                  │
│  Nodo MASTER (sempre online, DA DEPLOYARE):                     │
│  ├── jarvis:8000      (FastAPI + Granian + LlamaEngine CPU)     │
│  ├── qdrant:6333      (database vettoriale centralizzato)       │
│  ├── searxng:8081     (metasearch anonimo)                      │
│  ├── crawl4ai:11235   (scraper headless)                        │
│  ├── Bot Telegram + Userbots (centralizzato sul Master)         │
│  └── Modello: Gemma 4 26B A4B it (CPU, ~14.2GB RAM)            │
└──────────────────────┬───────────────────────────────────────────┘
                       │ Tailscale VPN (WireGuard)
                       │ EXTERNAL_GPU_URL=http://100.64.0.2:8000
                       │
┌──────────────────────▼───────────────────────────────────────────┐
│  LAPTOP LENOVO IdeaPad Gaming 3 (OpenSUSE Tumbleweed)            │
│  i5-11300H, 16GB RAM, NVIDIA RTX 3050 Ti (4GB VRAM)            │
│                                                                  │
│  Nodo WORKER GPU (Online):                                       │
│  ├── jarvis_worker:8000   QDRANT_HOST=100.64.0.1                │
│  ├── Modello: Qwen3.5-4B (GPU, full GPU offload)                │
│  └── TELEGRAM_ENABLED=false (centralizzato sul Master)          │
└──────────────────────────────────────────────────────────────────┘
```

## Flusso di Inferenza (Locale Attuale)

```
Client (API HTTP / Browser / Telegram)
  │
  ▼
jarvis:8000
  ├── LlamaEngine: Qwen3.5-4B (GPU, auto-detected params)
  │     ├── flash_attn=true, n_gpu_layers=-1 (full GPU)
  │     └── ~35-40 tok/s, ~3334 MiB VRAM
  │
  ├── Embedding: FastEmbed ONNX CPU (BAAI/bge-base-en-v1.5)
  │     └── 0 VRAM, 768 dims
  │
  ├── Compressore: Qwen3.5-0.8B (CPU, 0 VRAM)
  │     └── Context compression (skip < 1000ch, fallback raw)
  │
  ├── RAG: chunk codice da Qdrant (AST-aware, Tree-sitter)
  ├── Memoria: ricordi da Mem0 (Qdrant)
  ├── Synaptiq: grafo strutturale del codice
  ├── Web: SearXNG + Crawl4AI (se richiesto)
  ├── Hardware Identity: core/hardware.py (nvidia-smi, /proc/cpuinfo,
  │     /proc/meminfo, hostname) → [HARDWARE IDENTITY] nel system prompt
  └── Super-prompt XML → risposta LLM → loop tool-calling
```

## Flusso di Inferenza e Failover (Futuro Master/Worker)

```
Client (Cherry Studio / Jan / Continue / Cursor / Telegram)
  │
  ▼
Master jarvis:8000 (VPS)
  ├── [EXTERNAL_GPU_URL valorizzato?]
  │     ├── SÌ: ping Worker (timeout 1.5s)
  │     │       ├── Worker ONLINE  → offload GPU via HTTP POST
  │     │       └── Worker OFFLINE → fallback CPU locale (Gemma 4 26B)
  │     └── NO: inferenza locale CPU
  │
  ├── RAG: chunk codice da Qdrant (AST-aware, Tree-sitter)
  ├── Memoria: ricordi da Mem0 (Qdrant)
  ├── Web: SearXNG + Crawl4AI
  └── Super-prompt XML → risposta LLM → loop tool-calling
```
