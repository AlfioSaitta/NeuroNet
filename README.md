# NeuroNet — AI Cognitive Proxy

![Python: 3.11](https://img.shields.io/badge/Python-3.11-yellow)
![License: Private](https://img.shields.io/badge/License-Private-red)

**NeuroNet** è un ecosistema AI locale Enterprise. Il cuore è **Jarvis**, un **Cognitive Proxy** asincrono (FastAPI + Granian) — non un semplice proxy API, ma un middleware cognitivo che arricchisce ogni interazione LLM con memoria episodica, RAG AST-aware, web intelligence in tempo reale, tool-calling agentico, bot Telegram e scheduling.

L'inferenza avviene **interamente in-process** tramite `llama-cpp-python` su modelli GGUF: i pesi sono caricati in VRAM all'avvio e mantenuti caldi. **Nessun dato lascia la tua infrastruttura.**

---

## 📑 Indice

- [1. Core Features](#1-✨-core-features)
- [2. Full Message Pipeline](#2-🔄-full-message-pipeline)
- [3. Analisi Codebase](#3-🧠-analisi-completa-del-codebase)
- [4. Documentazione](#4-📚-documentazione)
- [5. Modelli LLM](#5-🤖-modelli-llm)
- [6. Endpoint API](#6-🔌-endpoint-api--telemetry)
- [7. Configurazione Rapida](#7-🔧-configurazione-rapida)
- [8. Avvio Rapido](#8-🚀-avvio-rapido)
- [9. CUDA Overlay](#9-⚠️-cuda-130-overlay--nota-critica)
- [10. Provider Esterni](#10-🌐-strategia-integrazione-provider-esterni)
- [11. Changelog](#11-📝-changelog)
- [12. Pannello di Amministrazione](#12-🖥️-pannello-di-amministrazione)

---

## 1. ✨ Core Features

<details>
<summary><b>🧠 Core AI & Inferenza</b> — LlamaEngine, Thinking Mode, GPU Offloading</summary>

| Feature | File | Stato | Dettaglio |
|---|---|---|---|
| LlamaEngine singleton | `llm_engine.py` | ✅ | Modelli GGUF caldi in VRAM, PriorityLock chat>embedding |
| Flash Attention | `llm_engine.py` | ✅ | Riduzione VRAM 30-50% |
| Warmup CUDA JIT | `llm_engine.py` | ✅ | Evita delay 30s+ prima richiesta |
| GPU Offloading remoto | `llm_engine.py` | ✅ | Worker remoto con failover 1.5s ping |
| Thinking Mode | `llm_engine.py` | ✅ | Supporto `<\|think\|>` Gemma/DeepSeek/QwQ |
| Compressione caveman | `llm_engine.py` | ✅ | Qwen3.5 CPU, raw fallback |
| `_strip_thinking()` | `llm_engine.py` | ✅ | Rimuove metacognizione da risposte Gatekeeper |
| Model profiles | `model_profiles.py` | ✅ | Auto-rilevamento famiglia GGUF (7 famiglie) |
| Multi-modello (chat/embed) | `llm_engine.py` | ✅ | Due modelli separati in VRAM, priorità differenziate |

</details>

<details>
<summary><b>📚 RAG</b> — AST Chunking, Reranker Duale, Cache Semantica</summary>

| Feature | File | Stato | Dettaglio |
|---|---|---|---|
| AST Chunking semantico | `rag.py` | ✅ | Tree-sitter per 9 linguaggi |
| Reranker duale | `rag_reranker.py` | ✅ | Qwen3-Reranker (primario) + FlashRank (fallback) |
| Cross-collection fallback | `rag.py` | ✅ | Fallback su tutte le collezioni |
| Gitignore-aware | `rag.py` | ✅ | pathspec .gitignore |
| Watchdog real-time | `rag.py` | ✅ | PollingObserver, re-embedding automatico |
| Semantic Cache | `rag_cache.py` | ✅ | Cache risposte, soglia cosine 0.88 |
| Web Knowledge Cache | `rag_cache.py` | ✅ | Memorizza risultati web per reuse |
| Synaptiq Engine | `synaptiq_engine.py` | ✅ | Grafo strutturale, hybrid search, dead code, impact, community |

</details>

<details>
<summary><b>🧠 Memoria & Contesto</b> — Mem0, Ricerca Filtrata, Consolidamento Notturno</summary>

| Feature | File | Stato | Dettaglio |
|---|---|---|---|
| Memoria episodica (Mem0+Qdrant) | `memory.py` | ✅ | Salvataggio automatico con metadati progetto |
| Ricerca filtrata | `memory.py` | ✅ | Per `user_id` + `project` |
| Warmup spaCy/BM25 | `memory.py` | ✅ | Evita delay 10-30s all'avvio |
| Tag `<MEMORY>` | `tag_processor.py` | ✅ | Salvataggio esplicito da risposta LLM |
| Backup/export JSON | `memory_backup.py` | ✅ | Disaster recovery |
| Consolidamento notturno | `reflection_agent.py` | ✅ | Episodica → profilo sintetico (3:00 UTC) |

</details>

<details>
<summary><b>🧩 Prompt Builder & Gatekeeper</b> — Classificazione, Budget Allocator, Super-prompt XML</summary>

| Feature | File | Stato | Dettaglio |
|---|---|---|---|
| LLM Gatekeeper | `prompt_builder.py` | ✅ | Classifica intento (keyword+regex+LLM grammar) |
| Web Intelligence | `prompt_builder.py` | ✅ | `/web` prefix → SearXNG + Crawl4AI o auto-discovery |
| Budget Allocator | `prompt_builder.py` | ✅ | 55% RAG / 20% web / 10% mem / 15% tree, max 15K char |
| Super-prompt XML | `prompt_builder.py` | ✅ | 7 tag contestuali |
| Format Rules | `prompt_builder.py` | ✅ | Tabelle, code block, bold + sezione `---` finale |
| TagSafeStream | `tag_processor.py` | ✅ | State machine anti-leak tag XML in streaming |
| 21 tag d'azione XML | `tag_processor.py` | ✅ | MEMORY, SCHEDULE, SSH, TODO, WEB, FILE, EXEC, COMMIT... |

</details>

<details>
<summary><b>🤖 Telegram & Userbot</b> — Bot Ufficiale, Multi-Userbot Telethon</summary>

| Feature | File | Stato | Dettaglio |
|---|---|---|---|
| Bot Telegram ufficiale | `telegram_bot.py` | ✅ | Menu a bottoni, whitelist, admin panel |
| Multi-Userbot Telethon | `telegram_userbot_manager.py` | ✅ | Clone per utente via OTP, chat private |
| Messaggi vocali | `telegram_bot.py` | ✅ | Trascrizione faster-whisper + risposta gTTS |

</details>

<details>
<summary><b>🔧 Tool-Calling & Scheduling</b> — Loop Agentico, Skill Dinamiche, APScheduler</summary>

| Feature | File | Stato | Dettaglio |
|---|---|---|---|
| Tool-calling nativo | `agent_tools.py` | ✅ | 5 built-in tool: write, read, delete, replace, shell |
| Skill dinamiche YAML | `skills_manager.py` | ✅ | Skill in `jarvis/skills/` caricate a runtime |
| Conferma utente Telegram | `agent_tools.py` | ✅ | Timeout 5 min per operazioni distruttive |
| Ricorsione LLM | `agent_tools.py` | ✅ | Risultato tool → nuovo giro → risposta |
| APScheduler | `cron_agent.py` | ✅ | CronTrigger, DateTrigger, timer relativi |
| Task Manager | `task_manager.py` | ✅ | Task persistenti con priorità e scadenze |

</details>

<details>
<summary><b>🏗️ Infrastruttura & DevOps</b> — CUDA Overlay, SSH, Dashboard, MCP</summary>

| Feature | File | Stato | Dettaglio |
|---|---|---|---|
| CUDA 13.0 Overlay | `Dockerfile` | ✅ | Base 12.2 + overlay 13.0 per driver 580.x |
| SSH remoto via tag `<SSH>` | `infrastructure.py` | ✅ | Esecuzione asyncssh su server remoti |
| Dashboard web Chart.js | `dashboard.py` | ✅ | GPU/System/RAG metrics in tempo reale, **Settings panel con 73 env var categorizzate** |
| Session Store SQLite | `session_store.py` | ✅ | Chat session persistente per dashboard |
| Intent Classifier | `classificatore.py` | ✅ | Classificazione intenti centralizzata (Intent enum) |
| OpenAI API | `openai/` | ✅ | 25 endpoint: Chat, Audio, Assistants/Threads/Runs |
| MCP Server v2 | `mcp_server_v2.py` | ✅ | Streamable HTTP, 8 tool + 7 resources |
| MCP Server stdio | `mcp_server.py` | ✅ | Legacy per agenti AI esterni |

</details>

---

## 2. 🔄 Full Message Pipeline

> 📖 **Diagramma dettagliato del flusso end-to-end** (9 step: Routing → Pipeline Tracer → Gatekeeper → Context Gathering → Super-prompt → Generazione → Streaming → Tool-calling Loop → Output): [`docs/PIPELINE.md`](docs/PIPELINE.md)

```
Input → Routing → PipelineTracer → Gatekeeper (intento?)
       → Context Gathering [Web | Memoria | RAG | Synaptiq]
       → Super-prompt XML → LLM Generation
       → TagSafeStream → Tool-calling Loop → Output
```

---

## 3. 🧠 Analisi Completa del Codebase

> 📖 **Analisi dettagliata con diagrammi ASCII e descrizioni approfondite:** [`docs/COMPONENTS.md`](docs/COMPONENTS.md)

| # | Componente | File | Righe | Ruolo |
|---|---|---|---|---|
| 1 | 🏭 LlamaEngine | `llm_engine.py` | 635 | Singleton inferenza GGUF, PriorityLock, offloading GPU |
| 2 | 📚 Pipeline RAG | `rag.py` | 1.797 | AST chunking (Tree-sitter), reranker duale, watchdog |
| 3 | 🧠 Memoria Episodica | `memory.py` | 187 | Mem0+Qdrant, spaCy entità, backup JSON |
| 4 | 🧩 Prompt Builder | `prompt_builder.py` | 530 | Gatekeeper, super-prompt XML, budget allocator |
| 5 | 🔧 Loop Agentico | `agent_tools.py` | 1.008 | 5 tool built-in, skill dinamiche, conferma Telegram |
| 6 | 🤖 Telegram Bot | `telegram_bot.py` | 1.176 | Menu bottoni, whitelist, messaggi vocali |
| 7 | 🕐 Scheduler | `cron_agent.py` | 186 | APScheduler, CronTrigger/DateTrigger/Relative |
| 8 | ☕ Task Manager | `task_manager.py` | 73 | ToDo persistenti con priorità e scadenze |
| 9 | 🌙 Reflection Agent | `reflection_agent.py` | 82 | Consolidamento memoria notturno (3:00 UTC) |
| 10 | 🔌 Infrastructure | `infrastructure.py` | 45 | Registro server SSH, esecuzione asyncssh |
| 11 | 📊 Dashboard Web | `dashboard.py` | 1.983 | Chart.js, GPU/System/RAG metrics |
| 12 | 🗺️ Model Profiles | `model_profiles.py` | 292 | Auto-rilevamento famiglia GGUF (7 famiglie) |
| 13 | 🧬 Synaptiq Engine | `synaptiq_engine.py` | — | Grafo strutturale: hybrid search, dead code, impact, community |
| 14 | 🔍 Pipeline Telemetry | `telemetry.py` | 442 | PipelineTracer, GatekeeperStats, ring buffer 500 |

**Totale: ~8.500+ righe di core engine Python** (esclusi template, legacy, skills, test)

---

## 4. 📚 Documentazione

| File | Contenuto | Target |
|---|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Topologia Master/Worker, flusso inferenza, failover, gestione Telegram | Chiunque |
| [`docs/COMPONENTS.md`](docs/COMPONENTS.md) | Analisi completa dei 14 componenti con diagrammi ASCII | Sviluppatori |
| [`docs/PIPELINE.md`](docs/PIPELINE.md) | Diagramma dettagliato del flusso end-to-end Input→Response | Sviluppatori |
| [`docs/SETUP.md`](docs/SETUP.md) | Installazione, configurazione, modelli, manutenzione, CUDA overlay | DevOps |
| [`docs/AGENTS.md`](docs/AGENTS.md) | Guida operativa completa per agenti AI che lavorano sul progetto | **AI Agents** |
| [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) | Tutti gli endpoint: Jarvis nativi, OpenAI, Telemetry, MCP | Integratori |
| [`docs/TAGS_REFERENCE.md`](docs/TAGS_REFERENCE.md) | Riferimento completo dei 21 tag XML d'azione | Sviluppatori |
| [`docs/STRATEGY.md`](docs/STRATEGY.md) | Strategia integrazione provider esterni (Gemini, privacy) | Architetti |
| [`docs/plans/master_worker_implementation.md`](docs/plans/master_worker_implementation.md) | Piano di deploy dettagliato VPS Master + Worker | DevOps |

**Docs utente:** [`CHANGELOG.md`](CHANGELOG.md) · [`SETUP.md`](docs/SETUP.md)  
**Docs interni:** [`AGENTS.md`](docs/AGENTS.md) (leggi PRIMA di lavorare sul codice)

---

## 5. 🤖 Modelli LLM

> 📖 **Elenco completo (Worker GPU, Master VPS, VRAM/RAM, benchmark):** [`docs/SETUP.md`](docs/SETUP.md)

| Ruolo | Modello | Quantizzazione | Memoria | Velocità |
|---|---|---|---|---|
| **Chat (attivo)** | Gemma 4 E2B QAT | Q4_K_XL (2.5 GB) | 1.036 MiB VRAM | ~6.88 tok/s |
| **Embedding** | Qwen3-Embedding-0.6B | Q8_0 | ~400 MiB VRAM | — |
| **Reranker** | Qwen3-Reranker-0.6B | fp16 CPU | ~600 MB RAM | — |
| **Backup Chat** | Qwen3.5-4B | Q4_K_XL (2.8 GB) | 1.924 MiB VRAM | ~6.24 tok/s |
| **Master (futuro)** | Gemma 4 26B A4B | Q4_K_XL | ~14.2 GB RAM | ~8-12 tok/s attesi |

Jarvis usa **esclusivamente `llama-cpp-python`** con file GGUF. Nessun processo Ollama.

---

## 6. 🔌 Endpoint API & Telemetry

> 📖 **Tutti gli endpoint e diagnostica MCP:** [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md)

| Categoria | Endpoint | Quantità |
|---|---|---|
| **Jarvis nativi** | `/api/chat`, `/api/generate`, `/api/embeddings`, `/api/tags` ... | 8 |
| **OpenAI-compatibili** | `/v1/chat/completions`, `/v1/embeddings`, `/v1/audio/*`, Assistants API ... | 25 |
| **Pipeline Telemetry** | `/api/telemetry/{status,traces,gatekeeper,errors,model,pending_ops}` | 8 |
| **MCP v2** | `POST /api/mcp/v2` (Streamable HTTP) | 8 tool + 7 resources |

**43 endpoint totali.** PipelineTracer per-request con ring buffer 500 trace. Diagnostica AI via MCP.

---

## 7. 🔧 Configurazione Rapida

> 📖 **Configurazione completa, variabili d'ambiente, modelli, manutenzione:** [`docs/SETUP.md`](docs/SETUP.md)

```env
# === ARCHITETTURA ===
QDRANT_HOST=localhost
EXTERNAL_GPU_URL=

# === MODELLO LLM ===
LLAMA_MODEL_PATH=./models/Qwen3.5-4B-UD-Q4_K_XL.gguf
N_GPU_LAYERS=15
LLM_FLASH_ATTN=true

# === RAG ===
MAIN_PROJECT_PATH=/host_fs/home/alfio/Projects
EMBEDDING_DIMS=768

# === WATCHDOG FILESYSTEM ===
WATCHDOG_ENABLED=true
WATCHDOG_TIMEOUT=5
WATCHDOG_WATCH_MODE=per_project
```

---

## 8. 🚀 Avvio Rapido

> 📖 **Installazione completa (prerequisiti, build Docker, verifica GPU, test):** [`docs/SETUP.md`](docs/SETUP.md)

```bash
# Build e avvio Worker GPU
./start_worker.sh

# Test rapido
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"model":"local","messages":[{"role":"user","content":"Ciao"}],"stream":false}'

# Log
docker logs jarvis_worker --tail=50 -f
```

> 📖 **Tutti i comandi** (manutenzione, backup, sync, reset RAG, Qdrant, MCP, Telemetry): [`docs/SETUP.md`](docs/SETUP.md)

---

## 9. ⚠️ CUDA 13.0 Overlay — Nota Critica

> 📖 **Diagnosi e fix dettagliati:** [`docs/SETUP.md`](docs/SETUP.md)

Il container usa overlay **CUDA 13.0** su base 12.2 per compatibilità con driver NVIDIA 580.159.03. Se il container crasha con `ggml_cuda_can_mul_mat`, verificare che `nvidia-smi` mostri CUDA 13.0.

---

## 10. 🌐 Strategia Integrazione Provider Esterni

> 📖 **Piano completo, architettura ProviderRouter, strategie di routing, privacy:** [`docs/STRATEGY.md`](docs/STRATEGY.md)

Jarvis è **100% locale** — nessuna dipendenza cloud. Pianificata integrazione Gemini API come fallback per conoscenza enciclopedica, multimodalità e contesto 1M+ token, con 4 strategie di routing e privacy mode configurabile.

---

## 11. 📝 Changelog

> 📜 **Storico completo (v9.0.0 → v9.7.0):** [`CHANGELOG.md`](CHANGELOG.md)

---

## 12. 🖥️ Pannello di Amministrazione

Jarvis include una dashboard web completa accessibile su `/dashboard/` o `/admin/`. Il pannello è suddiviso in **viste** (tab laterali) e fornisce metriche in tempo reale, diagnostica e controllo del sistema.

![Admin Dashboard](docs/images/admin-dashboard.png)

### ✅ Monitor
Vista principale con telemetria in tempo reale: GPU (VRAM, temperatura, utilizzo), modelli LLM caricati, health dei servizi (Qdrant, SearXNG, Crawl4AI), statistiche inferenza (richieste totali, token), storico RAG, metriche di sistema (CPU, RAM, disco, rete), cronologia errori.

### ✅ Code Graph
Visualizzazione interattiva delle collezioni Qdrant tramite Sigma.js (FA2 layout). Ogni collezione è esplorabile come grafo vettoriale. Include funzioni di re-index e delete collection.

### ✅ Chat
Interfaccia chat in-browser con streaming SSE, markdown rendering, supporto drag-and-drop file e shortcut `/` per comandi rapidi.

### ✅ Management
Viste di amministrazione:
- **Settings** — Pannello configurazione con **73 variabili d'ambiente** categorizzate in 12 gruppi. Supporta tipi text, number, float, boolean, select (dropdown), secret (password + toggle visibilità). **Simple Mode** (25 setting basic) / **Advanced Mode** (tutti i 63 visibili). Badge ⚡ per parametri che richiedono restart. Persistenza immediata su `.env`.
- **Code Graph** — Lista collezioni Qdrant, re-index, delete.
- **Models** — Lista modelli GGUF disponibili, switch runtime.
- **Tasks** — CRUD task con priorità e scadenze.
- **Cron** — Job schedulati (APScheduler), attivazione/pausa.
- **Analytics** — Statistiche inferenza, telemetry, gatekeeper, distribuzione errori.

### ✅ Logs
Viewer log Docker con filtro per servizio e auto-scroll.

### Architettura Frontend
Il pannello è implementato come modulo separato `jarvis/admin_panel/`:
| Componente | Descrizione |
|---|---|
| `__init__.py` | Router FastAPI, mount static files, route `/dashboard/` e `/admin/` |
| `templates/index.html` | Template HTML unico con tutte le viste (CSS-in-JS residuo solo per stili dinamici) |
| `static/css/style.css` | Tema scuro custom, ~500 righe, classi utility (flex, grid, gap, card, badge) |
| `static/js/main.js` | Init dashboard, cambio view, polling `/api/dashboard/*` |
| `static/js/charts.js` | Chart.js: GPU usage, inference counters, RAG chunks history |
| `static/js/graph.js` | Sigma.js graph viewer con FA2 layout |
| `static/js/chat.js` | Chat streaming SSE, drag-drop file, shortcut `/` |
| `static/js/telemetry.js` | Polling telemetry (10 funzioni dominio-specifiche, Page Visibility API) |
| `static/js/management.js` | Admin views: Settings, Code Graph, Models, Tasks, Cron, Analytics |
| `static/js/logs.js` | Docker logs viewer |
| `static/js/utils.js` | Utility condivise (`fetchWithTimeout`, `showToast`, `escapeHtml`) |

### Backend
I dati sono serviti da `jarvis/dashboard.py` (API Router FastAPI) che espone endpoint `/api/dashboard/*` per ogni vista. La configurazione settings è gestita tramite `SETTINGS_META` (dict di 73 voci con metadati estesi) e `_persist_env()` per scrittura atomica su `.env`.

---

🌐 **NeuroNet** — *Infrastruttura di Intelligenza Artificiale Locale Riservata.*
