# Piano: Visualizzazione Grafo Synaptiq per-Progetto (Admin Panel)

> **Stato:** 💡 Proposta (verificata con codice reale v9.8.1)  
> **Versione:** 2.0  
> **Data:** 2026-07-21  
> **Proprietario:** Sisyphus  
> **Cross-reference:** `synaptiq_engine.py` (647 righe), `dashboard.py` (2401 righe), `routes/projects.py` (296 righe), `graph.js` (689 righe), `management.js` (813 righe), `LadybugBackend` (2819 righe), `parser_phase.py` (428 righe), `structure.py`  

---

## 0. Risultati Cross-Reference (Bug nel Piano Originale)

| # | Issue | Gravità | Dettaglio |
|---|---|---|---|
| 1 | `file_path` nei nodi è **relativo**, non assoluto | 🔴 CRITICAL | `read_file()` imposta `FileEntry.path = str(relative)` (es. `src/main.py`). Il piano originale usava `n.file_path STARTS WITH $prefix` con path assoluto — NON FUNZIONA. |
| 2 | Synaptiq DB contiene **un solo progetto** | 🔴 CRITICAL | `LadybugBackend.bulk_load()` rimpiazza l'intero database. Dopo `analyze()`, la Ladybug DB ha SOLO i dati dell'ultimo progetto analizzato. |
| 3 | Manca `_last_project_path` | 🟡 MEDIUM | SynaptiqEngine non traccia quale progetto è stato analizzato. `_last_analyze_result` non include il path. Serve aggiungere. |
| 4 | `MATCH (n)` non funziona senza label | 🟡 MEDIUM | LadybugDB richiede `MATCH (n:TableName)`. Bisogna iterare su tutte le 11 tabelle (`_NODE_TABLE_NAMES`). |
| 5 | `buildGroupLayout` usa `ext`/`group` | 🟢 LOW | I nodi Synaptiq hanno `label`. Va aggiunto `group` = `label` per posizionamento iniziale. |
| 6 | `renderSigmaGraph` usa `p.payload` | 🟢 LOW | I nodi API devono includere `payload` o `createNode` deve fornirlo. |
| 7 | `_node_columns`/`_row_to_node` privati | 🟢 LOW | Costruiamo colonne manualmente. |

**Conseguenza:** L'endpoint non deve FILTRARE per path — deve restituire l'intero grafo corrente (che ha solo un progetto) con metadati su quale progetto è stato analizzato.

---

## 1. Obiettivo

Aggiungere alla vista **Projects** dell'admin panel un pulsante `🧬 Graph` per ogni progetto che apre un modal Sigma.js con la visualizzazione del **grafo strutturale Synaptiq** corrente.

**Realtà:** Ladybug DB ha sempre e solo i dati dell'**ultimo progetto analizzato**. Il pulsante mostra i dati disponibili, con un avviso se il progetto richiesto non coincide con quello nel grafo.

---

## 2. Architettura del Sistema (Verificata)

### 2.1 SynaptiqEngine — Stato Corrente

```
synaptiq_engine (singleton)
├── _storage: LadybugBackend  →  data/synaptiq/synaptiq.lb  (SINGOLO file)
├── _initialized: bool
├── _last_analyze_duration: float
├── _last_analyze_result: dict       ← NON include project_path!
├── _last_analysis_time: dict[str, float]  ← tracking per watchdog
└── _pending_requests: set[str]
```

### 2.2 Come `analyze()` gestisce il DB

```
analyze(path)
  → run_pipeline(repo_path=Path(path), storage, full=full)
    → bulk_load(graph)  ← SOSTITUISCE l'intero DB!
```

`bulk_load()` costruisce un DB `.rebuild` temporaneo, poi fa swap atomico. Il DB finisce sempre con SOLO i dati dell'ultimo path analizzato.

### 2.3 Formato Node ID

```python
def generate_id(label: NodeLabel, file_path: str, symbol_name: str = "") -> str:
    return f"{label.value}:{file_path}:{symbol_name}"
```

Esempi:
- `file:src/main.py:` (file_path vuoto dopo `:` perché file non ha symbol_name)
- `function:src/main.py:start_server`
- `class:src/lib/models.py:User`

**Il `file_path` nella seconda posizione è RELATIVO a `repo_path`.**

### 2.4 Tabelle Node (LadybugDB)

```
_NODE_TABLE_NAMES = [
    'File', 'Folder', 'Function', 'Class', 'Method', 'Interface',
    'TypeAlias', 'Enum', 'Module', 'Community', 'Process'
]
```

Ogni tabella ha le stesse colonne (da `_NODE_COLUMN_NAMES`):

| Indice | Colonna | Tipo |
|---|---|---|
| 0 | `id` | STRING |
| 1 | `name` | STRING |
| 2 | `file_path` | STRING (relativo!) |
| 3 | `start_line` | INT64 |
| 4 | `end_line` | INT64 |
| 5 | `content` | STRING |
| 6 | `signature` | STRING |
| 7 | `language` | STRING |
| 8 | `class_name` | STRING |
| 9 | `is_dead` | BOOL |
| 10 | `is_entry_point` | BOOL |
| 11 | `is_exported` | BOOL |
| 12 | `properties_json` | STRING |

### 2.5 Tabella Relationship

Tabella unica `CodeRelation` con colonne: `rel_type`, `confidence`, `role`, `step_number`, `strength`, `co_changes`, `symbols`.

La query `MATCH (a)-[r:CodeRelation]->(b)` è **label-less** e funziona su tutte le tabelle.

### 2.6 Frontend — `renderSigmaGraph(config)`

Funzione **già refactorizzata e condivisa** tra Vector Graph e Memory Graph. Il config richiede:

| Parametro | Descrizione |
|---|---|
| `points` | Array nodi con `id`, `ext`/`group` (per posizionamento), `payload` |
| `links` | Array edge con `source`, `target` |
| `title` | Stringa titolo modal |
| `errorPrefix` | Prefisso per messaggi errore |
| `filterField` | Campo per filtro (es. `'label'`, `'ext'`) |
| `getLegendHTML` | Funzione → legenda HTML |
| `setupFilter` | Funzione → popola dropdown filtro |
| `createNode(p, pdeg, maxDeg, hubThreshold, nc)` | → attributi nodo (label, size, color, payload) |
| `createEdge(l)` | → attributi edge (color, size, similarity) |
| `onNodeClick(node, attrs, sigmaGraph)` | → HTML per info panel |
| `onEdgeClick(edge, src, tgt, sigmaGraph)` | → HTML per info panel |
| `hoverLabel(node, attrs)` | → stringa tooltip |

---

## 3. Modifiche Dettagliate

### 3.1 `synaptiq_engine.py` — Aggiungere `_last_project_path` e `get_graph_data()`

#### 3.1.1 Nuovo attributo in `__init__` (dopo riga 56)

```python
self._last_project_path: str = ""  # Path dell'ultimo progetto analizzato
```

#### 3.1.2 Aggiornare `_analyze_one()` per salvare il path

In `_analyze_one()`, dopo la chiamata a `analyze()` (riga 600), aggiungere:

```python
result = await self.analyze(project_path, full_rebuild=False)
if result:
    self._last_project_path = project_path  # <-- NUOVO
return result
```

#### 3.1.3 Aggiornare `status()` per esporre il path (dopo riga 483)

In `status()`, nel dict `stats`, aggiungere:

```python
"last_project_path": self._last_project_path,
```

#### 3.1.4 Nuovo metodo `get_graph_data()`

```python
async def get_graph_data(
    self, max_nodes: int = 500
) -> dict[str, Any]:
    """Restituisce TUTTI i nodi e le relazioni del grafo corrente.
    
    Poiché Ladybackend.bulk_load() rimpiazza l'intero DB a ogni analisi,
    il grafo contiene SOLO i dati dell'ultimo progetto analizzato.
    NON esegue filtraggio per path — restituisce tutto ciò che c'è.
    
    Args:
        max_nodes: Limite massimo nodi da restituire.
    
    Returns:
        dict con:
          - nodes: list[dict] con id, name, label, file_path, start_line,
            language, signature, group (== label per layout)
          - relationships: list[dict] con source, target, rel_type, confidence
          - stats: dict con conteggi per label + totali
          - truncated: bool se max_nodes superato
          - last_project_path: str → path dell'ultima analisi
    """
    if not self._storage or not self._initialized:
        return {
            "nodes": [], "relationships": [],
            "error": "Synaptiq not initialized",
            "last_project_path": self._last_project_path,
        }
    
    columns = (
        "n.id, n.name, n.file_path, n.start_line, n.end_line, "
        "n.content, n.signature, n.language, n.class_name, "
        "n.is_dead, n.is_entry_point, n.is_exported, n.properties_json"
    )
    
    async with self._rwlock.reader():
        storage = self._storage
        assert storage is not None
        
        nodes: list[dict[str, Any]] = []
        node_ids: set[str] = set()
        truncated = False
        
        # 1. Query ogni tabella — saltiamo Community/Process (non sono simboli di codice)
        for table in _NODE_TABLE_NAMES:
            if table in ('Community', 'Process'):
                continue
            try:
                rows = storage.execute_raw(
                    f"MATCH (n:{table}) WHERE n.id IS NOT NULL "
                    f"RETURN {columns} LIMIT $limit",
                    {"limit": max_nodes - len(nodes)}
                )
            except Exception as e:
                logger.debug(
                    "Synaptiq graph: table %s query error: %s", table, e,
                )
                continue
            
            for row in rows:
                if len(nodes) >= max_nodes:
                    truncated = True
                    break
                nid = row[0]
                if nid not in node_ids:
                    node_ids.add(nid)
                    label = table.lower()
                    nodes.append({
                        "id": nid,
                        "name": row[1] or "",
                        "label": label,
                        "group": label,       # per buildGroupLayout
                        "file_path": row[2] or "",
                        "start_line": row[3] or 0,
                        "language": row[7] or "",
                        "signature": row[6] or "",
                    })
            
            if truncated:
                break
        
        # 2. Relazioni tra i nodi che abbiamo
        relationships: list[dict[str, Any]] = []
        if node_ids:
            ids_list = list(node_ids)
            batch_size = 500
            for i in range(0, len(ids_list), batch_size):
                batch = ids_list[i:i+batch_size]
                try:
                    rel_rows = storage.execute_raw(
                        "MATCH (a)-[r:CodeRelation]->(b) "
                        "WHERE a.id IN $ids AND b.id IN $ids "
                        "RETURN a.id, b.id, r.rel_type, r.confidence "
                        "LIMIT $limit",
                        {"ids": batch, "limit": max_nodes * 3},
                    )
                    for rr in rel_rows:
                        relationships.append({
                            "source": rr[0],
                            "target": rr[1],
                            "rel_type": rr[2] or "calls",
                            "confidence": float(rr[3]) if rr[3] is not None else 1.0,
                        })
                except Exception as e:
                    logger.debug(
                        "Synaptiq graph: rel query error: %s", e,
                    )
        
        # 3. Stats
        from collections import Counter
        label_counts: Counter = Counter()
        for n in nodes:
            label_counts[n["label"]] += 1
        stats: dict[str, Any] = dict(label_counts)
        stats["total_nodes"] = len(nodes)
        stats["total_relationships"] = len(relationships)
        
        return {
            "nodes": nodes,
            "relationships": relationships,
            "stats": stats,
            "truncated": truncated,
            "last_project_path": self._last_project_path,
        }
```

**Note implementative:**
- `execute_raw()` è sincrono — blocca brevemente l'event loop. Accettabile per query sub-ms.
- La reader lock (`_rwlock.reader()`) impedisce scritture concorrenti da `analyze()`.
- `_NODE_TABLE_NAMES` è importabile da `synaptiq.core.storage.ladybug_backend`.
- La query relazionale usa `a.id IN $ids AND b.id IN $ids` — funziona in LadybugDB (confermato da `get_callers` e `remove_nodes_by_id`).

### 3.2 `routes/projects.py` — Nuovo Endpoint `GET /{name}/synaptiq/graph`

Aggiungere dopo l'ultimo endpoint (dopo riga 296):

```python
@router.get("/{name}/synaptiq/graph")
async def get_project_synaptiq_graph(
    name: str, _: dict = Depends(require_admin)
):
    """Restituisce il grafo Synaptiq corrente.
    
    NOTA: LadybugDB contiene solo l'ultimo progetto analizzato.
    Il campo 'current_project' indica quale progetto è nel grafo.
    Se non corrisponde a 'name', il frontend mostra un avviso.
    """
    name = _sanitize_name(name)
    
    # Verifica che il progetto esista nel RAG
    project_path = get_project_path(name)
    if not project_path:
        return JSONResponse(
            {"error": "Project has no filesystem path — orphan collection"},
            status_code=400,
        )
    
    # Synaptiq check con import lazy (pattern sicurezza AGENTS.md §9)
    if not SYNAPTIQ_ENABLED:
        return JSONResponse({
            "synaptiq_available": False,
            "synaptiq_initialized": False,
            "error": "Synaptiq not available",
        })
    
    try:
        from synaptiq_engine import synaptiq_engine
    except ImportError:
        return JSONResponse({
            "synaptiq_available": False,
            "error": "Synaptiq engine module not found",
        })
    
    if not synaptiq_engine.is_initialized:
        return JSONResponse({
            "synaptiq_available": True,
            "synaptiq_initialized": False,
            "error": "Synaptiq not initialized. Run a project analysis first.",
        })
    
    data = await synaptiq_engine.get_graph_data(max_nodes=500)
    
    data["synaptiq_available"] = True
    data["synaptiq_initialized"] = True
    data["requested_project"] = name
    data["requested_project_path"] = project_path
    
    # Determina se il grafo corrisponde al progetto richiesto
    last_path = data.get("last_project_path", "")
    if last_path:
        # Cerca il nome progetto dal path
        last_name = None
        for proj_path in WORKSPACE_PROJECTS:
            if os.path.normpath(proj_path) == os.path.normpath(last_path):
                last_name = os.path.basename(proj_path)
                break
        if not last_name:
            for ep_path in parse_external_projects():
                if os.path.normpath(ep_path) == os.path.normpath(last_path):
                    last_name = os.path.basename(ep_path)
                    break
        if not last_name:
            last_name = os.path.basename(last_path.rstrip("/"))
        
        data["current_project"] = last_name
        data["project_match"] = (last_name == name or 
                                 last_name.lower() == name.lower())
    else:
        data["current_project"] = None
        data["project_match"] = False
    
    return JSONResponse(data)
```

**Import da aggiungere in testa a `routes/projects.py`:**
```python
from config import SYNAPTIQ_ENABLED, WORKSPACE_PROJECTS, parse_external_projects
```

### 3.3 `management.js` — Bottone `🧬 Graph` e Funzione

#### 3.3.1 Modificare `loadProjects()` — Aggiungere bottone (riga 57-60)

Nella sezione `project-card-actions` delle card, aggiungere il terzo pulsante:

```javascript
<div class="project-card-actions">
    <button class="btn btn-xs" onclick="reindexProject('${escapeHtml(p.name)}')">⟳ Re-index</button>
    ${p.source !== 'orphan' 
        ? `<button class="btn btn-xs" onclick="openSynaptiqGraph('${escapeHtml(p.name)}')">🧬 Graph</button>` 
        : ''}
    <button class="btn btn-xs btn-outline" onclick="deleteProjectCollection('${escapeHtml(p.name)}')">🗑️ Delete Collection</button>
</div>
```

**Nota:** Il bottone non appare per progetti `orphan` (senza path filesystem → nessuna analisi Synaptiq possibile).

#### 3.3.2 Nuova funzione `openSynaptiqGraph(name)` in management.js

Da aggiungere DOPO la funzione `registerProject()` (dopo riga 143):

```javascript
// ── Synaptiq Project Graph ──

const SYNAPTIQ_LABEL_COLORS = {
    'file':       '#888888',
    'folder':     '#AA8844',
    'function':   '#3572A5',
    'class':      '#F7DF1E',
    'method':     '#3178C6',
    'interface':  '#b388ff',
    'type_alias': '#FF8A65',
    'enum':       '#66BB6A',
    'module':     '#AB47BC',
};

const SYNAPTIQ_REL_COLORS = {
    'calls':      'rgba(0, 255, 204, 0.6)',
    'defines':    'rgba(255, 200, 50, 0.6)',
    'imports':    'rgba(100, 150, 255, 0.6)',
    'extends':    'rgba(255, 100, 100, 0.6)',
    'implements': 'rgba(255, 100, 200, 0.6)',
    'contains':   'rgba(200, 200, 200, 0.4)',
    'uses_type':  'rgba(255, 180, 50, 0.5)',
    'exports':    'rgba(0, 200, 100, 0.5)',
    'coupled_with': 'rgba(200, 100, 200, 0.4)',
};

const LABEL_NAMES = {
    'file': 'File', 'folder': 'Folder', 'function': 'Function',
    'class': 'Class', 'method': 'Method', 'interface': 'Interface',
    'type_alias': 'Type Alias', 'enum': 'Enum', 'module': 'Module',
};

async function openSynaptiqGraph(projectName) {
    try {
        const res = await fetchWithTimeout(
            `/api/projects/${encodeURIComponent(projectName)}/synaptiq/graph`,
            {},
            15000
        );
        const data = await res.json();

        // Gestione errori
        if (data.error) {
            showToast(data.error, 'error');
            return;
        }

        if (data.synaptiq_available === false) {
            showToast('Synaptiq non installato o disabilitato', 'warning');
            return;
        }

        if (data.synaptiq_initialized === false) {
            showToast('Synaptiq non inizializzato. Re-index per attivare.', 'warning');
            return;
        }

        const nodes = data.nodes || [];
        const edges = data.relationships || [];

        // Avviso se il grafo è di un altro progetto
        if (data.project_match === false && data.current_project) {
            showToast(
                `⚠️ Grafo attuale: "${data.current_project}" (richiesto: "${projectName}"). Re-index per aggiornare.`,
                'warning'
            );
        }

        if (nodes.length === 0) {
            showToast('Nessun dato strutturale. Re-index per popolare il grafo Synaptiq.', 'info');
            return;
        }

        // Legenda label
        const labelSet = new Set(nodes.map(n => n.label).filter(Boolean));
        const sortedLabels = Array.from(labelSet).sort();

        const legendItems = sortedLabels.map(label =>
            `<div class="legend-row">
                <span class="legend-dot" style="background:${SYNAPTIQ_LABEL_COLORS[label] || '#888'}"></span>
                ${LABEL_NAMES[label] || label}
            </div>`
        ).join('');

        // Legenda relazioni (mostra solo i tipi presenti)
        const relTypes = new Set(edges.map(e => e.rel_type).filter(Boolean));
        const relLegend = Array.from(relTypes).sort().map(rt =>
            `<div class="legend-row" style="opacity:0.7;">
                <span class="legend-dot" style="background:${SYNAPTIQ_REL_COLORS[rt] || '#888'}"></span>
                <span class="text-xs">${rt}</span>
            </div>`
        ).join('');

        const fullLegend = legendItems + (relLegend ? '<hr style="margin:6px 0;opacity:0.2;">' + relLegend : '');

        // Titolo con avviso truncated
        let title = `${projectName} — Synaptiq Graph (${nodes.length} symbols, ${edges.length} rels)`;
        if (data.truncated) title += ' ⚠️ first 500 nodes';

        await renderSigmaGraph({
            points: nodes,
            links: edges,
            title: title,
            errorPrefix: 'Synaptiq Graph',
            filterField: 'label',
            getLegendHTML: () => fullLegend,
            setupFilter: () => {
                const sel = document.getElementById('file-type-filter');
                sel.innerHTML = '<option value="ALL">All Types</option>';
                sortedLabels.forEach(l => {
                    const opt = document.createElement('option');
                    opt.value = l;
                    opt.innerText = LABEL_NAMES[l] || l;
                    sel.appendChild(opt);
                });
            },
            createNode: (p, pdeg, maxDeg, hubThreshold, nc) => {
                const label = p.label || 'unknown';
                const normDeg = Math.min(1, pdeg / Math.max(1, maxDeg));
                const size = Math.max(4, Math.min(18, 3 + normDeg * 12));
                const isHub = pdeg > hubThreshold;
                return {
                    label: isHub ? `${p.name} — ${pdeg} conn` : (nc < 300 && label === 'file' ? p.name : ''),
                    size: nc > 500 ? size * 0.7 : size,
                    color: SYNAPTIQ_LABEL_COLORS[label] || '#888',
                    label: label,
                    payload: p,
                };
            },
            createEdge: (l) => {
                const rt = l.rel_type || 'calls';
                const color = SYNAPTIQ_REL_COLORS[rt] || 'rgba(0, 255, 204, 0.4)';
                const conf = l.confidence ?? 1.0;
                return {
                    color: color,
                    size: Math.max(0.5, conf * 2),
                    similarity: conf,
                };
            },
            onNodeClick: (node, attrs) => {
                const p = attrs.payload || {};
                const loc = p.file_path ? `${p.file_path}:${p.start_line || '?'}` : '?';
                const lang = p.language ? ` — ${p.language}` : '';
                let html = `
                    <div class="property-row"><div class="property-label">Name</div><div class="property-value mono">${escapeHtml(p.name || node)}</div></div>
                    <div class="property-row"><div class="property-label">Type</div><div class="property-value">${escapeHtml(p.label || '?')}${lang}</div></div>
                    <div class="property-row"><div class="property-label">Location</div><div class="property-value mono text-xs">${escapeHtml(loc)}</div></div>`;
                if (p.signature) {
                    html += `<div class="property-row"><div class="property-label">Signature</div><div class="property-value mono text-xs" style="word-break:break-all;">${escapeHtml(p.signature)}</div></div>`;
                }
                html += `<div class="property-row"><div class="property-label">Connections</div><div class="property-value">${attrs.degree || 0}</div></div>`;
                return html;
            },
            onEdgeClick: (edge, src, tgt, sigmaGraph) => {
                const conf = sigmaGraph.getEdgeAttribute(edge, 'similarity');
                return `
                    <div class="property-row"><div class="property-label">Source</div><div class="property-value mono text-xs">${escapeHtml(src)}</div></div>
                    <div class="property-row"><div class="property-label">Target</div><div class="property-value mono text-xs">${escapeHtml(tgt)}</div></div>
                    <div class="property-row"><div class="property-label">Confidence</div><div class="property-value">${typeof conf === 'number' ? (conf * 100).toFixed(0) + '%' : '100%'}</div></div>
                `;
            },
            hoverLabel: (node, attrs) => {
                const p = attrs.payload || {};
                return `${p.name || node} (${p.label || '?'}) — ${attrs.degree || 0} connections`;
            },
        });
    } catch(e) {
        if (e.name === 'AbortError') return;
        console.error('Synaptiq graph error:', e);
        showToast('Error loading Synaptiq graph: ' + e.message, 'error');
    }
}
```

---

## 4. Altre Correzioni dal Cross-Reference

### 4.1 `_last_project_path` in `run_initial_analysis`

In `run_initial_analysis()` (riga 549), dopo `asyncio.gather`, impostare `_last_project_path` all'ultimo path valido:

```python
if valid:
    self._last_project_path = valid[-1]  # ultimo progetto analizzato
```

### 4.2 `_last_project_path` in `notify_file_event`

In `notify_file_event(project_path)` (riga 511), il path è già passato. Aggiungere dopo la creazione del task:

```python
self._last_project_path = project_path
```

### 4.3 Import di `_NODE_TABLE_NAMES` in synaptiq_engine.py

Aggiungere in testa a `synaptiq_engine.py` (dopo riga 26):

```python
from synaptiq.core.storage.ladybug_backend import _NODE_TABLE_NAMES
```

**Nota:** `_NODE_TABLE_NAMES` inizia con underscore ma è definita a livello modulo in `ladybug_backend.py`. Importabile direttamente. In alternativa, hardcodare la lista nel metodo (11 nomi, non cambiano spesso). Preferiamo import per allineamento futuro.

### 4.4 `SYNAPTIQ_ENABLED` in routes/projects.py

Il file `routes/projects.py` già importa da `config.py` ma non include `SYNAPTIQ_ENABLED`. Aggiungere alla riga 19:

```python
from config import (
    WORKSPACE_PROJECTS, EXTERNAL_PROJECTS, HOST_FS_PREFIX,
    SYNAPTIQ_ENABLED, parse_external_projects,
)
```

---

## 5. Casi Limite e Risposte API

| Condizione | Risposta API | Comportamento Frontend |
|---|---|---|
| Synaptiq non installato | `{synaptiq_available: false}` | Toast warning, bottone visibile ma non funziona |
| Synaptiq non inizializzato | `{synaptiq_initialized: false}` | Toast "non inizializzato", suggerisce re-index |
| Nessuna analisi fatta (grafo vuoto) | `{nodes: [], relationships: []}` | Toast "nessun dato strutturale" |
| Progetto "orphan" | 400 `{error: "no filesystem path"}` | Bottone non renderizzato (`source !== 'orphan'`) |
| Synaptiq ha dati ma di progetto diverso | `{project_match: false, current_project: "..."}` | Toast warning "Grafo attuale: [progetto]" |
| Troppi nodi | `{truncated: true}` | Titolo modal mostra "first 500 nodes" |
| Path progetto non trovato | 404 | Toast errore 404 |
| Timeout query Ladybug | Exception | Toast "Errore caricamento grafo" |
| Rel query fallisce | API ritorna solo nodi (senza edge) | Grafo visualizzato senza edge |

---

## 6. Riepilogo Modifiche ai File

| File | Cosa | Righe ~ | Differenza dal piano v1 |
|---|---|---|---|
| `synaptiq_engine.py` | `_last_project_path` tracking in `__init__`, `_analyze_one`, `status`, plus `get_graph_data()` | ~80 | **RIVOLUZIONATO:** no path filtering, usa iterazione tabelle Ladybug, traccia ultimo progetto |
| `routes/projects.py` | Endpoint `GET /{name}/synaptiq/graph` + new import | ~60 | **RIVOLUZIONATO:** non filtra per path, aggiunge `project_match` check |
| `management.js` | Bottone `🧬 Graph` in `loadProjects()` + `openSynaptiqGraph()` | ~110 | **AGGIORNATO:** warning mismatch progetto, `group` field, `payload` handling |
| `graph.js` | Nessuna modifica | 0 | ✅ `renderSigmaGraph` già compatibile |

**Totale: ~250 righe** (+50 rispetto a v1 per la logica di project matching)

---

## 7. Test Plan (Aggiornato)

1. **Unit test `get_graph_data()`**: Mock `execute_raw()` per restituire nodi fittizi → verifica struttura output.
2. **Unit test `_last_project_path`**: Dopo `_analyze_one(path)`, verifica `_last_project_path == path`.
3. **API test**: `GET /api/projects/NeuroNet/synaptiq/graph` → 200 con nodi + `project_match: true`.
4. **API test**: `GET /api/projects/SlotBuilder/synaptiq/graph` con grafo di NeuroNet → 200 ma `project_match: false`, `current_project: "NeuroNet"`.
5. **API test**: `GET /api/projects/Fake/synaptiq/graph` → 404.
6. **API test**: `GET /api/projects/OrphanProj/synaptiq/graph` → 400 con `"orphan collection"`.
7. **Frontend**: click 🧬 su progetto match → modal con grafo.
8. **Frontend**: click 🧬 su progetto mismatch → modal + toast di avviso.
9. **Frontend**: click 🧬 su progetto senza dati → toast "nessun dato".
10. **Regressione**: `openGraphModal()` (vector graph) → ancora funzionante.
11. **Regressione**: `openMemoryGraphModal()` → ancora funzionante.
12. **Errore**: Disabilitare Synaptiq → click 🧬 → toast "Synaptiq non disponibile".

---

## 8. Known Limitation (Pre-esistente)

SynaptiqEngine usa un **singolo database Ladybug** (`data/synaptiq/synaptiq.lb`) che viene completamente sostituito a ogni analisi (`bulk_load()`). Questo significa:

- Solo **un progetto** può essere presente nel grafo alla volta
- Se si analizza il progetto A e poi il progetto B, il grafo di A viene perso
- Per vedere il grafo del progetto B, l'utente deve fare il re-index (che triggera Synaptiq)

**Questo è un limite architetturale del design attuale di SynaptiqEngine.** Non viene risolto in questa iterazione. Una futura iterazione potrebbe introdurre database separati per progetto o multi-project graph merging.
