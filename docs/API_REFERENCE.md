# API Reference

## Endpoint API Jarvis

### API Native Jarvis

| Endpoint | Metodo | Funzione |
|---|---|---|
| `/api/chat` | POST | Chat con memoria + RAG + tool-calling |
| `/api/generate` | POST | Generate + cache semantica |
| `/api/embed` / `/api/embeddings` | POST | Embeddings (legacy) |
| `/api/tags`, `/api/ps`, `/api/show`, `/api/version` | GET/POST | Stub compatibilità Ollama |
| `/api/project-tree` | GET | Albero del progetto indicizzato |
| `/api/webhook/git` | POST | Git webhook → pull → re-ingestion |
| `/api/reset-all` | GET/POST | Reset RAG + Mem0 |
| `/docs` | GET | Swagger UI |

### Pipeline Telemetry

| Endpoint | Metodo | Funzione |
|---|---|---|
| `/api/telemetry/traces` | GET | Ultimi N pipeline trace completati |
| `/api/telemetry/traces/active` | GET | Trace correntemente in esecuzione |
| `/api/telemetry/traces/{request_id}` | GET | Cerca trace per request_id |
| `/api/telemetry/gatekeeper` | GET | Statistiche cumulative Gatekeeper |
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
| `/v1/embeddings` | POST | Embeddings (float/base64 encoding) |
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
- **GatekeeperStats**: statistiche cumulative di classificazione (bypass rate, confidence media, by_intent)
- **Ring buffer 500 trace**: ultimi 500 trace completati sempre disponibili in memoria
- **HTTP REST**: 8 endpoint `/api/telemetry/*` per query diretta
- **MCP stdio**: server esterno per Claude Code / Cursor via `.mcp.json`
- **MCP SSE**: endpoint in-app `/api/mcp/sse` per connessioni persistenti

---

## Connessione al Server MCP di Jarvis

Jarvis espone due modalità di accesso MCP per permettere ad agenti AI esterni (Claude Code, Cursor, Continue, ecc.) di ispezionare lo stato interno del sistema a fini di diagnostica e debug.

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
      "description": "Jarvis telemetry — espone pipeline trace, gatekeeper stats, error counters e stato del sistema per diagnostica AI."
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

### Modalità 2: Endpoint MCP SSE (in-app)

Jarvis espone un endpoint SSE direttamente via FastAPI. Utile per agenti che supportano il trasporto SSE persistente.

1. Connessione SSE:
   ```
   GET /api/mcp/sse
   ```
   Il server risponde con un evento `endpoint` che specifica l'URL per i messaggi.

2. Invio comandi JSON-RPC:
   ```
   POST /api/mcp/message?session_id=<id>
   Content-Type: application/json

   {"jsonrpc":"2.0","id":1,"method":"tools/list"}
   ```

### Elenco Completo Tool MCP

| Tool | Descrizione | Parametri |
|---|---|---|
| `get_recent_traces` | Ultimi N pipeline trace completati | `limit` (int, default 10) |
| `get_active_traces` | Trace correntemente in esecuzione | nessuno |
| `get_trace_by_id` | Cerca un trace completato per request_id | `request_id` (stringa, required) |
| `get_gatekeeper_stats` | Statistiche cumulative Gatekeeper | nessuno |
| `get_errors` | Contatori di errore per diagnostica | nessuno |
| `get_status` | Stato sistema: uptime, richieste, token | nessuno |
| `get_model_info` | Info modello LLM: family, GPU layers | nessuno |
| `get_pending_ops` | Operazioni pendenti: background tasks, coda watchdog | nessuno |
| `get_llm_call_breakdown` | Analisi aggregata chiamate LLM da ultimi 100 trace | nessuno |

### Risorse MCP (resources)

| URI | Descrizione |
|---|---|
| `jarvis://traces/recent` | Ultimi 10 pipeline trace |
| `jarvis://traces/active` | Trace attualmente in esecuzione |
| `jarvis://gatekeeper/stats` | Statistiche cumulative Gatekeeper |
| `jarvis://errors/counters` | Contatori di errore |
| `jarvis://system/status` | Uptime, richieste, token |
| `jarvis://model/info` | Informazioni modello LLM |
| `jarvis://system/pending_ops` | Operazioni pendenti |

### Esempio di Utilizzo

**Debug di una richiesta lenta:**
1. Chiama `get_recent_traces(limit=5)` per vedere gli ultimi trace
2. Identifica il `request_id` del trace più lento
3. Chiama `get_trace_by_id(request_id="abc123")` per vedere i dettagli
4. Analizza gli step: `build_omniscient_prompt`, `gemma_generation`, `tool_execution`

**Verifica dello stato del Gatekeeper:**
1. Chiama `get_gatekeeper_stats()`
2. Controlla `bypassed` vs `llm_called` — se il bypass rate è basso, il Gatekeeper sta funzionando correttamente
3. Controlla `avg_confidence` — se < 0.7, il modello Gatekeeper potrebbe avere problemi

**Diagnostica errori:**
1. Chiama `get_errors()` per vedere i contatori errori
2. Chiama `get_status()` per verificare uptime e richieste totali
3. Se `total_requests` è alto ma non ci sono trace recenti, potrebbe esserci un problema di inizializzazione del tracer
