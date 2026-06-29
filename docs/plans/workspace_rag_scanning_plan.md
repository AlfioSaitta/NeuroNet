# Piano: Workspace-wide RAG Scanning

> **Obiettivo:** Sostituire la lista manuale `EXTERNAL_PROJECTS` con un auto-discovery basato su `WORKSPACE_DIR` che scansiona tutte le subdirectory di `/home/alfio/Projects` come progetti individuali.

---

## 1. Stato Attuale

### Discovery progetti (rag.py:819 – `ingest_local_documents()`)

```python
# Solo 2 meccanismi di discovery:
# 1) os.walk(DOC_DIR)  → data/documents/ (vuoto, symlink puliti)
# 2) EXTERNAL_PROJECTS → lista manuale /path:Name (4 progetti)
```

### Variabili coinvolte (config.py)

| Variabile | Valore attuale | Ruolo |
|---|---|---|
| `DOCUMENTS_DIR` | `/home/alfio/Projects/ai-ecosystem/data/documents` | Directory monitorata dal watchdog |
| `DOC_DIR` | alias di `DOCUMENTS_DIR` | Usata ovunque in rag.py |
| `EXTERNAL_PROJECTS` | `"/path/ProjA/:NomeA, /path/ProjB/:NomeB, ..."` | Unica fonte di progetti extra |
| `HOST_FS_PREFIX` | `/host_fs` (Docker) / `""` (host) | Prefisso per mount Docker |
| *(mancante)* | — | Nessuna variabile per workspace |

### Collezioni Qdrant attuali

| Collezione | Progetto | Progetti ignorati |
|---|---|---|
| `collateral_docs_NeuroNet_v3` | ai-ecosystem | **collateral-go** |
| `collateral_docs_Shield_Proxy_v3` | ShieldProxy | **LastCasino** |
| `collateral_docs_SlotBuilder_v3` | SlotBuilder | **RumpiIPTV** |
| `collateral_docs_StreamAI_IPTV_v3` | StreamAI-IPTV | **RumpiIPTV-OLD** |
| | | **RumpiIPTV-ORIGINAL** |
| | | **Docs** (documentazione!) |

---

## 2. Modifiche Necessarie

### Overview dei file da modificare

| File | Tipo modifica | Complessità |
|---|---|---|
| `.env` | ➕ Nuova variabile | Trivial |
| `.env.example` | 📝 Documentazione | Trivial |
| `jarvis/config.py` | 🛠️ Logica discovery progetti | Media |
| `jarvis/rag.py` | 🛠️ `ingest_local_documents()`, watchdog | Alta |
| `jarvis/main.py` | 🛠️ Setup watchdog, cleanup symlink | Bassa |
| `jarvis/prompt_builder.py` | 🩹 Possibile tweak lista progetti | Bassa |
| `docker-compose.worker.yml` | 🩹 Volume mount (se necessario) | Bassa |
| `docs/AGENTS.md` | 📝 Documentazione | Bassa |

---

## 3. Fase 1 — Nuova variabile WORKSPACE_DIR

### `.env` — Aggiungere

```env
# ==============================================================================
# WORKSPACE — Directory radice per scoperta automatica progetti RAG
# ==============================================================================
# Tutte le subdirectory non nascoste di questo path vengono riconosciute
# come progetti individuali. La cartella "Docs/" viene trattata come
# documentazione (threshold score più basso).
# Formato: percorso assoluto sul filesystem host.
# In Docker: usare il prefisso HOST_FS (es. /host_fs/home/alfio/Projects)
WORKSPACE_DIR=/home/alfio/Projects
```

### `.env` — Deprecare EXTERNAL_PROJECTS (mantenere per compatibilità)

```env
# [DEPRECATO] Usare WORKSPACE_DIR per scoperta automatica.
# Mantenuto per progetti FUORI dal workspace principale.
EXTERNAL_PROJECTS=
```

### `config.py` — Parsing WORKSPACE_DIR

```python
# Dopo EXTERNAL_PROJECTS (riga 120)
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "")

# Auto-discover projects from WORKSPACE_DIR
WORKSPACE_PROJECTS: list[tuple[str, str]] = []  # [(path, name), ...]
if WORKSPACE_DIR and os.path.isdir(WORKSPACE_DIR):
    try:
        entries = sorted(os.listdir(WORKSPACE_DIR))
        for entry in entries:
            if entry.startswith('.'):
                continue
            entry_path = os.path.join(WORKSPACE_DIR, entry)
            if os.path.isdir(entry_path):
                WORKSPACE_PROJECTS.append((entry_path, entry))
        logger.info(f"📂 Scoperti {len(WORKSPACE_PROJECTS)} progetti in {WORKSPACE_DIR}")
    except OSError as e:
        logger.warning(f"Impossibile leggere WORKSPACE_DIR {WORKSPACE_DIR}: {e}")

# Merge con EXTERNAL_PROJECTS per backward compat
ALL_PROJECTS: list[tuple[str, str]] = list(WORKSPACE_PROJECTS)
if EXTERNAL_PROJECTS.strip():
    for pair in EXTERNAL_PROJECTS.split(','):
        pair = pair.strip()
        if ':' not in pair:
            continue
        host_path, folder_name = pair.split(':', 1)
        host_path = host_path.strip()
        folder_name = folder_name.strip()
        # Evita duplicati con quelli già scoperti
        if not any(p[1] == folder_name for p in ALL_PROJECTS):
            ALL_PROJECTS.append((host_path, folder_name))
```

---

## 4. Fase 2 — Modifica ingest_local_documents() in rag.py

### Punto di partenza (attuale, riga 819)

Attualmente la funzione:
1. `os.walk(DOC_DIR)` → file in `data/documents/`
2. Poi walka ogni path in `EXTERNAL_PROJECTS` con `folder_name/` prefisso

### Nuova logica

```python
async def ingest_local_documents():
    """Scansione WORKSPACE_DIR + EXTERNAL_PROJECTS: ogni cartella = progetto."""
    # ... (stessi controlli iniziali: is_reindexing, state lock, ignore_filter) ...

    loop = asyncio.get_running_loop()
    current_files = {}
    visited_inodes = set()

    # ── 1. Walk WORKSPACE_DIR (nuovo) ──
    if WORKSPACE_DIR and os.path.isdir(WORKSPACE_DIR):
        ws_ignore = GitignoreFilter(WORKSPACE_DIR)
        for root, dirs, files in await loop.run_in_executor(
            None, lambda: list(os.walk(WORKSPACE_DIR, followlinks=False))
        ):
            # inode tracking per evitare loop symlink
            try:
                st = os.stat(root)
                inode_key = (st.st_dev, st.st_ino)
                if inode_key in visited_inodes:
                    dirs[:] = []
                    continue
                visited_inodes.add(inode_key)
            except OSError:
                pass
            dirs[:] = [
                d for d in dirs
                if d not in ('.git', 'node_modules', 'venv', 'vendor', '__pycache__')
                and not ws_ignore.is_ignored(os.path.relpath(os.path.join(root, d), WORKSPACE_DIR))
            ]
            for file in files:
                fp = os.path.join(root, file)
                rp = os.path.relpath(fp, WORKSPACE_DIR)  # es. "SlotBuilder/main.go"
                if rp.endswith(VALID_EXTENSIONS) and not ws_ignore.is_ignored(rp):
                    current_files[rp] = fp

    # ── 2. Walk EXTERNAL_PROJECTS (backward compat) ──
    # ... (stessa logica attuale, righe 859-893) ...

    # ── 3. Walk DOC_DIR legacy (se ha contenuto) ──
    # ... (solo se DOC_DIR ha file, per backward compat) ...

    # ── 4. Cleanup file rimossi + processamento nuovi ──
    # ... (stessa logica attuale) ...
```

### Nuova costante VALID_EXTENSIONS

Estrarre la lista di estensioni valide in una costante a livello modulo:
```python
VALID_EXTENSIONS = (
    '.go', '.py', '.jsx', '.tsx', '.js', '.ts',
    '.md', '.json', '.txt',
    '.c', '.cpp', '.h', '.hpp',
    '.java', '.rs', '.sql', '.yaml', '.yml'
)
```

### Modifica GitignoreFilter

Il `GitignoreFilter` attualmente prende `DOC_DIR` come base. Va modificato per accettare un path arbitrario:
```python
filt = GitignoreFilter(WORKSPACE_DIR)  # invece di DOC_DIR
```

### Nomi collezioni Qdrant — già univoci

`get_workspace_col_name()` usa l'intero primo segmento del `rel_path`, che corrisponde al nome completo della directory. Quindi ogni progetto ha già la sua collezione univoca:

| Path relativo | parts[0] | Dopo `re.sub(r'[^a-zA-Z0-9_]', '_', ...)` | Collezione Qdrant |
|---|---|---|---|
| `RumpiIPTV/src/main.go` | `RumpiIPTV` | `RumpiIPTV` | `collateral_docs_RumpiIPTV_v3` |
| `RumpiIPTV-OLD/src/main.go` | `RumpiIPTV-OLD` | `RumpiIPTV_OLD` | `collateral_docs_RumpiIPTV_OLD_v3` |
| `RumpiIPTV-ORIGINAL/src/main.go` | `RumpiIPTV-ORIGINAL` | `RumpiIPTV_ORIGINAL` | `collateral_docs_RumpiIPTV_ORIGINAL_v3` |

**Conclusione:** le collezioni Qdrant sono già distinte. Non serve modificare `get_workspace_col_name()`.

### ⚠️ Project detection a query time — AMBIGUO

Il VERO problema è in `_match_project_in_query()` (rag.py:1481). Quando l'utente dice "RumpiIPTV", la regex `\brumpiiptv\b` matcha anche dentro `rumpiiptv-old` e `rumpiiptv original` perché `\b` matcha tra `v` (parola) e `-`/` ` (non-parola). Inoltre, l'iterazione del dict può restituire il progetto sbagliato.

**Esempio:**
- Query: *"mostra il codice di RumpiIPTV-OLD"*
- `re.search(r'\brumpiiptv\b', "rumpiiptv-old")` → **MATCH** (tra `v` e `-`)
- Il dict ha `"rumpiiptv" → "RumpiIPTV"` prima di `"rumpiiptv_old" → "RumpiIPTV_OLD"`
- **Risultato errato:** viene selezionato `RumpiIPTV` invece di `RumpiIPTV_OLD`

#### Fix: Longest-match-first in `_match_project_in_query()`

```python
def _match_project_in_query(query: str, alias_to_project: dict[str, str]) -> str | None:
    query_lower = query.lower()

    # 🔥 LONGEST ALIAS FIRST: evita che "RumpiIPTV" matchi prima di "RumpiIPTV_OLD"
    sorted_aliases = sorted(alias_to_project.keys(), key=len, reverse=True)

    for alias in sorted_aliases:
        project = alias_to_project[alias]
        if re.search(r'\b' + re.escape(alias) + r'\b', query_lower):
            return project

    # Cerca prefissi di path (stessa logica attuale)
    path_match = re.search(r'\b([A-Za-z][\w.-]*)[/\\]', query)
    if path_match:
        dir_name = path_match.group(1).lower()
        for alias in sorted_aliases:
            project = alias_to_project[alias]
            if dir_name == alias:
                return project

    return None
```

**Perché funziona:**
- `"rumpiiptv_old"` (11 char) viene controllato prima di `"rumpiiptv"` (9 char)
- `re.search(r'\brumpiiptv_old\b', "rumpiiptv old")` → NON matcha (underscore non sta per spazio)
- `re.search(r'\brumpiiptv-old\b', "rumpiiptv-old")` → MATCH (trattino dalla trasformazione underscore→trattino)
- L'utente che dice "RumpiIPTV" (esatto) fa matchare solo il progetto base

#### Fix complementare: `_alias_to_project()` — evitare collisioni

L'attuale `_alias_to_project()` genera alias multipli (lowercase, underscore→trattino, underscore→spazio). Per progetti con nomi simili, alcuni alias possono sovrapporsi. Soluzione: generare alias dal nome ORIGINALE della directory (prima della trasformazione in nome collezione), preservando trattini e caratteri speciali:

```python
def _alias_to_project(projects: list[str]) -> dict[str, str]:
    """Costruisce mappa alias → nome progetto (gestisce varianti di separatori)."""
    alias_map = {}
    for p in projects:
        # Nome originale (come appare in Qdrant, con underscore)
        alias_map[p.lower()] = p
        # Con trattino (es. "rumpiiptv-old" → RumpiIPTV_OLD)
        alias_map[p.replace('_', '-').lower()] = p
        # Con spazio (es. "rumpiiptv old" → RumpiIPTV_OLD)
        alias_map[p.replace('_', ' ').lower()] = p
        # 🔥 ANCHE il nome EXATTO della directory se diverso dal nome collezione
        # (gestito separatamente in detect_project, vedi sotto)
    return alias_map
```

Non serve modificare `_alias_to_project()` — gli alias generati sono già univoci per le collezioni (`rumpiiptv` ≠ `rumpiiptv_old` ≠ `rumpiiptv_original`). Il fix del longest-match in `_match_project_in_query` è sufficiente.

### Stessa logica in `search_documents()` — adeguare `_ws_name()`

Anche `_ws_name()` in `search_documents()` (rag.py:1187) fa matching di progetto nella query. Va allineato allo stesso principio di longest-match:

```python
# In search_documents(), riga 1205-1220:
# SORT workspace names by length (longest first) to match "RumpiIPTV_OLD"
# before "RumpiIPTV"
ws_list = sorted(col_names, key=lambda c: len(_ws_name(c)), reverse=True)
for c in ws_list:
    ws = _ws_name(c)
    ws_lower = ws.lower()
    # ... same matching logic ...
```

Questo garantisce che in fase di retrieval, la collezione giusta venga interrogata per prima.

### Trattamento speciale per Docs/

Nel `search_documents()` (riga 1168), quando il progetto identificato è `Docs`, usare `RAG_CONFIG["top_k_docs"]` e `RAG_CONFIG["score_threshold_docs"]` invece dei valori per codice:

```python
# In search_documents(), dopo individuazione progetto:
if project_name and project_name.lower() in ('docs', 'documentation', 'documents'):
    top_k = RAG_CONFIG["top_k_docs"]
    required_score = RAG_CONFIG["score_threshold_docs"]
```

---

## 5. Fase 3 — Adeguamento Watchdog

### main.py — Watchdog DynamicRagEventHandler

Attualmente il watchdog è inizializzato con `DOC_DIR`:
```python
handler = DynamicRagEventHandler(asyncio.get_running_loop(), state.file_event_queue, DOC_DIR)
```

Dobbiamo anche watchare `WORKSPACE_DIR` per il filesystem monitoring:

```python
if WATCHDOG_ENABLED:
    # Watch su DOC_DIR (legacy)
    observer.schedule(handler, DOC_DIR, recursive=True)
    # Watch su WORKSPACE_DIR (nuovo)
    if WORKSPACE_DIR and os.path.isdir(WORKSPACE_DIR):
        observer.schedule(handler, WORKSPACE_DIR, recursive=True)
```

### DynamicRagEventHandler — path_mapping

L'handler watchdog attualmente ignora i path basandosi su `DOC_DIR`:
```python
self.ignore_filter = GitignoreFilter(doc_dir)
```

Per il watchdog su WORKSPACE_DIR, serve un ignore filter flessibile, o passare il path corretto:
```python
def is_valid(self, path, is_dir):
    if is_dir:
        return False
    if not path.endswith(VALID_EXTENSIONS):
        return False
    # Applica gitignore del progetto appropriato
    return True
```

### Health check watchdog

Il watchdog health monitor (main.py:204) già si riavvia automaticamente — va solo esteso per coprire entrambi i watch paths se necessario.

---

## 6. Fase 4 — Adeguamento Docker

### docker-compose.worker.yml

Verificare che il workspace sia montato nel container. Già presente:
```yaml
volumes:
  - /home/alfio:/host_fs/home/alfio    # ✅ WORKSPACE_DIR accessibile come /host_fs/home/alfio/Projects
  - ./data/documents:/app/documents     # ✅ DOC_DIR legacy
```

Nessuna modifica necessaria se `WORKSPACE_DIR` punta a `/host_fs/home/alfio/Projects` in Docker.

### Dockerfile

Nessuna modifica necessaria.

---

## 7. Fase 5 — Prompt Builder e Lista Progetti

### prompt_builder.py

La funzione `list_rag_projects()` già restituisce i progetti dalle collezioni Qdrant — funziona già. Opzionale: mostrare anche i progetti non ancora indicizzati (scoperti ma senza collezione).

### detect_project()

Già funziona correttamente perché usa `list_rag_projects()`.

---

## 8. Possibili Rischi e Mitigazioni

| Rischio | Impatto | Mitigazione |
|---|---|---|
| **Workspace con molti file** (>100k) | Rallentamento ingestione iniziale | Batch processing già presente (batch_size=20) |
| **Progetti con .gitignore divergenti** | File ignorati erroneamente | `GitignoreFilter` già implementato e testato |
| **Docs/ con documentazione molto lunga** | Chunk grandi | Rientra nel chunking già implementato |
| **Duplicati tra WORKSPACE_DIR e EXTERNAL_PROJECTS** | Doppia indicizzazione | Merge con dedup in config.py |
| **Re-indicizzazione completa** | Tempo lungo (ore) | `VECTOR_DB_VERSION` per migrazione pulita |
| **Watchdog su workspace grande** | CPU elevata | PollingObserver già ottimizzato, `visited_inodes` tracking |

---

## 9. Ordine di Implementazione

| Step | File | Cosa fare | Dipende da |
|---|---|---|---|
| 1 | `config.py` | Aggiungere `WORKSPACE_DIR`, `WORKSPACE_PROJECTS`, `ALL_PROJECTS` | — |
| 2 | `rag.py` | Aggiungere `VALID_EXTENSIONS` costante | — |
| 3 | `rag.py` | Modificare `ingest_local_documents()` per usare `WORKSPACE_DIR` | Step 1 |
| 4 | `rag.py` | Modificare `search_documents()` per Docs threshold | — |
| 5 | `main.py` | Aggiungere watch su `WORKSPACE_DIR` nel watchdog | Step 1 |
| 6 | `.env` | Aggiungere `WORKSPACE_DIR` | — |
| 7 | `.env.example` | Documentare `WORKSPACE_DIR` | — |
| 8 | `docs/AGENTS.md` | Aggiornare documentazione | — |

---

## 10. Test Plan

### Pre-implementazione
- [ ] Verificare `ls /home/alfio/Projects/` contiene tutti i progetti attesi (già fatto: 10 subdirectory)
- [ ] Verificare il Docker volume `/home/alfio:/host_fs/home/alfio` monta correttamente (già verificato)

### Post-implementazione
- [ ] `docker compose restart jarvis_worker` → log mostra `"Scoperti N progetti in /path/Projects"`
- [ ] `curl localhost:8000/api/chat` con "quali progetti hai" → lista include tutti i nuovi progetti
- [ ] Query per progetto RumpiIPTV → risultati dal progetto
- [ ] Query per Docs → risultati con threshold docs (più basso)
- [ ] File nuovo salvato in un progetto → watchdog triggera re-embedding
- [ ] Re-indicizzazione completa: `rm data/jarvis_mem0/rag_state_v3*` + restart
- [ ] Nessun crash, nessun file duplicato

---

## Appendice — Diagramma Flusso Nuovo

```
                    ┌──────────────────────────┐
                    │   WORKSPACE_DIR (.env)    │
                    │   /home/alfio/Projects    │
                    └──────────┬───────────────┘
                               │ os.listdir()
                               ▼
                    ┌──────────────────────────┐
                    │  Auto-discover projects   │
                    │  (salta .* nascoste)      │
                    │                           │
                    │  1. ai-ecosystem/         │
                    │  2. collateral-go/        │
                    │  3. Docs/                 │  ← documentation
                    │  4. LastCasino/           │
                    │  5. RumpiIPTV/            │
                    │  6. RumpiIPTV-OLD/        │
                    │  7. RumpiIPTV-ORIGINAL/   │
                    │  8. ShieldProxy/          │
                    │  9. SlotBuilder/          │
                    │ 10. StreamAI-IPTV/        │
                    └──────────┬───────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
          ▼                    ▼                    ▼
  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
  │ ingest_local │    │  Watchdog     │    │ search_docs  │
  │ _documents() │    │  PollObserver │    │ ()           │
  │              │    │              │    │              │
  │ os.walk per  │    │ 2 observer   │    │ Project match│
  │ ogni progetto│    │ schedule     │    │ → collection │
  │ + gitignore  │    │ (DOC_DIR +   │    │ Docs → docs  │
  │ + inode dedup│    │  WORKSPACE)  │    │ threshold    │
  └──────────────┘    └──────────────┘    └──────────────┘
```

---

*Piano creato il 2026-06-28. Da approvare prima dell'implementazione.*
