# API Reference

## Endpoint API Jarvis

### API Native Jarvis

| Endpoint | Metodo | Funzione |
|---|---|---|
| `/api/chat` | POST | Chat con memoria + RAG + tool-calling |
| `/api/generate` | POST | Generate + cache semantica |
| `/api/embed` / `/api/embeddings` | POST | Embeddings (via FastEmbed ONNX CPU) |
| `/api/tags`, `/api/ps`, `/api/show`, `/api/version` | GET/POST | Stub compatibilità Ollama |
| `/api/project-tree` | GET | Albero del progetto indicizzato |
| `/api/webhook/git` | POST | Git webhook → pull → re-ingestion |
| `/api/reset-all` | GET/POST | Reset RAG + Mem0 |
| `/docs` | GET | Swagger UI |

### Auth & Profile

| Endpoint | Metodo | Funzione |
|---|---|---|
| `/api/auth/login` | POST | Login con username/password → JWT cookie |
| `/api/auth/logout` | POST | Logout, cancella cookie JWT |
| `/api/auth/me` | GET | Info utente corrente (richiede auth) |
| `/api/auth/api-key` | GET | Lista API key dell'utente corrente |
| `/api/auth/api-key` | POST | Genera nuova API key (con rotate option) |
| `/api/auth/api-key/{key_id}/reveal` | GET | Rivela chiave appena generata (cache 5 min) |
| `/api/auth/api-key/{id}/revoke` | POST | Revoca API key |
| `/api/auth/change-password` | POST | Cambia password utente corrente |
| `/api/auth/telegram` | POST | Link/unlink Telegram ID |

### Admin: User Management

| Endpoint | Metodo | Funzione |
|---|---|---|
| `/api/users` | GET | Lista utenti (admin only) |
| `/api/users` | POST | Crea nuovo utente (admin only) |
| `/api/users/{id}` | PUT | Modifica utente (admin only) |
| `/api/users/{id}` | DELETE | Elimina utente (admin only) |
| `/api/users/{id}/activate` | PUT | Attiva utente (admin only) |
| `/api/users/{id}/deactivate` | PUT | Disattiva utente (admin only) |

### Admin: Project Management

| Endpoint | Metodo | Funzione |
|---|---|---|
| `/api/projects` | GET | Lista progetti con stato |
| `/api/projects/register` | POST | Registra nuovo progetto RAG |
| `/api/projects/{name}/reindex` | POST | Trigger re-ingestion RAG + Synaptiq |
| `/api/projects/{name}/collection` | DELETE | Cancella collezione Qdrant |
| `/api/projects/{name}/synaptiq/graph` | GET | Grafo Synaptiq (nodi/relazioni per Sigma.js) |

### Dashboard & Settings

| Endpoint | Metodo | Funzione |
|---|---|---|
| `/api/dashboard/settings` | GET | Lista 73 env var con metadati (type, category, restart_required) |
| `/api/dashboard/settings` | POST | Aggiorna variabili, type coercion, persist su `.env` |
| `/api/dashboard/*` | GET | Metriche GPU, modelli, health, RAG, telemetry |

### MCP v2 (Streamable HTTP)

| Endpoint | Metodo | Funzione |
|---|---|---|
| `/api/mcp/v2` | POST | Endpoint MCP Streamable HTTP (JSON-RPC) — 24 tool + 8 resources |

### Pipeline Telemetry

| Endpoint | Metodo | Funzione |
|---|---|---|
| `/api/telemetry/traces` | GET | Ultimi N pipeline trace completati |
| `/api/telemetry/traces/active` | GET | Trace correntemente in esecuzione |
| `/api/telemetry/traces/{request_id}` | GET | Cerca trace per request_id |
| `/api/telemetry/intent` | GET | Statistiche cumulative classificatore intenti |
| `/api/telemetry/errors` | GET | Contatori di errore |
| `/api/telemetry/status` | GET | Uptime, richieste, token, stato sistema |
| `/api/telemetry/model` | GET | Informazioni modello LLM (family, GPU layers) |
| `/api/telemetry/pending_ops` | GET | Background tasks, coda watchdog |

### MCP Server (SSE Transport)

| Endpoint | Metodo | Funzione |
|---|---|---|
| `/api/mcp/sse` | GET | Connessione SSE persistente per MCP |
| `/api/mcp/message` | POST | Invio messaggio JSON-RPC MCP |

### OpenAI-compatibili

| Endpoint | Metodo | Funzione |
|---|---|---|
| `/v1/chat/completions` | POST | Chat completion (streaming SSE, tool-calling, confirmation tokens) |
| `/v1/completions` | POST | Text completion legacy (streaming SSE, echo) |
| `/v1/embeddings` | POST | Embeddings (float/base64 encoding, FastEmbed ONNX CPU) |
| `/v1/models` | GET | Lista modelli |
| `/v1/models/{model_name}` | GET | Dettaglio modello |
| `/v1/moderations` | POST | Moderazione contenuti (LLM-based + keyword fallback) |
| `/v1/audio/transcriptions` | POST | Trascrizione audio (faster-whisper) |
| `/v1/audio/translations` | POST | Traduzione audio → inglese (faster-whisper) |
| `/v1/audio/speech` | POST | Text-to-speech (gTTS) |
| `/v1/images/generations` | POST | Stub 400 (model not available) |
| `/v1/images/edits` | POST | Stub 400 (model not available) |
| `/v1/images/variations` | POST | Stub 400 (model not available) |
| `/v1/assistants` | GET | Lista Assistenti |
| `/v1/assistants` | POST | Crea Assistente |
| `/v1/assistants/{id}` | GET | Dettaglio Assistente |
| `/v1/assistants/{id}` | POST | Modifica Assistente |
| `/v1/assistants/{id}` | DELETE | Cancella Assistente |
| `/v1/threads` | POST | Crea Thread |
| `/v1/threads/{id}` | GET | Dettaglio Thread |
| `/v1/threads/{id}/runs` | POST | Esegui Run su Thread |
| `/v1/threads/{id}/runs/{run_id}/submit_tool_outputs` | POST | Tool output per Run |
| `/v1/vector_stores` | GET/POST | Lista/Crea Vector Store |
| `/v1/files` | POST | Upload file |
| `/v1/uploads` | POST | Upload large file in parti |

## Pipeline Telemetry & MCP per Diagnostica AI

- **PipelineTracer**: tracciamento per-request con step timing, LLM calls, gatekeeper decisioni, tool calls
- **IntentStats**: statistiche cumulative di classificazione (bypass rate, confidence media, by_intent)
- **Ring buffer 500 trace**: ultimi 500 trace completati sempre disponibili in memoria
- **HTTP REST**: 8 endpoint `/api/telemetry/*` per query diretta
- **MCP stdio**: server esterno per Claude Code / Cursor via `.mcp.json`
- **MCP v2**: endpoint Streamable HTTP `/api/mcp/v2` — 24 tool + 8 resources

---

## Connessione al Server MCP di Jarvis

Jarvis espone due modalità di accesso MCP per permettere ad agenti AI esterni (Claude Code, Cursor, Continue, ecc.) di ispezionare lo stato interno del sistema a fini di diagnostica e debug. Il server MCP v1 (stdio e SSE) è deprecato in favore di MCP v2 Streamable HTTP.

### Modalità 1: Server MCP stdio (per agenti esterni)

Configura il tuo agente AI per lanciare il server MCP come subprocesso. Jarvis include già il file `.mcp.json` nella root del progetto:

```json
{
  "mcpServers": {
    "jarvis-telemetry": {
      "command": "python",
      "args": ["-m", "jarvis.mcp_server"],
      "env": {
        "JARVIS_URL": "http://localhost:8000"
      },
      "description": "Jarvis telemetry — espone pipeline trace, intent stats, error counters e stato del sistema per diagnostica AI."
    }
  }
}
```

**Claude Code / Cursor** rilevano automaticamente `.mcp.json` nella root del progetto. L'agente può quindi usare i tool MCP per ispezionare Jarvis.

**Uso standalone** (per test):
```bash
# Collega il server MCP a un'istanza Jarvis in esecuzione
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python -m jarvis.mcp_server
```

Se Jarvis è su un host diverso:
```bash
JARVIS_URL=http://192.168.1.100:8000 python -m jarvis.mcp_server
```

### Modalità 2: Endpoint MCP v2 Streamable HTTP

Jarvis espone un endpoint MCP v2 conforme al protocollo Streamable HTTP (RFC 2025-11-25) direttamente via FastAPI.

```
POST /api/mcp/v2
Content-Type: application/json

{"jsonrpc":"2.0","id":1,"method":"tools/list"}
```

Lo streaming delle risposte avviene via SSE quando il client invia l'header `Accept: text/event-stream`.

**Esempio di chiamata con curl:**
```bash
curl -X POST http://localhost:8000/api/mcp/v2 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

**Connessione SSE per streaming:**
```bash
curl -X POST http://localhost:8000/api/mcp/v2 \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

### Elenco Completo Tool MCP v2

| Tool | Descrizione | Parametri |
|---|---|---|
| `get_recent_traces` | Ultimi N pipeline trace completati | `limit` (int, default 10) |
| `get_active_traces` | Trace correntemente in esecuzione | nessuno |
| `get_trace_by_id` | Cerca un trace completato per request_id | `request_id` (stringa, required) |
| `get_trace_full` | Trace completo con testi dei prompt intermedi (system, RAG, compressione, risposta LLM) | `request_id` (stringa, required) |
| `get_intent_stats` | Statistiche cumulative classificatore intenti | nessuno |
| `get_errors` | Contatori di errore per diagnostica | nessuno |
| `get_status` | Stato sistema: uptime, richieste, token | nessuno |
| `get_model_info` | Info modello LLM: family, GPU layers | nessuno |
| `get_pending_ops` | Operazioni pendenti: background tasks, coda watchdog | nessuno |
| `chat_send` | Invia messaggio alla pipeline chat (gatekeeper + RAG + compressione), restituisce trace_id | `message`, `user_id` |
| `code_intelligence` | Ricerca ibrida RAG + Synaptiq (contesto semantico + analisi strutturale) | `query`, `project` |
| `jarvis_chat` | Pipeline chat completa (RAG + memoria + Synaptiq + web search), restituisce trace_id | `message`, `user_id` |
| `jarvis_exec` | Esegue comando shell whitelisted (EXEC_ALLOWED_COMMANDS); readonly senza conferma | `command`, `args` |
| `jarvis_rag_search` | Ricerca RAG (Qdrant) documenti/codice semanticamente simili. **Fix 03/08:** → `rag.engine.search_documents` | `query`, `project`, `top_k` (max 20) |
| `jarvis_memory_search` | Ricerca memoria episodica (Mem0) per user_id. **Fix 03/08:** → `state.memory.search` via `mem0_executor` | `query`, `user_id` |
| `jarvis_synaptiq_query` | Analisi strutturale codice via Synaptiq (simboli, callers, blast radius) | `query`, `project` |
| `jarvis_web_search` | Ricerca web via SearXNG. **Fix 03/08:** → richiesta diretta `state.http_client` + `SEARXNG_HOST` | `query`, `num_results` (max 20) |
| `benchmark_raw` | Test raw LLM speed senza pipeline (TTFT, tok/s) | `prompt`, `max_tokens` |
| `benchmark_pipeline` | Test LLM via pipeline completa (misura overhead) | `prompt`, `max_tokens` |
| `list_sessions` | Lista sessioni chat con metadati | `limit`, `sort_by`, `user_id` |
| `get_session` | Recupera sessione chat completa per conversation_id | `conversation_id` |
| `search_sessions` | Cerca testo in tutte le sessioni chat | `query`, `user_id`, `limit` |

### Risorse MCP (resources)

| URI | Descrizione |
|---|---|
| `jarvis://traces/recent` | Ultimi 10 pipeline trace |
| `jarvis://traces/active` | Trace attualmente in esecuzione |
| `jarvis://intent/stats` | Statistiche cumulative classificatore intenti |
| `jarvis://errors/counters` | Contatori di errore |
| `jarvis://system/status` | Uptime, richieste, token |
| `jarvis://model/info` | Informazioni modello LLM |
| `jarvis://system/pending_ops` | Operazioni pendenti |
| `jarvis://sessions/list` | Lista sessioni chat con metadati |

### Esempio di Utilizzo

**Debug di una richiesta lenta:**
1. Chiama `get_recent_traces(limit=5)` per vedere gli ultimi trace
2. Identifica il `request_id` del trace più lento
3. Chiama `get_trace_by_id(request_id="abc123")` per vedere i dettagli
4. Analizza gli step: `build_omniscient_prompt`, `gemma_generation`, `tool_execution`

**Verifica dello stato del classificatore intenti:**
1. Chiama `get_intent_stats()`
2. Controlla `bypassed` vs `llm_called` — se il bypass rate è basso, il classificatore sta funzionando correttamente
3. Controlla `avg_confidence` — se < 0.7, il modello di classificazione potrebbe avere problemi

**Diagnostica errori:**
1. Chiama `get_errors()` per vedere i contatori errori
2. Chiama `get_status()` per verificare uptime e richieste totali
3. Se `total_requests` è alto ma non ci sono trace recenti, potrebbe esserci un problema di inizializzazione del tracer
