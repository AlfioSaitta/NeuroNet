# Piano: Admin Panel — Gestione Progetti

> **Stato:** Cross-Check Completato — Pronto per esecuzione  
> **Richiedente:** Alfio  
> **Data:** 2026-07-20  
> **Baseline:** commit `e7a57c1` (20/07/2026)

---

## 1. Obiettivo

Aggiungere al pannello di amministrazione (`/admin/`) una sezione **Projects** che permetta di:

1. **Lista progetti RAG** con metadati (punti Qdrant, dimensione, ultimo indicizzato, path)
2. **Re-indicizzare** un progetto specifico (oggi solo globale)
3. **Eliminare collezione Qdrant** di un progetto
4. **Registrare** un nuovo progetto (aggiungere path a EXTERNAL_PROJECTS via dashboard)
5. **User editing: multiselect progetti** al posto del campo testo libero `allowed_projects`

---

## 2. Stato Attuale — Mappatura Precisa

### 2.1 Collezioni Qdrant

| Aspetto | Valore |
|---|---|
| Naming pattern | `collateral_docs_{sanitized_name}_{VECTOR_DB_VERSION}` |
| Funzione esistente | `rag.py:631` — `get_project_col_name(project_name)` |
| `VECTOR_DB_VERSION` | `config.py:204` — default `"v1"`, sovrascrivibile via `.env` |
| Lettura progetti | `rag.py:1617` — `list_rag_projects(user=None)` → estrae nome da `c.name` stripping prefisso `"collateral_docs_"` e suffisso `_v\d+` |
| ACL filter | Se `user.role != "admin"`, filtra per `user.allowed_projects` (JSON list o `["*"]`) |

### 2.2 Ingest Documenti

| Funzione | Firma | Modificabile? |
|---|---|---|
| `ingest_local_documents()` | `rag.py:850` — nessun parametro | **NO** — va aggiunto parametro opzionale `single_project_path: str \| None = None` |
| Scansione | 1. WORKSPACE_PROJECTS → 2. EXTERNAL_PROJECTS → 3. DOC_DIR legacy | Filtrabile per progetto saltando gli altri |
| Re-index globale | `dashboard.py:1531` — `trigger_rag_reindex()` → lancia `ingest_local_documents()` in background | Esistente, non modificare |

**NOTA:** `ingest_local_documents()` NON supporta re-index per-singolo-progetto. È necessario modificarla per accettare `single_project_path` opzionale. Quando fornito, deve:
- SKIP punti 1-2-3 (scansione directory) se non pertinenti
- Processare SOLO i file del progetto specificato
- NON toccare `rag_state` degli altri progetti

### 2.3 Project Path Discovery

| Sorgente | Tipo | Accessibile da |
|---|---|---|
| `WORKSPACE_PROJECTS` | `config.py:191` — `list[str]` di path assoluti | Import diretto `from config import WORKSPACE_PROJECTS` |
| `EXTERNAL_PROJECTS` | `config.py:205` — stringa env `"path:name,path:name"` | `from config import EXTERNAL_PROJECTS` |
| `parse_external_projects()` | `config.py:209` → `list[str]` di path assoluti | Già importata in `rag.py:40` |
| `_find_project_root(filepath)` | `rag.py:1788` → path progetto o `None` | Interna a `rag.py` — da esportare come pubblica `find_project_root` |

### 2.4 Persistenza `.env`

| Funzione | Location | Meccanismo |
|---|---|---|
| `_persist_env(key, value)` | `dashboard.py:1648` | Lettura→modifica→scrittura atomica via `os.replace()` |
| `_ENV_FILE_PATH` | `dashboard.py:1645` | `jarvis/../.env` = `$PROJECT_ROOT/.env` |

**Compatibilità EXTERNAL_PROJECTS:** `_persist_env("EXTERNAL_PROJECTS", new_value)` funziona correttamente perché fa replace della riga `EXTERNAL_PROJECTS=...` nel `.env` (matcha `stripped.startswith(key_prefix)`).

### 2.5 Auth Pattern Esistente

| File | Router Prefix | Dipendenza |
|---|---|---|
| `routes/users.py:12` | `/api/users` | `dependencies=[Depends(require_admin)]` — globale |
| `routes/profile.py:13` | `/api/auth` | `dependencies=[Depends(require_auth)]` — globale |
| `auth.py:24` | `/api/auth` | Nessuna globale — check per-endpoint |

`get_current_user` (`auth.py:71`) estrae JWT da:
1. Cookie `access_token`
2. Header `Authorization: Bearer <token>`

**Funziona su QUALSIASI path**, non solo `/api/dashboard/*`. Quindi i nuovi endpoint projects possono avere prefix `/api/projects` e usare `Depends(require_admin)`/`Depends(require_auth)`.

### 2.6 Middleware Dashboard Auth (`main.py:592`)

- Protegge `/api/dashboard/*` e `/admin/*` con JWT
- `ADMIN_ONLY_PATHS` (`main.py:575`) — tuple di prefissi path admin-only
- I nuovi endpoint projects NON vanno sotto `/api/dashboard/` per evitare conflitti col middleware. Usare prefix `/api/projects`.

### 2.7 Schema Utenti

```sql
allowed_projects TEXT DEFAULT '[]'  -- JSON list: ["NeuroNet", "SlotBuilder"] o ["*"]
```

### 2.8 Frontend Sidebar Attuale

```
Monitoring:  Monitor | Chat | Code Graph
Management:  Users | Models | Tasks | Logs | Analytics | Settings
             ^─── admin-only
Profile
```

---

## 3. Specifica Endpoint API

### 3.1 `GET /api/projects` — Lista progetti

**Auth:** `Depends(require_auth)` — utente autenticato, admin vede tutti, altrimenti filtrato per `allowed_projects`

**Pattern chiamata Qdrant:** usare `state.qdrant.get_collection(col_name)` — **NON** usare REST API (`state.http_client`), perché `QDRANT_HOST` può essere `"local"` (default, in-process mode) e la REST API non è disponibile. `AsyncQdrantClient.get_collection()` restituisce `CollectionInfo` con `points_count` e `config.params.vectors.size`, funziona sia in modalità local che remota.

**Logica:**
1. Chiama `list_rag_projects(user)` per ottenere nomi (già filtrato per ACL)
2. Per ogni nome, recupera info collezione da Qdrant via REST
3. Calcola `last_indexed` dal max `mtime` in `rag_state` per tutti i file del progetto
4. Determina `path` cercando in `WORKSPACE_PROJECTS` e `EXTERNAL_PROJECTS`
5. Determina `source`: `"workspace"` se in WORKSPACE_PROJECTS, `"external"` se in EXTERNAL_PROJECTS, `"orphan"` se solo in Qdrant

```json
{
  "projects": [
    {
      "name": "NeuroNet",
      "collection_name": "collateral_docs_NeuroNet_v3",
      "points": 1247,
      "dimension": 768,
      "status": "green",
      "last_indexed": 1721487600,
      "path": "/host_fs/home/alfio/Projects/NeuroNet",
      "source": "workspace"
    }
  ]
}
```

**Status:**
- `green` — collezione esiste in Qdrant con punti > 0
- `yellow` — collezione esiste ma 0 punti (in attesa di re-index)
- `red` — errore Qdrant
- `unknown` — collezione non trovata, progetto solo su filesystem

### 3.2 `GET /api/projects/{name}` — Dettaglio progetto

**Auth:** `Depends(require_admin)`

**Response:** Singolo oggetto progetto + `files_count` e `last_errors`.

### 3.3 `POST /api/projects/reindex` — Re-index singolo progetto

**Auth:** `Depends(require_admin)`

**Body:**
```json
{"name": "NeuroNet"}
```

**Logica:**
1. Trova `project_path` da `get_project_path(name)` — cerca in WORKSPACE_PROJECTS e EXTERNAL_PROJECTS
2. Se non trovato ma collezione Qdrant esiste → errore 404 con `"Collection exists but project path not found. Use DELETE to remove collection."`
3. Se path trovato ma non esiste su filesystem → errore 400: `"Project path {path} not accessible"`
4. Se `state.is_reindexing` è True → **permette comunque** il re-index singolo progetto (usa flag separato `_single_reindex_lock` o semplicemente procede perché il lock globale non è ancora stato preso al momento del check). Il guard `state.is_reindexing` in `ingest_local_documents` blocca solo le full scan, non i singoli progetti.
5. Chiama `ingest_local_documents(single_project_path=project_path)` in background
6. Ritorna `{"status": "ok", "message": "Re-index started for NeuroNet"}`

### 3.4 `DELETE /api/projects/{name}/collection` — Elimina collezione

**Auth:** `Depends(require_admin)`

**Logica:**
1. Calcola `collection_name = get_project_col_name(name)`
2. Cancella via `await state.qdrant.delete_collection(collection_name)` — **NON** via REST API (stessa ragione della local mode). `AsyncQdrantClient.delete_collection()` funziona sia in modalità local che remota.
3. Pulisce `rag_state` per tutti i file del progetto (rel_path che iniziano con `name + "/"`)
4. **NON** modifica il `.env` — la collezione Qdrant è eliminata ma il progetto rimane registrato
5. Rimuove il progetto dal runtime cache `_registered_project_paths` se presente
6. Ritorna `{"status": "ok", "message": "Collection for {name} deleted"}`

### 3.5 `POST /api/projects/register` — Registra nuovo progetto

**Auth:** `Depends(require_admin)`

**Body:**
```json
{
  "path": "/home/alfio/Projects/NewProject",
  "name": "NewProject"
}
```

**Validazione path:**
```python
def _resolve_register_path(raw_path: str) -> str | None:
    """Tenta di risolvere il path considerando HOST_FS_PREFIX."""
    if os.path.isdir(raw_path):
        return raw_path
    if HOST_FS_PREFIX:
        prefixed = os.path.join(HOST_FS_PREFIX, raw_path.lstrip("/"))
        if os.path.isdir(prefixed):
            return prefixed
    return None
```

**Logica:**
1. Valida path con `_resolve_register_path()` — se fallisce, 400 con guida Docker
2. Verifica duplicati: WORKSPACE_PROJECTS (400), EXTERNAL_PROJECTS (400), list_rag_projects (400)
3. Legge EXTERNAL_PROJECTS corrente, appende `path:name`
4. Chiama `_persist_env("EXTERNAL_PROJECTS", new_value)`
5. Crea collezione Qdrant se non esiste
6. Avvia ingest in background
7. Aggiunge `name → resolved_path` al runtime cache `_registered_project_paths[name] = resolved_path` (così `get_project_path()` lo trova subito senza attendere il restart)
8. Ritorna `{"status": "ok", "message": "Project NewProject registered and indexing started", "needs_restart": true}`

> **Perché `needs_restart: true`:** `_persist_env()` scrive su `.env` ma la variabile `config.EXTERNAL_PROJECTS` in memoria non viene aggiornata. Il re-index immediato funziona perché passiamo `single_project_path=resolved_path` direttamente. Ma per futuri re-index globali o riavvii del watchdog, il processo deve essere riavviato per leggere il nuovo `.env`. Il runtime cache `_registered_project_paths` colma il gap fino al restart.

**NOTA Docker:** Se `HOST_FS_PREFIX` è impostato e il path non esiste neanche col prefisso, ritorna errore 400 con messaggio:
> `"Path /home/alfio/Projects/NewProject not accessible in container. On Docker, the host path must be mounted and accessible via HOST_FS_PREFIX (/host_fs/...). Add the mount to docker-compose.worker.yml first."`

### 3.6 `GET /api/projects/available` — Progetti filesystem non ancora indicizzati

**Auth:** `Depends(require_admin)`

**Logica:** Scansiona `WORKSPACE_DIR` per sottodirectory che **non** hanno ancora una collezione Qdrant corrispondente.

```json
{
  "candidates": [
    {"name": "NewProject", "path": "/host_fs/home/alfio/Projects/NewProject", "source": "workspace"}
  ]
}
```

---

## 4. Modifiche Backend — Per File

### 4.1 `jarvis/rag.py` — Modifiche

| Cosa | Dove | Dettaglio |
|---|---|---|
| Esportare `get_project_col_name` | Già pubblica (`rag.py:631`) | **Nessuna modifica** — già importabile |
| Aggiungere `get_project_path(name)` | Nuova funzione | Cerca in `_registered_project_paths` (runtime cache), poi `WORKSPACE_PROJECTS`, poi `parse_external_projects()` per match di basename |
| Aggiungere `_registered_project_paths` dict | Nuova variabile modulo in `rag.py` | `dict[str, str]` — cache runtime per progetti registrati via API (prima del restart). `get_project_path()` controlla questo dict per primo. |
| Aggiungere parametro `single_project_path` a `ingest_local_documents()` | Modifica firma `rag.py:850` | Se fornito: salta tutti gli altri progetti, processa solo quel path |
| Aggiungere `get_project_last_indexed(name)` | Nuova funzione | Scansiona `rag_state` per file del progetto, ritorna max `mtime` |
| Esportare `_find_project_root` come `find_project_root` | `rag.py:1788` | Basta rimuovere underscore leading |

**Dettaglio modifica `ingest_local_documents()`:**

```python
async def ingest_local_documents(single_project_path: str | None = None):
    """Se single_project_path è fornito, indicizza SOLO quel progetto."""
    if state.is_reindexing:
        logger.info("Re-indexing già in corso, salto scansione duplicata")
        return
    state.is_reindexing = True
    async with state.state_lock:
        _load_state_unsafe()

    current_files: dict[str, str] = {}
    visited_inodes: set = set()

    if single_project_path:
        # ── Single project mode ──
        if not os.path.isdir(single_project_path):
            logger.warning(f"Single project path not found: {single_project_path}")
            state.is_reindexing = False
            return
        proj_name = os.path.basename(single_project_path)
        logger.info(f"📁 Single project re-index: {proj_name} ({single_project_path})")
        proj_filter = GitignoreFilter(single_project_path)
        files = await _walk_directory(
            single_project_path, folder_prefix=proj_name,
            ignore_filter=proj_filter, visited_inodes=visited_inodes
        )
        for rp, fp in files.items():
            if rp not in current_files:
                current_files[rp] = fp
    else:
        # ── Full scan (codice esistente invariato) ──
        if WORKSPACE_DIR and os.path.isdir(WORKSPACE_DIR):
            for proj_dir in WORKSPACE_PROJECTS:
                ...  # codice invariato
        if EXTERNAL_PROJECTS.strip():
            ...  # codice invariato
        if os.path.isdir(DOC_DIR):
            ...  # codice invariato

    # ── 4. Pulizia file rimossi (SOLO in full scan) ──
    if not single_project_path:
        ...  # codice invariato

    # ── 5. Processamento file nuovi/modificati (codice invariato) ──
    ...
    
    # ── 6. Root node + tree cache ──
    # Genera root node per ogni progetto (codice invariato)
    files_by_project: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for rp, fp in current_files.items():
        proj = rp.replace('\\', '/').split('/')[0] if '/' in rp.replace('\\', '/') else "default"
        files_by_project[proj].append((rp, fp))
    for proj_name, proj_files in files_by_project.items():
        await update_project_root_node(proj_name, proj_files)
    
    # Tree cache: SOLO in full scan (single-project ha dati parziali)
    if not single_project_path:
        await update_project_tree_cache()
    
    state.is_reindexing = False
```

### 4.2 `jarvis/routes/projects.py` — CREARE (nuovo file)

**Pattern:** segue `routes/users.py` (FastAPI router con prefix e dependencies).

```python
"""Projects management API — list, reindex, delete, register RAG projects."""

from fastapi import APIRouter, Depends
from auth import require_admin, require_auth

router = APIRouter(prefix="/api/projects", tags=["projects"])
```

**Dipendenze per-endpoint:**
- `GET /` → `Depends(require_auth)` (utente normale vede solo i suoi progetti tramite ACL)
- `GET /{name}`, `POST /register`, `POST /reindex`, `DELETE /{name}/collection`, `GET /available` → `Depends(require_admin)`

**Import necessari (tutti con `from X import`):**
```python
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from auth import require_auth, require_admin
from rag import (
    list_rag_projects, get_project_col_name, get_project_path,
    get_project_last_indexed, ingest_local_documents,
)
from config import WORKSPACE_PROJECTS, EXTERNAL_PROJECTS, HOST_FS_PREFIX, parse_external_projects
import state
```

### 4.3 `jarvis/main.py` — Modifiche

| Cosa | Dove | Dettaglio |
|---|---|---|
| Registrare router | Dopo `profile_router` (linea 738) | `from routes.projects import router as projects_router` + `app.include_router(projects_router)` |

Blocco da aggiungere dopo linea 738:
```python
from routes.projects import router as projects_router
app.include_router(projects_router)
```

**NOTA:** I path `/api/projects/*` NON sono coperti da `dashboard_auth_middleware` (che controlla solo `/api/dashboard/*` e `/admin/*`). L'auth è gestita dai `Depends()` del router. Nessuna modifica al middleware.

### 4.4 `jarvis/config.py` — Nessuna modifica

`WORKSPACE_PROJECTS`, `EXTERNAL_PROJECTS`, `VECTOR_DB_VERSION`, `parse_external_projects()` sono già esportati e importabili.

### 4.5 `jarvis/dashboard.py` — Nessuna modifica

Gli endpoint esistenti `get_rag_collections()` (Code Graph) e `delete_rag_collection()` rimangono (usano REST API via `state.http_client`, con la limitazione nota di non funzionare in modalità `QDRANT_HOST="local"`). Il nuovo `routes/projects.py` invece usa `state.qdrant.get_collection()` e `state.qdrant.delete_collection()` che funzionano in entrambe le modalità.

---

## 5. Specifica Frontend

### 5.1 Sidebar — `templates/index.html` ~ linee 119-121

Dopo il bottone Settings, aggiungere:

```html
<button class="sidebar-item admin-only" data-view="projects" onclick="switchView('projects')">
  <span class="si-icon">📁</span><span class="si-label">Projects</span>
</button>
```

### 5.2 View HTML — `#view-projects`

Inserita dopo `#view-settings` (~ linea 632):

```html
<!-- ── VIEW: Projects ── -->
<div class="view" id="view-projects">
  <div class="card flex-col h-full">
    <div class="card-header mb-8 flex-shrink-0">
      <span class="dot dot-accent"></span> 📁 Projects
      <span class="flex-1"></span>
      <span id="projects-count" class="text-muted text-sm"></span>
      <button class="btn btn-xs" onclick="openRegisterProjectModal()">➕ Register Project</button>
    </div>
    <div class="projects-scroll" id="projects-container">
      <!-- Project cards iniettati via JS -->
    </div>
  </div>
</div>
```

**Project card template (generato da JS `loadProjects()`):**
```html
<div class="card project-card">
  <div class="project-card-header">
    <span class="project-name mono fw-600">NeuroNet</span>
    <span class="badge badge-workspace">workspace</span>
    <span class="flex-1"></span>
    <span class="status-dot status-green" title="Healthy"></span>
  </div>
  <div class="project-card-body">
    <div class="project-stat">
      <span class="stat-label">Points</span>
      <span class="stat-value">1,247</span>
    </div>
    <div class="project-stat">
      <span class="stat-label">Dimension</span>
      <span class="stat-value">768</span>
    </div>
    <div class="project-stat" style="grid-column:span 2;">
      <span class="stat-label">Path</span>
      <span class="stat-value mono text-muted text-xs">/host_fs/.../NeuroNet</span>
    </div>
    <div class="project-stat">
      <span class="stat-label">Last Indexed</span>
      <span class="stat-value text-muted text-xs">2 hours ago</span>
    </div>
  </div>
  <div class="project-card-actions">
    <button class="btn btn-xs" onclick="reindexProject('NeuroNet')">⟳ Re-index</button>
    <button class="btn btn-xs btn-outline" onclick="deleteProjectCollection('NeuroNet')">🗑️ Delete</button>
  </div>
</div>
```

### 5.3 Register Project Modal

Aggiungere prima della chiusura di `</body>`:

```html
<div id="register-project-modal" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeRegisterProjectModal()">
  <div class="modal-content" style="max-width:500px;" onclick="event.stopPropagation()">
    <div class="modal-header">
      <span class="dot dot-accent"></span> Register Project
      <span class="flex-1"></span>
      <button class="btn-icon" onclick="closeRegisterProjectModal()">✕</button>
    </div>
    <div class="modal-body">
      <form id="register-project-form" onsubmit="registerProject(event)">
        <div class="form-group">
          <label for="register-project-path">Filesystem Path</label>
          <input type="text" id="register-project-path" placeholder="/path/to/project" required>
        </div>
        <div class="form-group">
          <label for="register-project-name">Project Name</label>
          <input type="text" id="register-project-name" placeholder="Auto-detected from folder name" required>
        </div>
        <div id="register-project-msg" class="form-msg"></div>
        <div class="form-actions">
          <button type="submit" class="btn">Register & Index</button>
        </div>
      </form>

      <hr class="my-16">
      <p class="text-muted text-sm mb-8">Available projects (not yet indexed):</p>
      <div id="available-projects-list" class="text-sm">
        <p class="text-muted">Loading...</p>
      </div>
    </div>
  </div>
</div>
```

### 5.4 User Modal — `allowed_projects` Multiselect

Nell'HTML del modal utente (intorno a linea 838-840), sostituire:

```html
<div class="form-group">
  <label for="user-allowed-projects">Allowed Projects</label>
  <input type="text" id="user-allowed-projects" placeholder='e.g. NeuroNet, SlotBuilder or * for all'>
</div>
```

Con:

```html
<div class="form-group">
  <label for="user-allowed-projects">Allowed Projects</label>
  <select id="user-allowed-projects" multiple style="width:100%;min-height:100px;">
    <option value="*">* (All projects)</option>
  </select>
  <p class="text-muted text-xs mt-4">Hold Ctrl/Cmd to select multiple. Select <strong>*</strong> for all projects.</p>
</div>
```

**Modifica `openUserModal()` in management.js:**

```javascript
// Caricare progetti disponibili
async function populateProjectSelect(selectedProjects) {
    const select = document.getElementById('user-allowed-projects');
    try {
        const res = await fetch('/api/projects');
        const data = await res.json();
        // Mantieni opzione * sempre
        select.innerHTML = '<option value="*">* (All projects)</option>';
        if (data.projects) {
            data.projects.forEach(p => {
                const opt = document.createElement('option');
                opt.value = p.name;
                opt.textContent = p.name;
                if (selectedProjects && selectedProjects.includes(p.name)) {
                    opt.selected = true;
                }
                select.appendChild(opt);
            });
        }
        // Se * è selezionato, deseleziona tutto
        if (selectedProjects && selectedProjects.length === 1 && selectedProjects[0] === '*') {
            select.value = ['*'];
        }
    } catch(e) {
        // Fallback a input text se API non risponde
        select.outerHTML = '<input type="text" id="user-allowed-projects" placeholder="e.g. NeuroNet, SlotBuilder or * for all" value="' + (selectedProjects || []).join(', ') + '">';
    }
}
```

**Modifica `saveUser()` in management.js:**

```javascript
const projectsSelect = document.getElementById('user-allowed-projects');
let allowed_projects = [];
if (projectsSelect && projectsSelect.tagName === 'SELECT') {
    const selected = Array.from(projectsSelect.selectedOptions).map(o => o.value);
    if (selected.includes('*')) {
        allowed_projects = ['*'];
    } else {
        allowed_projects = selected;
    }
} else if (projectsSelect) {
    // Fallback a input text
    const projectsRaw = projectsSelect.value.trim();
    // ... logica esistente
}
```

### 5.5 `static/js/management.js` — Nuove funzioni

| Funzione | Scopo |
|---|---|
| `loadProjects()` | Carica `GET /api/projects`, renderizza card. Pattern identico a `loadUsers()`. |
| `openRegisterProjectModal()` | Mostra modal, carica `GET /api/projects/available` |
| `closeRegisterProjectModal()` | Nasconde modal, resetta form |
| `registerProject(event)` | Submit form → `POST /api/projects/register` → toast + refresh |
| `reindexProject(name)` | `POST /api/projects/reindex` → toast + refresh dopo 2s |
| `deleteProjectCollection(name)` | `confirm()` → `DELETE /api/projects/{name}/collection` → refresh |
| `loadAvailableProjects()` | `GET /api/projects/available` → renderizza candidati cliccabili |

**Pattern `reindexProject()`:**
```javascript
async function reindexProject(name) {
    try {
        const res = await fetchWithTimeout('/api/projects/reindex', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name}),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            showToast(err.detail || 'Re-index failed', 'error');
            return;
        }
        const data = await res.json();
        showToast(data.message || 'Re-index started', 'success');
        setTimeout(loadProjects, 2000);
    } catch(e) {
        showToast('Error: ' + e.message, 'error');
    }
}
```

### 5.6 `static/js/main.js` — Aggiungere handler switchView

In `switchView()` (~ linea 91), aggiungere:

```javascript
if (viewName === 'projects') loadProjects();
```

### 5.7 `static/css/style.css` — Classi per project cards

```css
.project-card { }
.project-card-header { display: flex; align-items: center; gap: 8px; padding: 12px 16px; border-bottom: 1px solid rgba(var(--border-subtle-rgb), 0.3); }
.project-card-body { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; padding: 12px 16px; }
.project-card-actions { display: flex; gap: 8px; padding: 8px 16px; border-top: 1px solid rgba(var(--border-subtle-rgb), 0.3); }
.project-stat { display: flex; flex-direction: column; gap: 2px; }
.stat-label { font-size: 0.65rem; color: rgba(var(--text-muted-rgb), 0.7); text-transform: uppercase; letter-spacing: 0.5px; }
.stat-value { font-size: 0.82rem; }
.projects-scroll { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; padding: 12px; }
.status-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
.status-dot.status-green { background: #22c55e; }
.status-dot.status-yellow { background: #eab308; }
.status-dot.status-red { background: #ef4444; }
```

---

## 6. Integrazione con Existing Code Graph View

La view **Code Graph** (`#view-graph`) resta invariata — mostra **tutte** le collezioni Qdrant (incluse `semantic_cache_*`, `file_profiles_*`, ecc.).

La nuova view **Projects** mostra solo le collezioni `collateral_docs_*` con metadati progettuali.

**Differenza:** Code Graph è tecnico (punti, dimensione, graph button). Projects è gestionale (ri-indicizzazione, registrazione, path, ultimo aggiornamento).

---

## 7. Security & Edge Cases

| Caso | Comportamento |
|---|---|
| Utente normale chiama `GET /api/projects` | Vede solo progetti in `allowed_projects` (ACL via `list_rag_projects(user)`) |
| Utente normale chiama `POST /api/projects/reindex` | 401 — `require_admin` |
| Registra path già in WORKSPACE_DIR | Errore 400: "Project already registered in WORKSPACE" |
| Registra path già in EXTERNAL_PROJECTS | Errore 400: "Project already registered" |
| Path non esiste su filesystem | 400: "Path not found" |
| Re-index di progetto senza path (solo collezione Qdrant) | 404: "Collection exists but project path not found. Use DELETE to remove collection." |
| Delete di collezione inesistente | 404: "Collection not found" |
| Re-index già in corso (`state.is_reindexing`) | 409: "Re-index already in progress" |
| `.env` non scrivibile durante register | 500: "Failed to persist configuration" |
| Re-registra progetto dopo delete collezione | OK — ri-registra e ri-indicizza |
| `EXTERNAL_PROJECTS` già contiene altri progetti | `_persist_env()` fa replace della riga esistente, non append |
| Multiselect: selezionare `*` e altri progetti | Salva solo `["*"]` |
| Qdrant offline durante list progetti | projects vuoto + log warning, nessun crash |
| Docker: path non accessibile nel container | Guida l'utente a montare il volume e riprovare |
| `name` path-traversal (`../../etc`) | Sanitizzare: `re.sub(r'[^a-zA-Z0-9_ ]', '', name)` |

---

## 8. Dettaglio Implementazione — Funzioni Chiave

### 8.1 `rag.py` — `get_project_path(name)`

```python
# Runtime cache per progetti registrati via API (colma gap fino al restart)
_registered_project_paths: dict[str, str] = {}

def get_project_path(project_name: str) -> str | None:
    """Cerca il path assoluto di un progetto per nome.
    Cerca PRIMA nel runtime cache _registered_project_paths,
    poi in WORKSPACE_PROJECTS, poi in EXTERNAL_PROJECTS.
    """
    name_lower = project_name.lower()
    # 1. Runtime cache (per progetti appena registrati via API)
    if project_name in _registered_project_paths:
        return _registered_project_paths[project_name]
    # 2. Cerca in WORKSPACE_PROJECTS
    for proj_path in WORKSPACE_PROJECTS:
        if os.path.basename(proj_path).lower() == name_lower:
            return proj_path
    # 3. Cerca in EXTERNAL_PROJECTS
    for ep_path in parse_external_projects():
        if os.path.basename(ep_path).lower() == name_lower:
            return ep_path
    # 4. Cerca per path completo (se name è un path)
    if os.path.isdir(project_name):
        return os.path.normpath(project_name)
    return None
```

### 8.2 `rag.py` — `get_project_last_indexed(name)`

```python
def get_project_last_indexed(project_name: str) -> int | None:
    """Ritorna il timestamp più recente tra i file indicizzati del progetto."""
    prefix = project_name.replace(' ', '_').replace('-', '_') + "/"
    max_mtime: float | None = None
    for rel_path, data in state.rag_state.items():
        if rel_path.startswith(prefix):
            mtime = data.get("mtime") if isinstance(data, dict) else None
            if mtime and (max_mtime is None or mtime > max_mtime):
                max_mtime = mtime
    return int(max_mtime) if max_mtime else None
```

### 8.3 `rag.py` — `find_project_root(filepath)` (esportazione pubblica)

Rinominare `_find_project_root` → `find_project_root` rimuovendo underscore. Nessuna modifica al corpo (già funzionante).

```python
def find_project_root(filepath: str) -> str | None:
    """Trova il project root a partire da un filepath assoluto.
    Cerca prima in WORKSPACE_PROJECTS poi in EXTERNAL_PROJECTS.
    """
    norm = os.path.normpath(filepath) + os.sep
    for proj in WORKSPACE_PROJECTS:
        p_norm = os.path.normpath(proj) + os.sep
        if norm.startswith(p_norm):
            return os.path.normpath(proj)
    for ep in parse_external_projects():
        p_norm = os.path.normpath(ep) + os.sep
        if norm.startswith(p_norm):
            return os.path.normpath(ep)
    return None
```

### 8.4 `rag.py` — Modifica `ingest_local_documents()` firma

Aggiungere `single_project_path: str | None = None` come primo parametro:

```python
async def ingest_local_documents(single_project_path: str | None = None):
```

### 8.5 `routes/projects.py` — Scheletro `GET /api/projects`

```python
@router.get("")
async def list_projects(user: dict = Depends(require_auth)):
    project_names = await list_rag_projects(user)
    results = []
    for name in project_names:
        col_name = get_project_col_name(name)
        points = 0
        dims = None
        status = "unknown"

        # Query Qdrant per stats via AsyncQdrantClient (funziona in local e remote mode)
        try:
            col_info = await state.qdrant.get_collection(col_name)
            pts = col_info.points_count or 0
            points = pts
            # Estrai dimensione dal config
            vc = col_info.config.params.vectors if col_info.config and col_info.config.params else None
            if isinstance(vc, dict):
                # Named vectors: prendi il primo
                first = list(vc.values())[0]
                dims = first.size if hasattr(first, 'size') else None
            elif hasattr(vc, 'size'):
                dims = vc.size
            else:
                dims = None
            status = "green" if pts > 0 else "yellow"
        except Exception:
            status = "red"

        last_idx = get_project_last_indexed(name)
        path = get_project_path(name)
        # Determine source
        if path and path in WORKSPACE_PROJECTS:
            source = "workspace"
        elif path:
            source = "external"
        else:
            source = "orphan"

        results.append({
            "name": name,
            "collection_name": col_name,
            "points": points,
            "dimension": dims,
            "last_indexed": last_idx,
            "path": path,
            "source": source,
            "status": status,
        })

    return {"projects": results}
```

---

## 9. Implementazione Steps (Ordine di Esecuzione)

### Step 1: `rag.py` — Aggiungere helper functions
- `get_project_path(name)` → percorso filesystem
- `get_project_last_indexed(name)` → max mtime da `rag_state`
- `find_project_root()` (esportare come pubblica)
- Modificare `ingest_local_documents(single_project_path=None)`
- Verificare che tutte le nuove funzioni siano importabili

### Step 2: `routes/projects.py` — CREARE nuovo file
- Implementare tutti i 6 endpoint
- Usare `state.qdrant.get_collection()` / `state.qdrant.delete_collection()` per Qdrant (NON `state.http_client` — la REST API non funziona in modalità `QDRANT_HOST="local"`)
- Usare `_persist_env` da `dashboard.py` per register (import: `from dashboard import _persist_env`)

### Step 3: `main.py` — Registrare router
- `from routes.projects import router as projects_router`
- `app.include_router(projects_router)`
- Aggiungere dopo linea 738 (dopo profile_router)

### Step 4: `templates/index.html` — Sidebar + View + Modal
- Aggiungere sidebar item "Projects" sotto Settings
- Aggiungere `<div class="view" id="view-projects">`
- Aggiungere Register Project modal HTML (prima di `</body>`)
- Modificare User modal: text input → `<select multiple>`

### Step 5: `static/js/management.js` — Funzioni Projects CRUD
- `loadProjects()`, `openRegisterProjectModal()`, `closeRegisterProjectModal()`
- `registerProject()`, `reindexProject()`, `deleteProjectCollection()`
- `loadAvailableProjects()`, `populateProjectSelect()`
- Modificare `openUserModal()` e `saveUser()` per multiselect

### Step 6: `static/js/main.js` — Switch handler
- Aggiungere `if (viewName === 'projects') loadProjects()` in `switchView()`

### Step 7: `static/css/style.css` — Project card styles
- `.project-card`, `.project-card-header/body/actions`
- `.project-stat`, `.stat-label/value`
- `.status-dot`, `.status-green/yellow/red`
- `.projects-scroll`

### Step 8: Verifica finale
- `lsp_diagnostics` su tutti i file modificati
- Verificare che `from dashboard import _persist_env` in projects.py funzioni
- Verificare che `from rag import get_project_path, get_project_last_indexed` funzioni
- Verificare che WORKSPACE_DIR vuoto non causi crash in `get_project_path()`

---

## 10. Albero delle Dipendenze tra gli Step

```
Step 1 (rag.py helpers)
  │
  └── Step 2 (routes/projects.py)  ← API endpoints usano rag.py helpers
        │
        ├── Step 3 (main.py)       ← registra router (1 riga)
        │
        ├── Step 4 (index.html)    ← sidebar, view HTML, modal, user multiselect
        │     │
        │     ├── Step 5 (management.js) ← chiama API (Step 2) su HTML (Step 4)
        │     │
        │     └── Step 6 (main.js)      ← switchView handler (1 riga)
        │
        └── Step 7 (style.css)     ← indipendente
```

**Parallelismo possibile:**
- Steps 1, 4, 7 possono partire in parallelo (backend helper + HTML template + CSS)
- Step 2 può partire dopo Step 1
- Steps 5, 6 possono partire dopo Steps 2 + 4
- Step 3 è isolato

---

## 11. Cose da NON Fare

- **Non modificare `dashboard.py`** — gli endpoint esistenti rimangono per backward compat
- **Non modificare `config.py`** — tutte le costanti necessarie sono già esportate
- **Non modificare `user_manager.py`** — il campo `allowed_projects` e la logica di salvataggio sono già corretti
- **Non modificare `auth.py`** — `require_admin`/`require_auth` funzionano già correttamente
- **Non aggiungere endpoint sotto `/api/dashboard/`** — usare `/api/projects` con auth via Depends
- **Non rimuovere `get_rag_collections()`/`delete_rag_collection()` in dashboard.py** — Code Graph view li usa ancora

---

## 12. Riferimenti per Implementazione

| File | Linea | Cosa |
|---|---|---|
| `jarvis/rag.py` | 631 | `get_project_col_name(project_name)` — già esiste |
| `jarvis/rag.py` | 850 | `ingest_local_documents()` — da modificare |
| `jarvis/rag.py` | 1617 | `list_rag_projects(user)` — ACL-aware project list |
| `jarvis/rag.py` | 1788 | `_find_project_root(filepath)` — da esportare come pubblica |
| `jarvis/rag.py` | 555-616 | `rag_state` load/save — per `last_indexed` |
| `jarvis/config.py` | 191-200 | `WORKSPACE_PROJECTS` — auto-discovered |
| `jarvis/config.py` | 205 | `EXTERNAL_PROJECTS` — env var string |
| `jarvis/config.py` | 209-230 | `parse_external_projects()` — parser |
| `jarvis/config.py` | 89 | `QDRANT_HOST=os.getenv("QDRANT_HOST","local")` — default "local" (NON usare REST API) |
| `jarvis/dashboard.py` | 1645-1696 | `_persist_env()` — atomic .env write |
| `jarvis/auth.py` | 71-109 | `get_current_user`, `require_admin`, `require_auth` |
| `jarvis/routes/users.py` | tutto | Pattern router con prefix + dependencies |
| `jarvis/main.py` | 575-580 | `ADMIN_ONLY_PATHS` (non modificare) |
| `jarvis/main.py` | 728-738 | Registrazione router (seguire pattern) |
| `jarvis/user_manager.py` | 66-95 | Schema `allowed_projects TEXT` |
| `jarvis/admin_panel/templates/index.html` | 93-121 | Sidebar items pattern |
| `jarvis/admin_panel/templates/index.html` | 634-651 | Users view pattern (copiare struttura) |
| `jarvis/admin_panel/templates/index.html` | 838-840 | User modal `allowed_projects` (da modificare) |
| `jarvis/admin_panel/static/js/management.js` | 461-614 | `loadUsers()` pattern per `loadProjects()` |
| `jarvis/admin_panel/static/js/main.js` | 72-93 | `switchView()` handler pattern |
