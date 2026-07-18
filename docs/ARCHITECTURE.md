# Architettura del Sistema

## Topologia Master/Worker Edge-Cloud

```
┌──────────────────────────────────────────────────────────────────┐
│  VPS Debian (OVH)                                                │
│  8 vCore, 24GB RAM, NO GPU                                      │
│                                                                  │
│  Nodo MASTER (sempre online):                                    │
│  ├── jarvis:8000      (FastAPI + Granian + LlamaEngine CPU)     │
│  ├── qdrant:6333      (database vettoriale centralizzato)       │
│  ├── searxng:8081     (metasearch anonimo)                      │
│  ├── crawl4ai:11235   (scraper headless)                        │
│  ├── Bot Telegram + Userbots (TELEGRAM_ENABLED=true)            │
│  └── Modello: gemma-4-26B-A4B-it (CPU, ~14.2GB RAM)            │
└──────────────────────┬───────────────────────────────────────────┘
                       │ Tailscale VPN (WireGuard)
                       │ EXTERNAL_GPU_URL=http://100.64.0.2:8000
                       │
┌──────────────────────▼───────────────────────────────────────────┐
│  Laptop LENOVO IdeaPad Gaming 3 (OpenSUSE Tumbleweed)            │
│  i5-11300H, 16GB RAM, NVIDIA RTX 3050 Ti (4GB VRAM)            │
│                                                                  │
│  Nodo WORKER GPU (Online):                                       │
│  ├── jarvis_worker:8000   QDRANT_HOST=100.64.0.1                │
│  ├── Modello: Qwen3.5-4B-UD-Q4_K_XL.gguf (GPU)                 │
│  └── TELEGRAM_ENABLED=false (centralizzato sul Master)          │
└──────────────────────────────────────────────────────────────────┘
```

## Flusso di Inferenza e Failover

```
Client (Cherry Studio / Jan / Continue / Cursor / Telegram)
  │
  ▼
Master jarvis:8000
  ├── [EXTERNAL_GPU_URL valorizzato?]
  │     ├── SÌ: ping Worker (timeout 1.5s)
  │     │       ├── Worker ONLINE  → offload GPU via HTTP POST
  │     │       └── Worker OFFLINE → fallback CPU locale
  │     └── NO: inferenza locale CPU
  │
  ├── RAG: chunk codice da Qdrant (AST-aware, Tree-sitter)
  ├── Memoria: ricordi da Mem0 (Qdrant)
  ├── Web: SearXNG + Crawl4AI (prefisso /web o auto-discovery)
  └── Super-prompt XML → risposta LLM → loop tool-calling
```

## Gestione Esclusiva del Bot Telegram

Il bot Telegram è centralizzato sul nodo **Master (VPS)** per disponibilità 24/7:
- **Master:** `TELEGRAM_ENABLED=true` — Bot ufficiale + tutti gli Userbot
- **Worker:** `TELEGRAM_ENABLED=false` — mai abilitare (causa conflitti di sessione)
