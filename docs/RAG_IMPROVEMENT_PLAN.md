# Piano di Miglioramento RAG — NeuroNet

## Misurazione baseline

Prima di iniziare qualsiasi modifica, eseguire questi test per stabilire la baseline corrente.

### Test A — Similarity distribution
```bash
# Per ogni collezione, ottieni la distribuzione delle similarità tra chunk
curl -s "http://localhost:8000/api/dashboard/qdrant/collateral_docs_{COLLEZIONE}_v3/vectors" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
sims = [l['similarity'] for l in data.get('links', [])]
if sims:
    import statistics
    print(f'Count: {len(sims)}')
    print(f'Mean: {statistics.mean(sims):.3f}')
    print(f'Median: {statistics.median(sims):.3f}')
    print(f'Min: {min(sims):.3f}  Max: {max(sims):.3f}')
    # Quartiles
    sims.sort()
    print(f'Q1: {sims[len(sims)//4]:.3f}  Q3: {sims[3*len(sims)//4]:.3f}')
else:
    print('No links')
"
```

### Test B — Retrieval relevance (query manuali)
Usare query rappresentative per ogni tipo di progetto, registrando:
- Numero di chunk restituiti
- Score del primo risultato
- Pertinenza percepita (1-5)
- Tempo di risposta

**Query di test:**

| # | Query | Tipo | Progetto target |
|---|---|---|---|
| 1 | `"configurazione proxy e blocking delle richieste"` | cross-file | Shield_Proxy |
| 2 | `"come gestire le connessioni websocket e la sicurezza"` | cross-file | Shield_Proxy |
| 3 | `"pool di memoria e worker pool pattern"` | specifico | Shield_Proxy |
| 4 | `"generazione slot machine e configurazione rtp"` | specifico | SlotBuilder |
| 5 | `"autenticazione e gestione utenti"` | cross-project | tutti |
| 6 | `"algoritmo di compressione dati"` | cross-file | Shield_Proxy |

```bash
# Template per testare una query
QUERY="la tua query qui"
curl -s -X POST "http://localhost:8000/api/chat" \
  -H "Content-Type: application/json" \
  -d "{\"message\": \"$QUERY\", \"user_id\": \"test_rag\", \"conversation_id\": \"rag_test\"}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('response','')[:500])"
```

### Test C — Cross-file connection density
```bash
python3 -c "
import subprocess, json
result = subprocess.run(
    ['curl', '-s', 'http://localhost:8000/api/dashboard/stats'],
    capture_output=True, text=True
)
stats = json.loads(result.stdout)
print('=== BASELINE RAG STATUS ===')
print(f'Files indicizzati: {stats[\"rag_stats\"][\"indexed_files\"]}')
print(f'Collezioni: {len(stats[\"qdrant_collections\"])}')
for col in stats['qdrant_collections'][:10]:
    cname = col if isinstance(col, str) else col.get('name', '?')
    r = subprocess.run(['curl', '-s', f'http://localhost:8000/api/dashboard/qdrant/{cname}/vectors'],
                       capture_output=True, text=True)
    d = json.loads(r.stdout)
    pts = len(d.get('points', []))
    links = len(d.get('links', []))
    print(f'  {cname.split(\"collateral_docs_\")[1].split(\"_v\")[0] if \"collateral_docs_\" in cname else cname}: {pts} pts, {links} links')
"
```

---

## Fase 1 — Bug fix e pulizia immediate

### 1.1 Fix dependency search (B1)
**Stato:** 🔲 TODO
**File:** `rag.py` — funzione `search_documents()`, righe ~930–960

**Problema:** `FieldCondition(key="filename", match=MatchValue(value=dep))` confronta nomi di modulo (es. `"lodash"`, `"net/http"`) con il campo `filename` di Qdrant. Non matcha mai perché i filename sono path relativi come `node_modules/lodash/index.js`.

**Soluzione:** Usare `query_points` con filtro `should` basato su `filename` contenente il nome del modulo (operatore `MatchText`), oppure vector similarity search invece di scroll.

**Test pre-post:**
```bash
# PRIMA: verifica che la dependency search non funzioni
# Controlla che i payload abbiano il campo 'deps' popolato
# DOPO: ripeti la stessa query e verifica che i chunk delle dipendenze appaiano
```

**Criterio di successo:** Query RAG su codice con dipendenze restituisce chunk da file importati (es. query su `handlers.go` include chunk da `config.go`)

---

### 1.2 Make delete-upsert atomic (B2)
**Stato:** 🔲 TODO
**File:** `rag.py` — funzione `process_single_file()`, righe ~517–521

**Problema:** Prima delete dei vecchi punti, poi upsert dei nuovi. Se l'upsert fallisce, i dati sono persi.

**Soluzione:** Invertire l'ordine: upsert prima, poi delete dei vecchi con stesso filename.

**Test:**
```bash
# PRIMA: simula un fallimento (es. ferma Qdrant) durante la reindicizzazione di un file
# Verifica che i vecchi chunk siano persi
# DOPO: ripeti il test, verifica che i vecchi chunk sopravvivano
```

**Criterio di successo:** La reindicizzazione di un file che fallisce a metà non perde i chunk precedenti

---

### 1.3 Fix embedding prefix noise (B5)
**Stato:** 🔲 TODO
**File:** `rag.py` — funzione `process_single_file()`, riga ~488

**Problema:** Il testo inviato all'embedding è `"FILE: {path} | CONTENUTO: {chunk}"`. Questo prefisso di ~20 token è rumore che degrada la qualità della similarità semantica.

**Soluzione:** Rimuovere il prefisso dal testo embedded. Spostare filename e path nel payload Qdrant.

**Test:** Test B (query manuali) — confronta la pertinenza dei risultati prima e dopo.

**Criterio di successo:** Le query semantiche tornano chunk più pertinenti (valutazione soggettiva 1-5 migliora di almeno 1 punto)

---

### 1.4 Rimuovere overlap (arXiv 2026 benchmark)
**Stato:** 🔲 TODO
**File:** `config.py` — `CHUNK_OVERLAP = 400`

**Problema:** arXiv systematic analysis (Jan 2026) ha trovato che l'overlap nei chunk non dà beneficio misurabile, ma aumenta i costi di indicizzazione.

**Soluzione:** Impostare `CHUNK_OVERLAP = 0` di default, renderlo configurabile via env var.

**Test:** Test A (similarity distribution) — confronta la distribuzione prima e dopo.

**Criterio di successo:** Numero di chunk simile o inferiore, qualità retrieval invariata o migliore

---

## Fase 2 — Chunking avanzato

### 2.1 Chunking ricorsivo a 512 token
**Stato:** 🔲 TODO
**File:** `rag.py` — `ast_code_chunking()`, `config.py` — `CHUNK_SIZE`

**Problema:** `CHUNK_SIZE=4000` caratteri è troppo grande. Benchmark Vecta 2026: 512 token = 69% accuracy, chunking semantico = 54%.

**Soluzione:**
- Ridurre chunk size a 512 token (usando `tiktoken` o tokenizer del modello)
- Rendere configurabile via `RAG_CHUNK_SIZE` env var
- Aggiungere `RAG_CHUNK_OVERLAP` env var

**Test:**
```bash
# Confronta distribuzione dimensione chunk prima/dopo
python3 -c "
import subprocess, json, statistics
r = subprocess.run(['curl', '-s', 'http://localhost:8000/api/dashboard/qdrant/collateral_docs_Shield_Proxy_v3/vectors'],
                   capture_output=True, text=True)
d = json.loads(r.stdout)
lengths = [len(p.get('payload',{}).get('text','')) for p in d.get('points',[])]
print(f'Chunk count: {len(lengths)}')
print(f'Mean chars: {statistics.mean(lengths):.0f}')
print(f'Median chars: {statistics.median(lengths):.0f}')
print(f'Min chars: {min(lengths)}  Max chars: {max(lengths)}')
"
```

**Criterio di successo:** Dimensione media chunk ~500-700 caratteri (corrispondente a ~128-256 token), retrieval migliorata del 10%+ (Test B)

---

### 2.2 Section-aware chunking con contesto gerarchico
**Stato:** 🔲 TODO
**File:** `rag.py`

**Problema:** Il `"// CONTESTO GERARCHICO: ..."` è embedded come testo, creando rumore. Inoltre non c'è modo di filtrare per sezione.

**Soluzione:** Salvare il path della sezione come metadato Qdrant (`section_hierarchy: ["package", "struct", "method"]`). Opzionalmente prependere solo per chunk che iniziano una nuova sezione.

**Test:** Query specifiche su metodi annidati (es. `"metodo X dentro struct Y dentro package Z"`)

**Criterio di successo:** I chunk di metodi/funzioni annidati sono recuperati con score più alto rispetto a prima

---

### 2.3 Parent-child chunking (hierarchical retrieval)
**Stato:** 🔲 TODO
**File:** `rag.py`, `search_documents()`

**Problema:** Chunk piccoli (512 token) hanno alta precisione ma perdono contesto. Chunk grandi hanno contesto ma bassa precisione.

**Soluzione:** Implementare ParentDocumentRetriever pattern:
- Index: chunk piccoli (512 token) per retrieval
- Store: chunk grandi (l'intera funzione/classe, ~2000 token) per generazione
- Payload: `parent_chunk_id` → retrieve il piccolo, restituisci il grande

**Test:**
```bash
# Confronta la completezza delle risposte
# PRIMA: la risposta include solo il chunk piccolo
# DOPO: la risposta include l'intera funzione/classe
```

**Criterio di successo:** Le risposte LLM hanno più contesto e sono più complete (valutazione soggettiva)

---

## Fase 3 — Hybrid search

### 3.1 Parallelizzare le query Qdrant
**Stato:** 🔲 TODO
**File:** `rag.py` — `search_documents()`

**Problema:** N collezioni = N query Qdrant sequenziali.

**Soluzione:** `asyncio.gather()` per eseguire tutte le query in parallelo.

**Test:**
```bash
# Misura tempo prima/dopo
time curl -s -X POST "http://localhost:8000/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"message": "autenticazione e sicurezza", "user_id": "test_rag", "conversation_id": "rag_speed"}'
```

**Criterio di successo:** Riduzione del tempo di risposta del 30-50% per query cross-collection

---

### 3.2 Aggiungere sparse vector (BM25) a Qdrant
**Stato:** 🔲 TODO
**File:** `rag.py`, `config.py`

**Problema:** Solo dense vector search (embedding). Perde match esatti su keyword.

**Soluzione:** Qdrant supporta `HybridQuery` con `HybridVectorConfig`. Aggiungere sparse vector BM25 in-process.

**Test:**
```bash
# Query con keyword esatte (es. nome funzione specifico)
# PRIMA: la query "func NewProxy" potrebbe non matchare chunk che contengono esattamente "NewProxy"
# DOPO: BM25 cattura il match esatto
```

**Criterio di successo:** Query con keyword specifiche (nomi di funzione, variabili, errori) restituiscono chunk precisi che prima andavano persi

---

## Fase 4 — Graph-aware RAG

### 4.1 Fix estrazione dipendenze reali (Go, Python, JS)
**Stato:** 🔲 TODO
**File:** `rag.py` — `extract_dependencies()`

**Problema:** `re.findall(r'"([^"]+)"', head)` per Go matcha TUTTE le stringhe. Per Python e JS, solo primi 2500 caratteri.

**Soluzione:** Usare tree-sitter per estrarre IMPORT reali da ogni linguaggio.

**Test:**
```bash
# Confronta dipendenze PRIMA (regex) vs DOPO (tree-sitter)
python3 -c "
import subprocess, json
r = subprocess.run(['curl', '-s', 'http://localhost:8000/api/dashboard/stats'],
                   capture_output=True, text=True)
d = json.loads(r.stdout)
# Ispirato: mostra statistiche dipendenze se disponibili
"
```

**Criterio di successo:** Dipendenze accurate al 90%+ (vs ~30% con regex attuale)

---

### 4.2 Dependency graph traversal in search
**Stato:** 🔲 TODO
**File:** `rag.py` — `search_documents()`

**Problema:** Quando un chunk matcha, non vengono inclusi chunk da file correlati (dipendenze/dipendenti).

**Soluzione:** Dopo il retrieval iniziale, seguire le dipendenze:
1. Trova chunk rilevanti (top-5)
2. Collection `depends_on` da payload → query Qdrant per quei file
3. Aggiungi chunk da file dipendenti al contesto (con score ridotto)

**Test:** Query che richiedono conoscenza cross-file (es. flusso richiesta → handler → validazione → storage)

**Criterio di successo:** Risposte LLM includono informazioni da file correlati non direttamente menzionati nella query

---

### 4.3 File-level co-embedding
**Stato:** 🔲 TODO
**File:** `rag.py` — `process_single_file()`

**Problema:** Non c'è una rappresentazione vettoriale dell'intero file per trovare file semanticamente simili.

**Soluzione:** Creare un punto Qdrant separato per ogni file con:
- Vector = media di tutti i chunk embedding del file
- Payload = filename, project, summary stats
- Collection separata: `file_profiles_{VERSION}`

**Test:**
```bash
# Query su un file → trova file simili per struttura/semantica
```

**Criterio di successo:** File con pattern architetturale simile sono raggruppati e recuperabili

---

## Fase 5 — Quality of Life

### 5.1 ID chunk deterministici
**Stato:** 🔲 TODO
**File:** `rag.py` — `process_single_file()`

**Soluzione:** Rimpiazzare `uuid.uuid4()` con `hashlib.md5(rel_path + str(start_line) + str(end_line)).hexdigest()`.
Questo permette upsert idempotente: re-indicizzare lo stesso file aggiorna i chunk esistenti invece di crearne di nuovi.

**Test:** Re-indicizzare lo stesso file due volte → stesso numero di chunk, stessi ID.

**Criterio di successo:** `md5(rel_path + line_range)` è unico e deterministico

---

### 5.2 Context budget per LLM
**Stato:** 🔲 TODO
**File:** `rag.py` — `search_documents()`

**Soluzione:** Prima di restituire la stringa RAG concatenata, calcolare i token approssimativi. Troncare a `num_ctx - prompt_tokens - reserve_tokens`.

```python
MAX_RAG_TOKENS = num_ctx - 1500  # 1500 = prompt template + output
```

**Test:** Inviare una query su un progetto vasto → verificare che la stringa RAG non ecceda il limite.

**Criterio di successo:** La risposta LLM non viene mai troncata per overflow contesto

---

### 5.3 Rendere chunk size configurabile via env
**Stato:** 🔲 TODO
**File:** `config.py`

**Soluzione:** Aggiungere env var:
- `RAG_CHUNK_SIZE` (default: 512, in token)
- `RAG_CHUNK_OVERLAP` (default: 0 — arXiv Jan 2026: no measurable benefit)
- `RAG_EMBEDDING_BATCH_SIZE` (default: 8, up from 3)

**Test:** Verificare che le env var siano lette correttamente e applicate.

**Criterio di successo:** `os.getenv("RAG_CHUNK_SIZE")` è usato in `ast_code_chunking()`

---

## Fase 6 — Monitoring e valutazione continua

### 6.1 Dashboard RAG metrics
**Stato:** 🔲 TODO

Aggiungere alla dashboard:
- Numero di chunk per collezione
- Distribuzione similarità link (istogramma)
- Rapporto same-file / cross-file links
- Tempo medio di query

### 6.2 Test automatizzati settimanali
**Stato:** 🔲 TODO

Script che esegue tutte le query di Test B e registra:
- `{query, timestamp, num_results, top_score, response_time}`
- Salva in `data/rag_benchmark_log.jsonl`
- Confronta con baseline

---

## Riepilogo roadmap

| Fase | Priorità | Sforzo | Impatto | Dipendenze |
|---|---|---|---|---|
| 1.1 Fix dependency search | 🔴 Critico | 2-3h | Alto | Nessuna |
| 1.2 Atomic upsert | 🔴 Critico | 30min | Alto | Nessuna |
| 1.3 Fix embedding prefix | 🔴 Critico | 1h | Alto | Richiede re-indicizzazione |
| 1.4 Rimuovi overlap | 🟡 Alto | 30min | Basso | Nessuna |
| 2.1 Chunk 512 token | 🟡 Alto | 2h | Molto alto | Richiede re-indicizzazione |
| 2.2 Section-aware | 🟡 Alto | 3h | Alto | 2.1 |
| 2.3 Parent-child | 🟡 Alto | 4h | Alto | 2.1 |
| 3.1 Parallel query | 🟡 Alto | 30min | Medio | Nessuna |
| 3.2 Hybrid search | 🟢 Medio | 4h | Molto alto | Nessuna |
| 4.1 Fix dipendenze reali | 🟢 Medio | 3h | Alto | Nessuna |
| 4.2 Graph traversal | 🟢 Medio | 5h | Alto | 4.1 |
| 4.3 File co-embedding | 🔵 Basso | 3h | Medio | 4.1 |
| 5.1 ID deterministici | 🔵 Basso | 1h | Basso | Nessuna |
| 5.2 Context budget | 🔵 Basso | 1h | Medio | Nessuna |
| 5.3 Env var chunk | 🔵 Basso | 30min | Basso | 2.1 |

**Legenda priorità:**
- 🔴 Critico = bug che produce risultati errati o perdita dati
- 🟡 Alto = miglioramento misurabile della qualità retrieval
- 🟢 Medio = nuova funzionalità con impatto positivo
- 🔵 Basso = quality of life, refactoring

---

## Note tecniche

### Re-indicizzazione
Le fasi 1.3, 2.1, 2.2, 2.3 richiedono di re-indicizzare tutti i documenti perché cambia la struttura dei chunk e/o del payload. Comando per forzare re-indicizzazione:

```bash
# Trigger re-ingestion via watchdog: tocca un file in /app/documents
touch /app/documents/NeuroNet/README.md
```

### Rollback
Salvare un backup della directory `data/qdrant_local` prima di ogni fase:

```bash
tar -czf /tmp/qdrant_backup_$(date +%Y%m%d_%H%M%S).tar.gz /app/data/qdrant_local
```
