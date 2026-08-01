# 🚀 Engineering & Architectural Blueprint: Jarvis AI Development Studio (Chameleon Software Studio)

> **Documento Strategic, Technical & Operational Blueprint v5.0 (SOTA Tool-Calling & Autonomy Release)**  
> **Destinatario:** Alfio Saitta / Collateral Studios & AI Development Agents  
> **Obiettivo:** Trasformazione architetturale di Jarvis da proxy LLM/RAG Engine in un **Centro di Sviluppo Software AI-Driven Integrato (AI Software Studio)** nativo nel Web Browser, equipaggiato per la totale autonomia esecutiva su codice, script e documentazione.  
> **Data Ultima Revisione:** 29 Luglio 2026  
> **Stato:** *Pianificazione Avanzata, Analisi Codice & Mappatura Tool-Calling per Autonomia Totale (SOTA)*

---

## 📋 1. Executive Summary & Visione d'Insieme

Attualmente **Jarvis** è un ecosistema di backend asincrono ([main.py](file:///home/alfio/Projects/NeuroNet/jarvis/main.py), basato su FastAPI + Granian) con un sofisticato livello cognitivo:
- **Inferenza Locale Ottimizzata:** Nessuna dipendenza da Ollama esterno. Esegue GGUF via `llama-cpp-python` in [core/llm_engine.py](file:///home/alfio/Projects/NeuroNet/jarvis/core/llm_engine.py), adibendo la GPU RTX 3050 Ti (4GB) ai calcoli tensoriali INT4 del modello chat principale (attualmente [Qwen3.5-4B-UD-Q4_K_XL](file:///home/alfio/Projects/NeuroNet/jarvis/models/Qwen3.5-4B-UD-Q4_K_XL.gguf)), mentre le elaborazioni ausiliarie (come FastEmbed ONNX e il Gatekeeper `Qwen3.5-0.8B`) sono scaricate a 0 VRAM sulla CPU con istruzioni AVX512.
- **RAG & Analisi Grafo Strutturale:** Combinazione in tempo reale tra chunking semantico AST-Aware (Tree-sitter su 9 linguaggi via [rag/chunking.py](file:///home/alfio/Projects/NeuroNet/jarvis/rag/chunking.py)) e analisi topologica del grafo via [graph/synaptiq_engine.py](file:///home/alfio/Projects/NeuroNet/jarvis/graph/synaptiq_engine.py).

L'obiettivo strategico di questo piano è compiere il balzo evolutivo da **"Assistente LLM passivo"** a **"Piattaforma Operativa Integrata (Chameleon AI Software Studio)"**. L'interfaccia di amministrazione ([admin/panel/](file:///home/alfio/Projects/NeuroNet/jarvis/admin/panel)) si espanderà in uno studio interattivo dove lo sviluppo software avviene in modalità **Zero-Manual-Coding**: 
l'utente coordina flussi di lavoro Kanban, orchestra un team di **Sotto-Agenti AI** specializzati, visiona l'impatto topologico sul codice tramite il grafo Synaptiq e collauda/approva in diretta le modifiche in ambienti Git isolati (Worktrees), visualizzati tramite diff interattivo.

---

## 🏗️ 2. Analisi dell'Architettura Attuale e Mappatura Moduli

La nuova architettura si innesta nei design pattern nativi di Jarvis: **Singleton Asincroni per lo Stato Globale** ([core/state.py](file:///home/alfio/Projects/NeuroNet/jarvis/core/state.py)), **Modularità Ristretta (< 250 LOC per file Python)**, **Lazy Import Safety (per Synaptiq)** e **Vanilla JS/CSS Tematico ad Alte Prestazioni** nel frontend.

```
┌───────────────────────────────────────────────────────────────────────────────────────────┐
│                    CHAMELEON SOFTWARE STUDIO (WEB UI / ADMIN PANEL)                       │
│  ┌───────────────────────┐  ┌──────────────────────────┐  ┌────────────────────────────┐  │
│  │  kanban.js & dev.js   │  │   Interactive Chat Hub   │  │  Synaptiq Impact & Graph   │  │
│  │  (Ticket Drag & Drop) │  │  (stream + /no_think tag)│  │   (Sigma.js Web Worker)    │  │
│  └───────────┬───────────┘  └─────────────┬────────────┘  └─────────────┬──────────────┘  │
└──────────────┼────────────────────────────┼─────────────────────────────┼─────────────────┘
               │ JSON / REST v2             │ Server-Sent Events (SSE)    │ Graph JSON
┌──────────────▼────────────────────────────▼─────────────────────────────▼─────────────────┐
│                           JARVIS CORE BACKEND (FastAPI + Granian)                         │
│  ┌─────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ API Routing in main.py: [routes/tickets.py] ── [routes/teams.py]                    │  │
│  └─────────────────────────────────────────────────────────────────────────────────────┘  │
│  ┌───────────────────────┐  ┌──────────────────────────┐  ┌────────────────────────────┐  │
│  │   Git Worktree Engine │  │    Agentic Loop & Tags   │  │  DevStudio Store (SQLite)  │  │
│  │   [dev_studio/git.py] │  │  [agent/tool_handlers.py]│  │   [dev_studio/store.py]    │  │
│  └───────────┬───────────┘  └─────────────┬────────────┘  └─────────────┬──────────────┘  │
└──────────────┼────────────────────────────┼─────────────────────────────┼─────────────────┘
               ▼                            ▼                             ▼
┌───────────────────────────────────────────────────────────────────────────────────────────┐
│                           LOCAL SYSTEM, MODELS & PERSISTENCE                              │
│    [Git Worktree Namespaces] ──── [Qwen3.5 GPU / Gemma 4 VPS] ──── [Qdrant v3 + Mem0]      │
└───────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 🎨 3. Il Nuovo Layout del Pannello Web: "Chameleon Studio UI"

Per rispettare gli standard estetici d'eccellenza richiesti in **NeuroNet**, il layout dell'interfaccia di amministrazione ([templates/index.html](file:///home/alfio/Projects/NeuroNet/jarvis/admin/panel/templates/index.html) e [static/css/style.css](file:///home/alfio/Projects/NeuroNet/jarvis/admin/panel/static/css/style.css)) si trasformerà in una postazione di ingegneria visiva immersiva (*Visual Excellence*), pensata per meravigliare l'utente e garantire un controllo ergonomico totale a colpo d'occhio.

### 3.1. Anatomia del Nuovo Dashboard (Navigazione Estesa in [index.html:L90-L133](file:///home/alfio/Projects/NeuroNet/jarvis/admin/panel/templates/index.html#L90-L133))
L'albero del menu laterale (`#sidebar`) integrerà una nuova categoria principale **`Studio & AI Team`**, posizionata sopra le sezioni esistenti `Monitoring` e `Management`:

```
┌── ✦ NEURONET v9.7 ─────────────────────────────────────────────── [GPU: 41°C 🟢] [Tailscale 🌐] ── [👤 Alfio ▾] ──┐
│ ┌─ STUDIO & AI TEAM ─────────────┐ ┌─ WORKSPACE ATTIVA: [ AlfioSaitta / NeuroNet ] (git: main) ─────────────────┐ │
│ │ 🚀 Studio Hub                  │ │                                                                          │ │
│ │ 📋 Kanban Board          [12]  │ │   ┌── BACKLOG ─────────┐  ┌── IN PROGRESS ──────┐  ┌── REVIEW REQUIRED ──┐   │ │
│ │ ⚡ Split Inspector         [2]   │ │   │ [TICK-103] Low       │  │ [TICK-102] High     │  │ [TICK-101] Blocker  │   │ │
│ │ 👥 Team & Persona              │ │   │ Aggiungi export    │  │ UI Glassmorphism    │  │ Refactor JWT ACLs   │   │ │
│ ├─ MONITORING ───────────────────┤ │   │ 👤 Unassigned      │  │ 🌿 wt: feat/TICK-102│  │ 🌿 wt: feat/TICK-101│   │ │
│ │ 📊 Monitor                     │ │   │                    │  │ 🤖 UI Engineer (4B) │  │ 🤖 Core Arch (26B)  │   │ │
│ │ 💬 Chat                        │ │   └────────────────────┘  │ ⚡ 38.4 tok/s INT4  │  │ ⚠️ Rischio: Medio   │   │ │
│ │ 🕸️ Code Graph                  │ │                           └─────────────────────┘  │ ⏳ In attesa collaudo│   │ │
│ ├─ MANAGEMENT ───────────────────┤ │                                                    └──────────┬──────────┘   │ │
│ │ 👥 Users   🤖 Models           │ │                                                               │              │ │
│ │ ☑️ Tasks    📋 Logs             │ │                                                               ▼              │ │
│ │ 📈 Analytics ⚙️ Settings       │ │                                               [ 🟢 Approve ]  [ 🟡 Grill ]   │ │
│ │ 📁 Projects                    │ │                                                                          │ │
│ └────────────────────────────────┘ └──────────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

### 3.2. Le Quattro Nuove Viste Interattive Dettagliate

#### 1. 🚀 Vista: "Studio Hub" (Centro di Comando di Sprint & Risorse)
* **Design Token:** Sfrutta pannelli translucidi in **Glassmorphism** (`backdrop-filter: blur(16px); background: rgba(15, 23, 42, 0.7); border: 1px solid rgba(255, 255, 255, 0.08);`) con ombreggiatura al plasma e tipografia `Inter` e `JetBrains Mono` ad alto contrasto (come definito in [index.html:L7-L9](file:///home/alfio/Projects/NeuroNet/jarvis/admin/panel/templates/index.html#L7-L9)).
* **Contenuto:**  
  * **Intestazione di Risorsa:** Mostra in tempo reale lo stato delle capacità matematiche del sistema (VRAM impegnata sulla GPU RTX 3050 Ti locale via [admin/telemetry_collector.py](file:///home/alfio/Projects/NeuroNet/jarvis/admin/telemetry_collector.py), stato dei Core Tensor, e prontezza della delegazione verso il **Master VPS** su rete Tailscale).
  * **Sprint & Project Selector:** Griglia visuale delle directory analizzate in [core/config.py:WORKSPACE_PROJECTS](file:///home/alfio/Projects/NeuroNet/jarvis/core/config.py). Con un singolo clic sul progetto desiderato, l'utente cambia istantaneamente il contesto della Board Kanban e aggiorna lo stato globale tramite la funzione [set_last_project(user_id, conversation_id, project)](file:///home/alfio/Projects/NeuroNet/jarvis/core/state.py#L202) in `core/state.py`.

#### 2. 📋 Vista: "Kanban Board" (Motore del Workflow Agentico)
* **Reattivià Drag-and-Drop:** Gestita dal futuro modulo Vanilla JS `studio_kanban.js`. Le card possono essere spostate fluidamente tra le colonne, attivando una chiamata verso il nuovo router REST `PUT /api/tickets/{id}/status`.
* **Le Colonne e le Loro Magie Visive:**
  * **`Backlog & Requirements`**: Ticket in attesa di specifiche complete.
  * **`To Do`**: Requisiti pronti per l'assegnazione ad un membro o al Team AI.
  * **`In Progress (AI Autocoding)`**:  
    * **Feedback Dinamico Live:** Il bordo della card inizia a pulsare con una tenue fluorescenza azzurrina o verde fluo in base all'AI al lavoro (utilizzando classi speculari a `.dot.pulsing` in [style.css](file:///home/alfio/Projects/NeuroNet/jarvis/admin/panel/static/css/style.css)).
    * **Chip Git Worktree:** Appare un chip verde scuro che indica l'ambiente di lavoro effimero istanziato in automatico: `🌿 worktree: feature/TICK-102`.
    * **Live Telemetry Pill:** Un indicatore (confermato dai dati letti da [core/state.py:pipeline_traces](file:///home/alfio/Projects/NeuroNet/jarvis/core/state.py#L239)) che mostra la velocità di generazione (es. `⚡ 37.8 tok/s [Qwen3.5 GPU]`).
  * **`Review Required (Human Validation)`**: Card con bordo dorato scintillante, indicante l'obbligo di revisione e il livello di rischio calcolato dal grafo (*Low*, *Medium*, *High*).
  * **`Merged & Done`**: Archivio storico dei ticket risolti con il link di rimbalzo al diff del commit sul repository locale.

#### 3. ⚡ Vista: "Split Inspector" (L'Ambiente Interattivo "Grill & Iterate")
Quando l'utente clicca su un ticket in colonna `Review Required`, la UI scompare con una transizione animata aprirsi sullo **Split Inspector**: un ambiente di ispezione simultaneo diviso in tre sezioni sincronizzate:

```
┌─ [TICK-101] Refactor JWT Auth Middleware ─────────────────────────────────────────────────────────────────────────┐
│  FILE TREE & SANDBOX TERMINAL      │  LIVE DIFF & IMPACT VIEWER               │  INTERACTIVE AGENT CHAT         │
├────────────────────────────────────┼──────────────────────────────────────────┼─────────────────────────────────┤
│  📁 jarvis/                        │ ⚠️ Synaptiq Impact Analysis: MEDIUM      │ 🤖 [Core Architect]:             │
│   ├── 📁 api/                      │ 🔗 Dipendenze esposte: 4 rotte protette  │ Ho rifattorizzato auth.py per   │
│   │    └── 📄 auth.py  [+18 -4]    │ ──────────────────────────────────────── │ accogliere il multi-tenant ACL. │
│   └── 📁 routes/                   │ - def require_auth(request: Request):    │ L'albero Tree-sitter non rileva │
│        └── 📄 projects.py          │ + def require_auth(                      │ errori sintattici.              │
│ ────────────────────────────────── │ +     request: Request,                  │                                 │
│ 🐞 TEST RUNNER (pytest Sandbox):   │ +     team_id: str = Header(None)        │ 👤 [Alfio]:                      │
│ $ pytest tests/test_auth.py        │ + ):                                     │ Il team_id va estrapolato dal   │
│ 🟩 4 passed, 0 failed (0.84s)    │ +     # Verified multi-tenant scoping    │ payload JWT, non da Header!     │
│ ────────────────────────────────── │ ──────────────────────────────────────── │ ─────────────────────────────── │
│ [ ▶ Ristampa Log ] [ 🔄 Riprova ] │ [ 🟢 APPROVE & MERGE ]  [ 🔴 REJECT ]     │ [ > Invia feedback (Grill) ]    │
└────────────────────────────────────┴──────────────────────────────────────────┴─────────────────────────────────┤
```

* **Colonna 1: File Explorer & Terminale di Collaudo:**
  - Mostra l'albero dei file alterati nel namespace provvisorio `worktree`.
  - Include un visualizzatore di standard output connesso ai log di esecuzione dei test (tramite il tool `run_shell_command` e il suo executor [_tool_run_shell_command](file:///home/alfio/Projects/NeuroNet/jarvis/agent/tool_handlers.py) in `tool_handlers.py`), mostrando in diretta il superamento delle verifiche locali (`pytest`, `npm test`, linter).
* **Colonna 2: Live Diff & Rischio Topologico (Synaptiq):**
  - Il cuore visivo di Git: diff colorato con palette di contrasto morbido formattato via `marked.js` e `highlight.js` (caricati in [index.html:L8-L12](file:///home/alfio/Projects/NeuroNet/jarvis/admin/panel/templates/index.html#L8-L12)).
  - In alto spicca il **Semaforo di Impatto Synaptiq**: visualizza istantaneamente col grafo vettoriale miniauturizzato (tramite Web Worker di *Sigma.js* gestito da [static/js/graph.js](file:///home/alfio/Projects/NeuroNet/jarvis/admin/panel/static/js/graph.js)) quali file subiranno potenziali ripercussioni se il codice viene unito nel ramo `main`.
* **Colonna 3: Chat Interattiva di Revisione ("Grill Me"):**
  - Connessa via UUID persistente `conversation_id` 1:1 al Ticket, riutilizzando il protocollo di stream in [static/js/chat.js](file:///home/alfio/Projects/NeuroNet/jarvis/admin/panel/static/js/chat.js).
  - L'utente conversa direttamente col Sotto-agente che ha redatto la patch per richiedere aggiustamenti istantanei nel worktree, aggiornando il diff in tempo reale senza uscire dalla vista!

#### 4. 👥 Vista: "Team & Agenti" (Governo delle "Persona" AI e Modelli)
Griglia visiva con avatar per gestire sviluppatori umani (via [require_admin](file:///home/alfio/Projects/NeuroNet/jarvis/api/auth/auth.py)) e configurare i Sotto-Agenti AI evocati durante lo sviluppo, assegnando a ciascuna Persona un profilo hardware esplicito delegato a [core/model_profiles.py](file:///home/alfio/Projects/NeuroNet/jarvis/core/model_profiles.py):

| Nome Profilo UI & Avatar | Descrizione Ruolo (Persona) | Hardware Assigned & LLM Engine Setup | Tooling Mapped (Permessi ACL) |
| :--- | :--- | :--- | :--- |
| **🏛️ System Architect** | Architetto del software, supervisore dei pattern strutturali e coordinatore grafo. | **Gemma 4 26B** su **Master VPS (Remote Tailscale)** - Contesto esteso a 32,768 token. | Sola lettura (RAG Tree-sitter, ricerca Synaptiq, consultazione Web via SearXNG). |
| **💻 Code Engineer** | Scrittore veloce di moduli Python/JS, esperto di refactoring e stile <250 LOC. | **Qwen3.5-4B** su **Worker Host Locale (RTX 3050 Ti)** - 100% GPU Layer, INT4 Tensor core (`~35-40 tok/s`). | Full Write (scrittura file multi-chunk, manipolazione Git nel worktree via `_tool_replace_in_file`). |
| **🐞 QA & Debugber Bot** | Ispettore log, analista eccezioni e verificatore suite di test esecutivi. | **Qwen3.5-4B** (Worker locale) con supporto del **Gatekeeper Qwen3.5-0.8B (CPU)** per la compressione di stack-trace lunghi (>2048 ctx). | Tool di esecuzione shell con Pager `run_shell_command` in ambiente sandbox. |
| **🛡️ Security Auditor** | Check vulnerabilità di codice, validazione permessi JWT e revisione PR. | **Gemma 4 E2B QAT** (Worker locale) - `N_GPU_LAYERS=15` (bloccato in [model_profiles.py](file:///home/alfio/Projects/NeuroNet/jarvis/core/model_profiles.py)), no FlashAttn (1036 MiB VRAM). | Ispezione Git Diff (`_tool_git_diff`), linter statico e audit delle dipendenze. |

---

## 🧭 4. Cosa Si Troverà Davanti l'Utente All'Accesso ("The First-Login Experience")

Dal momento esatto in cui l'utente digita `http://localhost:8000/admin` nel proprio browser, si attiva un'esperienza visiva ad alta immersione e produttività:

### Step 1: L'Ingresso Solenne & Reattivo (26ms Greeting Short-Circuit)
1. L'utente supera il login protetto tramite JWT (cookie `httpOnly` verificato da [checkAuth() in main.js:L9](file:///home/alfio/Projects/NeuroNet/jarvis/admin/panel/static/js/main.js#L9) e [auth.py](file:///home/alfio/Projects/NeuroNet/jarvis/api/auth/auth.py)).
2. **Il Benvenuto Istantaneo:** Grazie al recente *Greeting Short-Circuit* nel motore di Jarvis, una frase di saluto dell'AI o l'inizializzazione del pannello si risolve in appena **26 millisecondi**, senza attendere i 60-70 secondi di caricamento dei pesanti pesi GGUF in VRAM.
3. Il cruscotto superiore ([navbar-status in index.html:L68](file:///home/alfio/Projects/NeuroNet/jarvis/admin/panel/templates/index.html#L68)) pulsa docilmente: la spia verde GPU avvisa che la scheda grafica locale è fresca (`41°C` via `nvidia-smi`), Qdrant è agganciato al grafo locale e la VPN Tailscale è in ascolto sul master.

### Step 2: La Routine Operativa Zero-Manual-Coding (Il Flusso "No-IDE")
L'utente non ha bisogno di aprire VS Code o Neovim per programmare. Si posiziona sulla scheda **🚀 Studio Hub**:
1. Seleziona il progetto attivo, ad esempio **`AlfioSaitta / NeuroNet`**. Il sistema interroga via aiosqlite il database `data/dev_studio.db` e dispiega la **📋 Kanban Board** animata con transizioni fluide di espulsione card.
2. **Assegnazione di un Compito:**
   - L'utente individua una card nella colonna `To Do`, come *"TICK-104: Aggiungi esportazione in CSV per le metriche di telemetria"*.
   - Afferra la card e con il cursore la trascina dentro la colonna `In Progress [AI Autocoding]`, rilasciandola sopra il profilo dell'agente 💻 **Code Engineer**.
3. **Il Teatro Virtuale dell'AI al Lavoro:**
   - Senza indugi, Jarvis riceve la chiamata REST e invoca il tool `git_workspace`: sull'host in esecuzione si genera il worktree invisibile `/tmp/jarvis_worktrees/TICK-104`.
   - La card sulla board si anima: una barra di avanzamento color ciano inizia a correre sul fondo del Ticket, riflettendo la pipeline di tracciamento di [core/telemetry_api.py](file:///home/alfio/Projects/NeuroNet/jarvis/core/telemetry_api.py). L'utente vede in diretta le notifiche istantanee:
     - `🔍 [RAG Tree-sitter]: Ispezionati 3 file in core/telemetry_api.py`
     - `✍️ [Code Engineer]: Modifica in corso (37 tok/s su RTX 3050 Ti)...`
     - `🐞 [QA & Debug]: Esecuzione test unitario di salvataggio... 🟩 PASSED`

### Step 3: Il Collaudo e la Soddisfazione Ingegneristica
1. Concluso il compito (in genere 30-90 secondi in background), la card del ticket si sposta da sola (tramite il tag XML `<TICKET_STATE status="REVIEW_REQUIRED"/>`) verso la colonna `Review Required` con un'animazione luminosa ambra e un segnale acustico acuto ma soffuso (accompagnata da una notifica push sul telefono dall'ascoltatore [tg_bot/bot.py](file:///home/alfio/Projects/NeuroNet/jarvis/tg_bot/bot.py)).
2. L'utente clicca sulla card arancione: lo schermo intero svela la vista **⚡ Split Inspector**.
3. Esamina l'albero di impatto Synaptiq al centro: il semaforo è **🟢 LOW RISK**, confermando che nessuna dipendenza di sistema è compromessa. Legge il Live Diff: la patch è pulita, rispetta il limite formale delle <250 LOC e presenta le docstrings originali conservate.
4. L'utente sorride, spunta la casella del test visivo nel terminale incorporato e preme il tasto principale: **🟢 Approve & Merge**.
5. Con una micro-animazione di conferma, il sistema unisce istantaneamente le modifiche su Git `main`, ripulendo la cartella `/tmp/jarvis_worktrees/` e dicendo al demone Qdrant di re-indicizzare in 5 secondi via [ingest_local_documents()](file:///home/alfio/Projects/NeuroNet/jarvis/rag/engine.py#L516) le funzioni appena introdotte nel motore di intelligenza RAG.

---

## 🛠️ 5. Dettagli Tecnologici, Hook e Mappatura sul Codice Esistente

Per sostenere un layout così ricco pur mantenendo assoluta fedeltà alla regola **<250 LOC per modulo**, lo strato dati e gli handler verranno spartiti in piccoli sub-helpers asincroni specializzati sotto **`jarvis/dev_studio/`**, ed innestati a caldo in [main.py](file:///home/alfio/Projects/NeuroNet/jarvis/main.py).

### 5.1. Hook nel Main Router ([main.py:L291-L305](file:///home/alfio/Projects/NeuroNet/jarvis/main.py#L291))
Attualmente `main.py` registra i router delle API e della dashboard attorno alle righe 291-303:
```python
# Mappatura esistente in main.py:
app.include_router(dashboard_router)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(profile_router)
app.include_router(projects_router)

# ➕ NUOVA INIEZIONE CHAMELEON STUDIO (Fase 2):
from routes.tickets import router as tickets_router
from routes.teams import router as teams_router
app.include_router(tickets_router)
app.include_router(teams_router)
```

---

### 5.2. Persistenza Relazionale SQLite ([dev_studio/store.py](file:///home/alfio/Projects/NeuroNet/jarvis/dev_studio/store.py) e [dev_studio/models.py](file:///home/alfio/Projects/NeuroNet/jarvis/dev_studio/models.py))
Adottando il design pattern asincrono di [api/auth/user_manager.py:L59](file:///home/alfio/Projects/NeuroNet/jarvis/api/auth/user_manager.py#L59) (utilizzo nativo di **`aiosqlite`** in un Singleton istenziato nel lifecycle in [core/lifecycle.py](file:///home/alfio/Projects/NeuroNet/jarvis/core/lifecycle.py)), il sistema integrerà il DB relazionale ausiliario `data/dev_studio.db`:

```sql
-- DDL del database dev_studio.db (Chameleon Software Studio)

CREATE TABLE IF NOT EXISTS dev_projects (
    id TEXT PRIMARY KEY,                       -- UUID v4
    name TEXT UNIQUE NOT NULL,                 -- Mappa in core/config.py (WORKSPACE_PROJECTS)
    repository_path TEXT NOT NULL,             -- Path locale es: "/home/alfio/Projects/NeuroNet"
    qdrant_collection TEXT NOT NULL,           -- Mappa al get_project_col_name in core/qdrant_utils.py
    default_branch TEXT DEFAULT 'main',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS team_profiles (
    id TEXT PRIMARY KEY,                       -- Es: "ai-role-architect" o UUID utente
    project_id TEXT REFERENCES dev_projects(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL,                -- Es: "Core Architect (Gemma 4)", "Alfio (Lead)"
    profile_type TEXT CHECK(profile_type IN ('HUMAN', 'AI_SUBAGENT')),
    role_title TEXT NOT NULL,                  -- Es: "System Architect", "QA & Debug"
    system_prompt_override TEXT,               -- System prompt persona per la generazione AI in agent/prompt.py
    target_model_family TEXT DEFAULT 'inherit',-- 'qwen', 'gemma', 'deepseek', o 'inherit' per auto-detection
    allowed_tool_groups TEXT DEFAULT '["all"]',-- JSON array di permessi (es. '["read", "synaptiq"]')
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,                       -- Codice leggibile, es: "TICK-101"
    project_id TEXT REFERENCES dev_projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT DEFAULT 'BACKLOG',              -- BACKLOG, TODO, IN_PROGRESS, REVIEW_REQUIRED, DONE, BLOCKED
    priority TEXT DEFAULT 'MEDIUM',             -- LOW, MEDIUM, HIGH, BLOCKER
    assignee_id TEXT REFERENCES team_profiles(id),
    conversation_id TEXT UNIQUE NOT NULL,      -- Chiave esterna logica alle chat di memoria Mem0 & State
    worktree_path TEXT,                        -- Path isolato ("/tmp/jarvis_worktrees/TICK-101")
    target_branch TEXT,                        -- Branch del ticket ("feature/TICK-101-auth")
    impact_report_json TEXT,                   -- Output serializzato del Synaptiq Impact Analyzer
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

### 5.3. Mappatura Dispatch Table nei Tool Handlers ([agent/tools.py:L910](file:///home/alfio/Projects/NeuroNet/jarvis/agent/tools.py#L910))
Attualmente, la dispatch table in `agent/tools.py` mappa stringhe ai rispettivi esecutori:
```python
_HANDLERS = {
    "read_file": _tool_read_file,
    "read_file_range": _tool_read_file_range,
    "write_file": _tool_write_file,
    "replace_in_file": _tool_replace_in_file,
    # ➕ ESTENSIONI DEV STUDIO (Importate dal modulo dev_studio/tool_extensions.py per <250 LOC):
    "ticket_update_status": _tool_ticket_update_status,
    "synaptiq_impact_check": _tool_synaptiq_impact_check,
    "git_worktree_commit": _tool_git_worktree_commit,
}
```

* **L'Esecutore di Rischio con Lazy Import (Pattern Inviolabile):**
  Nel nuovo file `dev_studio/tool_extensions.py`, l'analisi topologica ottempera inderogabilmente alla sicurezza di importazione:
  ```python
  # ✅ CORRETTO — Import lazily dentro try/except per la sicurezza assoluta dei processi
  async def _tool_synaptiq_impact_check(args: dict, confirmation_mgr=None):
      if SYNAPTIQ_ENABLED:
          try:
              from graph.synaptiq_engine import synaptiq_engine
              return await synaptiq_engine.get_impact_analysis(args.get("target_file"))
          except Exception as e:
              logger.warning(f"Synaptiq fallback in DevStudio tool: {e}")
      return {"risk": "LOW", "affected_files": [], "error": "Synaptiq disabled or failed"}
  ```

---

### 5.4. Automazione Sincronizzazione RAG post-Merge ([rag/engine.py:L516](file:///home/alfio/Projects/NeuroNet/jarvis/rag/engine.py#L516))
Quando l'utente preme **🟢 Approve & Merge** dall'interfaccia Split Inspector, la rotta API `POST /api/tickets/{id}/approve` esegue una chiamata diretta alle funzioni del motore RAG e al database di stato:

```python
# Hook di fusione e sincronizzazione AST Tree-sitter su Qdrant
import core.state as state
from rag.engine import ingest_local_documents
from core.qdrant_utils import get_project_col_name

async def handle_ticket_approval(ticket_id: str, user: dict):
    ticket = await dev_store.get_ticket(ticket_id)
    # 1. Esegui il merge git asincrono nel repository locale
    await git_workspace.merge_worktree_branch(ticket.project_id, ticket.worktree_path, ticket.target_branch)
    # 2. Aggiorna la variabile di progetto su state
    state.set_last_project(user_id=user["username"], conversation_id=ticket.conversation_id, project=ticket.project_id)
    # 3. Triggera l'ingestion semantica Tree-sitter solo per questo progetto su Qdrant!
    asyncio.create_task(ingest_local_documents(single_project_path=ticket.repository_path))
    # 4. Spunta il ticket come DONE
    await dev_store.update_ticket_status(ticket_id, "DONE")
```

---

## 🧩 6. Catalogo Degli Strumenti per l'Autonomia Totale Software (Tools Architecture)

Per trasformare Jarvis in un Software Engineer completamente autonomo, in grado di operare sulla Kanban Board senza alcun aiuto manuale o scrittura di codice esterna (*Zero-Manual-Coding*), l'ecosistema di tool-calling sarà integrato con un nuovo catalogo di strumenti agentici ad alta specializzazione.

> **⚠️ REGOLA COSTITUZIONALE DI MODULARITÀ (<250 LOC):**  
> Durante l'estrazione moduli del 29/07, il file [agent/tool_handlers.py](file:///home/alfio/Projects/NeuroNet/jarvis/agent/tool_handlers.py) ha già raggiunto le 630+ linee per inglobare le funzioni storiche dal core. Per non violare la regola formale dei 250 LOC su *nuovi* file e non gonfiare ulteriormente i file legacy, i nuovi strumenti saranno immatricolati in quattro moduli ultra-leggeri (ciascuno < 220 righe) all'interno del pacchetto `jarvis/dev_studio/`:
> 1. `dev_studio/tools_code.py`: Refactor, analisi LOC e modifiche multi-blocco.
> 2. `dev_studio/tools_git.py`: Worktrees, merge e sincronizzazione con rami Git.
> 3. `dev_studio/tools_devops.py`: Esecutori di test unitari, linter automatici e sandbox scratch.
> 4. `dev_studio/tools_doc.py`: Generazione diagrammi Mermaid, changelog e documentazione utente.

I nuovi tool si agganceranno in tempo reale al dispatch table `_HANDLERS` di [agent/tools.py:L910](file:///home/alfio/Projects/NeuroNet/jarvis/agent/tools.py#L910), estendendo i tre pilastri dell'autonomia software:

---

### 6.1. Pilastro 1: Gestione Autonoma del Codice Sorgente (Source & AST Tools in `tools_code.py` / `tools_git.py`)

| Nome Tool | Tipologia | Scopo e Comportamento nel Flusso Agentico | Intervallo / Aggancio Backend |
| :--- | :--- | :--- | :--- |
| **`multi_replace_file_content`** | Modifica <br>*(Nuovo)* | Risolve il limite dell'attuale `replace_in_file` (che fallisce su blocchi non adiacenti o riscrizioni multiple). Permette al Sotto-agente AI di specificare un array di `ReplacementChunks` (StartLine, EndLine, TargetContent, ReplacementContent) in una singola passata atomica sul file target all'interno del worktree. Riduce del 60% l'uso di token ed elimina race conditions. | `dev_studio/tools_code.py` <br>(Estende `_HANDLERS` in [tools.py:L910](file:///home/alfio/Projects/NeuroNet/jarvis/agent/tools.py#L910)) |
| **`file_move` / `file_rename`** | Refactoring <br>*(Nuovo)* | Consente l'esecuzone di un `git mv` sicuro per rinominare o spostare moduli e file di configurazione senza perdere la history Git né gli indici semantici su Qdrant. | `dev_studio/tools_git.py` |
| **`ast_symbol_definition`** | Ricerca RAG <br>*(Nuovo)* | Tool di ricerca strutturale istantanea. Invece di usare chiamate costose con `search_code` (grep cieco), si poggia al parser Tree-sitter di [rag/chunking.py](file:///home/alfio/Projects/NeuroNet/jarvis/rag/chunking.py) e al grafo in [graph/synaptiq_engine.py](file:///home/alfio/Projects/NeuroNet/jarvis/graph/synaptiq_engine.py) per restituire esattamante file, intervallo di riga e firma del simbolo cercato (`class`, `def`, `interface`). | `dev_studio/tools_code.py` |
| **`git_worktree_create`** | Worktree <br>*(Nuovo)* | Evocato non appena un Ticket viene assegnato alla colonna *In Progress*. Creata un branch isolato `feature/<ticket_id>` collegato al path temporaneo `/tmp/jarvis_worktrees/<ticket_id>`. Assicura che nessun test o scrittura dell'AI inquini il workspace locale principale dell'utente. | `dev_studio/tools_git.py` |
| **`git_worktree_remove`** | Worktree <br>*(Nuovo)* | Smantella istantaneamente la sandbox `/tmp/jarvis_worktrees/` in caso di rifiuto patch (Reject) o dopo un'operazione di merge conclusa con successo. | `dev_studio/tools_git.py` |
| **`read_file` / `search_code`** | Lettura <br>*(Esistenti)* | Tools storici (righe 60 e 93 di [agent/tool_handlers.py](file:///home/alfio/Projects/NeuroNet/jarvis/agent/tool_handlers.py)) riutilizzati dall'AI in modalità di sola lettura per contestualizzare gli snippet di codice. | [tool_handlers.py:L60](file:///home/alfio/Projects/NeuroNet/jarvis/agent/tool_handlers.py#L60) |

---

### 6.2. Pilastro 2: Esecuzione Script, DevOps & Collaudo Automatico (QA Tools in `tools_devops.py`)

| Nome Tool | Tipologia | Scopo e Comportamento nel Flusso Agentico | Intervallo / Aggancio Backend |
| :--- | :--- | :--- | :--- |
| **`run_test_suite`** | Testing <br>*(Nuovo)* | Esecutore strutturato per suite unitarie (`pytest`, `go test`, `npm test`, `cargo test`). L'agente esegue il test all'interno del worktree. Il tool filtra i log verbosi e restituisce al modello solo i sommari operativi, il numero di test superati/falliti e le specifiche righe di eccezione in rosso, popolando la console integrata nella UI dello **Split Inspector**. | `dev_studio/tools_devops.py` <br>(Sostituisce `run_shell_command` per le QA Persona) |
| **`run_linter_fixer`** | Code Quality <br>*(Nuovo)* | Tool automatico di correzione preventiva. Esegue `ruff check --fix` su Python, `eslint --fix` su TS/JS o `gofmt` su Go nel worktree del Ticket. Viene richiamato obbligatoriamente dal Sotto-agente prima di impostare la card su `Review Required`, garantendo stile e zero warning di import orfanati. | `dev_studio/tools_devops.py` |
| **`script_execute_scratch`** | Sandbox <br>*(Nuovo)* | Permette all'AI di creare ed eseguire script esplorativi effimeri (es. verifiche algoritmi, elaborazioni dati o test riproduzione bug) facendoli girare nella cartella protetta `data/scratch/<conversation_id>/`. Non rilascia artefatti accidentali su Git e cancella la memoria al termine della task. | `dev_studio/tools_devops.py` |
| **`run_shell_command`** | Shell <br>*(Esistente)* | Tool storico in [agent/tool_handlers.py:L519](file:///home/alfio/Projects/NeuroNet/jarvis/agent/tool_handlers.py#L519). Mantenuto per le sole operazioni di sistema dell'Amministratore o di build esplicite con paginazione ridotta `PAGER=cat`. | [tool_handlers.py:L519](file:///home/alfio/Projects/NeuroNet/jarvis/agent/tool_handlers.py#L519) |

---

### 6.3. Pilastro 3: Gestione della Documentazione, Diagrammi e Regole (Doc Tools in `tools_doc.py`)

| Nome Tool | Tipologia | Scopo e Comportamento nel Flusso Agentico | Intervallo / Aggancio Backend |
| :--- | :--- | :--- | :--- |
| **`doc_verify_loc_constraint`** | **Normativa / ACL** <br>*(Nuovo & CRITICO)* | **Il Guardiano delle 250 LOC:** Prima della sottomissione in `Review Required`, l'agente esegue questo tool di compliance su tutti i file `.py` e `.js` toccati dal Ticket. Conta le righe reali ignorando linee vuote. Se rileva un modulo >250 LOC, rifiuta di promuovere la card e impone al modello un refactoring di scissione sub-modulare! | `dev_studio/tools_doc.py` |
| **`diagram_generate_mermaid`** | Visual Design <br>*(Nuovo)* | Permette al **System Architect** di generare diagrammi di flusso di architettura e grafi delle chiamate in sintassi `mermaid`. Questi grafi si incorporano come anteprime visuali interattive nel riassunto della pull request all'interno dello **Split Inspector**, svelando all'istante all'utente la logica di interazione tra oggetti e funzioni. | `dev_studio/tools_doc.py` |
| **`doc_generate_changelog`** | Autoconsistenza <br>*(Nuovo)* | Sincronizza automaticamente il file `CHANGELOG.md` e le note del Ticket riprendendo l'analisi semantica del diff e il semaforo di rischio importato via *Synaptiq Engine*. Genera report completi con alert esplicativi `[!NOTE]`, `[!WARNING]` o `[!IMPORTANT]`. | `dev_studio/tools_doc.py` |
| **`doc_update_readme`** | Sincronizzazione <br>*(Nuovo)* | Tool azionato a collaudo ultimato o durante un importante cambio infrastrutturale per allineare le guide in `docs/` e `README.md` in presenza di nuovi endpoint REST o parametri `.env`. Preserva fedelmente docstrings e commenti preesistenti nel codebase. | `dev_studio/tools_doc.py` |
| **`skill_create_workflow`** | Distillazione <br>*(Nuovo)* | Raccoglie la traiettoria di un ticket superato (come un refactor o un settaggio complicato di porte) e genera un pacchetto YAML/Markdown permanente salva-tempo, allocandolo in `builtin/skills/<skill_name>/SKILL.md` in modo che Jarvis possa riutilizzare istanteamente questa conoscenza nelle chat future (coerente al comando utente `/learn`). | `dev_studio/tools_doc.py` <br>(Integrato con `load_skill` [tool_handlers.py:L581](file:///home/alfio/Projects/NeuroNet/jarvis/agent/tool_handlers.py#L581)) |

---

## 📡 7. Sincronizzazione Notifiche su Telegram & Telemetria Realtime

* **Webhook Esteso nel Bot Telegram ([tg_bot/bot.py](file:///home/alfio/Projects/NeuroNet/jarvis/tg_bot/bot.py)):**
  - Ricezione avviso istantaneo: 
    ```
    🤖 [Chameleon AI Studio] - TICK-101 pronto per la revisione!
    📌 Titolo: Refactoring del Middleware di Autenticazione JWT
    ⚠️ Impatto Synaptiq: MEDIUM (4 file rotti valutati da Tree-sitter)
    💬 Per collaudare e unire in main, invia il comando: /ticket approve TICK-101
    💬 Per respingere le modifiche: /ticket reject TICK-101
    ```
  - **Creazione Vocale Ticket (On-the-go):** L'utente invia una nota vocale al bot Telegram. Jarvis sfrutta il proprio pipeline in `prompt.py` per estrapolare i requisiti, compilare il campo `description`, creare il ticket su `dev_studio.db`, posizionarerla in colonna `TO DO` della board e associarla a **System Architect (Gemma 4 26B)**!
* **Analisi dei Consumi nella Telemetria ([admin/panel/static/js/telemetry.js](file:///home/alfio/Projects/NeuroNet/jarvis/admin/panel/static/js/telemetry.js)):**
  - I grafici su `charts.js` non misureranno più solo `tok/s` grezzi dal ring buffer [state.py:pipeline_traces](file:///home/alfio/Projects/NeuroNet/jarvis/core/state.py#L239), ma suddivideranno le performance in base al Ticket completato dalle specifiche "Persona AI", mostrando il risparmio VRAM garantito dal Gatekeeper (`Qwen3.5-0.8B` su CPU) per stack-trace estesi.

---

## 📅 8. Roadmap Operativa e Implementazione per Fasi (Phasing Plan)

| Step | Nome Fase | File Target & Simboli Coinvolti | Budget LOC | Descrizione Risultati e Avanzamenti |
| :--- | :--- | :--- | :--- | :--- |
| **Fase 1** | **Database & Core Store Layer** | **Nuovo:** `dev_studio/__init__.py`<br>**Nuovo:** `dev_studio/store.py` (Singleton `aiosqlite`)<br>**Nuovo:** `dev_studio/models.py`<br>**Edit:** `main.py` | `<250 LOC`<br>`<200 LOC`<br>`<150 LOC`<br>`+8 linee` | Implementazione singleton `aiosqlite` di `data/dev_studio.db` con tabelle di governance per progetti, team e ticket. Agganciamento al lifespan asincrono in `main.py`. |
| **Fase 2** | **Routing & API Security** | **Nuovo:** `routes/tickets.py`<br>**Nuovo:** `routes/teams.py`<br>**Edit:** `api/auth/auth.py` (`require_auth`) | `<240 LOC`<br>`<200 LOC`<br>`+15 linee` | Creazione endpoint REST protetti e tools MCP v2 ([api/mcp/server_v2.py](file:///home/alfio/Projects/NeuroNet/jarvis/api/mcp/server_v2.py)) per gestione stati Kanban, ruoli e tracciamento `conversation_id`. |
| **Fase 3** | **Tool-Calling per L'Autonomia Totale** | **Nuovo:** `dev_studio/tools_code.py`<br>**Nuovo:** `dev_studio/tools_git.py`<br>**Nuovo:** `dev_studio/tools_devops.py`<br>**Nuovo:** `dev_studio/tools_doc.py`<br>**Edit:** `agent/tools.py` (`_HANDLERS`) | `<220 LOC`<br>`<200 LOC`<br>`<220 LOC`<br>`<180 LOC`<br>`+35 linee` | Implementazione del catalogo completo di tools per manipolazione multi-chunk, collaudi strutturati nel worktree, sandboxed scratch e verifica inflessibile della regola `< 250 LOC`. |
| **Fase 4** | **Chameleon Studio UI & Kanban JS**| **Nuovo:** `static/js/studio_kanban.js`<br>**Nuovo:** `static/js/studio_split.js`<br>**Nuovo:** `static/js/studio_team.js`<br>**Edit:** `index.html`, `style.css` | `<250 LOC`<br>`<240 LOC`<br>`<180 LOC`<br>`+60 linee` | Sviluppo del pannello web immersivo in Vanilla JS con Drag-and-Drop per la Board Kanban, Glassmorphism tematico e Split-View Inspector con diff su `marked.js`/`highlight.js`. |
| **Fase 5** | **AI Subagents & Hardware Binding** | **Edit:** `core/model_profiles.py` (`_family_hardware_defaults`)<br>**Edit:** `agent/prompt.py` (`build_omniscient_prompt`) | `+45 linee`<br>`+40 linee` | Interconnessione delle Persona AI ai profili di auto-detection GGUF (smistando l'architettura su Gemma 4 VPS e la scrittura di codice su Qwen3.5 GPU). |
| **Fase 6** | **Synaptiq Impact & Telegram Hooks** | **Edit:** `tg_bot/bot.py`<br>**Edit:** `graph/synaptiq_bridge.py` | `+55 linee`<br>`+30 linee` | Iniezione dell'Impact Analyzer di Synaptiq nei report dei Ticket e aggancio comandi di collaudo `/ticket approve` su canali Telegram e voice notes. |

---

## 🔒 9. Regole Ferree & Prevenzione Errori (Checklist per gli Agenti AI)

Per il successo incondizionato e la stabilità dell'architettura nel tempo, ogni operazione futura da parte degli Agenti AI e sviluppatori dovrà sottostare rigidamente ai 6 precetti costituzionali del progetto:
1. **Nessun File sopra le 250 LOC (Tool di verifica integrato):** Qualora un nuovo file JS o Python come `studio_kanban.js` o `routes/tickets.py` si avvicinasse alla linea di guardia delle 240 linee, dovrà subire il frazionamento sub-modulare immediato (es: `studio_kanban_events.js` o `routes/ticket_helpers.py`), garantito in esecuzione dal tool `doc_verify_loc_constraint`.
2. **Nessun Override su `N_GPU_LAYERS` in Creazione Ticket AI:** Gli sviluppatori o i Sotto-agenti evocheranno il modello fuggendo configurazioni manuali e basandosi sul rilevamento binario dell'header GGUF di `_family_hardware_defaults()` in [core/model_profiles.py](file:///home/alfio/Projects/NeuroNet/jarvis/core/model_profiles.py) (mai forzare layer `-1` su Gemma 4 su laptop RTX 3050 Ti: rischio segfault accidentali e sovraccarichi VRAM).
3. **Import Safety Tassativo su Synaptiq Engine:** Qualsiasi chiamata o riferimento al motore di grafi in DevStudio dovrà avvenire dentro la funzione di ispezione a runtime via lazy load e con gestione eccezioni `try/except` (vedi Sezione 5.3).
4. **Nessun Framework Eccessivo nella Web UI o DB:** Non installare o suggerire librerie estranee come SQLAlchemy per il database o React/Tailwind pre-compilato nel pannello admin. Mantenere fede alla velocità nativa di **FastAPI + Granian + aiosqlite + Vanilla JS/CSS moderno**.
5. **Isolamento del Workspace (`conversation_id` universale):** Le operazioni esecutive comandate dalla board Kanban richiederanno l'identificativo univoco `conversation_id` (rilevabile in `state.py`) per storicizzare log e riflessioni nella memoria Mem0 del ticket.
6. **Inviolabilità di Commenti e Docstrings:** Qualunque modifica correttiva apportata a file Python o JS esistenti non cancellerà mai né altererà le docstrings originarie e le spiegazioni sintattiche pre-esistenti all'interno del codice.

---

*Il presente documento di ingegneria visiva e strutturale risiede sul filesystem in `docs/plans/jarvis_ai_dev_studio_plan.md` ed è configurato come la fonte della verità per l'avanzamento tecnologico verso **Chameleon Software Studio**.*
