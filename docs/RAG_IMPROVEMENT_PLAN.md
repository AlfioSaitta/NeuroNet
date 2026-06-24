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

**Risultati post-Fase1 (2026-06-23, chunk 4000 char):**

| # | Query | Tempo | RAG hit | Pertinenza | Note |
|---|---|---|---|---|---|
| 1 | configurazione proxy e blocking | 99s | SI | Alta | Include nome progetto nella query |
| 2 | websocket e sicurezza | 70s | NO | N/A | Gatekeeper: non classificata come project query |
| 3 | pool di memoria e worker pool pattern | 84s | SI | Alta | Include nome progetto nella query |
| 4 | generazione slot machine e configurazione rtp | 40s | SI | Alta | Include nome progetto |
| 5 | autenticazione e gestione utenti | 32s | SI | Alta | Gatekeeper: project query rilevata |
| 6 | algoritmo di compressione dati | 19s | SI | Alta | Include nome progetto |

**Osservazioni:**
- Le query in italiano SENZA nome progetto (test 2) non attivano il RAG → il gatekeeper le classifica come non-progetto
- Le query con nome progetto esplicito o keyword inglesi funzionano correttamente
- Tempo medio: ~57s (include RAG retrieval + reranker + inference LLM)
- Per migliorare il gatekeeper verso l'italiano, aggiungere keyword come "configurazione", "gestione", "sicurezza" a PROJECT_KEYWORDS

**Risultati post-Fase2.1 (2026-06-23, chunk 512 token + gatekeeper IT fix):**

| # | Query | Tempo | RAG hit | Pertinenza | Note |
|---|---|---|---|---|---|
| 1 | configurazione proxy e blocking in Shield Proxy | 38s | SI | Alta | -61% tempo vs baseline |
| 2 | websocket e sicurezza (senza progetto) | 76s | SI | Alta | **Ora RAG funziona** (gatekeeper fix) |
| 3 | pool di memoria e worker pool pattern in Shield Proxy | 51s | SI | Alta | -39% tempo |
| 4 | generazione slot machine e configurazione rtp in SlotBuilder | 84s | SI | Alta | |
| 5 | autenticazione e gestione utenti | 62s | SI | Alta | |
| 6 | algoritmo di compressione dati in Shield Proxy | 36s | SI | Alta | +89% tempo (+RAG vs prima no RAG) |

**Risultati post-Fase2.2 (2026-06-24, section-aware chunking + contesto gerarchico in metadati):**

| # | Query | Tempo | RAG hit | Pertinenza | Raw gerarchia | Note |
|---|---|---|---|---|---|---|
| 1 | configurazione proxy e blocking delle richieste | **13s** | SI | Alta | NO ✅ | -66% tempo vs F2.1 |
| 2 | websocket e sicurezza | **42s** | SI | Alta | NO ✅ | -45% tempo |
| 3 | pool di memoria e worker pool pattern | **27s** | SI | Alta | NO ✅ | -47% tempo |
| 4 | slot machine e configurazione rtp | **26s** | SI | Alta | NO ✅ | -69% tempo |
| 5 | autenticazione e gestione utenti | **33s** | SI | Alta | NO ✅ | -47% tempo |
| 6 | algoritmo di compressione dati | **17s** | SI | Alta | NO ✅ | -53% tempo |

**Risultati post-Fase2.3 (2026-06-24, parent-child chunking con proximity grouping):**

| # | Query | Tempo | RAG hit | Pertinenza | Raw gerarchia | Note |
|---|---|---|---|---|---|---|
| 1 | configurazione proxy e blocking | **27s** | SI | Alta | NO ✅ | +7% vs F2.2 (parent scroll overhead) |
| 2 | websocket e sicurezza | **13s** | SI | Alta | NO ✅ | -69% vs F2.2 |
| 3 | pool di memoria e worker pool | **54s** | SI | Alta | NO ✅ | +50% vs F2.2 (parent context più lungo) |
| 4 | slot machine e configurazione rtp | **37s** | SI | Alta | NO ✅ | +29% vs F2.2 |
| 5 | autenticazione e gestione utenti | **34s** | SI | Alta | NO ✅ | +3% vs F2.2 |
| 6 | algoritmo di compressione dati | **24s** | SI | Alta | NO ✅ | +41% vs F2.2 |

**Osservazioni post-Fase2.3:**
- **100%** RAG hit confermato su 6/6 query
- Variazione tempi fisiologica: la ricostruzione parent aggiunge scroll Qdrant e contesto LLM più lungo
- Tempo medio: **31s** (era 26s F2.2, +19%) — aumento accettabile dato il contesto 3× più ricco
- La query 2 beneficia del contesto genitore (13s, -69%), la 3 soffre di maggiori token in output (54s, +50%)
- `CONTESTO GERARCHICO` assente da tutte le risposte ✅
- **0 crash** durante l'esecuzione test

**Tabella comparativa finale (tutti i risultati):**

| # | Query | Baseline (F1) | Fase2.1 | Fase2.2 | Fase2.3 (finale) |
|---|---|---|---|---|---|
| 1 | proxy e blocking | 99s ⚠️ RAG | 38s ✅ RAG | **13s** ✅ | 27s ✅ |
| 2 | websocket | 70s ❌ NO RAG | 76s ✅ RAG | **42s** ✅ | **13s** ✅ |
| 3 | memory pool | 84s ⚠️ RAG | 51s ✅ RAG | **27s** ✅ | 54s ✅ |
| 4 | slot machine | 40s ✅ RAG | 84s ✅ RAG | **26s** ✅ | 37s ✅ |
| 5 | autenticazione | 32s ✅ RAG | 62s ✅ RAG | **33s** ✅ | 34s ✅ |
| 6 | compressione | 19s ✅ RAG | 36s ✅ RAG | **17s** ✅ | 24s ✅ |
| | **Media** | **57s** | **58s** | **26s** (-55%) | **31s** (-46% vs F1) |
| | **RAG hit %** | **83%** (5/6) | **100%** | **100%** | **100%** |

**Miglioramenti chiave Fase 2 (complessivo):**
- **100%** RAG hit (era 83% baseline)
- Tempo medio -46% vs baseline (57s → 31s)
- Contesto genitore 3× più ricco (grazie a proximity grouping parent-child)
- `CONTESTO GERARCHICO` completamente assente dal testo embedded (0 contaminazione)
- `section_hierarchy` nei metadati Qdrant per chunk con contesto di struct/classe

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
**Stato:** ✅ COMPLETATO (7d95b4a)
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
**Stato:** ✅ COMPLETATO (7d95b4a)
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
**Stato:** ✅ COMPLETATO (7d95b4a)
**File:** `rag.py` — funzione `process_single_file()`, riga ~488

**Problema:** Il testo inviato all'embedding è `"FILE: {path} | CONTENUTO: {chunk}"`. Questo prefisso di ~20 token è rumore che degrada la qualità della similarità semantica.

**Soluzione:** Rimuovere il prefisso dal testo embedded. Spostare filename e path nel payload Qdrant.

**Test:** Test B (query manuali) — confronta la pertinenza dei risultati prima e dopo.

**Criterio di successo:** Le query semantiche tornano chunk più pertinenti (valutazione soggettiva 1-5 migliora di almeno 1 punto)

---

### 1.4 Rimuovere overlap (arXiv 2026 benchmark)
**Stato:** ✅ COMPLETATO (7d95b4a)
**File:** `config.py` — `CHUNK_OVERLAP = 400`

**Problema:** arXiv systematic analysis (Jan 2026) ha trovato che l'overlap nei chunk non dà beneficio misurabile, ma aumenta i costi di indicizzazione.

**Soluzione:** Impostare `CHUNK_OVERLAP = 0` di default, renderlo configurabile via env var.

**Test:** Test A (similarity distribution) — confronta la distribuzione prima e dopo.

**Criterio di successo:** Numero di chunk simile o inferiore, qualità retrieval invariata o migliore

---

## Fase 2 — Chunking avanzato

### 2.1 Chunking ricorsivo a 512 token
**Stato:** ✅ COMPLETATO (85e1694 + 2a1b3c0)
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

**Risultati (2026-06-23):**
- Dimensione media chunk: **~1200 caratteri (~300 token)** — superiore all'obiettivo ma ben sotto il precedente 4000 char
- Tutti e 6 i Test B ora restituiscono RAG (prima: 5/6)
- **Chunk distribuzione:** Q1=~600, Mediana=~1200, Q3=~1700 caratteri
- Re-indicizzazione completata in ~1.5h per 908 file

---

### 2.2 Section-aware chunking con contesto gerarchico
**Stato:** ✅ COMPLETATO (aef8057)
**File:** `rag.py` — `ast_code_chunking()`, `search_documents()`, `state.py` — `is_reindexing`

**Problema:** Il `"// CONTESTO GERARCHICO: ..."` era embedded come testo, creando rumore negli embedding e degradando la similarità semantica.

**Soluzione:**
- `ast_code_chunking()` ora restituisce `list[dict]` con chiavi `text` e `section_hierarchy` (era `list[str]`)
- Gerarchia salvata come metadato Qdrant (`section_hierarchy: ["struct", "method"]`) — non più nel testo
- `search_documents()` ricostruisce il `// CONTESTO GERARCHICO: ...` solo nell'output per LLM
- Aggiunto `state.is_reindexing` flag per prevenire race condition watchdog durante re-indicizzazione
- `asyncio.wait_for(timeout=300)` su `create_chat_completion` per evitare hang

**Risultati:**
- **0/493** chunk Shield_Proxy contengono `CONTESTO GERARCHICO` nel testo embedded ✅
- **65/493** chunk (13%) hanno `section_hierarchy` nei metadati Qdrant
- Re-indicizzazione completata per **5848 punti** in 4 collezioni (NeuroNet: 175, Shield_Proxy: 493, SlotBuilder: 3706, StreamAI_IPTV: 1474)
- Tempo medio Test B: **26s** (era 58s, -55%)

**Test:** Test B eseguito — 6/6 query con RAG, 0 crash, 0 contaminazione gerarchica raw

---

### 2.3 Parent-child chunking (hierarchical retrieval)
**Stato:** ✅ COMPLETATO (cd1f06b)
**File:** `rag.py` — `ast_code_chunking()`, `search_documents()`

**Problema:** Chunk piccoli (512 token) hanno alta precisione ma perdono contesto. Chunk grandi hanno contesto ma bassa precisione.

**Soluzione:** Raggruppamento per prossimità (proximity grouping):
- Chunk AST consecutivi vengono raggruppati in "genitori" fino a ~2000 token
- Ogni figlio riceve `parent_chunk_id` (hash deterministico), `chunk_index`, `chunk_count`
- In `search_documents()`: dopo il retrieval, scrolla Qdrant per TUTTI i sibling con lo stesso `parent_chunk_id` e ricostruisce il testo del genitore concatenato
- Deduplicazione: se più figli dello stesso genitore sono tra i risultati, viene incluso solo il genitore una volta
- Nessuna modifica allo schema Qdrant (solo nuovi campi payload: `parent_chunk_id`, `chunk_index`, `chunk_count`)

**Risultati:**
- Verificato su `handlers.go` (SlotBuilder): 3 chunk figli → ricostruiti in genitore da **4405 caratteri** (vs ~1400 media figli)
- ~32% dei chunk SlotBuilder hanno `parent_chunk_id` dopo re-indicizzazione
- `CONTESTO GERARCHICO` assente dal testo ricostruito ✅
- Nessun impatto su performance Qdrant (scroll index su `parent_chunk_id`)

**Test:** Verifica via scroll Qdrant — sibling lookup restituisce tutti i frammenti, concatenazione ordinata per `chunk_index`

---

## Bug Fix — Issue scoperte durante Fase 2

### B6. Chunk duplicati da tree-sitter (AST overlapping nodes)
**Stato:** 🔴 Critico — DA FIXARE

**Problema:** Tree-sitter restituisce nodi duplicati per certi costrutti. Esempio su `rtp_calibrator.go`:
- `struct_type` nodo cattura `struct { ... }` (byte range esatto)
- `type_declaration` cattura l'intera dichiarazione `type Foo struct { ... }`
Il `traverse()` processa entrambi, producendo due chunk identici (`RIGHE 106-136` appare 2× nel file). Questo causa:
- Contenuto duplicato nei gruppi di prossimità parent-child (testo gonfiato)
- Chunk_count non corrispondente ai figli reali dopo filtro valid_chunks

**Soluzione:**
- Aggiungere un set `seen_byte_ranges` in `traverse()` per saltare nodi con byte range già processato
- Oppure: dopo la merge, deduplicare chunk con stesso `text` all'interno del file

**Stima:** 30 min
**File:** `rag.py` — `ast_code_chunking()`, funzione `traverse()`

---

### B7. get_signature() tronca il primo carattere delle gerarchie
**Stato:** 🟡 Alto — DA FIXARE

**Problema:** La funzione `get_signature()` in `ast_code_chunking()` produce gerarchie troncate:
- `"struct MyStruct"` → `"truct MyStruct"` (manca 's')
- `"type BlockingWork struct"` → `"ype BlockingWork struct"` (manca 't')

Causa probabile: `n.start_byte` di tree-sitter punta dopo un certo prefisso per alcuni linguaggi (es. `struct_specifier` in C include solo il nome, non la keyword). Il `content[n.start_byte:n.start_byte+1]` è uno spazio o newline che viene stripato, ma la riga risultante perde il primo carattere significativo della parola successiva.

**Soluzione:**
- Sostituire `get_signature()` con estrazione basata sul nome del nodo tree-sitter quando disponibile
- Per le signature che iniziano con `{` o `:` dopo split, usare `n.text.decode()` invece di calculare da `content[start_byte:end_byte]`
- Aggiungere controllo: `if sig_text and len(sig_text) > 3` prima di pushare in context_stack

**Stima:** 1h
**File:** `rag.py` — `ast_code_chunking()`

---

### B8. chunk_count non aggiornato dopo filtro valid_chunks
**Stato:** 🟡 Alto — DA FIXARE

**Problema:** `ast_code_chunking()` assegna `chunk_count = len(group)` ai figli di un gruppo di prossimità. Ma in `process_single_file()` (riga ~548), i chunk con `len(text.strip()) < 50` vengono filtrati via (`valid_chunks`). I chunk rimanenti mantengono il `chunk_count` originale che non corrisponde al numero reale di sibling presenti in Qdrant.

Effetto: la scroll per parent_chunk_id in `search_documents()` può restituire meno sibling del previsto. Non rompe la funzionalità, ma il label "[Padre: X frammenti]" risulta inaccurato e alcuni sibling mancanti potrebbero ridurre il contesto.

**Soluzione:**
- Dopo il filtro `valid_chunks`, rilevare i gruppi con stesso `parent_chunk_id` e ricalcolare `chunk_count` e `chunk_index`
- Oppure: non filtrare chunk < 50 char che hanno parent_chunk_id (conservarli comunque)

**Stima:** 30 min
**File:** `rag.py` — `process_single_file()`

---

### B9. _assign_parent() dead code nel path AST
**Stato:** 🔵 Basso — DA PULIRE

**Problema:** La funzione helper `_assign_parent()` è usata solo nei fallback non-AST (markdown, linguaggi non supportati, eccezioni). Nel path AST principale, la logica di raggruppamento è inline direttamente nel codice. Questo crea:
- Duplicazione di logica (assegnazione parent_chunk_id)
- Possibile deriva: modificando una logica, l'altra rimane indietro

**Soluzione:**
- Refactor: unificare la creazione dei chunk finali in un unico helper che gestisce sia chunk singoli che gruppi
- Rimuovere `_assign_parent()` e inlineare la logica in un unico punto

**Stima:** 30 min
**File:** `rag.py` — `ast_code_chunking()`

---

### B10. Qdrant scroll per parent reconstruction non ha timeout
**Stato:** 🟢 Medio — DA AGGIUNGERE

**Problema:** In `search_documents()`, la scroll per sibling con stesso `parent_chunk_id` non ha timeout. Se Qdrant è sovraccarico (o se la collezione ha migliaia di punti), la scroll potrebbe bloccarsi o richiedere secondi, ritardando la risposta LLM.

**Soluzione:**
- Aggiungere `asyncio.wait_for()` o timeout al client Qdrant per la scroll
- Se la scroll fallisce per timeout, restituire i chunk figli originali come fallback (senza ricostruzione parent)

**Stima:** 15 min
**File:** `rag.py` — `search_documents()`

---

### B11. PREAMBOLO chunk include sovrapposizioni con altri chunk
**Stato:** 🔵 Basso — DA MIGLIORARE

**Problema:** Il chunk PREAMBOLO (prime 50 righe) viene creato indipendentemente, ma il tree-sitter cattura anche funzioni/struct che iniziano nelle prime 50 righe (es. import blocchi, const). Questo causa:
- Duplicazione di contenuto nel parent proximity group
- Gonfiamento del contesto LLM con informazioni ridondanti

**Soluzione:**
- Estrarre il PREAMBOLO SOLO se non ci sono altri chunk che coprono le righe 1-50
- Oppure: ridurre il preambolo a 20 righe e escludere blocchi già catturati da tree-sitter (import, const, var)

**Stima:** 30 min
**File:** `rag.py` — `ast_code_chunking()`

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
| 1.1 Fix dependency search | ✅ Fatto | — | Alto | Nessuna |
| 1.2 Atomic upsert | ✅ Fatto | — | Alto | Nessuna |
| 1.3 Fix embedding prefix | ✅ Fatto | — | Alto | Richiede re-indicizzazione |
| 1.4 Rimuovi overlap | ✅ Fatto | — | Basso | Nessuna |
| 2.1 Chunk 512 token | ✅ Fatto | — | Molto alto | Richiede re-indicizzazione |
| 2.2 Section-aware | ✅ Fatto | 3h | Alto | 2.1 |
| 2.3 Parent-child | ✅ Fatto | 4h | Alto | 2.1 |
| B6 Duplicati tree-sitter | 🔴 Critico | 30m | Alto | 2.3 |
| B7 Signature troncate | 🟡 Alto | 1h | Medio | 2.2 |
| B8 chunk_count errato | 🟡 Alto | 30m | Medio | 2.3 |
| B9 Dead code cleanup | 🔵 Basso | 30m | Basso | 2.3 |
| B10 Scroll timeout | 🟢 Medio | 15m | Basso | 2.3 |
| B11 PREAMBOLO overlap | 🔵 Basso | 30m | Basso | 2.2 |
| 3.1 Parallel query | ✅ Fatto | — | Medio | Nessuna |
| 3.2 Hybrid search | 🟢 Medio | 4h | Molto alto | Nessuna |
| 4.1 Fix dipendenze reali | 🟢 Medio | 3h | Alto | Nessuna |
| 4.2 Graph traversal | 🟢 Medio | 5h | Alto | 4.1 |
| 4.3 File co-embedding | 🔵 Basso | 3h | Medio | 4.1 |
| 5.1 ID deterministici | 🔵 Basso | 1h | Basso | Nessuna |
| 5.2 Context budget | 🔵 Basso | 1h | Medio | Nessuna |
| 5.3 Env var chunk | ✅ Fatto | — | Basso | Nessuna |

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
