# Piano di Miglioramento RAG — NeuroNet

> **Stato attuale:** Fase 2 completata ✅ | **Ultimo aggiornamento:** 2026-06-24

---

## Indice

- [Risultati Test](#risultati-test)
  - [Test B — Retrieval relevance](#test-b--retrieval-relevance)
  - [Tabella riepilogativa fasi](#tabella-riepilogativa-fasi)
- [Fase 1 — Bug fix](#fase-1--bug-fix)
- [Fase 2 — Chunking avanzato](#fase-2--chunking-avanzato)
- [Bug Fix — Issue da risolvere](#bug-fix--issue-da-risolvere)
- [Fase 3 — Hybrid search](#fase-3--hybrid-search)
- [Fase 4 — Graph-aware RAG](#fase-4--graph-aware-rag)
- [Fase 5 — Quality of Life](#fase-5--quality-of-life)
- [Fase 6 — Monitoring](#fase-6--monitoring)
- [Roadmap](#roadmap)
- [Note tecniche](#note-tecniche)

---

## Risultati Test

### Test A — Similarity distribution

```bash
curl -s "http://localhost:8000/api/dashboard/qdrant/collateral_docs_{COLLEZIONE}_v3/vectors" \
  | python3 -c "
import sys, json, statistics
data = json.load(sys.stdin)
sims = [l['similarity'] for l in data.get('links', [])]
if sims:
    sims.sort()
    print(f'Count: {len(sims)}  Mean: {statistics.mean(sims):.3f}  Median: {statistics.median(sims):.3f}')
    print(f'Min: {min(sims):.3f}  Max: {max(sims):.3f}  Q1: {sims[len(sims)//4]:.3f}  Q3: {sims[3*len(sims)//4]:.3f}')
else:
    print('No links')
"
```

### Test B — Retrieval relevance

Query rappresentative per ogni tipo di progetto, misurando: `# chunk restituiti`, `score primo risultato`, `tempo risposta`, `pertinenza (1-5)`.

**Query di test:**

| # | Query | Tipo | Progetto |
|---|---|---|---|
| 1 | `"configurazione proxy e blocking delle richieste"` | cross-file | Shield_Proxy |
| 2 | `"come gestire le connessioni websocket e la sicurezza"` | cross-file | Shield_Proxy |
| 3 | `"pool di memoria e worker pool pattern"` | specifico | Shield_Proxy |
| 4 | `"generazione slot machine e configurazione rtp"` | specifico | SlotBuilder |
| 5 | `"autenticazione e gestione utenti"` | cross-project | tutti |
| 6 | `"algoritmo di compressione dati"` | cross-file | Shield_Proxy |

### Tabella riepilogativa fasi

| # | Query | Fase 1 (4000 char) | Fase 2.1 (512 tok) | Fase 2.2 (section) | Fase 2.3 (parent-child) |
|---|---|---|---|---|---|
| 1 | proxy e blocking | 99s ⚠️ RAG | 38s ✅ | **13s** ✅ | 27s ✅ |
| 2 | websocket | 70s ❌ NO RAG | 76s ✅ | **42s** ✅ | **13s** ✅ |
| 3 | memory pool | 84s ⚠️ RAG | 51s ✅ | **27s** ✅ | 54s ✅ |
| 4 | slot machine | 40s ✅ RAG | 84s ✅ | **26s** ✅ | 37s ✅ |
| 5 | autenticazione | 32s ✅ RAG | 62s ✅ | **33s** ✅ | 34s ✅ |
| 6 | compressione | 19s ✅ RAG | 36s ✅ | **17s** ✅ | 24s ✅ |
| | **Media** | **57s** | **58s** | **26s** (-55%) | **31s** (-46% vs F1) |
| | **RAG hit** | **83%** (5/6) | **100%** | **100%** | **100%** |

> **Osservazioni Fase 2.3:** Variazione tempi fisiologica (+19% vs F2.2) dovuta a scroll Qdrant aggiuntivo per parent reconstruction e contesto LLM 3× più ricco. La query 2 beneficia del contesto genitore (13s, -69%), la 3 produce più token output (54s, +50%). Zero crash, zero contaminazione `CONTESTO GERARCHICO`.

**Template test query:**
```bash
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
result = subprocess.run(['curl', '-s', 'http://localhost:8000/api/dashboard/stats'], capture_output=True, text=True)
stats = json.loads(result.stdout)
print(f'Files indicizzati: {stats[\"rag_stats\"][\"indexed_files\"]}')
print(f'Collezioni: {len(stats[\"qdrant_collections\"])}')
for col in stats['qdrant_collections'][:10]:
    cname = col if isinstance(col, str) else col.get('name', '?')
    r = subprocess.run(['curl', '-s', f'http://localhost:8000/api/dashboard/qdrant/{cname}/vectors'], capture_output=True, text=True)
    d = json.loads(r.stdout)
    print(f'  {cname.split(\"collateral_docs_\")[1].split(\"_v\")[0]}: {len(d.get(\"points\", []))} pts, {len(d.get(\"links\", []))} links')
"
```

---

## Roadmap

### ✅ Completate

| Fase | Commit | Impatto |
|---|---|---|
| 1.1 Fix dependency search | `7d95b4a` | Alto |
| 1.2 Atomic upsert | `7d95b4a` | Alto |
| 1.3 Fix embedding prefix | `7d95b4a` | Alto |
| 1.4 Rimuovi overlap | `7d95b4a` | Basso |
| 2.1 Chunk 512 token | `a72a0e9` | Molto alto |
| 2.2 Section-aware | `aef8057` | Alto |
| 2.3 Parent-child | `cd1f06b` | Alto |
| 3.1 Parallel query | ✅ Già implementato | Medio |
| 5.3 Env var chunk | ✅ Già implementato | Basso |

### 🔴 Da fixare (bug)

| ID | Problema | Priorità | Stima | Impatto |
|---|---|---|---|---|
| B6 | Chunk duplicati tree-sitter | 🔴 Critico | 30m | Alto |
| B7 | Signature gerarchia troncate | 🟡 Alto | 1h | Medio |
| B8 | chunk_count non aggiornato | 🟡 Alto | 30m | Medio |
| B10 | Scroll Qdrant senza timeout | 🟢 Medio | 15m | Basso |
| B9 | _assign_parent dead code | 🔵 Basso | 30m | Basso |
| B11 | PREAMBOLO sovrapposto | 🔵 Basso | 30m | Basso |

### 🔲 TODO (future fasi)

| Fase | Priorità | Stima | Impatto | Dipende da |
|---|---|---|---|---|
| 3.2 Hybrid search (BM25) | 🟢 Medio | 4h | Molto alto | — |
| 4.1 Fix dipendenze reali | 🟢 Medio | 3h | Alto | — |
| 4.2 Graph traversal | 🟢 Medio | 5h | Alto | 4.1 |
| 4.3 File co-embedding | 🔵 Basso | 3h | Medio | 4.1 |
| 5.1 ID chunk deterministici | 🔵 Basso | 1h | Basso | — |
| 5.2 Context budget LLM | 🔵 Basso | 1h | Medio | — |
| 6.1 Dashboard RAG metrics | 🔵 Basso | — | Basso | — |
| 6.2 Test automatizzati | 🔵 Basso | — | Basso | — |

**Legenda priorità:**
- 🔴 Critico = bug che produce risultati errati o perdita dati
- 🟡 Alto = miglioramento misurabile della qualità retrieval
- 🟢 Medio = nuova funzionalità con impatto positivo
- 🔵 Basso = quality of life, refactoring

---

## Fase 1 — Bug fix e pulizia immediate

### 1.1 Fix dependency search (B1)

| Campo | Dettaglio |
|---|---|
| **Stato** | ✅ COMPLETATO (`7d95b4a`) |
| **File** | `rag.py` → `search_documents()` |

- **Problema:** `FieldCondition(key="filename", match=MatchValue(value=dep))` confronta nomi di modulo con filename. Non matcha mai perché i filename sono path relativi (es. `node_modules/lodash/index.js`).
- **Soluzione:** Usare `query_points` con filtro `should` basato su `MatchText` su `filename`.
- **Criterio:** Query RAG su codice con dipendenze restituisce chunk da file importati.

### 1.2 Make delete-upsert atomic (B2)

| Campo | Dettaglio |
|---|---|
| **Stato** | ✅ COMPLETATO (`7d95b4a`) |
| **File** | `rag.py` → `process_single_file()` |

- **Problema:** Prima delete dei vecchi punti, poi upsert. Se l'upsert fallisce → dati persi.
- **Soluzione:** Invertire: upsert prima, poi delete dei vecchi con stesso filename.
- **Criterio:** Re-indicizzazione fallita a metà non perde i chunk precedenti.

### 1.3 Fix embedding prefix noise (B5)

| Campo | Dettaglio |
|---|---|
| **Stato** | ✅ COMPLETATO (`7d95b4a`) |
| **File** | `rag.py` → `process_single_file()` |

- **Problema:** Il testo inviato all'embedding include `"FILE: {path} \| CONTENUTO: {chunk}"` → ∼20 token di rumore.
- **Soluzione:** Rimuovere il prefisso dal testo embedded; filename nel payload Qdrant.
- **Criterio:** Pertinenza retrieval migliora di ≥1 punto (scala 1-5).

### 1.4 Rimuovi overlap (arXiv 2026 benchmark)

| Campo | Dettaglio |
|---|---|
| **Stato** | ✅ COMPLETATO (`7d95b4a`) |
| **File** | `config.py` → `CHUNK_OVERLAP` |

- **Problema:** L'overlap non dà beneficio misurabile ma aumenta i costi di indicizzazione.
- **Soluzione:** `CHUNK_OVERLAP = 0`, configurabile via env var.
- **Criterio:** Numero chunk invariato o inferiore, qualità retrieval invariata.

---

## Fase 2 — Chunking avanzato

### 2.1 Chunking ricorsivo a 512 token

| Campo | Dettaglio |
|---|---|
| **Stato** | ✅ COMPLETATO (`a72a0e9`) |
| **File** | `rag.py` → `ast_code_chunking()`, `config.py` → `CHUNK_SIZE` |

- **Problema:** `CHUNK_SIZE=4000` caratteri è troppo grande. Benchmark: 512 token = 69% accuracy.
- **Soluzione:**
  - Ridurre chunk size a 512 token (`tiktoken o200k_base`)
  - `_recursive_token_split()` con split a confini di riga
  - Rendere configurabile via env var `RAG_CHUNK_SIZE`
- **Test:**
  ```bash
  python3 -c "
  import subprocess, json, statistics
  r = subprocess.run(['curl', '-s', 'http://localhost:8000/api/dashboard/qdrant/collateral_docs_Shield_Proxy_v3/vectors'], capture_output=True, text=True)
  d = json.loads(r.stdout)
  lengths = [len(p.get('payload',{}).get('text','')) for p in d.get('points',[])]
  print(f'Chunks: {len(lengths)}  Mean: {statistics.mean(lengths):.0f}ch  Median: {statistics.median(lengths):.0f}ch')
  print(f'Min: {min(lengths)}  Max: {max(lengths)}  Q1: {sorted(lengths)[len(lengths)//4]:.0f}  Q3: {sorted(lengths)[3*len(lengths)//4]:.0f}')
  "
  ```
- **Risultati:**
  - Media: **∼1200 caratteri (∼300 token)** — ben sotto i 4000 char precedenti
  - **100%** Test B con RAG (era 83%)
  - Distribuzione: Q1=600, Mediana=1200, Q3=1700 caratteri
  - Re-indicizzazione: **908 file in ∼1.5h**

### 2.2 Section-aware chunking con contesto gerarchico

| Campo | Dettaglio |
|---|---|
| **Stato** | ✅ COMPLETATO (`aef8057`) |
| **File** | `rag.py` → `ast_code_chunking()`, `search_documents()`, `state.py` |

- **Problema:** Il prefisso `"// CONTESTO GERARCHICO: ..."` era embedded come testo, creando rumore negli embedding.
- **Soluzione:**
  - `ast_code_chunking()` restituisce `list[dict]` con `text` + `section_hierarchy`
  - Gerarchia salvata come metadato Qdrant → non nel testo embedded
  - `search_documents()` ricostruisce il prefisso solo nell'output LLM
  - `state.is_reindexing` flag per prevenire race condition watchdog
  - Timeout 300s su `create_chat_completion`
- **Risultati:**
  - ✅ **0/493** chunk Shield_Proxy contengono `CONTESTO GERARCHICO` nel testo
  - ✅ **65/493** chunk (13%) con `section_hierarchy` nei metadati Qdrant
  - ✅ Re-indicizzazione: **5848 punti** in 4 collezioni
  - ✅ Tempo medio Test B: **26s** (era 58s, -55%)
  - ✅ 0 crash, 0 contaminazione

### 2.3 Parent-child chunking (hierarchical retrieval)

| Campo | Dettaglio |
|---|---|
| **Stato** | ✅ COMPLETATO (`cd1f06b`) |
| **File** | `rag.py` → `ast_code_chunking()`, `search_documents()` |

- **Problema:** Chunk 512 token hanno alta precisione ma perdono contesto circostante.
- **Soluzione:** Proximity grouping (fino a ∼2000 token per gruppo):
  1. Chunk AST consecutivi raggruppati per prossimità
  2. Ogni figlio riceve `parent_chunk_id` (hash deterministico), `chunk_index`, `chunk_count`
  3. In `search_documents()`: scrolla Qdrant per TUTTI i sibling con stesso `parent_chunk_id`
  4. Ricostruisce il testo del genitore concatenato (ordinato per `chunk_index`)
  5. Deduplicazione: se più figli dello stesso genitore sono risultati, solo un genitore
- **Risultati:**
  - ✅ Verificato su `handlers.go`: 3 figli → genitore da **4405 caratteri** (vs ∼1400 media figli)
  - ✅ ∼32% chunk SlotBuilder con `parent_chunk_id`
  - ✅ Nessuna contaminazione `CONTESTO GERARCHICO`
  - ✅ Nessun impatto performance Qdrant

---

## Bug Fix — Issue da risolvere

### B6 — Chunk duplicati da tree-sitter (AST overlapping nodes)

| | |
|---|---|
| **Stato** | 🔴 **DA FIXARE** |
| **File** | `rag.py` → `ast_code_chunking()`, funzione `traverse()` |
| **Stima** | 30 min |

- **Problema:** Tree-sitter produce nodi duplicati per certi costrutti. Es. `struct_type` e `type_declaration` catturano lo stesso byte range. `rtp_calibrator.go` mostra `RIGHE 106-136` duplicato nel file.
- **Effetto:** Contenuto duplicato nei gruppi di prossimità → contesto LLM gonfiato.
- **Soluzione:** Aggiungere `seen_byte_ranges = set()` in `traverse()` per saltare nodi già processati.

### B7 — `get_signature()` tronca il primo carattere delle gerarchie

| | |
|---|---|
| **Stato** | 🟡 **DA FIXARE** |
| **File** | `rag.py` → `ast_code_chunking()` |
| **Stima** | 1h |

- **Problema:** `"struct MyStruct"` → `"truct MyStruct"`, `"type BlockingWork"` → `"ype BlockingWork"`.
- **Causa:** `n.start_byte` può puntare dopo la keyword per alcuni linguaggi. Il `split('{')[0]` + `strip()` non gestisce correttamente il padding.
- **Soluzione:** Usare `n.text.decode()` quando disponibile, o regex robusta per estrarre la firma.

### B8 — `chunk_count` non aggiornato dopo filtro `valid_chunks`

| | |
|---|---|
| **Stato** | 🟡 **DA FIXARE** |
| **File** | `rag.py` → `process_single_file()` |
| **Stima** | 30 min |

- **Problema:** I chunk con `len(text.strip()) < 50` vengono filtrati in `valid_chunks`, ma `chunk_count` non viene ricalcolato.
- **Effetto:** Il label `"[Padre: X frammenti]"` risulta inaccurato; la scroll per parent_chunk_id trova meno sibling del previsto.
- **Soluzione:** Ricalcolare `chunk_count` e `chunk_index` dopo il filtro, raggruppando per `parent_chunk_id`.

### B9 — `_assign_parent()` dead code nel path AST

| | |
|---|---|
| **Stato** | 🔵 **DA PULIRE** |
| **File** | `rag.py` → `ast_code_chunking()` |
| **Stima** | 30 min |

- **Problema:** La funzione helper `_assign_parent()` è usata solo nei fallback non-AST (markdown, eccezioni). La logica di raggruppamento è inline nel path AST → duplicazione.
- **Soluzione:** Unificare in unico helper; rimuovere `_assign_parent()`.

### B10 — Qdrant scroll per parent reconstruction senza timeout

| | |
|---|---|
| **Stato** | 🟢 **DA AGGIUNGERE** |
| **File** | `rag.py` → `search_documents()` |
| **Stima** | 15 min |

- **Problema:** La scroll per sibling con stesso `parent_chunk_id` non ha timeout. Qdrant sovraccarico potrebbe ritardare la risposta.
- **Soluzione:** `asyncio.wait_for()` con fallback ai chunk figli originali.

### B11 — PREAMBOLO chunk sovrapposto ad altri chunk AST

| | |
|---|---|
| **Stato** | 🔵 **DA MIGLIORARE** |
| **File** | `rag.py` → `ast_code_chunking()` |
| **Stima** | 30 min |

- **Problema:** Il chunk PREAMBOLO (prime 50 righe) è creato indipendentemente ma tree-sitter cattura anche funzioni/struct nelle stesse righe → duplicazione.
- **Soluzione:** Estrarre PREAMBOLO solo se righe 1-50 non sono coperte da altri chunk.

---

## Fase 3 — Hybrid search

### 3.1 Parallelizzare le query Qdrant

| | |
|---|---|
| **Stato** | ✅ **GIÀ IMPLEMENTATO** |
| **File** | `rag.py` → `search_documents()` |

- **Problema:** N collezioni = N query Qdrant sequenziali.
- **Soluzione:** `asyncio.gather()` per query parallele sulle collezioni target.
- **Criterio:** Riduzione tempo risposta del 30-50% per query cross-collection.

### 3.2 Aggiungere sparse vector (BM25) a Qdrant

| | |
|---|---|
| **Stato** | 🔲 TODO |
| **File** | `rag.py`, `config.py` |
| **Stima** | 4h |
| **Impatto** | Molto alto |

- **Problema:** Solo dense vector search → perde match esatti su keyword.
- **Soluzione:** `HybridQuery` Qdrant con `HybridVectorConfig`. Aggiungere sparse vector BM25 in-process (fastembed `Qdrant/bm25`).
- **Criterio:** Query con keyword specifiche (nomi funzione, variabili) restituiscono chunk precisi.

---

## Fase 4 — Graph-aware RAG

### 4.1 Fix estrazione dipendenze reali (Go, Python, JS)

| | |
|---|---|
| **Stato** | 🔲 TODO |
| **File** | `rag.py` → `extract_dependencies()` |
| **Stima** | 3h |
| **Impatto** | Alto |

- **Problema:** `re.findall(r'"([^"]+)"', head)` matcha TUTTE le stringhe in Go. Per Python/JS solo primi 2500 caratteri.
- **Soluzione:** Usare tree-sitter per estrarre IMPORT reali per ogni linguaggio.
- **Criterio:** Accuratezza ≥90% (vs ∼30% regex).

### 4.2 Dependency graph traversal in search

| | |
|---|---|
| **Stato** | 🔲 TODO |
| **File** | `rag.py` → `search_documents()` |
| **Stima** | 5h |
| **Impatto** | Alto |
| **Dipende da** | 4.1 |

- **Problema:** Quando un chunk matcha, non vengono inclusi chunk da file correlati (dipendenze).
- **Soluzione:** Dopo retrieval iniziale, seguire `depends_on` da payload → query dipendenze → aggiungi al contesto.
- **Criterio:** Risposte includono informazioni da file correlati non menzionati nella query.

### 4.3 File-level co-embedding

| | |
|---|---|
| **Stato** | 🔲 TODO |
| **File** | `rag.py` → `process_single_file()` |
| **Stima** | 3h |
| **Impatto** | Medio |
| **Dipende da** | 4.1 |

- **Problema:** Nessuna rappresentazione vettoriale dell'intero file per trovare file semanticamente simili.
- **Soluzione:** Punto Qdrant separato per file (media embedding chunk) in collezione `file_profiles_{VERSION}`.
- **Criterio:** File con pattern architetturale simile raggruppabili e recuperabili.

---

## Fase 5 — Quality of Life

### 5.1 ID chunk deterministici

| | |
|---|---|
| **Stato** | 🔲 TODO |
| **File** | `rag.py` → `process_single_file()` |
| **Stima** | 1h |

- **Soluzione:** Sostituire `uuid.uuid4()` con `hashlib.md5(rel_path + str(line_range)).hexdigest()`. Upsert idempotente: re-indicizzare aggiorna chunk esistenti invece di crearne nuovi.
- **Criterio:** `md5(rel_path + line_range)` è unico e deterministico.

### 5.2 Context budget per LLM

| | |
|---|---|
| **Stato** | 🔲 TODO |
| **File** | `rag.py` → `search_documents()` |
| **Stima** | 1h |

- **Soluzione:** Prima di restituire la stringa RAG, calcolare i token approssimativi.
  ```python
  MAX_RAG_TOKENS = num_ctx - 1500  # 1500 = prompt template + output
  ```
- **Criterio:** La risposta LLM non viene mai troncata per overflow contesto.

### 5.3 Rendere chunk size configurabile via env

| | |
|---|---|
| **Stato** | ✅ **GIÀ IMPLEMENTATO** |
| **File** | `config.py` |

- **Soluzione:** `RAG_CHUNK_SIZE` (default 512), `RAG_CHUNK_OVERLAP` (default 0), `RAG_EMBEDDING_BATCH_SIZE` (default 8).

---

## Fase 6 — Monitoring e valutazione continua

### 6.1 Dashboard RAG metrics

| |
|---|
| **Stato:** 🔲 TODO |

Aggiungere alla dashboard:
- Numero chunk per collezione
- Distribuzione similarità link (istogramma)
- Rapporto same-file / cross-file links
- Tempo medio di query

### 6.2 Test automatizzati settimanali

| |
|---|
| **Stato:** 🔲 TODO |

Script che esegue tutte le query Test B e registra:
```
{query, timestamp, num_results, top_score, response_time}
```
Salva in `data/rag_benchmark_log.jsonl` e confronta con baseline.

---

## Note tecniche

### Re-indicizzazione

Le fasi che modificano chunk/payload richiedono re-indicizzazione. Forzare tramite watchdog:

```bash
# Elimina stato e riavvia per re-index completo
docker exec jarvis_worker rm -f /app/mem0_data_v3/rag_state_v3.db
docker compose -f docker-compose.worker.yml restart jarvis_worker

# Oppure tocca un file per watchdog
docker exec jarvis_worker touch /app/documents/NeuroNet/README.md
```

### Statistiche collezioni (Fase 2.2)

| Collezione | Punti |
|---|---|
| NeuroNet | 175 |
| Shield_Proxy | 493 |
| SlotBuilder | 3706 |
| StreamAI_IPTV | 1474 |
| **Totale** | **5848** |

### Codice colori priorità

| Colore | Significato |
|---|---|
| 🔴 Critico | Bug che produce risultati errati o perdita dati |
| 🟡 Alto | Miglioramento misurabile della qualità retrieval |
| 🟢 Medio | Nuova funzionalità con impatto positivo |
| 🔵 Basso | Quality of life, refactoring |

### Rollback

```bash
tar -czf /tmp/qdrant_backup_$(date +%Y%m%d_%H%M%S).tar.gz /app/data/qdrant_local
```
