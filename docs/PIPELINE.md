# Full Message Pipeline: Input → Response

Di seguito il flusso completo che ogni messaggio utente attraversa, dal momento in cui arriva all'endpoint fino alla risposta LLM elaborata e restituita.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  INPUT: Messaggio utente                                                    │
│  (Telegram / HTTP / OpenAI API / MCP)                                       │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
                                    ▼
                               ╔═══════════════════════════╗
                               ║  SHORT-CIRCUIT CHECK      ║
                               ║  (main.py / is_greeting)  ║
                               ║                           ║
                               ║  Se saluto puro:          ║
                               ║  1-3 token, nessuna       ║
                               ║  richiesta → risposta     ║
                               ║  immediata (26ms)         ║
                               ║  0 token LLM consumati    ║
                               ╚═══════════════════════════╝
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  1. ROUTING (main.py)                                                        │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ Endpoint API → handler specifico:                                       ││
│  │  ├── POST /api/chat          → handle_chat()                            ││
│  │  ├── POST /api/generate      → handle_generate()                        ││
│  │  ├── POST /api/embed         → handle_embed()                           ││
│  │  ├── POST /api/mcp/v2        → mcp_v2.handle_mcp_request()              ││
│  │  ├── POST /v1/chat/*         → openai.chat.chat_completions()            ││
│  │  ├── POST /v1/completions    → openai.completions.completions()          ││
│  │  ├── POST /api/auth/*        → auth.require_auth + endpoints            ││
│  │  ├── POST /api/dashboard/*   → dashboard.settings_router                ││
│  │  ├── GET/POST /api/projects/* → routes.projects_router                  ││
│  │  ├── GET/POST /api/users/*   → routes.users_router (admin only)         ││
│  │  └── Telegram message        → tg_bot.bot.handle_telegram_message()     ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                                                              │
│  In handle_chat() e openai.chat.chat_completions():                          │
│  build_omniscient_prompt() ora restituisce tuple (messages, context)         │
│  invece di solo messages — per retrocompatibilità con chiamate esistenti     │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  2. PIPELINE TRACER — Avvio tracciamento                                    │
│                                                                              │
│  start_step("keyword_bypass")                                                │
│  ├── Keyword bypass check (es. "memoria", "/web", "/docs")                  │
│  │     └── match → salta Gatekeeper LLM (bypass)                            │
│  └── PipelineTracer.start() → request_id univoco                             │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  3. GATEKEEPER (jarvis/agent/prompt.py) — 3-tier classification             │
│                                                                              │
│  build_omniscient_prompt(user_message)                                       │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ Tier 1 — Keyword/Regex bypass:                                          ││
│  │  ├── Cache hit, comandi rapidi, "/" prefix                              ││
│  │  └── Bypassa interamente il Gatekeeper LLM                              ││
│  │                                                                         ││
│  │ Tier 2 — Qwen3.5-0.8B classification (CPU, 4096 ctx):                  ││
│  │  ├── Classifica intento in: progetto/codice / conversazione / comando   ││
│  │  │   rapido                                                              ││
│  │  └── 6 few-shot esempi per accuracy                                     ││
│  │                                                                         ││
│  │ Tier 3 — Qwen3.5-0.8B compression:                                     ││
│  │  ├── Se progetto/codice → compressa prompt lungo in formato caveman     ││
│  │  ├── GATEKEEPER_MAX_CHARS=1500 guard per overflow                       ││
│  │  └── Pass-through se ratio compressione negativo                        ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                                                              │
│  → GatekeeperStats.record(intent, confidence, bypassed)                     │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  4. CONTEXT GATHERING (jarvis/agent/prompt.py)                              │
│                                                                              │
│  Fase parallela di arricchimento — ogni sorgente è indipendente:            │
│                                                                              │
│  ┌────────────────────┐  ┌────────────────────┐  ┌──────────────────────┐   │
│  │ WEB INTELLIGENCE   │  │ MEM0 RICERCA       │  │ PROGETTO ATTIVO     │   │
│  │                    │  │                    │  │                      │   │
│  │ Se /web o auto:   │  │ Se progetto attivo │  │ detect_project_in_   │   │
│  │  ┌──────────────┐ │  │  ┌──────────────┐ │  │ conversation()       │   │
│  │  │ SearXNG      │ │  │  │ Mem0.search  │ │  │  ├── Persist per     │   │
│  │  │ (metasearch  │ │  │  │ filtrato per  │ │  │  │   conversazione   │   │
│  │  │  anonimo)    │ │  │  │ user+project  │ │  │  └── Reset per       │   │
│  │  └──────┬───────┘ │  │  │ limit 5       │ │  │      conversaz.     │   │
│  │         │         │  │  └──────┬───────┘ │  │      generiche       │   │
│  │  ┌──────▼───────┐ │  │         │         │  └──────────────────────┘   │
│  │  │ Crawl4AI     │ │  │         ▼         │                              │
│  │  │ (scraper     │ │  │ ┌──────────────┐ │                              │
│  │  │  headless)   │ │  │ │ extract_     │ │                              │
│  │  └──────────────┘ │  │ │ memories()   │ │                              │
│  │                    │  │ └──────────────┘ │                              │
│  └────────┬───────────┘  └────────┬─────────┘  └──────────┬───────────────┘│
│           │                      │                       │                 │
│           ▼                      ▼                       ▼                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ RAG DOCUMENTALE (se gatekeeper=True)                                    ││
│  │                                                                         ││
│  │  → FastEmbed ONNX CPU embedding (BAAI/bge-base-en-v1.5, 768 dims)      ││
│  │  → Qdrant search (collezione progetto attivo)                           ││
│  │  → Reranker duale: Qwen3-Reranker → FlashRank (fallback)                ││
│  │  → Synaptiq Engine: hybrid search (vettori + grafo strutturale)         ││
│  │  → Cross-collection fallback se progetto specifico fallisce             ││
│  │  → Semantic Cache check (soglia cosine 0.96)                           ││
│  │  → File matching: include chunk con file path nel prompt                ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ BUDGET ALLOCATOR — Distribuzione dinamica contesto                      ││
│  │                                                                         ││
│  │  55% RAG │ 20% web │ 10% memoria │ 15% project tree                    ││
│  │  └── Max 15000 caratteri (≈11k tokens)                                 ││
│  └─────────────────────────────────────────────────────────────────────────┘│
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  5. SUPER-PROMPT ASSEMBLY (jarvis/agent/prompt.py)                          │
│                                                                              │
│  Costruzione prompt XML strutturato con 7 sezioni:                          │
│                                                                              │
│  <system_instructions>                                                       │
│    [regole formato: tabelle, code block, grassetto, sezione finale ---]      │
│  </system_instructions>                                                      │
│  <user_memory> [memorie episodiche del tuo progetto] </user_memory>          │
│  <todo_list> [task pendenti] </todo_list>                                   │
│  <project_tree> [struttura file del progetto] </project_tree>               │
│  <retrieved_code> [chunk RAG + Synaptiq grafo] </retrieved_code>            │
│  <web_data> [risultati web search] </web_data>                              │
│  <active_project> [project_name] </active_project>                          │
│  <user>messaggio utente originale</user>                                     │
│                                                                              │
│  → finalize_trace parametro decide se chiudere il PipelineTracer            │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  6. GENERAZIONE LLM (jarvis/core/llm_engine.py)                             │
│                                                                              │
│  LlamaEngine.generate_chat()                                                 │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ HARDWARE PROFILE AUTO-DETECTION:                                        ││
│  │  ▸ detect_model_family() → legge header GGUF (qwen/gemma/deepseek/...)  ││
│  │  ▸ _family_hardware_defaults() → parametri ottimali per famiglia        ││
│  │  ▸ n_gpu_layers, flash_attn, n_ubatch risolti con priorità:             ││
│  │      1. .env esplicito  2. default per famiglia  3. fallback globale    ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ INFERENZA LOCALE (llama-cpp-python):                                    ││
│  │  ├── Qwen3.5-4B (full GPU, -1 layer, flash_attn=true, ~35-40 tok/s)   ││
│  │  ├── Thinking Mode: inject <|think|> nel system prompt se supportato    ││
│  │  ├── PriorityLock.acquire(priority=0) → attesa se embedding in corso    ││
│  │  └── Model.generate() → streaming o full response                       ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                                                              │
│  → PipelineTracer.start_step("inference") → llm_calls[] registro            │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  7. STREAMING + TAG PROCESSING (jarvis/agent/tags.py)                       │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ TagSafeStream — Anti-leak tag XML in streaming                          ││
│  │                                                                         ││
│  │  Ogni chunk LLM:                                                        ││
│  │  ├── TagSafeStream.process(chunk)                                       ││
│  │  │     ├── Se dentro tag → bufferizza (non yielda)                      ││
│  │  │     ├── Se tag completo → yielda testo safe                          ││
│  │  │     └── Se chunk successivo → riprende stato _in_tag                 ││
│  │  ├── Per Qwen/DeepSeek: sostituisce [DONE] mancante con data: [DONE]    ││
│  │  │     (fix Cherry Studio — risposte vuote senza terminatore SSE)       ││
│  │  ├── Rimuove tag <reasoning> dal response visibile allo streaming       ││
│  │  ├── Supporta prefix /no_think per disabilitare reasoning assistente    ││
│  │  └── yield al client (SSE / Telegram / HTTP stream)                     ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                                                              │
│  A FINE STREAM:                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ process_all_tags(full_text) → side effects:                                ││
│  │                                                                         ││
│  │  ├── <MEMORY>testo</MEMORY>       → Mem0.save()                        ││
│  │  ├── <SCHEDULE>cron|msg</SCHEDULE> → cron_agent.add_job()              ││
│  │  ├── <NOTIFY_ONCE>data|msg</...>  → DateTrigger job                    ││
│  │  ├── <NOTIFY_IN>min|msg</...>     → timer relativo                     ││
│  │  ├── <SSH>server|cmd</SSH>        → asyncssh.exec()                    ││
│  │  ├── <TODO_ADD>desc|prio|...</...> → task_manager.add_todo()           ││
│  │  ├── <TODO_DONE>id</TODO_DONE>    → task_manager.mark_done()           ││
│  │  ├── <WEB>query</WEB>             → web_search.search() + reinject     ││
│  │  ├── <FILE>path</FILE>            → read_file() + reinject             ││
│  │  ├── <THINK_DEEP/>                → modalità ragionamento approfondito ││
│  │  ├── <CACHE_CLEAR/>               → rag_cache.clear()                  ││
│  │  ├── <RAG>project</RAG>           → RAG forzato su progetto            ││
│  │  ├── <EMOTION>stato</EMOTION>     → stato UI                           ││
│  │  ├── <CONFIDENCE>0.95</...>       → autovalutazione                    ││
│  │  ├── <ASK>domanda</ASK>           → reverse interaction user           ││
│  │  ├── <SUMMARY target="uid">text</...> → memoria altro utente           ││
│  │  ├── <BRANCH>proj|branch</BRANCH> → git checkout                      ││
│  │  ├── <COMMIT>message</COMMIT>     → git commit                         ││
│  │  └── <EXEC>timeout|cmd</EXEC>     → shell readonly (whitelist)         ││
│  └─────────────────────────────────────────────────────────────────────────┘│
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  8. TOOL-CALLING LOOP (jarvis/agent/tools.py) — Se risposta contiene tool_calls │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ execute_tool_call(tool_name, arguments):                                ││
│  │                                                                         ││
│  │  ├── write_file(path, content)    → scrive file 🛡️ conferma            ││
│  │  ├── read_file(path)              → legge file (max 8K caratteri)       ││
│  │  ├── delete_file(path)            → cancella file 🛡️ conferma          ││
│  │  ├── replace_in_file(SEARCH/REPLACE) → patch 🛡️ conferma               ││
│  │  ├── run_shell_command(cmd)       → bash (60s timeout) 🛡️ conferma     ││
│  │  └── skill_* (dinamici da YAML)   → skill personalizzata 🛡️ conferma  ││
│  │                                                                         ││
│  │  Ogni tool 🛡️ → Telegram/HTTP conferma utente (timeout 5 min)          ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                                                              │
│  → Risultato tool reiniettato → nuovo giro LLM                              │
│  → Loop fino a max_tool_rounds o risposta finale                             │
│  → PipelineTracer count_tool_call()                                          │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  9. POST-PROCESSING & OUTPUT                                                │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ 1. _strip_thinking() → rimuove <think> e metacognizione residua         ││
│  │ 2. _compress_prompt() → compressione caveman se richiesta              ││
│  │ 3. Telegram safe formatting → escape MarkdownV2 per Telegram            ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                                                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────────────────┐  │
│  │ Telegram message │  │ HTTP SSE stream   │  │ OpenAI JSON response      │  │
│  │ (MarkdownV2)     │  │ (chunked per tok) │  │ (choices[0].message)      │  │
│  └──────────────────┘  └──────────────────┘  └───────────────────────────┘  │
│                                                                              │
│  → PipelineTracer.end() → trace completato → ring buffer (ultimi 500)      │
└──────────────────────────────────────────────────────────────────────────────┘
```
