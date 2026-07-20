# 🐛 Bug Report — NeuroNet / Jarvis

> **Data:** 2026-07-20  
> **Analisi:** Cross-codebase logic audit  
> **Stato:** Nessuna modifica al codice — solo report  
> **Scope:** admin project management, auth, user_manager, routes, frontend JS/CSS

---

## RIEPILOGO

| Severità | Conteggio | File interessati |
|----------|-----------|------------------|
| 🔴 CRITICAL | 1 | `routes/projects.py` |
| 🟠 HIGH | 0 | — |
| 🟡 MEDIUM | 5 | `routes/projects.py`, `auth.py`, `user_manager.py`, `management.js` |
| 🔵 LOW | 4 | `user_manager.py`, `auth.py`, `management.js`, `config.py` |

---

## 🔴 BUG #1 (CRITICAL) — Route Ordering: `/available` endpoint unreachable

| Campo | Valore |
|-------|--------|
| **File** | `jarvis/routes/projects.py` |
| **Linee** | `/{name}` riga 107, `/available` riga 272 |
| **Tipo** | Routing — ordine di registrazione |
| **Precondizione** | Nessuna — colpisce sempre |
| **Versione introdotta** | Creazione del file |

### Causa

FastAPI matcha le route **in ordine di registrazione** nell'APIRouter. Le route sono registrate in quest'ordine:

```
1. GET  /api/projects""         → list_projects()      (riga 74)
2. GET  /api/projects/{name}    → get_project()        (riga 107)
3. POST /api/projects/reindex   → reindex_project()    (riga 140)
4. POST /api/projects/register  → register_project()   (riga 202)
5. GET  /api/projects/available → available_projects() (riga 272)
```

`/{name}` è registrato prima di `/available`. FastAPI interpreta il path `/api/projects/available` come `/{name}=available` e chiama `get_project("available")` invece di `available_projects()`.

### Impatto

- La feature "Available Projects" nel modale **Register Project** è completamente **rotta**
- `GET /api/projects/available` restituisce un errore 404/500 (collezione "available" non esiste) invece della lista dei progetti non indicizzati
- L'admin non può vedere quali progetti del workspace sono disponibili per la registrazione

### Frontend rotto

`management.js:150` chiama `fetchWithTimeout('/api/projects/available')` — che non funzionerà mai.

### Fix

Spostare `@router.get("/available")` PRIMA di `@router.get("/{name}")` (riga 107). In FastAPI, le route statiche devono precedere quelle con path parameter.

---

## 🟡 BUG #2 (MEDIUM) — `auth.py`: KeyError su `payload["sub"]`

| Campo | Valore |
|-------|--------|
| **File** | `jarvis/auth.py` |
| **Linea** | 94 |
| **Tipo** | Runtime exception |
| **Precondizione** | JWT valido (firma + expiration) MA senza claim `sub` |

### Causa

```python
user = await user_manager.get_user(payload["sub"])
```

`payload["sub"]` assume che il claim `sub` sia sempre presente. Se un token viene craftato (o generato da una versione precedente) senza `sub`, scatta `KeyError` → **500 Internal Server Error** invece di 401.

### Impatto

- Crash silenzioso invece di risposta HTTP corretta
- Potrebbe oscurare attacchi o malfunzionamenti

### Fix

```python
user_id = payload.get("sub")
if not user_id:
    return None
```

---

## 🟡 BUG #3 (MEDIUM) — `user_manager.py`: Race condition in `delete_user()` (last admin guard)

| Campo | Valore |
|-------|--------|
| **File** | `jarvis/user_manager.py` |
| **Linee** | 352-359 |
| **Tipo** | Race condition (TOCTOU) |
| **Precondizione** | 2 richieste DELETE concorrenti sull'ultimo admin |

### Causa

Pattern check-then-act senza transazione atomica:

```python
if user["role"] == "admin":
    admin_count = await self._fetchone(
        "SELECT COUNT(*) as cnt FROM users WHERE role = 'admin' AND is_active = 1"
    )
    if admin_count and admin_count["cnt"] <= 1:
        raise ValueError("Cannot delete the last admin")

await self._execute("DELETE FROM users WHERE id = ?", (user_id,))
```

Due richieste concorrenti possono entrambe superare il check (`cnt <= 1` → falso perché entrambi gli admin sono ancora presenti), poi entrambe eseguire DELETE, eliminando l'ultimo admin.

Nota secondaria: il check conta solo `is_active=1`, ma un admin inattivo potrebbe essere l'unico admin rimasto. Se un admin viene disattivato prima della cancellazione, il check `cnt <= 1` passa (l'admin inattivo non è contato) e l'ultimo admin reale viene cancellato.

### Fix

Usare una subquery atomica in SQL, o racchiudere in una transazione con `BEGIN EXCLUSIVE`:

```sql
DELETE FROM users WHERE id = ? AND (
    role != 'admin' OR 
    (SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1) > 1
)
```

---

## 🟡 BUG #4 (MEDIUM) — Inconsistenza sanitization: `_sanitize_name()` vs `get_project_col_name()`

| Campo | Valore |
|-------|--------|
| **File** | `jarvis/routes/projects.py` (riga 46) vs `jarvis/rag.py` |
| **Tipo** | Logic mismatch |
| **Precondizione** | Nome progetto contiene `-`, `.` o altri caratteri speciali |

### Causa

Due funzioni di sanitization diverse per lo stesso scopo:

**`routes/projects.py:_sanitize_name()`** (riga 46):
```python
return re.sub(r'[^a-zA-Z0-9_ ]', '', name)  # RIMUOVE
```

**`rag.py:get_project_col_name()`**:
```python
sanitized = re.sub(r'[^a-zA-Z0-9_ ]', '_', name)  # SOSTITUISCE con _
```

Esempio: progetto **"my-project"**
- Collection: `collateral_my_project_v3` (trattino → underscore)
- `_sanitize_name("my-project")` → `"myproject"` (trattino **rimosso**)
- `get_project_col_name("myproject")` → `collateral_myproject_v3` ❌ mismatch

### Impatto

- `GET /api/projects/my-project` fallisce (collection non trovata)
- Un progetto registrato con nome contenente trattini non è recuperabile via API

### Fix

Standardizzare: usare la stessa logica di `get_project_col_name()` (sostituzione con `_`) anche in `_sanitize_name()`, o rimuovere `_sanitize_name()` dall'endpoint `get_project()` dato che è già protetto da `require_admin` e il path parameter di FastAPI è già sicuro.

---

## 🟡 BUG #5 (MEDIUM) — XSS in `populateProjectSelect()` fallback

| Campo | Valore |
|-------|--------|
| **File** | `jarvis/admin_panel/static/js/management.js` |
| **Linea** | 192 |
| **Tipo** | XSS (stored, DOM-based) |
| **Precondizione** | API `/api/projects` non risponde + `selectedProjects` contiene payload malevolo |

### Causa

```javascript
select.outerHTML = '<input type="text" ... value="' + val + '">';
```

`val` non è sanitizzato con `escapeHtml()`. Se un utente ha `allowed_projects` contenente `"><img src=x onerror=alert(1)>`, viene eseguito.

### Impatto

- Stored XSS: un admin che modifica un utente malevolo esegue script arbitrari
- Escalation: rubare JWT cookie, modificare settings, creare admin

### Fix

```javascript
select.outerHTML = '<input type="text" ... value="' + escapeHtml(val) + '">';
```

---

## 🟡 BUG #6 (MEDIUM) — Frontend: No 401 redirect su session expiry

| Campo | Valore |
|-------|--------|
| **File** | `jarvis/admin_panel/static/js/management.js` |
| **Linee** | Multiple: 659, 789, 805, 77, 98, etc. |
| **Tipo** | UX / Security |
| **Precondizione** | JWT scaduto durante l'uso della dashboard |

### Causa

Quando il JWT scade, tutte le API admin restituiscono 401. La `checkAuth()` in `main.js` parte solo al load della pagina. Le funzioni CRUD in `management.js` gestiscono il 401 in modo incoerente:

- `loadUsers()` (line 659): mostra "Access denied" ma non reindirizza
- `saveUser()` (line 789): mostra "Error: Unknown error"
- `deleteUser()` (line 805): mostra "Error: Cannot delete"
- `loadProjects()` (line 77): non gestisce affatto il 401
- `registerProject()`: non gestisce il 401

Nessuna funzione chiama `window.location.href = '/admin/login'` sul 401.

### Impatto

- L'admin non capisce perché le operazioni falliscono
- Scarsa UX: bisogna ricaricare la pagina manualmente

### Fix

Creare una funzione wrapper che intercetta 401 globalmente:

```javascript
async function apiFetch(url, options) {
    const res = await fetch(url, options);
    if (res.status === 401) {
        window.location.href = '/admin/login';
        throw new Error('Session expired');
    }
    return res;
}
```

Oppure aggiungere un pattern `if (res.status === 401) window.location.href = '/admin/login'` in ogni handler.

---

## 🔵 BUG #7 (LOW) — Cookie `secure=False` in produzione

| Campo | Valore |
|-------|--------|
| **File** | `jarvis/auth.py` |
| **Linea** | 149 |
| **Tipo** | Security — session hijacking via HTTP |

### Causa

```python
response.set_cookie(
    key="access_token",
    value=token,
    httponly=True,
    max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    samesite="lax",
    secure=False,  # 🔴 Hardcoded False
)
```

Anche se il commento dice "Set True in production behind HTTPS", `secure=False` in produzione significa che il cookie JWT viene trasmesso in chiaro su HTTP.

### Fix

Rendere configurabile via `.env`:

```python
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"  # default sicuro per dev
```

E nel `config.py` aggiungere:
```python
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
```

---

## 🔵 BUG #8 (LOW) — SQLite senza WAL mode

| Campo | Valore |
|-------|--------|
| **File** | `jarvis/user_manager.py` |
| **Linee** | 156-164 |
| **Tipo** | Performance |

### Causa

`initialize()` imposta `PRAGMA foreign_keys = ON` ma non `PRAGMA journal_mode = WAL`. In assenza di WAL, SQLite usa journal mode `delete` che blocca il database durante le scritture, degradando le performance in presenza di letture concorrenti.

### Impatto

- La dashboard potrebbe impiegare più tempo del necessario per caricare users/API keys sotto carico concorrente
- In scenari ad alta concorrenza, rischio di `SQLITE_BUSY`

### Fix

Aggiungere dopo `PRAGMA foreign_keys = ON`:
```python
await self._conn.execute("PRAGMA journal_mode=WAL")
```

---

## 🔵 BUG #9 (LOW) — `VECTOR_DB_VERSION` default "v1" fuorviante

| Campo | Valore |
|-------|--------|
| **File** | `jarvis/config.py` |
| **Linea** | 204 |
| **Tipo** | Configuration footgun |

### Causa

```python
VECTOR_DB_VERSION = os.getenv("VECTOR_DB_VERSION", "v1")
```

Il default è `"v1"` ma il sistema attuale usa `v3`. Se un deploy dimentica di impostare `VECTOR_DB_VERSION=v3` nel `.env`, le collezioni Qdrant avranno nome `collateral_*_v1` invece di `collateral_*_v3` — il RAG sarà completamente silenzioso.

### Impatto

- RAG completamente non funzionante senza indicazione di errore
- Difficile da diagnosticare (nessun log di mismatch)

### Fix

Cambiare il default a `"v3"`:
```python
VECTOR_DB_VERSION = os.getenv("VECTOR_DB_VERSION", "v3")
```

---

## 🔵 BUG #10 (LOW) — `delete_user()` admin count ignora admin inattivi

| Campo | Valore |
|-------|--------|
| **File** | `jarvis/user_manager.py` |
| **Linee** | 352-357 |
| **Tipo** | Edge case logic |

### Causa

La query di conteggio admin:
```python
SELECT COUNT(*) as cnt FROM users WHERE role = 'admin' AND is_active = 1
```

conta solo admin **attivi**. Se l'ultimo admin rimasto viene prima disattivato (`is_active=0`) e poi cancellato, il check permette la cancellazione perché `cnt=0`.

### Impatto

- Possibile eliminare l'ultimo admin in 2 step (deactivate → delete)
- Il sistema rimane senza admin, bloccando la gestione utenti

### Fix

Rimuovere `AND is_active = 1` dalla query, o gestire esplicitamente il caso:
```python
SELECT COUNT(*) as cnt FROM users WHERE role = 'admin' AND id != ?
```

---

## 📋 Bug Precedentemente Fissati (non in questo report)

| Bug | File | Fix |
|-----|------|-----|
| `ingest_local_documents()` fallthrough in single-project mode | `rag.py` | `_has_project_path()` guard |
| Duplicate guard | `rag.py` | via `_has_project_path()` / tree-cache |
| `get_project_path()` sanitization mismatch → false "orphan" | `rag.py` | Sanitized fallback match in `get_project_path()` |

---

## 🧩 Pattern Generali Osservati

1. **Route ordering fragile**: `routes/projects.py` ha `/{name}` prima di path statici — pattern pericoloso che colpisce tutti i path non registrati esplicitamente
2. **Sanitization duplicata e inconsistente**: `_sanitize_name()` in `routes/projects.py` vs regex in `rag.py` fanno cose diverse con lo stesso input
3. **Auth error handling assente nel frontend**: nessuna funzione JS gestisce 401 come redirect
4. **Type coercion debole in SQLite**: `_fetchone()` non gestisce `None` come risultato in modo esplicito (alcuni path usano `assert`)
5. **Race condition in pattern check-then-act**: `delete_user()` è il caso più evidente, ma pattern simile in `create_user()` (uniqueness check via SELECT, poi INSERT) — anche se lì il vincolo UNIQUE in DB fornisce una rete di sicurezza

---

## Azioni Raccomandate

| Ordine | Bug | Azione | Sforzo |
|--------|-----|--------|--------|
| 1 | #1 — Route ordering CRITICAL | Spostare `/available` prima di `/{name}` | 1 min |
| 2 | #5 — XSS MEDIUM | Aggiungere `escapeHtml()` in `populateProjectSelect()` | 1 min |
| 3 | #6 — 401 redirect MEDIUM | Aggiungere wrapper API con 401 redirect | 15 min |
| 4 | #2 — KeyError MEDIUM | Usare `.get("sub")` | 1 min |
| 5 | #4 — Sanitization mismatch MEDIUM | Standardizzare regex | 5 min |
| 6 | #3 — Race condition MEDIUM | Transazione atomica in `delete_user()` | 10 min |
| 7 | #8 — WAL mode LOW | Aggiungere PRAGMA | 1 min |
| 8 | #10 — Admin count LOW | Rimuovere `is_active` filter | 1 min |
| 9 | #9 — VECTOR_DB_VERSION default LOW | Cambiare default a "v3" | 1 min |
| 10 | #7 — secure cookie LOW | Rendi configurabile | 5 min |
