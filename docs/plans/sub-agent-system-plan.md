# Piano Implementazione Sub-Agent System

## Stato Attuale

Jarvis ha 3 job schedulati via APScheduler, nessun framework formale di agenti:
- `sys_reflection` (03:00 UTC) — consolida memorie Mem0
- `sys_morning_recap` (09:00 UTC) — task pendenti via Telegram
- N job utente (cron/date/relativo) — prompt schedulati dall'utente

Mancano: orchestrator, health monitoring, spawning automatico, comunicazione tra agenti.

---

## Fase 1 — Fondazione: Agent Registry + Lifecycle

### 1.1 Modello Dati Agente

```python
@dataclass
class AgentSpec:
    id: str                          # "sys_reflection", "user_cron_abc123"
    name: str                        # Nome leggibile
    kind: AgentKind                  # system | user | event | periodic
    handler: Callable                # Funzione async da eseguire
    trigger: TriggerConfig           # CronTrigger / DateTrigger / EventTrigger
    owner_id: str | None             # Telegram user ID (se user-defined)
    max_retries: int = 3
    timeout: int = 300
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
```

File: `jarvis/agent_registry.py`

### 1.2 AgentRegistry

```python
class AgentRegistry:
    """Registry singleton di tutti gli agenti, system + user."""
    
    def __init__(self):
        self._agents: dict[str, AgentSpec] = {}
        self._scheduler: AsyncIOScheduler | None = None
    
    def register(self, spec: AgentSpec) -> None
    def unregister(self, agent_id: str) -> None
    def get(self, agent_id: str) -> AgentSpec | None
    def list(self, kind: AgentKind | None = None) -> list[AgentSpec]
    def start(self) -> None       # Avvia scheduler + tutti gli agenti abilitati
    def stop(self) -> None        # Ferma scheduler
```

### 1.3 Lifecycle Hook

Ogni agente ha un lifecycle opzionale:
```
on_start() → on_tick() → on_error(err) → on_complete(result) → on_stop()
```

Definito come protocollo / class-ABC opzionale. La maggior parte degli agenti implementa solo `on_tick()` (la vecchia handler function).

---

## Fase 2 — Spawning Automatico (Event-Driven)

### 2.1 EventBus

Sistema di eventi interno (disaccoppiato, nessuna dipendenza esterna):

```python
class EventBus:
    """Sistema pub-sub interno per eventi applicativi."""
    
    async def emit(self, event: AppEvent) -> None
    def on(self, event_type: str, handler: Callable) -> None
    
@dataclass
class AppEvent:
    type: str       # "file.created" | "file.modified" | "memory.added" | "telegram.message" | "system.boot"
    data: dict
    source: str
    timestamp: float = field(default_factory=time.time)
```

### 2.2 Trigger da Evento

Aggiungere `EventTrigger` ad APScheduler o trigger custom:
```python
@dataclass
class EventTriggerConfig:
    event_type: str
    debounce: float = 0.0       # Secondi di debounce
    filter: Callable | None = None  # Filtra eventi prima di spawnare
```

Esempi di agenti event-driven:
- `file_watchdog`: quando un file viene creato/modificato → spawna agente analisi
- `memory_consolidation_auto`: quando Mem0 accumula N nuove memorie → consolida
- `telegram_reaction`: quando un utente reagisce a un messaggio → spawna agente contestuale

### 2.3 Debounce / Rate Limiting

```python
class DebouncedTrigger:
    """Accumula eventi e spawna dopo debounce secondi di silenzio."""
    def __init__(self, event_type: str, window: float = 5.0):
        ...
```

---

## Fase 3 — Esecuzione Robusta

### 3.1 AgentRunner (Esecutore con Retry + Timeout)

```python
class AgentRunner:
    """Esegue un agente con retry, timeout, logging strutturato."""
    
    async def run(self, spec: AgentSpec, context: dict | None = None) -> AgentResult
```

Comportamento:
1. Acquisisce `chat_lock` (priorità configurabile, default 5)
2. Logga inizio esecuzione con agent_id + timestamp
3. Esegue handler con `asyncio.wait_for(timeout=spec.timeout)`
4. Su timeout: log + tentativo retry (se max_retries > 0)
5. Su errore: log + retry con backoff esponenziale
6. Salva risultato in `AgentResult` (stato, durata, output, errori)

### 3.2 AgentResult

```python
@dataclass
class AgentResult:
    agent_id: str
    status: AgentStatus        # success | failed | timeout | skipped
    started_at: float
    duration: float
    output: str = ""
    error: str | None = None
    retries: int = 0
```

### 3.3 Health Monitoring

Aggiungere a `dashboard.py` un endpoint `/api/agents` che espone:
```json
{
  "agents": [
    {
      "id": "sys_reflection",
      "name": "Memory Consolidation",
      "kind": "system",
      "status": "idle",
      "last_run": "2026-06-25T03:00:00Z",
      "last_duration": 12.5,
      "last_status": "success",
      "next_run": "2026-06-26T03:00:00Z",
      "total_runs": 42,
      "failure_rate": 0.02
    }
  ]
}
```

---

## Fase 4 — Agenti Specifici da Implementare

### 4.1 File Analyzer (Event-Driven)

```python
spec = AgentSpec(
    id="file_analyzer",
    name="Analisi Automatica File",
    kind=AgentKind.event,
    handler=analyze_new_file,
    trigger=EventTriggerConfig(event_type="file.created", debounce=2.0)
)
```

Alla creazione/modifica di un file in `DOC_DIR`:
1. Legge il file
2. Classifica il tipo (codice, doc, config, dati)
3. Genera embedding + indicizza in Qdrant
4. Salva sommario in Mem0

Bonus: integrazione con watchdog esistente (`DynamicRagEventHandler`).

### 4.2 Periodic Memory Consolidation (Multi-Utente)

Sostituire l'attuale `nightly_memory_consolidation` hardcoded per `alfio_dev`:

```python
spec = AgentSpec(
    id="memory_consolidation",
    name="Consolidamento Memorie",
    kind=AgentKind.periodic,
    handler=lambda: consolidate_all_users(),
    trigger=CronTriggerConfig(cron="0 3 * * *")
)
```

`consolidate_all_users()` scorre tutti gli utenti in `ALLOWED_USERS` e consolida le memorie di ciascuno.

### 4.3 Code Review Agent (Event-Driven, Facoltativo)

```python
spec = AgentSpec(
    id="code_review",
    name="Code Review Automatica",
    kind=AgentKind.event,
    handler=review_code_change,
    trigger=EventTriggerConfig(event_type="file.modified", filter=lambda e: e.data["path"].endswith(".py"))
)
```

Analizza le diff dei file Python modificati e posta un report su Telegram.

### 4.4 Idle Time Agent (Facoltativo)

Se la coda richieste è vuota per >60 secondi, esegue task in background:
- Pulizia cache
- Re-indexing file sporchi
- Consolidamento incrementale Mem0

---

## Fase 5 — UX e Gestione

### 5.1 Comandi Telegram

```
/agents                — Lista agenti + stato
/agent <id>            — Dettaglio agente
/agent <id> run        — Esecuzione forzata
/agent <id> disable    — Disabilita
/agent <id> enable     — Riabilita
/agent <id> logs       — Ultimi log
```

### 5.2 Dashboard Web

Nuova tab "Agenti" nella dashboard esistente con:
- Lista agenti + stato live
- Timeline esecuzioni
- Tassi di successo/fallimento
- Bottone "Run Now"

---

## Fase 6 — Migrazione dal Vecchio Sistema

### 6.1 Cron Jobs Utente Esistenti

I cron job utente attuali (file `cron_jobs.json`) restano funzionanti ma vengono re-importati come `AgentSpec` con `kind=user`.

### 6.2 System Jobs Esistenti

`sys_reflection` e `sys_morning_recap` vengono re-registrati tramite `AgentRegistry` invece di essere hardcoded in `init_scheduler()`.

---

## Roadmap e Priorità

| Fase | Cosa | Dipende da | Sforzo |
|------|------|-----------|--------|
| **1** | AgentRegistry + modello dati | — | 2-3h |
| **2a** | EventBus | — | 1-2h |
| **2b** | EventTrigger + spawning automatico | 1, 2a | 2h |
| **3** | AgentRunner (retry, timeout) | 1 | 2h |
| **4a** | File Analyzer agent | 1, 2b | 3-4h |
| **4b** | Memory Consolidation multi-utente | 1 | 1h |
| **5** | Telegram comandi + Dashboard | 1, 3 | 3h |
| **6** | Migrazione vecchi job | 1 | 1h |

**Totale stimato: ~15-18 ore**

### Priorità Consigliata

1. **Fase 1 + 3** (AgentRegistry + AgentRunner) — foundation, abilita tutto il resto
2. **Fase 6** (migrazione) — basso rischio, migliora subito la situazione attuale
3. **Fase 5** (Telegram comandi) — utile per debug e gestione
4. **Fase 2 + 4a** (EventBus + File Analyzer) — il primo vero agente automatico
5. **Fase 4b** (Memory Consolidation multi-utente) — fix del bug attuale
6. **Fase 4c, 4d** (Code Review, Idle Time) — nice-to-have

---

## Non in Scopo (per ora)

- **Agenti con tool calling autonomi** (es. "scrivi un test per questa funzione") — troppo complesso, richiederebbe sandboxing
- **Comunicazione tra agenti** (message passing, pub-sub avanzato) — overkill per single-server
- **Persistenza risultati** su Qdrant/DB — i log su file bastano
- **API REST per agenti esterni** — si può aggiungere dopo con un thin wrapper HTTP sull'AgentRegistry
