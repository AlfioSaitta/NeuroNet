# Changelog

Tutte le modifiche significative a NeuroNet/Jarvis sono documentate in questo file.

---

### v9.12.0 (2026-08-03) — Hardware Identity Block + Fix MCP tool imports

- **`core/hardware.py` (NUOVO):** Rilevamento identità hardware del server via comandi di sistema, **solo stdlib** (testabile standalone, nessuna catena di import pesante):
  - GPU + VRAM + driver via `nvidia-smi --query-gpu=name,memory.total,memory.free,driver_version`
  - CPU model via `/proc/cpuinfo` + threads via `os.cpu_count()`
  - RAM totale/disponibile via `/proc/meminfo` (kB → GiB), fallback `os.sysconf`
  - Hostname via `socket.gethostname()`
  - Cache `_HW_CACHE`, mai eccezioni (fallback "n/d" / "non rilevata"). API: `detect_hardware()`, `get_hardware_info()`, `get_hardware_block()`
- **`core/lifecycle.py`:** `detect_hardware()` eseguito all'avvio post-warmup (`await asyncio.to_thread`) con log `🖥️ Hardware rilevato:` — non bloccante, try/except, mai critico
- **`agent/prompt.py` — Hardware Identity Block:** Nuovo helper `_hardware_identity_block()` (concatenato a runtime, non nelle costanti module-level valutate all'import) che genera il blocco:
  ```
  [HARDWARE IDENTITY — REAL hardware of the Jarvis server]
  - Hostname / GPU / CPU / RAM
  If the user asks about your hardware, models, or setup, answer using THESE real values above. Never invent, never deflect.
  ```
  Iniettato in **8 rami** del system prompt: `_build_final_prompt` is_raw e non-raw, concise pipeline, greeting, web (con [WEB DATA]), general senza web, e **meta** (fix)
- **Fix ramo meta (trace `d2811fb00043`):** "Che hardware hai?" veniva classificato come intent `meta` e quel ramo non iniettava alcun system prompt → il modello vedeva solo datetime + lista progetti e inventava ("Apple M2 Pro Mac"). Ora il ramo meta inietta `GENERAL_CONVERSATION_SYSTEM` + hardware block, come greeting/general/concise
- **Fix MCP tool imports (`api/mcp/server_v2.py`):** 3 tool MCP con import rotti riparati:
  - `jarvis_rag_search` → `rag.engine.search_documents` (era `hybrid_search` inesistente)
  - `jarvis_memory_search` → `state.memory.search` via `mem0_executor` (era `memory.engine.search_memories` inesistente)
  - `jarvis_web_search` → SearXNG diretto via `state.http_client` + `SEARXNG_HOST` (era `rag.web_search.web_search` inesistente)
- **Verifica live post-riavvio (trace `4c7b101fa70b`):** risposta con RTX 3050 Ti / i5-11300H / 15.4 GiB reali, `system_prompt` contiene `HARDWARE IDENTITY`, `prompt_tokens` 175→552. Test standalone ramo meta (mock intent=meta) PASS. py_compile OK su hardware.py/lifecycle.py/prompt.py/server_v2.py
- **Documentazione:** AGENTS.md (nuovo modulo core, sezione cronologia 03/08, tabella completato), README.md, COMPONENTS.md, PIPELINE.md, CHANGELOG.md aggiornati

### v9.11.0 (2026-08-02) — Intent Understanding: router GBNF, 10 handler, MCP Reasoning Leak Fix

- **`agent/intent_router.py` (VERIFICATO):** `classify()` — tier-0 fast-path greeting (26ms) → cache LRU 60s → LLM GBNF (18 intent) → fallback regex. `IntentResult` contratto unico, `SLOT_EXTRACTORS` per intent, `INTENT_THRESHOLDS` (read 0.60 / write 0.70), `DISPATCH_TABLE` + `dispatch()`. Benchmark 100% intent (69/69) + 100% slot (67/67). Grep legacy (`GatekeeperResult`/`to_gatekeeper_result`/`extended_intent`/`INTENT_ROUTER_MODE`) = 0
- **`agent/intent_handlers.py` (ESTESO):** 10 intent handler (`schedule`/`memory`/`task`/`git`/`ssh`/`transcribe`/`fetch`/`translate`/`config`/`maintenance`) con firma unificata `(result, context)` + `register_handlers()`. Op distruttive via `_confirm_or_pending` (CONFIRM_REQ token-based, timeout 300s). Whitelist SSH read/write, mask segreti config
- **`agent/context_compressor.py` (NUOVO):** Estratto da `prompt.py` (92 righe) — `compress()` + `compress_concise()`, `COMPRESSOR_MIN_CHARS=1000`, 2 call site migrati
- **Rename `GATEKEEPER_*`→`COMPRESSOR_*`:** config, llm_engine, settings_manager, .env migrate + backup `.env.bak`. Rename `GatekeeperStats`→`IntentStats` (telemetry, state, rotta `/api/telemetry/intent`, MCP tool `get_intent_stats`, risorsa `jarvis://intent/stats`, dashboard) senza alias
- **MCP Reasoning Leak Fix (`api/mcp/server_v2.py`):** Helper `_run_chat_pipeline()` condiviso da `chat_send`/`jarvis_chat` — applica `configura_richiesta_agente()` (enable_thinking + logit_bias) e `strip_action_tags()` sul content. `agent/tags.py`: gestione chiusura `</think>` orfana in `strip_thinking_blocks()` + pattern plain `<think>...</think>` per famiglia qwen
- **Fix E2E slot `message` schedule:** estrazione dopo preposizione `di` (`\bdi\s+(.+)`) — nessuna whitelist verbi. 31/31 test PASS, verificato live (trace `3695c9169921`)
- **`main.py`:** `confirmation_mgr` token-based iniettato nel context di `dispatch()` (entrambi i rami) → write distruttive richiedono conferma. Fix duplicazione `handle_confirmation_token`
- **Test:** py_compile 13 file OK, `test_fast_path.py` 31/31, `test_intent_handlers_phase4.py` 31/31. Pushato su origin/main (`6a129cd..7766d25`, 6 commit atomici)
- **Documentazione:** AGENTS.md, README.md, PIPELINE/API_REFERENCE/SETUP/COMPONENTS/ARCHITECTURE allineati

### v9.10.0 (2026-07-29) — Module Extraction, Admin Panel Fixes, Cherry Studio Supporto

- **Module Extraction (7 moduli):** Estratti moduli da file oversized per rispettare limite 250 LOC:
  - `rag/chunking.py` (+437 righe) — AST chunking semantico via Tree-sitter, da `rag/engine.py`
  - `agent/tool_handlers.py` (+637 righe) — Handler tool-calling (file, shell, skills), da `agent/tools.py`
  - `agent/tag_handlers.py` (+320 righe) — Esecutori tag XML d'azione, da `agent/tags.py`
  - `core/reasoning.py` (+334 righe) — Logica ragionamento + chain-of-thought, da `main.py`
  - `core/chat_utils.py` (+146 righe) — Helper formattazione/validazione chat, da `main.py`
  - `core/telemetry_api.py` (+98 righe) — Endpoint API per telemetry, da `core/telemetry.py`
  - `core/qdrant_utils.py` (+51 righe) — `sanitize_project_name()` centralizzato, utility Qdrant
- **Admin Panel Fixes:**
  - `fetchLogs()` timeout portato a 30s (elimina richieste pendenti infinite)
  - `resetSettings` classList toggle corretto (non funzionava il cambio modalità Simple/Advanced)
  - Pulsanti restart funzionanti correttamente in Logs view
  - Race condition `_ingest_local_documents()` risolta con flag `_ingesting` + lock
  - Pulizia collezioni orfane Qdrant (step 4b in ingest): elimina collezioni senza progetto corrispondente
  - Rimosso endpoint orfano `/analytics/errors` (non più servito)
- **Cherry Studio Fix:**
  - `openai_api/chat.py`: `TagSafeStream` wrapper per Qwen/DeepSeek — sostituisce `[DONE]` mancante con `data: [DONE]`
  - Gatekeeper reasoning chiamato nel ramo corretto (non più saltato per Cherry Studio)
  - Supporto prefix `/no_think` per disabilitare reasoning dell'assistente
  - Rimozione tag `<reasoning>` dal response visibile allo streaming
- **Qdrant Utils:** Nuovo modulo `core/qdrant_utils.py` con `sanitize_project_name()` centralizzato (sostituisce logica duplicata)
- **build_omniscient_prompt retrocompatibilità:** `main.py` e `openai_api/chat.py` — funzione ora restituisce `tuple(messages, context)` invece di solo `messages`
- **Greeting Short-Circuit:** `main.py` — saluti puri (1-3 token, nessuna richiesta) bypassano LLM completamente: 26ms invece di 60-76s (0 token LLM consumati)
- **Documentazione:** AGENTS.md allineato a stato attuale con nuovi moduli, bug fix, cronologia aggiornata

### v9.9.0 (2026-07-27) — Hardware Profile Auto-Detection, FastEmbed, conversation_id fix

- **Hardware Profile Auto-Detection:** `model_profiles.py` nuova `_family_hardware_defaults()` mappa ogni famiglia GGUF (qwen/gemma/deepseek/llama/...) ai parametri GPU ottimali. `llm_engine.py`: `_load_chat_model()` rileva famiglia modello dall'header binario GGUF PRIMA del caricamento e applica `n_gpu_layers`, `flash_attn`, `n_ubatch` per famiglia. Per switchare modello basta cambiare `LLAMA_MODEL_PATH` — `N_GPU_LAYERS`/`flash_attn`/`n_ubatch` auto-detectati con priorità: .env > famiglia > fallback globale
- **FastEmbed ONNX CPU:** Sostituito subprocess `sentence-transformers` con FastEmbed nativo (`BAAI/bge-base-en-v1.5`). Zero VRAM consumata per embedding. Risolto crash `fused_gated_delta_net`
- **conversation_id fix (T4b):** `main.py` genera UUID se `conversation_id` non fornito nella request. Restituito in tutte le risposte (non-stream, streaming, timeout, confirm). Multi-turn ora funzionante tra richieste HTTP separate senza richiedere conversation_id manuale
- **AGENTS.md riscritto:** Nuova sezione §6 Hardware Profile Auto-Detection. Documentazione allineata a stato attuale. `.env` pulito dai vecchi override
- **Documentazione completa:** README.md, docs/ARCHITECTURE.md, docs/COMPONENTS.md, docs/PIPELINE.md, docs/SETUP.md, docs/API_REFERENCE.md, docs/CHANGELOG.md, docs/STRATEGY.md allineati al codice reale

### v9.8.1 (2026-07-20) — Light-Mode CSS, API Key UX, Auth Fixes

- **refactor(admin): theme-aware CSS with light-mode color variables:** 60+ hardcoded rgba values replaced with CSS custom properties for full light/dark theme support. Added `--primary-rgb`, `--secondary-rgb`, `--danger-rgb`, `--warning-rgb`, `--accent-rgb`, `--text-main-rgb`, `--text-muted-rgb` for rgba() usage. Added missing variables: `--card-bg`, `--input-bg`, `--surface-subtle`, `--border-subtle`, `--bg-elevated`. Chat, Settings, Graph, Management tables, Badges, Forms, Session sidebar, Buttons all converted to `rgba(var(--xxx-rgb), ...)` pattern
- **feat(profile): copy full API key from list via temporary cache:** New `_RECENT_KEYS` in-memory cache (5-min TTL) stores freshly generated keys. New `GET /api/auth/api-key/{key_id}/reveal` endpoint to retrieve cached key. `📋` button on each active key row calls the endpoint. Clipboard fallback (`execCommand('copy')`) for non-HTTPS environments
- **feat(profile): click-to-copy API key text:** Displayed key text in the warning card is now clickable to copy, with visual flash feedback
- **feat(profile): generate new key without revoking:** `➕ Generate New Key` button creates a key with `rotate: false`, keeping existing keys active. Users can always get a fresh copyable full key
- **fix(profile): hide revoked API keys:** `get_user_api_keys()` now filters with `AND is_active = 1` — revoked keys no longer clutter the profile page
- **fix(profile): remove misleading prefix copy:** Removed copy button from key list rows that copied only the prefix (confusing users into thinking it was the full key). Added clarifying text explaining prefix vs full key
- **fix(auth): reject invalid API keys even from localhost:** backward-compat pass-through for localhost previously allowed any Authorization header value (e.g. `Bearer dev`). Now requests with `Bearer` prefix but non-`sk-jarvis-*` key are always rejected with 401
- **fix(auth): missing import in reveal endpoint:** `reveal_api_key()` was missing `user_manager as um` import, causing `NameError` at runtime

### v9.8.0 (2026-07-20) — User Management & ACL: JWT Auth, Admin Panel, API Keys
- **UserManager SQLite:** Nuovo `jarvis/user_manager.py` — singleton SQLite (`aiosqlite`) per utenti e API key. CRUD utenti con bcrypt password hashing, generate/revoke/resolve API key (SHA256 hash, formato `sk-jarvis-<base64>`). Auto-seed safety net `ensure_admin_exists()` per bootstrap admin default
- **JWT Auth module:** Nuovo `jarvis/auth.py` — token creation/verification (PyJWT), FastAPI dependencies (`get_current_user`, `require_auth`, `require_admin`), auth endpoints (`POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`). Token letto da cookie httpOnly `access_token` o header `Authorization: Bearer`
- **Profile self-service API:** Nuovo `jarvis/routes/profile.py` — cambio password, list/create/revoke API key (con `rotate` flag), link/unlink Telegram ID
- **Admin user management API:** Nuovo `jarvis/routes/users.py` — CRUD utenti per admin: create/list/update/delete, activate/deactivate
- **RAG ACL filtering:** `search_documents()` e `list_rag_projects()` filtrano per `allowed_projects` dell'utente. Non-admin vedono solo i progetti autorizzati
- **Telegram DB-backed auth:** Sostituito static `ALLOWED_USERS` con DB-backed user authentication per bot Telegram, con cache 5 min
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
- **Dashboard modularizzato:** `dashboard_template.py` rifattorizzato in `admin_panel/` sub-package. 6 JS moduli, style.css, index.html separati. URL `/admin/` (primario), `/dashboard` (redirect)
- **graph.js deduplicato:** `renderSigmaGraph()` condivisa tra `openGraphModal()` e `openMemoryGraphModal()`. 856→689 righe (-19.5%)
- **index.html inline style -71%:** ~200 → 57 inline style, 30+ utility classi CSS
- **telemetry.js refactor:** `fetchStats()` splittata in 10 funzioni dominio-specifiche + Page Visibility API
- **Synaptiq Migration bug fixes:** 6 bug risolti: import crash (CRITICAL), KeyError su meta (MEDIUM), brace extra dashboard (MEDIUM), badge OFFLINE→IDLE (LOW), pathspec deprecation (LOW), label CodeGraph→Code Context (LOW)

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
- **Codebase cleanup:** Rimossi `scratch/` (script orfani), `__pycache__` dalla sorgente, symlink rotti in `documents/`
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

(End of file - total 99 lines)
