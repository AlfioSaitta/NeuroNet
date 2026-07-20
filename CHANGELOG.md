# Changelog

Tutte le modifiche significative a NeuroNet/Jarvis sono documentate in questo file.

---

### v9.8.0 (2026-07-20) — User Management & ACL: JWT Auth, Admin Panel, API Keys
- **UserManager SQLite:** Nuovo `jarvis/user_manager.py` — singleton SQLite (`aiosqlite`) per utenti e API key. CRUD utenti con bcrypt password hashing, generate/revoke/resolve API key (SHA256 hash, formato `sk-jarvis-<base64>`). Auto-seed safety net `ensure_admin_exists()` per bootstrap admin default
- **JWT Auth module:** Nuovo `jarvis/auth.py` — token creation/verification (PyJWT), FastAPI dependencies (`get_current_user`, `require_auth`, `require_admin`), auth endpoints (`POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`). Token letto da cookie httpOnly `access_token` o header `Authorization: Bearer`
- **Profile self-service API:** Nuovo `jarvis/routes/profile.py` — cambio password, list/create/revoke API key (con `rotate` flag), link/unlink Telegram ID
- **Admin user management API:** Nuovo `jarvis/routes/users.py` — CRUD utenti per admin: create/list/update/delete, activate/deactivate
- **Admin panel URL:** URL primario cambiato a `/admin/`. `/dashboard` redirect 301. Login page su `/admin/login` (standalone `login.html`). Nuove viste: Users (admin CRUD) e Profile (self-service password, API key, Telegram)
- **JWT_SECRET auto-persist:** `config.py` genera e scrive automaticamente `JWT_SECRET` nel `.env` se mancante
- **Bug fix: API key 500:** `key_obj["prefix"]` → `key_obj["key_prefix"]` in `routes/profile.py` (colonna DB `key_prefix`, non `prefix`)
- **Bug fix: Users/Profile view layout:** Div spostati dentro `<div class="main-content">` — erano fuori da `app-layout`, causando rendering in fondo alla pagina
- **Documentazione:** AGENTS.md, README.md aggiornati con nuovi moduli, auth flow, URL struttura

### v9.7.0 (2026-07-18) — Synaptiq Watchdog Automation + Documentazione Completa
- **Synaptiq Watchdog Automation:** `notify_file_event()` hook in RAG queue worker → debounce 30s per-project → `initial_analysis()` background task con grafo strutturale
- **Synaptiq Engine completo:** `synaptiq_engine.py` con hybrid search (vettori + PageRank grafo), dead code analysis, impact analysis, community detection. Grafo strutturale con nodi File/Function/Class e archi imports/calls/inherits
- **README riscritto:** Nuova sezione `✨ Features Complete` con matrice esaustiva di tutte le feature per categoria (Core AI, RAG, Memoria, Prompt Builder, Telegram, Agent Loop, Scheduling, Web, Infrastruttura)
- **Full Message Pipeline:** Diagramma ASCII completo end-to-end dal messaggio utente alla risposta LLM, con tutti i 9 step: Routing → Pipeline Tracer → Gatekeeper → Context Gathering (parallelo) → Super-prompt Assembly → Generazione → Streaming + Tag Processing → Tool-calling Loop → Output
- **Synaptiq Engine documentato:** Sezione dedicata `🧬 Synaptiq Engine` come componente #13, con diagramma flusso, tabella 4 modalità di ricerca, struttura grafo nodi/archi/metriche
- **Status table aggiornata:** Data 2026-07-18, Synaptiq Engine v2.0.5 row aggiunta

### v9.6.0 (2026-07-16) — MCP Server v2 + compressione ottimizzata + prompt format rules
- **MCP Server v2 Streamable HTTP**: nuovo endpoint `/api/mcp/v2` conforme MCP Streamable HTTP (RFC 2025-11-25). 8 tool + 7 resources. Rimossi vecchi endpoint SSE (`/api/mcp/sse`, `/api/mcp/message`).
- **Model info rewrite**: `get_telemetry_model()` ora legge da `config.py` invece che dal motore. Sync in `_mcp_handlers.py`. `GATEKEEPER_N_GPU_LAYERS` per offload GPU opzionale.
- **`_strip_thinking()`**: nuova funzione in `llm_engine.py` che rimuove tag `<think>`, analisi strutturate numerate e meta-ragionamenti dalle risposte del Gatekeeper Qwen3.5. Applicata in `extract_content()`, `compress_prompt()` e su ogni risposta LLM.
- **Compressor prompt riscritto**: `CAVEMAN_COMPRESSOR_SYSTEM_PROMPT` ora include esempio concreto INPUT/OUTPUT per guidare Qwen3.5 verso compressione reale invece di analisi.
- **Prompt format rules**: system prompt aggiornato con regole esplicite per tabelle Markdown, code block, grassetto. Sezione finale `---` con Riepilogo/Attenzione richiesta in ogni risposta.
- **Telemetry prompt tracing**: `PipelineTrace` ora include campi `system_prompt`, `rag_context`, `user_content`, `compressed_text`, `llm_response` per debug completo della pipeline.
- **`finalize_trace` parameter**: `build_omniscient_prompt()` supporta `finalize_trace=False` per uso esterno (MCP chat_send).
- **fix: options=None**: bug in `ollama_chat()` che causava errore quando `options` era nullo.
- **AGENTS.md**: regola n.9 (non riavviare Jarvis autonomamente), nota MCP diagnostic per agenti DEVs.

### v9.5.0 (2026-06-30) — TagSafeStream: fix leak tag XML nello streaming + documentazione completa
- **TagSafeStream introdotto**: nuova classe state machine in `tag_processor.py` che previene la fuga di tag XML incompleti (`<NOTIFY_ONCE>`, `<CONFIDENCE>`, ecc.) quando il LLM genera token uno alla volta. Mantiene stato `_in_tag`/`_sc_pending` tra chunk successivi e yielda solo contenuto safe
- **3 endpoint streaming aggiornati**: `openai_router.py`, `openai/chat.py`, `main.py` (entrambi `/api/chat` e `/api/generate`) ora usano `TagSafeStream.process()` invece di `strip_action_tags()` per ogni chunk
- **Side effects preservati**: `process_response_tags(full_text)` a fine stream continua a ricevere il testo completo con tag per salvataggio memoria, scheduling notifiche, esecuzione comandi
- **Documentazione espansa**: README.md e AGENTS.md aggiornati con tabella completa dei 21 tag XML (formato, visibilità, self-closing, descrizione), lista endpoint OpenAI completa (25 endpoint Assistants/Threads/Runs), e nota tecnica sul funzionamento dello streaming

### v9.4.0 (2026-06-29) — Refactor OpenAI in sottopacchetto + DB race fix
- **Refactor OpenAI:** `openai_router.py` → pacchetto `openai/` con 17 moduli. Lazy import tramite `init_openai_routes()`, init ritardato nell'lifespan
- **Assistants API:** Nuovi endpoint per Assistants, Threads, Runs, Vector Stores, Files, Uploads
- **DB race condition fix:** `asyncio.Lock` + double-check in `get_db()` di `openai/state.py` — risolve `RuntimeError: OpenAIDatabase not initialised` su richieste concorrenti
- **Audio API:** Aggiunto endpoint `/v1/audio/translations` (forced en); `/v1/audio/speech` migliorato
- **Images API:** Stub `/v1/images/*` (generations, edits, variations) con errore 400 standard OpenAI
- **Reranker modulare:** Estratto `rag_reranker.py` da `rag.py`: Qwen3-Reranker (transformers fp16 CPU) + fallback FlashRank ONNX
- **Cache semantica:** Estratto `rag_cache.py` da `rag.py`: `semantic_cache_search/store/clear`, `save_web_knowledge`, `search_web_knowledge`
- **Telegram formatting:** Estratto `telegram_format.py` da `tag_processor.py`: `telegram_safe_format()` con escape MarkdownV2/Markdown
- **Dashboard template:** Estratto `dashboard_template.py` da `dashboard.py`: template HTML/CSS/JS con Chart.js, Sigma.js, stile cyberpunk
- **Documentazione:** AGENTS.md e README.md aggiornati con nuovo pacchetto e fix

### v9.3.0 (2026-06-28) — OpenAI API completa + codebase cleanup
- **OpenAI API:** Implementati 6 nuovi endpoint: `/v1/completions`, `/v1/embeddings`, `/v1/audio/transcriptions`, `/v1/audio/speech`, `/v1/models/{model_name}`, `/v1/moderations`
- **main.py:** Da 967 a 1497 righe (+55%) — nuovi Pydantic models, streaming SSE, faster-whisper, gTTS
- **Codebase cleanup:** Rimossi `scratch/` (script orfani), `__pycache__/` dalla sorgente, symlink rotti in `documents/`
- **Documentazione:** README e AGENTS.md aggiornati con nuovi endpoint e struttura file attuale
- **docker-compose.yml:** Rimosso (superseduto dalla split vps.yml + worker.yml); deploy_vps.sh aggiornato a vps.yml

### v9.2.0 (2026-06-24) — Analisi completa + Architettura Provider
- **README:** Analisi completa e approfondita di tutti i 14 moduli Jarvis
- **Architettura:** Documentati componenti, flussi e dipendenze
- **Provider Esterni:** Valutata e pianificata integrazione provider cloud (Gemini)

### v9.1.0 (2026-06-23) — CUDA 13.0 Overlay + GPU Inference stabile
- **CUDA 13.0 overlay:** Pacchetti overlay su base 12.2 per driver 580.159.03
- **llama-cpp-python:** Build da GitHub main con GGML_CUDA=on, CMAKE_CUDA_ARCHITECTURES=86
- **GPU:** Inferenza stabile con n_gpu_layers=15, flash_attn=true
- **.dockerignore:** Esclusi modelli (8.7GB) dal build context
- **Modello:** Qwen3.5-4B-UD-Q4_K_XL.gguf, Qwen3-Embedding-0.6B-Q8_0

### v9.0.0 (2026-06-19) — Architettura Master/Worker
- **Architettura:** Migrazione da single-node a Master/Worker con VPN Tailscale
- **Networking:** Rimosso Ngrok, connettività via Tailscale WireGuard
- **Telegram:** Centralizzato sul Master — TELEGRAM_ENABLED=false sul Worker
- **llm_engine.py:** chat_format=None, n_gpu_layers e n_ctx da .env
- **Dockerfile:** Build llama-cpp-python da master GitHub per Gemma 4
