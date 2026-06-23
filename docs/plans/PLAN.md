# 📋 MASTER PLAN.md — Piano di Miglioramenti e Ottimizzazioni

> **Progetto:** Chameleon Cognitive Stack — Ecosistema AI Locale  
> **Versione attuale:** 8.6.7  
> **Data:** 2026-06-12  
> **Autore:** Analisi automatica del codebase + Agenti RAG & Endpoints Analysts

---

## Indice

1. [🔴 Priorità Alta — Sicurezza & Stabilità Critica](#-1-priorità-alta--sicurezza--stabilità-critica)
2. [🟠 Performance & VRAM](#-2-performance--vram)
3. [🟡 RAG Pipeline](#-3-rag-pipeline)
4. [🔵 Architettura & Endpoint](#-4-architettura--endpoint)
5. [🟣 Infrastruttura Docker](#-5-infrastruttura-docker)
6. [🟢 Bot Telegram](#-6-bot-telegram)
7. [⚪ Osservabilità & Monitoraggio](#-7-osservabilità--monitoraggio)
8. [💡 Nuove Feature](#-8-nuove-feature)

Legenda impatto: `🏋️ Alto` `🏃 Medio` `🚶 Basso`  
Legenda sforzo: `⏱️ <1h` `⏰ 1-4h` `🕐 4-8h` `📅 1-2gg` `📆 >2gg`

---

## 🔴 1. Priorità Alta — Sicurezza & Stabilità Critica

### 1.1 ✅ — File Handle Leak e TOCTOU Race in Ingestion

  - **Problema:** In `rag.py:256`, l'apertura del file `open(fp, 'rb').read()` avviene senza un blocco `with`. Se si verifica un'eccezione, il file handle viene perso (leak). Inoltre, il file potrebbe cambiare tra l'hash e la rilettura (TOCTOU).
  - **Impatto:** 🏋️ Alto — Esaurimento dei file descriptor (`Too many open files`), crash.
  - **Sforzo:** ⏰ 1-4h
  - **Soluzione:** Usare `Path(fp).read_bytes()` che gestisce l'apertura/chiusura in modo sicuro, o `aiofiles`. Leggere il file una volta sola e passarlo in memoria.

---

### 1.2 ✅ — I/O sincrono bloccante dentro il Lock Globale

  - **Problema:** Sempre in `rag.py:252-257`, l'hash viene computato leggendo gigabyte di file *mentre si detiene lo `state_lock`*. Questo paralizza l'intero event loop e affama tutte le altre richieste (incluso Telegram e RAG query) finché l'ingestion non finisce.
  - **Impatto:** 🏋️ Alto — Paralisi totale dell'app durante i ricaricamenti.
  - **Sforzo:** ⏰ 1-4h
  - **Soluzione:** Spostare la lettura e l'hash *fuori* dal blocco `async with state.state_lock`. Il lock deve proteggere solo l'accesso in memoria al dizionario `rag_state`.

---

### 1.3 ✅ — Vulnerabilità CORS con Credenziali (NUOVO)

  - **Problema:** In `main.py`, hai configurato `allow_origins=["*"]` insieme a `allow_credentials=True`. Questa configurazione è **espressamente proibita** dallo standard CORS e i browser moderni rifiuteranno le richieste credentialed.
  - **Impatto:** 🏋️ Alto — Errori CORS frontend o falle di sicurezza se esposto.
  - **Sforzo:** ⏱️ <1h
  - **Soluzione:** Selezionare origini specifiche (es. `["http://localhost:8080"]`) se si usano le credenziali, oppure settare `allow_credentials=False`.

---

### 1.4 — Endpoint `/api/reset-all` senza Autenticazione (Accetta GET)

  - **Problema:** `reset-all` accetta richieste `GET` non autenticate. Un browser prefetch o un crawler può cancellare l'intero database Qdrant accidentalmente.
  - **Impatto:** 🏋️ Alto — Perdita totale di memoria e documenti.
  - **Sforzo:** ⏱️ <1h
  - **Soluzione:** Rimuovere `GET`, forzare `POST`, e richiedere un token/API key in intestazione.

---

### 1.5 ✅ — Shutdown non sicuro (Set Mutation)

  - **Problema:** Modifica del set `state.background_tasks` durante la sua iterazione in `main.py`, causando `RuntimeError` allo spegnimento.
  - **Impatto:** 🏃 Medio — Spegnimento fallito, database non chiusi propriamente.
  - **Sforzo:** ⏱️ <1h
  - **Soluzione:** `tasks_to_cancel = list(state.background_tasks)`.

---

### 1.6 ✅ — Mancanza di validazione input e Rate Limiting

  - **Problema:** Nessun limite dimensione body, niente Pydantic validation (es. missing `messages` causa errori), niente rate limiting.
  - **Impatto:** 🏃 Medio — Denial of Service, OOM crash.
  - **Sforzo:** ⏰ 1-4h
  - **Soluzione:** Usare `slowapi` e definire `pydantic.BaseModel` per `/api/chat`.

---

### 1.7 ✅ — Crash Mem0 per Telemetria (NUOVO)

  - **Problema:** Crash della libreria `mem0` in fase di inizializzazione a causa del blocco della connessione a `app.posthog.com` in ambienti ristretti, generando `ReadTimeout` e mandando giù il proxy.
  - **Impatto:** 🏋️ Alto — Container in riavvio continuo o instabilità del RAG.
  - **Sforzo:** ⏱️ <1h
  - **Soluzione:** Disabilitata la telemetria iniettando la variabile `MEM0_ENABLE_TELEMETRY=false` in `docker-compose.yml` e implementato loop di retry in `memory.py` per avviare `mem0` in modo asincrono.

---

### 1.8 ✅ — Perdita Contesto Tool Calling nell'Agente Telegram (NUOVO)

  - **Problema:** L'agente esegue i tool e stampa l'esito per l'utente, ma nell'`history` della sessione salva solo l'ultimo testo e *non* il blocco di interscambio `tool_calls`/`tool_responses`.
  - **Impatto:** 🏋️ Alto — Al messaggio successivo, l'LLM dimentica completamente i file che ha appena letto o le modifiche che ha applicato tramite i tool.
  - **Sforzo:** ⏱️ <1h
  - **Soluzione:** Mantenere una lista cumulativa temporanea nel loop e fare l'extend dell'intero blocco (incluso il JSON del tool) nella cronologia utente alla fine dell'iterazione.

---

### 1.9 ✅ — Race Condition Sessioni Telegram (Concurrency) (NUOVO)

  - **Problema:** L'accesso al dizionario `user_sessions[user_id]["messages"]` in `telegram_bot.py` non è thread-safe. Messaggi sovrapposti dell'utente mentre l'LLM è impegnato in un loop di 60 secondi corrompono o sfalsano l'history.
  - **Impatto:** 🏋️ Alto — Allucinazioni e context overflow causati dalla concorrenza di messaggi simultanei.
  - **Sforzo:** ⏰ 1-4h
  - **Soluzione:** Associare un `asyncio.Lock()` per ogni `user_id` e attendere il completamento della risposta precedente prima di processare il messaggio successivo.

---

### 1.10 ✅ — Rischio Corruzione `rag_state.json` (NUOVO)

  - **Problema:** In `rag.py`, `_save_state_unsafe()` viene chiamato solo alla fine del loop di ingestion (che su repo enormi può durare ore). Se il container muore, tutti i vettori Qdrant generati sono orfani del `rag_state.json`, costringendo al re-embedding totale al successivo riavvio.
  - **Impatto:** 🏋️ Alto — Desincronizzazione RAG fatale su failure di container/host.
  - **Sforzo:** ⏱️ <1h
  - **Soluzione:** Chiamare il salvataggio o periodicamente (es. ogni 10 file) o delegare lo storage su un key-value store persistente (Redis) come proposto nel task 8.34.

---

## 🟠 2. Performance & VRAM

### 2.1 ✅ — Assenza di Batching nell'API Ollama (NUOVO)

  - **Problema:** `get_embedding` esegue richieste HTTP individuali per ogni singolo chunk. Ollama supporta l'invio di un array di prompt per l'embedding batch.
  - **Impatto:** 🏋️ Alto — Latenza estrema nell'ingestion per l'overhead HTTP.
  - **Sforzo:** ⏰ 1-4h
  - **Soluzione:** Modificare `get_embedding` per accettare liste di testi (`List[str]`) e fare un'unica chiamata POST.

---

### 2.2 ✅ — Pre-check MTIME prima dell'hashing (Risparmio I/O)

  - **Problema:** Ogni file viene letto e hashato anche se la data di modifica non è cambiata.
  - **Impatto:** 🏃 Medio — I/O e CPU sprecati.
  - **Sforzo:** ⏱️ <1h
  - **Soluzione:** Controllare prima `os.stat().st_mtime` e `st_size`.

---

### 2.3 ✅ — Salvare il JSON State dopo ogni singolo file

  - **Problema:** `_save_state_unsafe()` scrive su disco l'intero `rag_state` dict (potenzialmente MB) al completamento di *ogni* file, bloccando l'async loop sincronicamente.
  - **Impatto:** 🏋️ Alto — Amplificazione I/O distruttiva su codebase grandi.
  - **Sforzo:** ⏰ 1-4h
  - **Soluzione:** Salvare a batch (es. ogni 100 file) o usare SQLite/Redis per lo stato RAG.

---

### 2.4 ✅ — Discrepanza `num_ctx`

  - **Problema:** `Modelfile` imposta 16384, ma `config.py` e proxy forzano `4096`.
  - **Impatto:** 🏃 Medio — Risorse GPU preallocate sprecate.
  - **Sforzo:** ⏱️ <1h
  - **Soluzione:** Uniformare il parametro a `8192` o `16384` globalmente.

---

## 🟡 3. RAG Pipeline

### 3.1 ✅ — Incoerenza Qdrant Client (NUOVO)

  - **Problema:** In `rag.py:304-308`, la ricerca in Qdrant viene eseguita via richieste `httpx` raw, ignorando il client asincrono ufficiale `state.qdrant` che viene invece usato correttamente in ingestion.
  - **Impatto:** 🏃 Medio — Bypass di connection pooling, error handling e tipi.
  - **Sforzo:** ⏱️ <1h
  - **Soluzione:** Usare `await state.qdrant.search(...)`.

---

### 3.2 ✅ — AST Chunking Incompleto

  - **Problema:** Mancano linguaggi chiave (C/C++, Java, Rust, SQL, YAML) e nodi fondamentali in JS/TS (`class_declaration`, `arrow_function`), Python (`async_function_definition`), Go (`const`, `var`).
  - **Impatto:** 🏃 Medio — Molto codice moderno finisce nel fallback lineare.
  - **Sforzo:** 🕐 4-8h
  - **Soluzione:** Espandere la whitelist estensioni, includere parser mancanti e aggiungere nodi mancanti.

---

### 3.3 ✅ — Codice Top-Level (Preambolo) ignorato dall'AST

  - **Problema:** Import, costanti globali e config a livello di modulo non catturati dai nodi funzione/classe.
  - **Impatto:** 🏃 Medio — Contesto architetturale orfano.
  - **Sforzo:** ⏰ 1-4h
  - **Soluzione:** Aggiungere sistematicamente le prime 50 righe di ogni file come "Chunk 0" (Preambolo).

---

### 3.4 ✅ — Debouncing Watchdog Assente

  - **Problema:** I salvataggi di file IDE causano eventi a raffica, portando a ingestion duplicate del medesimo file contemporaneamente (race condition in `state_lock`).
  - **Impatto:** 🏃 Medio — Lavoro inutile e database vettoriale inquinato.
  - **Sforzo:** ⏰ 1-4h
  - **Soluzione:** Aggiungere un ritardo di debounce (1s) e rimuovere il file dalla coda se c'è un evento pendente per la stessa root.

---

### 3.5 ✅ — Ricerca Secondaria Dipendenze Debole

  - **Problema:** Vengono usate ricerche vettoriali sulle dipendenze passandole come prompt generico (`" ".join(deps)`), restituendo risultati non pertinenti.
  - **Impatto:** 🚶 Basso — La ricerca secondaria è inefficace.
  - **Sforzo:** ⏰ 1-4h
  - **Soluzione:** Usare un Filtro Esatto in Qdrant basato su string matching (`payload.filename MUST match dependency`), anziché similarità coseno vettoriale.

---

## 🔵 4. Architettura & Endpoint

### 4.1 ✅ — Pervasive Eccezioni Silenziate (NUOVO)

  - **Problema:** Errori RAG, errori parser Mem0, fallback LLM, crawl falliti... sono tutti chiusi con `except Exception: pass`. Degrado del sistema totalmente invisibile in console.
  - **Impatto:** 🏋️ Alto — Sistema apparentemente funzionante ma degradato.
  - **Sforzo:** ⏰ 1-4h
  - **Soluzione:** Sostituire *ogni* `pass` silenzioso con `logger.warning(...)`.

---

### 4.2 ✅ — Costo Gatekeeper LLM

  - **Problema:** Ogni query utente subisce 1-3 sec di latenza per classificazione LLM.
  - **Impatto:** 🏋️ Alto — Pessima UX chat.
  - **Sforzo:** ⏰ 1-4h
  - **Soluzione:** Implementare classificazione ibrida: Regex/Keywords primarie (0ms) -> Embedding Cosine Sim (50ms) -> LLM Fallback (1s).

---

### 4.3 ✅ — Budget Gestione Contesto

  - **Problema:** Troncamenti hardcoded in `prompt_builder.py` (`[:4500]`, `[:1500]`). Se il Web è vuoto, i 1500 char non vengono ridistribuiti al codice.
  - **Impatto:** 🏃 Medio — Sottoutilizzo del context window o crash.
  - **Sforzo:** 🕐 4-8h
  - **Soluzione:** Token Counter o `char_budget` condiviso con redistribuzione intelligente a serbatoi comunicanti.

### 4.4 ✅ — Carenze Compatibility OpenAI

  - **Problema:** La response di Ollama tradotta omette l'oggetto `usage`, il `finish_reason` in streaming, l'`index: 0` nei chunk, e non ha UUID unici.
  - **Impatto:** 🏃 Medio — Errori JSON parse in client rigidi.
  - **Sforzo:** ⏰ 1-4h
  - **Soluzione:** Conformare il JSON restituito esattamente allo standard Swagger OpenAI.

---

### 4.5 ✅ — Assenza Timeout Streaming Ollama (NUOVO)

  - **Problema:** Il proxy stream in `main.py` non ha timeout sui chunk. Se Ollama va in stallo a metà generazione o entra in loop infinito senza emettere newline, la connessione col client resta appesa indefinitamente.
  - **Impatto:** 🏃 Medio — Risorse e worker bloccati in Uvicorn in caso di blocco GPU.
  - **Sforzo:** ⏱️ <1h
  - **Soluzione:** Usare `asyncio.wait_for` nel ciclo `aiter_lines()` per imporre un idle_timeout di 30s tra un chunk e l'altro.

---

## 🟣 5. Infrastruttura Docker

### 5.1 — Mancanza Healthchecks

  - **Problema:** Compose `depends_on` senza conditions non garantisce avvii in ordine corretto. Il proxy parte prima che Ollama sia online.
  - **Impatto:** 🏋️ Alto — Possibili errori connessione all'avvio.
  - **Sforzo:** ⏰ 1-4h
  - **Soluzione:** Introdurre `healthcheck` su Ollama e Qdrant + `condition: service_healthy`.

### 5.2 — Sicurezza Immagine Docker

  - **Problema:** Runta da `root`, senza `.dockerignore`, size inutile per tools di build residui.
  - **Impatto:** 🏃 Medio — Sicurezza ridotta.
  - **Sforzo:** ⏰ 1-4h
  - **Soluzione:** Aggiungere un `.dockerignore`, refactoring su Multi-stage Build, e creare un utente `appuser` limitato.

### 5.3 ✅ — Eliminazione Dipendenza Ollama (Llama-cpp-python)

  - **Problema:** Il sistema dipendeva da un container Ollama esterno che saturava la VRAM in modo inefficiente e complicava il deployment.
  - **Impatto:** 🏋️ Alto — VRAM overhead, latenza rete e instabilità architetturale.
  - **Sforzo:** 🕐 4-8h
  - **Soluzione:** Migrato tutto il core LLM su `llama-cpp-python` (in-process). Creazione di finti endpoint `/api/tags`, `/api/show`, `/api/generate` sul loopback proxy per ingannare `mem0` e mantenere la compatibilità senza modificare eccessivamente il resto del codice. Fix del limite RAG Context overflow (29197 tokens -> 12000 chars limit).

---

## 🟢 6. Bot Telegram

### 6.1 ✅ — Sessioni Mono-Turno

  - **Problema:** Il bot reinizializza le chat messages vuote su ogni nuovo request text. Nessun ricordo dei turni passati.
  - **Impatto:** 🏃 Medio — L'LLM dimentica ciò che l'utente ha detto nel messaggio Telegram precedente.
  - **Sforzo:** 🕐 4-8h
  - **Soluzione:** Un buffer dizionario per `user_id` con gli ultimi 10 messaggi / max TTL 10 minuti.

### 6.2 ✅ — Formattazione e Comandi

  - **Problema:** Risponde in plain text e taglia a metà parola a 4000 char.
  - **Impatto:** 🚶 Basso.
  - **Sforzo:** ⏱️ <1h
  - **Soluzione:** Inviare in `parse_mode="Markdown"`, separare ai newline, aggiungere indicatori typing periodici, registrare comando `/web`.

### 6.3 ✅ — Sicurezza Globale Bot (NUOVO)

  - **Problema:** L'accesso era protetto da controlli manuali (`if update.effective_user.id`) ripetuti in ogni handler, portando ad un alto rischio di sviste su handler futuri o su callback.
  - **Impatto:** 🏋️ Alto — Rischio di data leak da utenti esterni non autorizzati.
  - **Sforzo:** ⏱️ <1h
  - **Soluzione:** Rimosso i controlli locali implementando un middleware globale `TypeHandler` con priorità assoluta (`group=-1`) che blocca qualsiasi attività da utenti sconosciuti lanciando `ApplicationHandlerStop`.

### 6.4 ✅ — Esploratore File /ls Interattivo (NUOVO)

  - **Problema:** L'output testuale ad albero per il file manager era illeggibile per i progetti di grandi dimensioni o veniva tagliato a causa dei limiti di lunghezza di Telegram.
  - **Impatto:** 🏃 Medio.
  - **Sforzo:** ⏰ 1-4h
  - **Soluzione:** Sostituito il comando `/tree` con un `/ls` interattivo basato su `InlineKeyboardMarkup`. Navigazione dinamica stile file manager con callback query. Possibilità di scaricare i file `📄` con conferma di download integrata.

### 6.5 ✅ — Gestione Task & Notifiche via Inline Keyboard (NUOVO)

  - **Problema:** Gli utenti dovevano usare comandi in linguaggio naturale per aggiungere, completare o rimuovere task (personali e globali) o allarmi, risultando scomodo per operazioni massive.
  - **Impatto:** 🏃 Medio — Miglioramento netto della UX.
  - **Sforzo:** ⏰ 1-4h
  - **Soluzione:** Aggiunta un'interfaccia a pulsanti per la sezione Task, ToDo e Notifiche. Permette di listare separatamente task di progetto e personali, con bottoni `[✅ Completa]` e `[🗑️ Rimuovi]` dedicati ad ogni elemento, oltre all'inserimento guidato.

---

### 6.6 ✅ — Tool Calling e Agente Autonomo (NUOVO)

  - **Problema:** L'LLM era limitato a consigliare snippet di codice testualmente senza poter agire in prima persona sul codebase, costringendo lo sviluppatore al copia-incolla.
  - **Impatto:** 🏋️ Alto — Produttività castrata dall'assenza di loop agentici autonomi.
  - **Sforzo:** 🕐 4-8h
  - **Soluzione:** Implementato un Agentic Loop (`while iterations < 5`) in `telegram_bot.py`. Integrati i tools in `agent_tools.py` con gli schemi JSON nativi (OpenAI/Ollama format). Il modello ora può eseguire in autonomia `read_file`, `write_file`, e `delete_file` lavorando in background e mostrando un feed visivo (🔧) in chat prima di rilasciare la risposta finale.

### 6.7 ✅ — Multi-Userbot per Interazione Gruppi/PM (NUOVO)

  - **Problema:** L'infrastruttura parlava solo via Bot API (limitata ai comandi o chat 1:1), rendendo impossibile operare nativamente in gruppi dove il bot era bloccato o rispondere ai PM come utente umano reale.
  - **Impatto:** 🏃 Medio — Scarsa scalabilità per automazione Customer Care o Team Chat.
  - **Sforzo:** ⏰ 1-4h
  - **Soluzione:** Creazione modulo `telegram_userbot_manager.py` usando `Telethon`. Ora ogni utente/membro del team (tramite `allowed_users`) può avviare un processo Userbot personale inviando numero e OTP da chat al bot principale (MTProto multiplexing).

### 6.8 ✅ — Timeout Rete Telegram (`NetworkError`)

  - **Problema:** Le disconnessioni fisiologiche in Long Polling generavano spam di stack traces (`httpx.ReadError`) enormi e inquinanti per i file log Docker.
  - **Impatto:** 🚶 Basso — Nessun crash, ma difficile fare debugging log.
  - **Sforzo:** ⏱️ <1h
  - **Soluzione:** Creato il filtro custom `TelegramNetworkErrorFilter` in `config.py` per sopprimere le eccezioni note di `Updater.polling_action_cb` sostituendole con un warning a linea singola elegante.

---

## ⚪ 7. Osservabilità & Monitoraggio

  - **Proposta 7.1:** **Logging JSON:** Usare `python-json-logger` per permettere ingestion a sistemi log aggregatori.
  - **Proposta 7.2:** **Endpoint Health:** Implementare `/health` con dettagli su file indicizzati e stato queue.
  - **Proposta 7.3:** **Trace IDs:** Generare un UUID per chiamata e iniettarlo nel logging pattern.

---

## 💡 8. Nuove Feature

- [x] **8.1 Workspace Isolati:** Una collezione vettoriale Qdrant dedicata ad ogni cartella radice in `documents/` (es. `collateral_docs_slotbuilder`), per evitare cross-contamination RAG.
- [x] **8.2 Cache Semantica LLM:** Memorizzare risposte simili con cosine similarity vettoriale >0.96.
- [x] **8.3 Dashboard Monitor:** Una interfaccia web minimalista per vedere Code Ingestion Status e VRAM.
- [x] **8.4 Git Webhooks:** Ingestion da repo remoti al push GitHub/Gitea.
- [x] **8.5 RAG Cross-Encoder Reranking (NUOVO):** Invece di affidarsi solo alla *Cosine Similarity* dei vettori Qdrant, applicare un modello `reranker` leggero ai primi 10 risultati per scartare il rumore e fornire all'LLM solo i 3 chunk con reale match semantico con la query.
- [x] **8.6 RAG Semantic Chunking (NUOVO):** Fondere dinamicamente piccoli frammenti AST consecutivi usando similarità del testo per creare chunk ottimali di lunghezza stabile, invece di dividerli ciecamente a `1200` chars o lasciare blocchi da 50 chars isolati.
- [x] **8.7 Voice Input via Telegram (NUOVO):** Supporto per i messaggi vocali Telegram: download dell'audio, trascrizione via *Whisper* in locale, inferenza RAG, e invio della risposta come messaggio vocale TTS (Text-to-Speech) o di testo.
- [x] **8.8 Esecuzione Comandi & Shell Access (NUOVO):** Permettere l'esecuzione di comandi shell sicuri (es. `git pull`, gestione Docker) direttamente dal bot Telegram tramite validazione dell'intent.
- [x] **8.9 Integrazione Git Avanzata (NUOVO):** Capacità dell'Agente di analizzare dinamicamente i diff, fare recensioni del codice (Code Review) sui nuovi commit, e creare summary automatici per la documentazione.
- [x] **8.10 Task Schedulati & Monitoraggio (Cron-Agent) (NUOVO):** Possibilità di istruire l'Agente in linguaggio naturale per eseguire controlli periodici (es. "Avvisami se la CPU supera l'80%" oppure "Controlla il log di errore ogni ora e fammi un riassunto").
- [x] **8.11 File Editing Autonomo (NUOVO):** Capacità dell'Agente di proporre e applicare patch al codice sorgente sul server in totale autonomia, dopo approvazione esplicita via bot.
- [ ] **8.12 Text-to-SQL & DB Interaction (NUOVO):** Modulo per permettere all'Agente di ispezionare dinamicamente database relazionali e non, eseguendo query SQL generate in tempo reale basate su domande utente.
- [x] **8.13 Gestione Infrastruttura (SSH/IP) (NUOVO):** Un vault/modulo sicuro per mantenere un inventario degli IP dei server e delle relative chiavi SSH, per consentire all'Agente di connettersi e gestire nodi esterni in totale autonomia.
- [x] **8.14 Skills / Actions Dinamiche (Deploy & SFTP) (NUOVO):** Un sistema a plugin (Skills) che insegna all'Agente a eseguire flussi operativi complessi in risposta a comandi testuali (es. "Fai il deploy del progetto X" oppure "Carica i file della cartella Y sul server SFTP Z").
- [x] **8.15 Task Management & ToDo System (NUOVO):** Un modulo di gestione delle attività (con e senza scadenza) per mantenere traccia del lavoro. Include la gestione di To-Do list, priorità e lo stato di avanzamento.
- [x] **8.16 Allarmi & Reminder Proattivi (NUOVO):** Sistema integrato di alert tramite il bot Telegram. L'Agente invierà notifiche e memo per l'organizzazione del lavoro (es. scadenze ravvicinate, reminder di appuntamenti o task pendenti da completare).
- [ ] **8.17 Integrazione Email & Calendari (NUOVO):** Possibilità di connettere caselle IMAP e calendari (es. Google Calendar) per permettere all'Agente di riassumere le email non lette, smistarle o fissare riunioni in agenda su richiesta testuale.
- [ ] **8.18 Data Analysis & Code Sandbox (NUOVO):** Un ambiente isolato (Jupyter-like) dove l'Agente può scrivere ed eseguire codice Python in tempo reale per analizzare file CSV/Excel inviati su Telegram e restituire grafici o insight numerici.
- [ ] **8.19 Analisi Documentale e OCR Avanzato (NUOVO):** Elaborazione e riassunto istantaneo di interi file PDF o immagini (ricevute via bot) per estrarre informazioni chiave, scansionare documenti contrattuali o tabelle.
- [x] **8.20 Self-Reflection & Memory Consolidation (NUOVO):** Un job notturno automatico in cui l'Agente rilegge le memorie episodiche della giornata e le "condensa" per imparare meglio le tue preferenze, ottimizzando lo spazio nel vector database (Qdrant).
- [ ] **8.21 Generazione Automatica Documentazione (NUOVO):** Generazione e aggiornamento continuo dei docstrings e file Markdown (`/docs`) in automatico durante la notte. L'Agente leggerà l'AST aggiornato e riscriverà la documentazione obsoleta senza intervento umano.
- [ ] **8.22 Assistente Debugging in Produzione (NUOVO):** Collegamento dell'Agente ai sistemi di log/error tracking (es. Sentry, Datadog). L'Agente riceverà gli stacktrace e, conoscendo l'intero albero del progetto, fornirà istantaneamente l'analisi del bug e la patch risolutiva.
- [ ] **8.23 Continuous Integration RAG & PR Review (NUOVO):** L'Agente si integrerà nei flussi di CI/CD (GitHub Actions/GitLab CI) per segnalare automaticamente ai developer potenziali conflitti logici, violazioni di pattern architetturali e fare code review autonoma sulle Pull Request.
- [ ] **8.24 Test Generation & Coverage (NUOVO):** Incaricare l'Agente di scansionare regolarmente i file con bassa test coverage per generare suite di unit test strutturati (PyTest, Jest, Go Test) pronti per essere committati nel repository.
- [ ] **8.25 Terminal & CLI Companion (NUOVO):** Creazione di un binario CLI leggero (es. `mem0-cli`) per permettere ai developer di interrogare l'agente locale direttamente dal terminale (es. `mem0 ask "come funziona il login qui?"`) e inviare output bash in pipe all'agente.
- [ ] **8.26 Refactoring di Massa Autonomo (NUOVO):** Capacità di affidare all'Agente task titanici come l'aggiornamento di un'intera API o la migrazione di una libreria deprecata su decine di file, generando automaticamente i branch e le Pull Request.
- [x] **8.27 RAG Line Numbering & IDE Apply Fix (NUOVO):** Iniezione dei numeri di riga nei chunk indicizzati dall'AST per permettere ad agenti esterni (es. plugin IDE come Continue) di localizzare le funzioni e applicare patch perfette.
- [x] **8.28 Full-File Context Bypass (NUOVO):** Capacità del Gatekeeper di rilevare nomi di file nel prompt e bypassare il limite dei chunk RAG, iniettando l'intero file per evitare modifiche monche da parte del LLM.
- [x] **8.29 Code Skeleton Auto-Generation (NUOVO):** Generatore automatico integrato nel RAG che tiene aggiornato un `.ai-skeleton.md` con l'intera alberatura e signature dei metodi per consentire visibilità architetturale globale senza sprecare token.
- [x] **8.30 Project-Specific Guidelines Injector (NUOVO):** Iniezione dinamica dei file guida (es. `.cursorrules`, `.ai-rules.md`, `.copilot-instructions.md`) per obbligare il proxy a rispettare gli standard architetturali del progetto durante la scrittura.
- [x] **8.31 Gestione Avanzata Logging & Silenziamento Adattivo (NUOVO):** Implementazione di filtri middleware avanzati per pulire la console da eventi ricorrenti innoqui (come HTTP 200/204, richieste da Dashboard) per far risaltare esclusivamente i veri allarmi e rendere la Docker console enterprise-ready.
- [ ] **8.32 Migrazione Automatica Database Vettoriali (NUOVO):** Automatizzare interamente la rilevazione di cambi delle dimensioni vettoriali nell'`.env` o cambio di Embedding model, gestendo in totale autonomia backup, distruzione controllata e rigenerazione delle collezioni Qdrant senza alcun reset manuale o cambio suffix `_vX`.
- [ ] **8.33 Sistema di Routing Dinamico dei Modelli (LLM Router) (NUOVO):** Instradare dinamicamente i task più leggeri (intent classification, tag extraction, task ricorsivi) a piccoli LLM iper-veloci (es. Phi-3 mini o Llama-3-8B), riservando la potenza bruta di Qwen Coder solo alle vere richieste logico-strutturali, massimizzando il throughput della VRAM.
- [ ] **8.34 Supporto High-Availability (HA) & Clustering (NUOVO):** Estrazione del Global State asincrono di `state.py` verso un database in memory come Redis. Questo permetterà di istanziare repliche multiple del Proxy dietro un load balancer, supportando migliaia di richieste concorrenti in team distribuiti.
- [ ] **8.35 Telegram Userbot (Auto-risposte a Chat Private) (NUOVO):** Integrazione del protocollo MTProto (via `Telethon` o `Pyrogram`) affiancato all'HTTP Bot. Permetterà all'Agente di agire letteralmente "a nome dell'utente", leggendo i messaggi in arrivo dai contatti privati e rispondendo automaticamente simulando la digitazione, usando il RAG e la memoria condivisa.
- [ ] **8.36 Self-Healing & Rollback Automatico (Auto-Programmazione):** Se l'Agente modifica il proprio codice causando un crash fatale o un errore di sintassi, un demone supervisore annulla l'ultima patch tramite backup locale, riavvia il proxy in modalità provvisoria e notifica l'errore per ri-analizzarlo.
- [ ] **8.37 TDD Autonomo (Test-Driven Development) (Auto-Programmazione):** L'Agente genera autonomamente dei test unitari (es. via `pytest`), li esegue per farli fallire, e poi scrive iterativamente il codice sorgente finché tutti i test non passano con successo (Green Light) in totale autonomia.
- [ ] **8.38 Auto-Dependency Management (Auto-Programmazione):** Se durante un'analisi o una richiesta l'Agente decide di usare una nuova libreria, esegue in autonomia il `pip install` o equivalente, verifica la compatibilità e aggiorna i file di requirements.
- [ ] **8.39 Syntax Linting & Fixes Preventivi (Auto-Programmazione):** Integrazione con linter (es. `flake8`). Subito dopo aver usato `replace_in_file`, l'Agente lancia automaticamente una validazione di sintassi; se rileva errori di indentazione o import mancanti, si auto-corregge prima di chiudere il task.

### 🛠️ Passi per l'implementazione del Telegram Userbot (8.35)

L'attuale bot usa le *HTTP Bot API* classiche, che Telegram restringe: un "BotFather bot" non può leggere le tue chat private con i tuoi amici. Per fare in modo che il proxy risponda **al posto tuo**, bisogna fargli fare il login usando il tuo numero di telefono (creando un *Userbot*).
Ecco i passaggi da seguire per implementarlo:
1. **Credenziali API MTProto**: L'utente dovrà ottenere un `API_ID` e `API_HASH` da `my.telegram.org` e inserirli nel file `.env` insieme al proprio numero di telefono.
2. **Libreria Telethon/Pyrogram**: Sostituire o affiancare `python-telegram-bot` con un client asincrono basato su MTProto (es. `Telethon`).
3. **Session Management**: Durante il primo avvio, il container Docker richiederà un input (il codice OTP ricevuto via Telegram e l'eventuale password 2FA) per generare il file di sessione `.session`. Sarà necessario un endpoint temporaneo del proxy per iniettare il codice OTP da fuori.
4. **Filtri e Sicurezza**: Implementare un sistema di **Whitelist / Blacklist**. Non vogliamo che l'AI risponda a caso al gruppo della famiglia o al capo. Definire nel `.env` o via file JSON una lista di `ALLOWED_PRIVATE_CHATS`.
5. **Humanization (Opzionale)**: Aggiungere delay variabili prima di inviare, mostrare l'evento `[typing...]` per una durata proporzionale alla lunghezza della risposta generata, e simulare un comportamento umano per non innescare filtri anti-spam di Telegram.
6. **Integrazione con la Pipeline RAG**: Connettere l'handler dei messaggi entranti di `Telethon` alla stessa `build_omniscient_prompt()` e ai servizi Mem0, facendo in modo che la memoria contestuale riconosca il destinatario e moduli il "Tone of Voice".

---

## 📊 Riepilogo Esecuzione per Priorità

### Sprint 1 — Urgenze (Security, TOCTOU, Deadlocks)
> Focus: 1.1, 1.2, 1.3, 1.4, 1.5, 1.8, 1.9, 1.10, 4.1, 5.1

### Sprint 2 — Performance Boost & Refactoring RAG
> Focus: 2.1, 2.3, 3.1, 3.4, 3.5, 4.3

### Sprint 3 — UX, Telegram, API & Docker Polish
> Focus: 1.6, 4.2, 4.4, 5.2, 6.1, 6.2

### Sprint 4 — Vision & Observability
> Focus: 7.1, 7.2, 8.1, 8.2

---
🌐 **Collateral Studios** — *Piano di Evoluzione Definitivo Infrastruttura IA*

---

## 🔬 9. Deep Architectural Analysis (v8.7+ Roadmap)

A seguito di un'ispezione profonda del codice sorgente di `mem0-proxy`, sono emerse limitazioni strutturali e nuove opportunità di implementazione cruciali per la scalabilità e la sicurezza enterprise:

### 9.1 🚨 Sicurezza: Falla di Esecuzione Remota (RCE) tramite `run_shell_command`
  - **Problema:** In `agent_tools.py`, il tool `run_shell_command` usa `subprocess.run(shell=True)` limitando solo la cartella di esecuzione. Essendo il container runnato come root e avendo mountati i dischi host (tramite `/host_fs` per External Projects), un'allucinazione dell'LLM potrebbe eseguire comandi distruttivi (`rm -rf /host_fs/*`) sull'intero host.
  - **Soluzione:** 1) Aggiungere un prompt di conferma interattiva (Y/N) su Telegram prima che il tool `run_shell_command` o `write_file` esegua modifiche critiche. 2) Limitare i privilegi dell'utente Docker (`appuser`).

### 9.2 ✅ 🚧 Collasso GPU per Concorrenza (LLM Queueing)
  - **Problema:** FastAPI accetta richieste `/api/chat` illimitate. Se 5 utenti interrogano il bot Telegram contemporaneamente, verranno lanciate 5 chiamate HTTP simultanee a Ollama. Questo porta Ollama a tentare di caricare il modello o saturare la VRAM con i KV Cache multipli, portando a OOM (Out Of Memory) o Timeout a cascata (`ReadTimeout` a 300s).
  - **Soluzione:** Introdurre in `state.py` un `asyncio.Semaphore(1)` (o 2 a seconda della VRAM) per serializzare le inferenze pesanti verso Ollama, mettendo le altre in coda di attesa (con un messaggio Telegram "⏳ In coda...").

### 9.3 ✅ 🗑️ Sottoutilizzo Cronico della Context Window
  - **Problema:** In `prompt_builder.py:138`, `MAX_BUDGET` per il codice RAG è hardcodato a `8000` caratteri (~2000 token). Dato che il `Modelfile` e config.py impostano `num_ctx` a 8192 o 16384 token, stiamo nutrendo l'LLM con appena il 20-30% della sua memoria disponibile, perdendo enormi porzioni di codice utile durante il RAG.
  - **Soluzione:** Calcolare dinamicamente il budget di caratteri: `(LLM_OPTIONS["num_ctx"] * 3.5) - len(system_prompt)`. Su 8k token, possiamo inviare ~25.000 caratteri di codice RAG!

### 9.4 ✅ 🐢 Blocco dell'Event Loop per Project Tree
  - **Problema:** In `prompt_builder.py`, `generate_project_tree()` viene ricalcolato sincronicamente ad *ogni* query. Su repository enormi (es. 10.000 file), la funzione `os.walk` blocca l'intero event loop di FastAPI per centinaia di millisecondi.
  - **Soluzione:** Caching asincrono. Aggiornare la variabile `state.project_tree` in background durante il watchdog (`rag_queue_worker`), e far leggere a `prompt_builder` solo la variabile in cache (O(1)).

### 9.5 ✅ 💾 Sincronia Fatale nel salvataggio RAG (`_save_state_unsafe`)
  - **Problema:** Mentre il bug 1.2 è stato risolto, `_save_state_unsafe()` usa `json.dump()` sincrono all'interno di un loop asincrono (in `rag.py:300`). Quando il file `rag_state.json` diventa di decine di Megabyte, scriverlo sincronicamente blocca le risposte HTTP di FastAPI per interi secondi.
  - **Soluzione:** Sostituire con `aiofiles` o, come proposto in `8.34`, spostare i metadati su un DB SQLite/Redis.
