# Full Message Pipeline: Input → Response

Di seguito il flusso completo che ogni messaggio utente attraversa, dal momento in cui arriva all'endpoint fino alla risposta LLM elaborata e restituita.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  INPUT: Messaggio utente                                                    │
│  (Telegram / HTTP / OpenAI API / MCP)                                       │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  1. ROUTING (main.py)                                                        │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ Endpoint API → handler specifico:                                       ││
│  │  ├── POST /api/chat       → handle_chat()                               ││
│  │  ├── POST /api/generate   → handle_generate()                           ││
│  │  ├── POST /v1/chat/*      → openai.chat.chat_completions()               ││
│  │  ├── POST /v1/completions → openai.completions.completions()            ││
│  │  ├── POST /api/embed      → handle_embed()                              ││
│  │  └── Telegram message     → telegram_bot.handle_telegram_message()      ││
│  └─────────────────────────────────────────────────────────────────────────┘│
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
│  3. GATEKEEPER (prompt_builder.py)                                          │
│                                                                              │
│  build_omniscient_prompt(user_message)                                       │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ LLM Gatekeeper — Classifica intento:                                    ││
│  │  ├── Keyword/Regex bypass (cache hit, comandi rapidi)                    ││
│  │  └── LLM grammar classification:                                        ││
│  │        ├── "progetto/codice"  → RAG + memoria + web                    ││
│  │        ├── "conversazione"    → solo memoria                            ││
│  │        └── "comando rapido"   → azione diretta                          ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                                                              │
│  → GatekeeperStats.record(intent, confidence, bypassed)                     │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  4. CONTEXT GATHERING (prompt_builder.py)                                   │
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
│  └────────┬───────────┘  └────────┬─────────┘  └──────────┬───────────────┘   │
│           │                      │                       │                    │
│           ▼                      ▼                       ▼                    │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ RAG DOCUMENTALE (se gatekeeper=True)                                    ││
│  │                                                                         ││
│  │  → Qdrant search (collezione progetto attivo)                           ││
│  │  → Reranker duale: Qwen3-Reranker → FlashRank (fallback)                ││
│  │  → Synaptiq Engine: hybrid search (vettori + grafo strutturale)         ││
│  │  → Cross-collection fallback se progetto specifico fallisce             ││
│  │  → Semantic Cache check (soglia cosine 0.88)                           ││
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
│  5. SUPER-PROMPT ASSEMBLY (prompt_builder.py)                               │
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
│  6. GENERAZIONE LLM (llm_engine.py)                                         │
│                                                                              │
│  LlamaEngine.generate_chat()                                                 │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ ROUTING INFERENZA:                                                      ││
│  │  ▸ Locale (llama-cpp-python) ← PRIORITARIO                             ││
│  │  ▸ EXTERNAL_GPU_URL configurato? → ping Worker (1.5s timeout)          ││
│  │      ├── OK → offload GPU via HTTP POST con meta nel body               ││
│  │      └── FAIL → fallback CPU locale                                     ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ PRE-PROCESSING:                                                         ││
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
│  7. STREAMING + TAG PROCESSING (tag_processor.py)                           │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ TagSafeStream — Anti-leak tag XML in streaming                         ││
│  │                                                                         ││
│  │  Ogni chunk LLM:                                                        ││
│  │  ├── TagSafeStream.process(chunk)                                       ││
│  │  │     ├── Se dentro tag → bufferizza (non yielda)                      ││
│  │  │     ├── Se tag completo → yielda testo safe                          ││
│  │  │     └── Se chunk successivo → riprende stato _in_tag                 ││
│  │  └── yield al client (SSE / Telegram / HTTP stream)                     ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                                                              │
│  A FINE STREAM:                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ process_response_tags(full_text) → side effects:                        ││
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
│  8. TOOL-CALLING LOOP (agent_tools.py) — Se risposta contiene tool_calls    │
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
│  │  Ogni tool 🛡️ → Telegram conferma utente (timeout 5 min)               ││
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
