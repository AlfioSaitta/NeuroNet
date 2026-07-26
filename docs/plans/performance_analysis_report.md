# 🔬 Performance Analysis Report — NeuroNet/Jarvis

**Data:** 2026-07-25 (aggiornato 2026-07-26)
**Modello attivo:** Gemma 4 E2B QAT (6.88 tok/s)
**VRAM:** 1036 MiB / 4096 MiB (25%)
**LOC esaminate:** ~12.000+ su 25+ moduli

---

## Indice

1. [Executive Summary](#1-executive-summary)
2. [LLM Pipeline](#2-llm-pipeline)
3. [RAG & Embedding](#3-rag--embedding)
4. [Memoria Episodica](#4-memoria-episodica)
5. [Synaptiq Engine](#5-synaptiq-engine)
6. [Scheduler & Tasks](#6-scheduler--tasks)
7. [Telegram & Userbot](#7-telegram--userbot)
8. [Dashboard & Admin](#8-dashboard--admin)
9. [Infrastruttura](#9-infrastruttura)
10. [Anti-Patterns Generali](#10-anti-patterns-generali)
11. [Stima Impatto Cumulativo](#11-stima-impatto-cumulativo)
12. [Priorità d'Intervento](#12-priorità-dintervento)

---

## 1. Executive Summary

L'analisi approfondita ha identificato **34 punti** (6 critici, 10 alti, 13 medi, 5 bassi) distribuiti su tutti i sottosistemi.

| Severità | Conteggio |
|---|---|---|
| 🔴 Critico | 6 |
| 🟠 Alto | 10 |
| 🟡 Medio | 13 |
| 🟢 Basso | 5 |
| **Totale** | **34** |

### Mappa Calore per Sottosistema

```
LLM Pipeline      ████████████████░░░  6 (3 critici, 2 alti, 1 medio) 🆕
RAG & Embedding   ████████████░░░░░░░  4 (1 critico, 2 alti, 1 medio)
Memoria Episodica ██████████████░░░░░  5 (1 critico, 2 alti, 2 medi) 🆕
Synaptiq          ████░░░░░░░░░░░░░░░  2 (1 medio, 1 basso)
Scheduler & Tasks ████████░░░░░░░░░░░  3 (1 critico, 1 alto, 1 medio)
Telegram/Userbot  ████░░░░░░░░░░░░░░░  2 (1 alto, 1 medio)
Dashboard/Admin   ██████░░░░░░░░░░░░░  3 (1 alto, 1 medio, 1 basso)
Infrastruttura    ████░░░░░░░░░░░░░░░  2 (2 bassi)
Anti-Patterns     ██████████████████░░  6 (2 critici, 1 alto, 3 medi) 🆕
```

### Top 5 per Impatto Utente

1. **Tripla chiamata LLM per richiesta** — TTFT 30-60s invece di 8-10s
2. **Cron job esegue pipeline LLM completa** — 30-60s per un reminder
3. **Mem0 → API → Mem0: phantom request loop** — richieste infinite ogni 2-5s 🆕
4. **Compress ValueError: gatekeeper 2048-ctx overflow** — crash su ogni richiesta con contesto grande 🆕
5. **Tool-calling rigenera risposta** — 17s → 34s con tool

---

## 2. LLM Pipeline

### C1 🔴 CRITICO — Tripla Chiamata LLM per Richiesta

**File:** `agent/prompt.py:458-864` + `core/llm_engine.py:516-675`
**Analisi:** 2026-07-26 — Sisyphus

#### Stato Attuale: Costo per Scenario

La pipeline ha 3 potenziali LLM call: Gatekeeper (Qwen3.5 CPU, grammar JSON), Compressor (Qwen3.5 CPU), e Gemma 4 (GPU). Il costo reale dipende dall'intento e dall'esito del keyword bypass:

| Scenario | Esempio | Bypass OK? | Gatekeeper | Compressor | Gemma 4 | **Totale LLM call** | **TTFT stimato** |
|---|---|---|---|---|---|---|---|
| **A** Saluto puro | "Ciao", "Buongiorno" | ✅ `PURE_GREETING` | — | — | ✅ | **0** | ~8s |
| **B** Meta/progetti | "Quali progetti hai?" | ✅ `META_PHRASES` | — | — | ✅ | **0** | ~8s |
| **C** Query progetto (bypassa) | "Spiega il codice main.py" | ✅ `PROJECT_KEYWORDS` | — | ✅ | ✅ | **2** | ~20-35s |
| **D** Query progetto (no bypass) | "Analizza impatto modifica config worker" | ❌ | ✅ | ✅ | ✅ | **3** | ~30-60s |
| **E** Conversazione generale | "Cosa sono le reti neurali?" | ❌ | ✅ (wasted!) | — | ✅ | **1** | ~13-18s |
| **F** Domanda su contesto | "Come va l'implementazione?" | ❌ | ✅ (wasted!) | — | ✅ | **1** | ~13-18s |

**Distribuzione stimata:** A+B ~20%, C ~50%, D ~15%, E+F ~15%. Solo il 15% delle richieste paga il caso peggiore (3 LLM call), ma il 65% (C+D) paga SEMPRE il compressor anche quando il contesto RAG è vuoto o minimo.

#### Costo del Compressor Qwen3.5 su CPU

Il compressor è chiamato per OGNI query progetto (C+D = ~65% delle richieste), indipendentemente dalla quantità di contesto RAG:

```python
# prompt.py:706-736 — RAG può essere VUOTO ma compressor viene chiamato lo stesso
rag_ctx_local = await search_documents(...)  # può tornare ""
# ...
await _run_compression(clean_msg, rag_context_for_compress, ...)  # CHIAMATO SEMPRE
```

`_run_compression` assembla il contesto (max 1500 chars dopo C6 fix, `_GK_MAX_CHARS`) e chiama Qwen3.5 CPU con `num_predict=512`:

- **Con RAG pieno** (3000+ chars prima del clamp): ~10-20s CPU
- **Con RAG vuoto o minimo** (< 500 chars): ~5-10s CPU comunque (l'LLM deve processare anche solo il prompt system + query)

Il clamp `_GK_MAX_CHARS=1500` ha risolto il crash (C6) ma non riduce la latenza: Qwen3.5 processa comunque 1500 token di prompt system + input.

#### Costo del Gatekeeper Qwen3.5 su CPU

`_run_gatekeeper` chiama `engine.classify_intent()` con LlamaGrammar per forzare output JSON strutturato:

```python
# llm_engine.py:550-557
grammar_obj = LlamaGrammar.from_string(grammar_str)  # compile grammatica
messages = [{"role": "user", "content": prompt}]
response = await self.generate_chat(messages, stream=False,
    options={"temperature": 0.0, "num_predict": 60}, grammar=grammar_obj, model="gatekeeper")
```

- Grammar compilation overhead: ~0.5-1s
- Qwen3.5 inference: ~3-8s per ~60 token (CPU, 0.6B param, 2048 ctx)
- **Costo esatto: ~5-10s per richiesta** per ottenere `{"intent":"project|meta|general","project":"null|Nome","confidence":0.95}`

#### Analisi Critica: "Dove viene sprecato il budget LLM?"

```
                                      ┌── Intent "general" → EARLY RETURN
                                      │   (nessuna RAG/compressione)
                                      │   MA: gatekeeper già pagato! ❌
               ┌── Bypass OK? ───NO──┴── Intent "project" → context gathering + compression + gemma4
               │                       (gatekeeper pagato IN PIÙ del bypass che poteva catturarlo)
Richiesta ─────┤
               │                   ┌── General/meta → EARLY RETURN (0 LLM)
               └── Bypass OK? ─YES─┴── Project → compression + gemma4 (2 LLM)
```

**I due sprechi principali:**
1. **Gatekeeper wasted** (scenario E+F, ~15%): Query generali che non matchano keyword → paghiamo 5-10s per sapere che è "general" e poi facciamo early return
2. **Compressor sprecato** (scenario C, ~50%): Query progetto semplici (RAG vuoto o < 500 chars) → paghiamo 5-10s di compressione che non riduce nulla di significativo

#### 🔍 Revisione Qualità: Opzioni che NON Deteriorano il Risultato

L'utente richiede di escludere qualsiasi soluzione che potrebbe peggiorare la qualità delle risposte. Ogni opzione è valutata per impatto sul risultato finale:

| # | Opzione | Rischio Qualità | Motivo | Verdetto |
|---|---|---|---|---|
| **1** | Skip compressor per contesto piccolo | **❌ Nullo** | Usa raw fallback già esistente. Il formato raw produce risposte PIÙ naturali per query semplici (system prompt meno "caveman"). Stessa informazione. | ✅ **SICURO** |
| **2** | Cache gatekeeper results | **⚠️ Stale classification** | Cache potrebbe tornare "general" per una query ora "project" in contesto cambiato → nessun RAG → risposta peggiore | ❌ **ESCLUSO** |
| **3** | Pure greeting check in main.py | **❌ Nullo** | Stessa regex, stessa logica, stesso flusso — solo spostato prima | ✅ **SICURO** |
| **4** | Gatekeeper+Compressor unificati | **🔴 Output misto fragile** | Qwen3.5 0.6B non fa bene due cose in una passata. Grammar JSON blocca output testo libero. Se una parte fallisce, si perdono entrambe. | ❌ **ESCLUSO** |
| **5** | Espansione keyword bypass | **⚠️ Falsi positivi** | Parole comuni ("perché", "verifica") in contesto generale → falso project → RAG sprecato. MA: con aggiunte CONSERVATIVE (solo termini tecnici puri) il rischio è nullo. | ✅ **SICURO (se conservativo)** |
| **6** | Classifier ONNX leggero | **🔴 Accuratezza inferiore** | BERT 110M vs Qwen3.5 0.6B: perde su casi ambigui. Serve training data che per definizione ha blind spot. | ❌ **ESCLUSO** |
| **7** | Context gathering parallelo | **❌ Nullo** | Stessi path codice, stessi dati, solo timing diverso. Se cancellato (gatekeeper→general), nessun danno. | ✅ **SICURO** |

**Opzioni escluse:** 2 (stale cache), 4 (output misto fragile), 6 (accuratezza inferiore).

Option 5 inclusa solo con aggiunte CONSERVATIVE (termini tecnici puri, niente parole comuni ambigue).

---

#### Opzioni Safe — Analisi Dettagliata

##### Opzione 1 🟢 — Skip Compressor per Contesto Piccolo (P0)

**Sforzo:** ~15min | **Impatto:** -1 LLM call per ~40% richieste progetto | **Qualità: SICURO**

Se il contesto da comprimere è sotto soglia, usa direttamente il formato raw invece di chiamare Qwen3.5:

```python
# In build_omniscient_prompt(), dopo _allocate_budget():
_total_context = len(rag_context_for_compress) + len(history_str) + len(clean_msg)
COMPRESSOR_MIN_CHARS = 2000  # env var
if _total_context < COMPRESSOR_MIN_CHARS:
    logger.info(f"🗜️ Contesto piccolo ({_total_context}ch < {COMPRESSOR_MIN_CHARS}ch), skip compressione")
    compressed = _build_raw_fallback(...)
    _compression_is_raw = True
else:
    compressed, _compression_is_raw = await _run_compression(...)
```

Perché è sicuro:
- `_build_raw_fallback()` esiste già (linee 392-404) come fallback per compressione fallita — identica logica di sistema prompt
- Il formato raw usa system prompt PIÙ NATURALE ("helpful coding assistant" invece di "direct coding assistant... skip pleasantries")
- Per query semplici, il formato raw produce risposte MIGLIORI (più naturali, meno forzate)
- L'informazione è identica: stessi RAG, stessa memoria, stessa history

```python
# Raw system prompt (più naturale per query semplici):
"You are Jarvis, a helpful coding assistant with access to project context."

# Caveman system prompt (ottimizzato per task codice complessi):
"You are Jarvis, a direct coding assistant. Be concise but natural. "
"Skip pleasantries and fluff — get straight to the point."
```

Configurabile via env var: `COMPRESSOR_MIN_CHARS=2000` (default), `COMPRESSOR_ENABLED=true` (master switch).

---

##### Opzione 3 🟢 — Pure Greeting Check in main.py (P1)

**Sforzo:** ~10min | **Impatto:** -0.1s overhead per saluti | **Qualità: SICURO**

Spostare il check `PURE_GREETING` da dentro `build_omniscient_prompt()` a `main.py`, prima della chiamata:

```python
# main.py (~linea 756) — PRIMA di build_omniscient_prompt()
if PURE_GREETING.match(str(raw_messages[-1].get("content", "")).strip().lower()):
    logger.info("🗣️ Saluto puro, skip build_omniscient_prompt")
    # Vai direttamente a generate_chat() con i messaggi raw
else:
    body["messages"] = await build_omniscient_prompt(...)
```

Stessa identica regex, stesso flusso di early return — solo eseguito prima. Risparmia ~50-100ms di allocazione tracer, datetime injection, history truncation.

---

##### Opzione 5 🟢 — Espansione Keyword Bypass (P1, SOLO pattern conservativi)

**Sforzo:** ~30min | **Impatto:** +5-10% bypass rate | **Qualità: SICURO solo con termini tecnici puri**

Le parole comuni (italiano generico) causano FALSI POSITIVI:
| Parola | Falso positivo? | Esempio dannoso |
|---|---|---|
| `perché` | ❌ SÌ | "Perché il cielo è blu?" → project → RAG sprecato |
| `verifica` | ❌ SÌ | "Verifica se domani piove" → project → RAG sprecato |
| `cosa fa` | ❌ SÌ | "Cosa fa quel rumore?" → project → RAG sprecato |

Aggiunte **SICURE** (solo termini tecnici / git):
```python
# Git e versioning (SICURO — semanticamente legati a codice)
'commit', 'branch', 'pull request', 'pr', 'issue', 'fix', 'feature',
# Azioni tecniche specifiche (SICURO — non appaiono in conversazione generale)
'migra', 'refactorizza', 'compila', 'deploy', 'builda',
# Verbi imperativi tecnici (SICURO — usati solo per richieste di codice)
'scrivi', 'crea', 'modifica', 'rimuovi', 'cancella',
'analizza', 'calcola', 'genera', 'converti', 'traduci (codice)'
```

🔴 **NON aggiungere**: `perché`, `perche`, `cosa fa`, `come funziona`, `dov'è`, `dove si trova`, `testa`, `verifica`, `confronta`, `compara`, `monitora`, `ottimizza`, `automatizza`, `configura` — troppi falsi positivi in linguaggio naturale.

---

##### Opzione 7 🟡 — Context Gathering in Parallelo con Gatekeeper (P1)

**Sforzo:** ~2h | **Impatto:** -0.5-2s su query progetto | **Qualità: SICURO**

Avviare RAG/Memory/Synaptiq gathering IN PARALLELO con il gatekeeper (invece che dopo):

```python
# Proposto: parallelo
gk_task = asyncio.create_task(_run_gatekeeper(msg, ctx))
ctx_task = asyncio.create_task(asyncio.gather(_gather_rag(), _gather_memory(), ...))
gk = await gk_task  # 5-10s
if gk.intent == "project":
    ctx = await ctx_task  # già completato (o quasi) — risparmia 0.5-2s
else:
    ctx_task.cancel()  # scarta contesto — nessun danno
```

Perché è sicuro:
- Stessi path codice: `_gather_rag()`, `_gather_memory()`, `_gather_synaptiq()` — stesse funzioni già usate
- Se gatekeeper dice "general" → contesto cancellato: nessun side effect (cancellazione remota? No, Qdrant è read-only)
- Dopo C2 fix, RAG parte SOLO per project intent → nessuna differenza per query generali
- Impatto limitato (~0.5-2s risparmiati) ma gratuito

---

##### Opzione 8 🟢 — Skip Compressor se Non Cè Contenuto Comprimibile (P0, REFINEMENT di Op1)

**Sforzo:** ~5min | **Impatto:** -1 LLM call per ~20% richieste progetto | **Qualità: SICURO**

Prima ancora del check dimensionale: se TUTTE le fonti di contesto sono vuote, il compressor non ha nulla da comprimere:

```python
# In build_omniscient_prompt(), DOPO context gathering ma PRIMA compressione:
_has_compressible_content = bool(rag_ctx or web_ctx or mem_ctx or cg_ctx)
if not _has_compressible_content:
    logger.info("🗜️ Nessun contenuto da comprimere (RAG/web/mem/Synaptiq vuoti), skip compressione")
    compressed = _build_raw_fallback(clean_msg, rag_final, web_final, mem_final, ...)
    _compression_is_raw = True
```

Questo copre il caso in cui:
- Query progetto non matcha nulla in Qdrant (RAG vuoto)
- Nessun web context
- Nessuna memoria rilevante
- Synaptiq non ha risultati

Il compressor riceverebbe solo `[PROJECT: X]\n[HISTORY]\n...\n[USER_QUERY]\n...` — tipicamente 200-500 chars di niente da comprimere. 5-10s sprecati.

**Nota:** Op8 è un sotto-caso di Op1 (contesto totale < 2000 chars è quasi sempre vero quando tutte le fonti sono vuote). Ma è un check ancora più economico (booleano vs calcolo lunghezza) e cattura il caso PRINCIPALE di compressor sprecato. Implementare ENTAMBI: prima Op8 (nessun contenuto → skip), poi Op1 (contesto piccolo → skip), infine compressor (contesto grande → comprimi).

---

#### Tabella Comparativa Finale (Solo Opzioni Qualità-Sicure)

| # | Opzione | Sforzo | LLM Call Risparmiate | Riduzione TTFT | Priorità |
|---|---|---|---|---|---|
| **1** | Skip compressor per contesto piccolo | **~15min** | 1 per ~40% richieste progetto | **-25% medio** | **P0** |
| **8** | Skip compressor se niente da comprimere | ~5min | 1 per ~20% richieste progetto | -15% medio | P0 (con Op1) |
| **3** | Pure greeting check in main.py | ~10min | 0 (overhead) | -0.1s | P1 |
| **5** | Espansione keyword (solo tecnici) | ~30min | 0.05 per richiesta (incrementale) | -3% medio | P1 |
| **7** | Context gathering parallelo | ~2h | 0 (solo latenza nascosta) | -1-2s su project | P1 |

#### Raccomandazione Finale

**Approccio a 3 livelli per il compressor** (Op1 + Op8 combinati):

```
Dopo context gathering:
  1. C'è contenuto da comprimere?  (Op8)
     NO  → raw fallback, salta compressor ✅
     SÌ  → vai a 2.
  2. Contesto < soglia (2000ch)?   (Op1)
     SÌ  → raw fallback, salta compressor ✅
     NO  → vai a 3.
  3. Esegui Caveman Compressor      (solo per ~25% richieste)
         → compressione utile: contesto +3000 chars
```

```
Dopo tutte le ottimizzazioni safe (Op1+3+5+7+8):

Scenario A (saluto):       0 LLM call, ~8s         ← già OK
Scenario B (meta):         0 LLM call, ~8s         ← già OK
Scenario C (progetto semplice): 1 LLM call (solo Gemma 4), ~10-15s  ← -50% ✅
Scenario D (progetto complesso): 2 LLM call (compressor + Gemma 4), ~18-28s  ← -40% ✅
Scenario E (generale):     1 LLM call (gatekeeper), ~13-18s  ← invariato
Scenario F (contesto):     1 LLM call (gatekeeper), ~13-18s  ← invariato

Riduzione LLM call media: da ~1.4 a ~0.8 per richiesta (-43%)
Riduzione TTFT media: da ~20-30s a ~12-18s
```

---

### C4 🔴 CRITICO — Tool-Calling con Doppia Generazione LLM

**File:** `main.py:807-842`

**Problema:** Quando il modello emette `tool_calls` in streaming, il flusso scarta la prima risposta e ne genera una SECONDA:

```python
response = await engine.generate_chat(...)   # PRIMA chiamata (non-stream)
# ... tool execution ...
response = await engine.generate_chat(...)   # SECONDA chiamata (risposta finale)
```

La prima generazione produce contenuto testuale che viene **completamente scartato**. L'utente vede la risposta solo dopo la SECONDA generazione.

**Impatto:** Latenza raddoppiata (~17s → ~34s per query con tool).

**Soluzione proposta:**
- Accumulare la prima risposta in un buffer invece di scartarla
- Dopo esecuzione tool, ri-usare il contenuto già generato + risultati tool
- Richiederebbe modifica al flusso streaming di llama-cpp-python (API di pausa/ripresa non disponibile)

**Sforzo:** ~2-3h | **Impatto:** Latenza tool-calling -50%

---

### C6 🔴 CRITICO — Compress ValueError: Gatekeeper 2048-ctx Overflow ✅ RISOLTO

**File:** `core/llm_engine.py:594-667` → fix a linea 626-633

**Problema:** `compress_prompt()` assemblava `raw_data` da history (1500 char) + rag_context (3000 char) + user_query senza limite sul totale. Il gatekeeper Qwen3.5 ha `GATEKEEPER_N_CTX=2048` token. Quando il totale superava questo limite (facile con testo CJK ~1 char/token, o dopo che il phantom loop aveva riempito la history), `llm.create_chat_completion()` lanciava `ValueError('Requested tokens (X) exceed context window of 2048')`.

Il valore `num_predict=512` nel compress era anche troppo alto: il compressore doveva produrre output breve, non 512 token.

**Fix applicato:**
- ✅ Aggiunto limite `_GK_MAX_CHARS = 1500` per `raw_data` prima di inviarlo al gatekeeper
- Budget: system prompt (~100-450 token CJK vs English) + risposta (~50 token) + overhead (~50 token) = ~200-550 token overhead; 2048 - 550 = ~1500 token per raw_data
- Se superato, tronca con log: `"Compress input Xch > 1500ch, truncating for gatekeeper 2048-ctx"`
- `num_predict=512` era già stato ridotto da 2048 a 512 in un fix precedente (linea 635)

**Sforzo:** ~15min ✅ | **Impatto:** Elimina ValueError su richieste con contesto grande

---

### H1 🟠 ALTO — httpx.AsyncClient Creato per Ogni Richiesta di Offloading ✅

**File:** `core/llm_engine.py:372-414`

**Problema:** Ogni `generate_chat()` con `EXTERNAL_GPU_URL` creava DUE nuovi `AsyncClient` (connection pool + handshake TCP + namespace eventi). Inoltre, `state.http_client` era già definito in `lifecycle.py:112` con timeout 300s ma non veniva mai usato qui.

L'health check con timeout 1.5s aggiungeva latenza anche quando il Worker era offline, ritardando il fallback a CPU locale.

**Soluzione applicata:**
- ✅ Rimosso health check separato (`AsyncClient timeout=1.5`) — l'errore sulla POST fa scattare direttamente il fallback
- ✅ Usato `state.http_client` singleton invece di creare nuovi `httpx.AsyncClient` per ogni richiesta
- ✅ Rimosso `import httpx` (non più necessario)
- Rimosso ~12 righe di codice boilerplate

**Sforzo:** ~30min ✅ | **Impatto:** -1.5s overhead offload quando Worker offline

---

### H3 🟠 ALTO — PriorityLock con Priorità Sempre 0

**File:** `core/llm_engine.py:41-70`

```python
class PriorityLock:
    """Lock asincrono con 3 livelli di priorità via heapq."""
    # ... ~30 righe di complessità heapq ...
    
    await lock.acquire(priority=0)  # SEMPRE chiamato con priority=0!
```

**Problema:** Chat, gatekeeper, embedding chiamano tutti `priority=0`. La complessità O(log n) dell'heap e la struttura a 3 code di priorità sono **completamente inutilizzate**. Un semplice `asyncio.Lock` sarebbe O(1) e 10 righe invece di 30.

**Soluzione proposta:** Sostituire con `asyncio.Lock` o documentare che PriorityLock è riservato per uso futuro (con env var `PRIORITY_LOCK_ENABLED`).

**Sforzo:** ~15min | **Impatto:** Minimo (5-15μs per lock), ma elimina codice morto

---

### M1 🟡 MEDIO — Thinking Mode Pattern Applicati per Tutte le Famiglie ✅ COMPLETATO

**File:** `agent/tags.py:29-109`

```python
THINKING_PATTERNS = [ ... ]  # 35 pattern per 7 famiglie modello

def strip_thinking_blocks(text, model_family="all"):
    # Con model_family="all" applica TUTTI i pattern
```

**Problema:** `strip_action_tags()` e `TagSafeStream` usavano `model_family="all"`, applicando pattern Qwen, DeepSeek, Mistral, Phi, Command-R anche quando il modello attivo è Gemma 4. Ogni pattern era una regex compilata con `re.DOTALL` — costo 1-5μs per pattern, ma 35× per ogni chunk di streaming.

**Soluzione applicata:**
- `strip_all_tags(text, model_family="all")` — nuovo parametro con default backward-compat
- `strip_action_tags(text, model_family="all")` — nuovo parametro con default backward-compat
- `TagSafeStream.__init__(model_family="all")` — nuovo parametro; filtra thinking blocks e Gemma residuals in base alla famiglia
- Tutti i caller (`main.py`, `dashboard.py`, `openai_api/chat.py`) aggiornati a passare `MODEL_PROFILE.family`

**Impatto:** Con Gemma 4 attivo, lo streaming valuta ~20 pattern invece di 35 per chunk. Per modelli Qwen/Llama/Mistral, il risparmio è ancora maggiore (~15 pattern anziché 35).

---

## 3. RAG & Embedding

### C2 🔴 CRITICO — RAG Eseguito per Ogni Richiesta (Anche Saluti) ✅ COMPLETATO

**File:** `agent/prompt.py:654-764`

**Problema:** `build_omniscient_prompt()` eseguiva SEMPRE per OGNI richiesta:

1. `list_rag_projects()` (prompt.py:568) → chiamata Qdrant HTTP
2. `search_documents()` (prompt.py:710) → embedding query + Qdrant search + reranker
3. Synaptiq hybrid search (prompt.py:720-729)
4. `_gather_memory()` (prompt.py:658) → ricerca Mem0 (2 chiamate)

Anche per un **"Ciao"**, la pipeline eseguiva embedding vettoriale, Qdrant scroll, reranker Qwen3 (CPU, 600MB RAM), e due ricerche Mem0.

**Soluzione applicata:**
1. **Cache `list_rag_projects()` con TTL 60s** — nuova `_get_cached_rag_projects()` con cache per-utente (`_RAG_PROJECTS_CACHE` dict). Elimina la chiamata HTTP Qdrant per ogni richiesta; per greeting/meta la cache è già popolata.
2. **`_gather_rag()` esegue `search_documents()` solo per project intent** — condizione cambiata da `if not _is_meta_query` a `if _is_project_query and not _is_meta_query`. Questo garantisce che embedding vettoriale e reranker non girino mai per query non-progetto.
3. **Le gather task (RAG/memoria/Synaptiq) erano già posizionate dopo gli early return** — verifica confermata.

**Impatto:** TTFT -30% su query non-progetto, ~300-800ms CPU risparmiati per richiesta

---

### H2 🟠 ALTO — Doppia Operazione Qdrant per File (Scroll + Upsert + Delete) ✅

**File:** `rag/engine.py:801-812`

**Problema:** `process_single_file()` eseguiva 3 operazioni Qdrant per file:
1. Scroll per recuperare vecchi chunk ID
2. Upsert nuovi punti
3. Delete vecchi punti

Per 1000 file in ingestion: **3000 round-trip Qdrant** invece di 1000.

**Soluzione applicata:**
- ✅ ID deterministici basati su `hashlib.md5(f"{rel_path}:{chunk_index}".encode()).hexdigest()`
- ✅ Qdrant upsert sovrascrive automaticamente per ID identico — eliminato scroll + delete
- ✅ Rimosso `import uuid` (non più necessario)
- Rimosse ~15 righe di scroll/delete boilerplate

**Sforzo:** ~30min ✅ | **Impatto:** -40% IO Qdrant durante ingestion/re-index

---

### H4 🟠 ALTO — state_lock Conteso tra Ingestion, Watchdog e Reset

**File:** `rag/engine.py:858-859`, `rag/engine.py:1048-1066`, `main.py:~320`

**Problema:** `process_single_file()` acquisisce `state.state_lock` per ogni file durante ingestion parallela. Con `MAX_CONCURRENT_EMBEDDINGS=8`, fino a 8 coroutine competono per lo stesso lock. Il lock è condiviso con:

- `rag_queue_worker()` — watchdog eventi
- `reset-all` endpoint — reset stato
- Session store — persist

**Impatto:** Colli di bottiglia in scrittura durante ingestion parallela. File in attesa del lock rallentano l'embedding batch.

**Soluzione proposta:**
- Lock separato per stato RAG (`rag_state_lock` in aggiunta a `state_lock`)
- Operazioni `rag_state.update()` in memoria con lock breve, persistenza periodica

**Sforzo:** ~1h | **Impatto:** Meno contenzione in ingestion parallela

---

### M2 🟡 MEDIO — Parser Tree-Sitter Ricreato per Ogni File

**File:** `rag/engine.py:190-259`

**Problema:** `extract_dependencies()` crea un nuovo `Parser()` per ogni file. Per 134K file in ingestion, ~134-670 secondi di overhead totale.

```python
for file_path in files:
    parser = Parser()           # ← nuova istanza per ogni file
    parser.language = ...
    tree = parser.parse(...)
```

**Soluzione proposta:** Cache singleton `Parser` per linguaggio:

```python
_PARSER_CACHE: dict[str, Parser] = {}

def _get_parser(language: str) -> Parser:
    if language not in _PARSER_CACHE:
        p = Parser()
        p.language = ...
        _PARSER_CACHE[language] = p
    return _PARSER_CACHE[language]
```

**Sforzo:** ~15min | **Impatto:** -1-5ms per file in ingestion

---

### M3 🟡 MEDIO — Upload File Completo per Richiesta File Specifica

**File:** `agent/prompt.py:689-706`

**Problema:** Quando l'utente chiede un file specifico (es. `main.py`), `_gather_rag()` esegue `os.walk` su DOC_DIR per trovare il file, poi `open()` e `read()` completo:

```python
matches = set(re.findall(r'\b([\w\.\-/]+\.(?:py|js|ts|...))\b', latest_msg))
for match in matches:
    for root, dirs, files in os.walk(DOC_DIR):  # ← walk completo ogni volta
        if filename_only in files:
            with open(fp, "r") as f:
                fc = f.read()  # ← file intero in memoria
```

`os.walk` su 335K file directory è costoso. Per match di filename, si potrebbe usare `glob` o un indice.

**Soluzione proposta:** Usare `glob.glob(f"**/{filename}", recursive=True)` o un indice filename→path pre-costruito.

**Sforzo:** ~30min | **Impatto:** -0.5-2s per richiesta file specifico

---

### M4 🟡 MEDIO — Reranker Carica Torch/Transformers a Livello Modulo

**File:** `rag/reranker.py:34-46`

**Problema:** Se `QWEN3_RERANKER_ENABLED=true`, `torch` e `transformers` vengono importati a livello modulo. Anche se il reranker non viene mai chiamato in una richiesta, i ~600MB di RAM del modello Qwen3-Reranker rimangono allocati.

**Soluzione proposta:** Rendere l'import lazy dentro `_reranker_fn()`:

```python
class LazyReranker:
    _model = None
    
    async def rerank(self, query, docs):
        if self._model is None:
            from transformers import AutoModel, ...  # import lazy
            self._model = AutoModel.from_pretrained(...)
        ...
```

**Sforzo:** ~30min | **Impatto:** -600MB RAM fisso (libera se reranker mai usato)

---

## 4. Memoria Episodica

### C7 🔴 CRITICO — Phantom Request Loop: Mem0 → API → Mem0 (Auto-Alimentante) ✅ RISOLTO

**File:** `core/config.py:372-374` (config) + `openai_api/chat.py:123-139, 177-194` (fix)

**Problema:** Mem0 è configurato per usare Jarvis stesso come backend LLM:
```python
"llm": {
    "provider": "openai",
    "config": {"model": MODEL_ID, "temperature": 0.0,
               "openai_base_url": "http://127.0.0.1:8000/v1"}
}
```
Ogni `memory.add()` triggera Mem0 a chiamare internamente `http://127.0.0.1:8000/v1/chat/completions` per entity extraction, con prompt in formato `## Summary...## Last k Messages...`. La richiesta finisce in `openai_api/chat.py` → `build_omniscient_prompt()` (aggiunge RAG/memoria contesto) → LLM → `process_response_tags()` (può creare ALTRE memorie) → loop infinito ogni ~2-5 secondi.

**Impatto:** Richieste infinite auto-alimentate, session flooding, CPU/GPU sprecati, crash per context overflow (contesto session cresce ad ogni iterazione). Self-amplificante: ogni giro aggiunge storia → contesto più grande → ValueError → fallback raw → memorizzato → altro giro.

**Perché il fix non è semplicemente "cambiare openai_base_url":** Mem0 non ha un'alternativa locale — non supporta llama-cpp-python direttamente. L'unica opzione è cortocircuitare la richiesta prima che causi recursion.

**Fix applicato:**
- ✅ `openai_api/chat.py:123-130`: Rilevamento richieste interne con `is_internal_query()` (messaggi che iniziano con `## Summary`)
- ✅ `openai_api/chat.py:132-139`: Bypass di `build_omniscient_prompt()` per richieste interne → usa messaggi raw direttamente (nessun RAG/memoria circolare)
- ✅ `openai_api/chat.py:177-194`: Skip di `process_response_tags()` per richieste interne → la risposta LLM non viene processata come memoria, interrompendo il loop
- ✅ `openai_api/chat.py:279`: Stessa protezione per il branch streaming
- Il fix è analogo a quello già presente in `main.py:674-682` per il nativo `/api/chat`

**Sforzo:** ~30min ✅ | **Impatto:** CRITICO — interrompe loop infinito, risolve flooding richieste, elimina la causa primaria del context overflow

---

### H5 🟠 ALTO — extract_entities() Chiamato per Ogni Salvataggio Memoria

**File:** `memory/engine.py:24-118`, `memory/engine.py:248-288`

**Problema:** `save_to_memory()` chiama `extract_entities(mem_text)` per ogni memoria salvata. `extract_entities()` è 94 righe con 6 pattern regex su un loop che può processare centinaia di match. Inoltre, ogni entità viene upsertata individualmente in Qdrant (1 round-trip per entità).

```python
# memory/engine.py:278-283
if mem_id:
    entities = extract_entities(text)    # ← 94 righe di regex per ogni salvataggio!
    if entities:
        await _ensure_entity_collection()
        for entity in entities:           # ← N round-trip Qdrant
            await _store_entities_for_memory(...)
```

**Impatto:** Per ogni `<MEMORY>` tag o salvataggio automatico: 5-50ms CPU regex + N round-trip Qdrant per entità. Con salvataggi frequenti (es. ogni messaggio), l'overhead si accumula.

**Soluzione proposta:**
- Rendere entity extraction **opzionale** (env var `ENTITY_EXTRACTION_ENABLED=false`)
- **Batch upsert** delle entità in una singola chiamata Qdrant invece di N chiamate
- Limitare extraction solo a memorie con `infer=True` (già parzialmente implementato ma non rispettato da tutti i chiamanti)

**Sforzo:** ~1h | **Impatto:** -5-50ms CPU + -N round-trip Qdrant per salvataggio memoria

---

### M10 🟡 MEDIO — Entity Collection con Vector Zero (Indice Inutile)

**File:** `memory/engine.py:185-200`

```python
await state.qdrant.upsert(
    points=[qdrant_models.PointStruct(
        id=ent_id,
        vector=[0.0] * 768,  # ← vector zero! Indice sprecato
        payload={...}
    )]
)
```

**Problema:** Le entità sono salvate con vector zero di 768 dimensioni. Qdrant crea un indice vettoriale (HNSW) su questi vector — ma sono tutti zero, quindi qualsiasi ricerca per cosine similarity restituisce risultati casuali. L'entity collection occupa spazio di indice e memoria inutilmente.

**Soluzione proposta:**
- Se le entità non sono cercate per similarità vettoriale: usare collection Qdrant **senza vector** (solo payload indicizzati)
- Oppure rimuovere entity collection e usare indici payload nativi Qdrant
- O generare embedding reali per le entità (ma costo aggiuntivo)

**Sforzo:** ~30min | **Impatto:** Riduzione spazio indice Qdrant, + pulizia architetturale

---

### M11 🟡 MEDIO — reindex_graph_connections() Non Cancellabile

**File:** `memory/engine.py:322-419`

**Problema:** `reindex_graph_connections()` scorre TUTTE le memorie in Qdrant con scroll paginato (limite 1000, max 5000). Per ogni memoria, estrae entità e upserta in entity collection. È un'operazione O(n) in lettura + O(m) in scrittura che può durare minuti e **non è cancellabile** (nessun check di `asyncio.current_task().cancelled()`).

**Soluzione proposta:**
- Aggiungere check periodici di cancellazione (`if task.cancelled(): return`)
- Aggiungere progress reporting via telemetry/callback
- Rendere avviabile come background task tracciata

**Sforzo:** ~30min | **Impatto:** Robustezza, evitare task orfani

---

## 5. Synaptiq Engine

### M12 🟡 MEDIO — get_graph_data() Carica l'Intero Grafo in Memoria

**File:** `graph/synaptiq_engine.py:520-597`

**Problema:** `get_graph_data()` chiama `storage.load_graph()` che carica l'intero grafo in memoria, poi itera su TUTTI i nodi e TUTTE le relazioni per costruire il dump JSON. Per un progetto con 5659 nodi e 21711 relazioni:

- ~5659 nodi × ~200 byte ≈ 1.1 MB
- ~21711 relazioni × ~100 byte ≈ 2.2 MB
- **Totale: ~3.3 MB + overhead Python oggetti**

Non enorme, ma cresce col progetto. Il reader lock è tenuto per tutta la durata della costruzione, bloccando altre operazioni di lettura.

**Soluzione proposta:**
- Aggiungere paginazione lato server (offset/limit) per grafi grandi
- O fornire una query endpoint-specifica invece di dump completo
- Benchmarkare: monitorare crescita memoria col numero di nodi

**Sforzo:** ~1h | **Impatto:** Minor memoria per dashboard, reader lock più breve

---

### L4 🟢 BASSO — Import Safety Pattern Duplicato in 5+ Moduli

**File:** `prompt.py:25-28`, `dashboard.py:20-23`, e altri

**Problema:** Il pattern `try/except ImportError` per Synaptiq è copiato in ogni modulo che lo usa:

```python
try:
    from graph.synaptiq_engine import synaptiq_engine
except ImportError:
    synaptiq_engine = None
```

**Soluzione proposta:** Centralizzare in `config.py`:

```python
# config.py
def get_synaptiq_engine():
    try:
        from graph.synaptiq_engine import synaptiq_engine
        return synaptiq_engine
    except ImportError:
        return None
```

**Sforzo:** ~15min | **Impatto:** DRY, meno boilerplate

---

## 6. Scheduler & Tasks

### C3 🔴 CRITICO — Cron Job Esegue Pipeline LLM Completa

**File:** `scheduler/cron.py:36-72`, `agent/tags.py:196-237`
**Analisi:** 2026-07-26 — Sisyphus

#### Stato Attuale: Flusso Completo

```
LLM emette tag              →  tags.py                    →  cron.py
<N NOTIFY_IN>30|Ricordami       _handle_notify_in()          add_relative_job()
  di comprare il pane</  ─────  split("|", 1)        ────  save_jobs()
                               prompt_text.strip()          scheduler.add_job()
                                           │
                              ⏰ Timer scatta (30 min dopo)
                                           │
                              execute_cron_job(job_id, "Ricordami di comprare il pane", chat_id)
                                │
                                ├── build_omniscient_prompt(messages)
                                │     ├── keyword_bypass (0 LLM)
                                │     ├── gatekeeper (Qwen3.5 CPU, 5-10s)
                                │     ├── context gathering (RAG + mem + Synaptiq)
                                │     ├── compressor (Qwen3.5 CPU, 5-10s)
                                │     └── build_final_prompt
                                │
                                ├── engine.generate_chat()  (Gemma 4 GPU, 8-17s)
                                │
                                └── send_message("🔔 Ricordati di comprare il pane!")
                                
TOTALE: ~30-60s per inviare "Ricordati di comprare il pane!"
```

#### Analisi: Chi Cosa e Perché

Tutti i job schedulati nascono da tag XML nella risposta LLM. I tag determinano semanticamente il TIPO di job:

| Tag | Funzione handler | Ruolo | Tipo di prompt | Frequenza stimata |
|---|---|---|---|---|
| `<NOTIFY_ONCE>` / `<NOTIFYONCE>` | `_handle_notify_once` | Promemoria SINGOLO a data fissa | **SEMPRE reminder puro** | ~50% |
| `<NOTIFY_IN>` / `<NOTIFYIN>` | `_handle_notify_in` | Timer RELATIVO tra N minuti | **SEMPRE reminder puro** | ~30% |
| `<SCHEDULE>` | `_handle_schedule` | Promemoria RICORRENTE (cron) | Reminder O azione programmata | ~20% |

Il dato cruciale: **NOTIFY_ONCE e NOTIFY_IN sono SEMPRE reminder puri.** Non ha senso che l'LLM li usi per richieste d'azione — per quelle esistono `<SSH>`, `<EXEC>`, `<SCHEDULE>`.

La `cron_instruction` nel codice (cron.py:41-47) dice:
> "Se è una richiesta di azione o ricerca, fornisci il risultato. Se è un semplice promemoria, scrivi un messaggio diretto all'utente ricordandogli il compito."

Il sistema stesso riconosce che ci sono due categorie — ma tratta TUTTI i job con la pipeline LLM completa.

#### Costo Reale

Per un reminder "Ricordami di comprare il pane":
1. `build_omniscient_prompt()` con messaggio fittizio "[SISTEMA: Esecuzione Task Schedulato]\nObiettivo: Ricordami di comprare il pane"
   - keyword_bypass → nessun match (testo di sistema, non utente)
   - gatekeeper Qwen3.5 → classifica come "general" o "project" → ~5-10s
   - context gathering (RAG + memoria + Synaptiq) → ~0.5-2s per nulla
   - compressor Qwen3.5 → comprime testo vuoto → ~5-10s
2. `generate_chat()` → Gemma 4 produce "Ecco un promemoria per comprare il pane!" → ~8-17s

**30-60s per riformulare "Ricordami di comprare il pane" in "Ricordati di comprare il pane".**

#### 🔍 Revisione Qualità

| Opzione | Rischio Qualità | Motivo | Verdetto |
|---|---|---|---|
| **1** Direct send per NOTIFY_ONCE/NOTIFY_IN | **❌ Nullo** | Questi tag sono SEMPRE reminder. La semantica del tag lo garantisce. | ✅ **SICURO** |
| **2** `concise=True` per SCHEDULE | **❌ Nullo** | `concise=True` già testato in produzione. Rimuove RAG/memoria ma preserva LLM generation. | ✅ **SICURO** |
| **3** Skip compressor anche per SCHEDULE (come C1-Op8) | **❌ Nullo** | Cron non ha contesto RAG da comprimere. Stessa logica di C1-Op8. | ✅ **SICURO** |

---

#### Opzione C3-Op1 🟢 — Direct Send per NOTIFY_ONCE / NOTIFY_IN (P0)

**Sforzo:** ~15min | **Impatto:** -100% LLM call per ~80% job schedulati | **Qualità: SICURO**

I tag NOTIFY_ONCE e NOTIFY_IN sono SEMPRE reminder puri — la semantica del tag è "avvisami tra N minuti / a data X". L'LLM usa questi tag ESCLUSIVAMENTE per notifiche temporali.

**Implementazione:** Aggiungere un prefisso `[REMINDER]` al prompt quando il job viene creato da NOTIFY tags, e controllarlo in `execute_cron_job`:

```python
# tags.py — _handle_notify_once / _handle_notify_in
prompt_text = prompt_text.strip()
MARKER = "[REMINDER] "
add_date_job(date_str, MARKER + prompt_text, ctx.chat_id or 0)

# cron.py — execute_cron_job
REMINDER_MARKER = "[REMINDER] "
if prompt.startswith(REMINDER_MARKER):
    text = prompt[len(REMINDER_MARKER):]
    bot_reply = f"🔔 **Promemoria**: {text}"
    if state.telegram_app and state.telegram_app.bot:
        await state.telegram_app.bot.send_message(
            chat_id=chat_id, text=bot_reply, parse_mode="Markdown"
        )
    if job_id.startswith("job_once_"):
        remove_cron_job(job_id)
    return  # ← salta TUTTA la pipeline LLM
```

Perché la qualità non si deteriora:
- Il testo del promemoria è SCRITTO DALL'UTENTE (o generato dal LLM quando l'utente chiede "ricordami X") — non c'è bisogno di riformularlo
- Il reminder ha un solo scopo: ricordare all'utente qualcosa a una data ora
- L'LLM non aggiunge informazioni (nessuna RAG/memoria da consultare)
- Inviare il testo direttamente è FUNZIONALMENTE IDENTICO a quello che farebbe l'LLM
- **Vantaggio collaterale**: il messaggio ricevuto è PIÙ FEDELE all'intenzione originale dell'utente (l'LLM potrebbe riformulare e introdurre errori)

**Backward compat:** I job già schedulati non hanno il marker `[REMINDER]` → passano dalla pipeline LLM come prima. Solo i NUOVI job beneficiano dell'ottimizzazione.

**Edge case:** Se il prompt contiene `[REMINDER]` come testo normale (iniziato da utente)? → Impossibile, perché il marker è aggiunto DALL'HANDLER del tag, non viene dal prompt utente.

---

#### Opzione C3-Op2 🟢 — `concise=True` per Job SCHEDULE (P1)

**Sforzo:** ~5min | **Impatto:** -RAG/memoria/Synaptiq/web per ~20% job | **Qualità: SICURO**

Per i job creati da `<SCHEDULE>` (che potrebbero essere reminder o azioni programmate), usare `concise=True` invece della pipeline completa:

```python
# cron.py — execute_cron_job, dopo il check REMINDER
enriched_messages = await build_omniscient_prompt(messages, concise=True)
```

`concise=True` salta:
- RAG search (nessun progetto attivo in cron)
- Memory search (nessun contesto utente in cron)
- Synaptiq (nessuna analisi struttura codice)
- Web search (nessuna query web in cron)
- **Preserva**: compressor + Gemma 4 generation

Riduce il tempo di context gathering da ~2s a ~0.1s. TTFT passa da 30-60s a ~15-25s per job SCHEDULE.

**Nota:** Il cron instruction non contiene progetti, codice, o riferimenti a memoria — non c'è nulla su cui fare RAG. `concise=False` era sbagliato fin dall'inizio per questo use case.

---

#### Opzione C3-Op3 🟢 — Skip Compressor per Cron (come C1-Op8, P1)

**Sforzo:** ~5min | **Impatto:** -1 LLM call per job SCHEDULE | **Qualità: SICURO**

Il cron instruction `"[SISTEMA: Esecuzione Task Schedulato]\nObiettivo: '..."` è corto (100-300 chars). Non c'è contesto RAG da comprimere. Applicare la stessa logica di C1-Op8: skip compressor se non c'è contenuto comprimibile.

Questo si ottiene automaticamente se C1-Op8 è implementato (il check `_has_compressible_content` funziona anche per cron). Ma anche come fix indipendente:

```python
# In execute_cron_job, con concise=True:
# build_omniscient_prompt con cron message produce contesto piccolo
# → compressor skippato automaticamente se C1-Op1/Op8 implementato
```

---

#### Tabella Comparativa C3

| # | Opzione | Sforzo | LLM Call Risparmiate | Latenza |
|---|---|---|---|---|
| **1** | Direct send NOTIFY_ONCE/NOTIFY_IN | **~15min** | **2 per ~80% job** (gatekeeper + compressor + Gemma 4) | **30-60s → 0.1s** |
| **2** | `concise=True` per SCHEDULE | ~5min | 0 (preserva compressor + Gemma 4) | 30-60s → 15-25s |
| **3** | Skip compressor cron (via C1-Op8) | ~5min (già in C1) | 1 per ~20% job | 15-25s → 10-15s |

**Impatto complessivo C3:**
- 80% dei job (NOTIFY): 0 LLM call, 0.1s latenza ✅
- 20% dei job (SCHEDULE): 1-2 LLM call, 10-25s latenza ✅ (vs 30-60s prima)
- **LLM call risparmiate: ~1.6 per job medio**
- **Latenza media: da ~40s a ~5s (-87%)**

---

### H9 🟠 ALTO — JSON Persist su Disco per Ogni Operazione

**File:** `scheduler/cron.py:26-34`, `scheduler/tasks.py:18-26`

**Problema:** `load_jobs()` e `save_jobs()` sono chiamati per ogni add/remove job. `load_tasks()` è chiamato per ogni `get_open_tasks()` — che è chiamato da `_allocate_budget()` in `prompt.py:318` per OGNI richiesta progetto.

**Quindi:** Ogni richiesta progetto fa I/O su disco (`open()` + `json.load()`) per caricare `tasks.json`.

```
Richiesta utente → prompt.py → _allocate_budget() → get_open_tasks() → load_tasks() → I/O disco
```

**Soluzione proposta:**
- Cachare tasks.json in memoria (invalida su write)
- O caricare all'avvio e persistere periodicamente (ogni 60s)

```python
_tasks_cache = None
_tasks_cache_ts = 0
TASKS_CACHE_TTL = 5  # secondi

def get_open_tasks(user_id=None):
    global _tasks_cache, _tasks_cache_ts
    now = time.time()
    if _tasks_cache is None or now - _tasks_cache_ts > TASKS_CACHE_TTL:
        _tasks_cache = load_tasks()
        _tasks_cache_ts = now
    # filtro da _tasks_cache...
```

**Sforzo:** ~30min | **Impatto:** ~5-20ms I/O risparmiati per richiesta progetto

---

### M13 🟡 MEDIO — ADMIN_USERS[0] Hardcoded in morning_recap

**File:** `scheduler/cron.py:170-183`

```python
await state.telegram_app.bot.send_message(chat_id=ADMIN_USERS[0], ...)
```

**Problema:** Se `ADMIN_USERS` è vuoto, crasha `IndexError`. Se l'admin non usa Telegram, lo spam daily recap è inutile. Inoltre, `send_message(parse_mode="Markdown")` può fallire silenziosamente se il messaggio contiene caratteri Markdown non escaped.

**Soluzione proposta:**
- Controllo esplicito `if ADMIN_USERS` (c'è già ma non basta)
- Aggiungere try/except per Markdown parse error
- Rendere destinatario configurabile via env var `MORNING_RECAP_CHAT_ID`

**Sforzo:** ~15min | **Impatto:** Robustezza

---

## 7. Telegram & Userbot

### H6 🟠 ALTO — userbot_sessions Dict Cresce Illimitatamente (Memory Leak) ✅

**File:** `tg_bot/userbot.py:22-23, 72-78, 194-210`

**Problema:**
1. Ogni nuova conversazione con un contatto creava una nuova entry in `userbot_sessions`
2. Le entry NON venivano MAI rimosse — solo sovrascritte quando TTL scadeva e arrivava un nuovo messaggio
3. Col tempo, centinaia di entry si accumulavano (ogni owner × ogni contatto)
4. Il TTL check avveniva SOLO all'arrivo di un nuovo messaggio — se non arrivavano messaggi per giorni, la entry restava in memoria

**Soluzione applicata:**
- ✅ Aggiunta `_cleanup_userbot_sessions()`: background task che pulisce le entry scadute ogni 5 minuti
- ✅ Avviata automaticamente in `auto_start_existing()` tramite `asyncio.create_task()`
- Stessa logica della proposta originale con list comprehension per expired + delete

```python
async def _cleanup_userbot_sessions():
    while True:
        await asyncio.sleep(300)
        now = time.time()
        expired = [k for k, v in list(userbot_sessions.items())
                   if now - v["last_active"] > SESSION_TTL]
        for k in expired:
            del userbot_sessions[k]
        if expired:
            logger.info(f"🧹 Pulite {len(expired)} sessioni userbot scadute")
```

**Sforzo:** ~15min ✅ | **Impatto:** Memory leak fix, bound superiore dimensioni dict

---

### M14 🟡 MEDIO — engine.generate_chat() Chiamata con LLM_OPTIONS Completi per Userbot

**File:** `tg_bot/userbot.py:98-103`

```python
response = await engine.generate_chat(
    messages,
    tools=None,
    options=LLM_OPTIONS,  # ← stessi parametri del modello principale!
    stream=False
)
```

**Problema:** LLM_OPTIONS contiene `num_ctx=12288`, `num_predict=2048`, ecc. — parametri pensati per query RAG complesse. Per risposte userbot (conversazione generica senza RAG), si potrebbe usare un profilo più lightweight (es. `num_ctx=4096`, `num_predict=512`).

**Soluzione proposta:** Usare `USERBOT_LLM_OPTIONS` con parametri ridotti o usare il modello Gatekeeper (Qwen3.5 CPU) per conversazioni userbot semplici.

**Sforzo:** ~15min | **Impatto:** Minor VRAM/latenza per risposte userbot

---

## 8. Dashboard & Admin

### H7 🟠 ALTO — 2406 Righe in un Singolo File (dashboard.py) ✅

**File:** `admin/dashboard.py`, `admin/settings_manager.py`, `admin/telemetry_collector.py`

**Problema:** 2406 righe contengono:
- Router FastAPI (`dashboard_router`)
- `TelemetryCache` + collector
- Chat session in-memory ring buffer
- GPU metrics via subprocess
- Settings management con `SETTINGS_META` (73 env var)
- Log viewer (`_tail_file`, `JARVIS_LOG_SOURCE`)
- RAG management (`get_rag_collections`, reindex)
- Container management
- File structure viewer

Tutto in un file → difficile manutenere, testare, o modificare senza effetti collaterali.

**Soluzione applicata:** Splittato in 3 file:
- ✅ `settings_manager.py` — `SETTINGS_META` (73 env var), `_persist_env()`, `SETTINGS_OVERRIDES` (~620 righe)
- ✅ `telemetry_collector.py` — `TelemetryCache`, `_collect_gpu_cache()`, `_collect_health_cache()`, `telemetry_collector_loop()`, `start_telemetry_collector()` (~250 righe)
- ✅ `dashboard.py` — ridotto da 2406 a ~1566 righe (−35%), solo route handlers + re-export dai sub-moduli

Backward compat mantenuta: `main.py`, `lifecycle.py`, `routes/projects.py` continuano a importare da `admin.dashboard`.

**Sforzo:** ~2h ✅ | **Impatto:** Manutenibilità, testabilità

---

### M15 🟡 MEDIO — subprocess.run(nvidia-smi) Ogni 5 Secondi per GPU Metrics

**File:** `admin/dashboard.py:53-100`

**Problema:** `_collect_gpu_cache()` esegue `subprocess.run(["nvidia-smi", ...])` in un thread pool ogni 5 secondi. Tre chiamate nvidia-smi separate (temperature, driver, processes). Ogni chiamata fork/un processo.

```python
async def _collect_gpu_cache():
    out = await loop.run_in_executor(None, lambda: subprocess.run(
        ["nvidia-smi", "--query-gpu=...", ...], timeout=5))
    out2 = await loop.run_in_executor(None, lambda: subprocess.run(
        ["nvidia-smi", "--query-gpu=driver_version", ...], timeout=3))
    out3 = await loop.run_in_executor(None, lambda: subprocess.run(
        ["nvidia-smi", "--query-compute-apps=...", ...], timeout=3))
```

**Soluzione proposta:**
- **Usare `pynvml`** (binding Python diretto a NVML, senza subprocess, millisecondi invece di secondi)
- O consolidare in UNA singola chiamata nvidia-smi con tutte le metriche

```python
# Con pynvml:
import pynvml
pynvml.nvmlInit()
handle = pynvml.nvmlDeviceGetHandleByIndex(0)
temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
util = pynvml.nvmlDeviceGetUtilizationRates(handle)
```

**Sforzo:** ~30min install pynvml + ~30min rewrite | **Impatto:** -subprocess overhead, metriche più veloci e robuste

---

### L5 🟢 BASSO — _persist_env() Riscrive Intero .env a Ogni Modifica Settings

**File:** `admin/settings_manager.py` (via settings endpoint, ex `admin/dashboard.py`)

**Problema:** Ogni modifica alle impostazioni riscrive l'intero file `.env` su disco con atomic write (tmp + replace). Per 73 env var, scrive tutto anche se cambia una sola variabile.

**Impatto:** Basso — modifiche settings sono rare rispetto alle richieste. La scrittura atomica è corretta.

**Soluzione proposta:** Se necessario in futuro, leggere `.env` esistente, modificare solo la riga interessata, e riscrivere.

---

## 9. Infrastruttura

### L1 🟢 BASSO — Module-Level I/O in config.py

**File:** `core/config.py:299-327`

**Problema:** `config.py` esegue I/O a livello modulo:
- Scrittura `.env` per JWT_SECRET se mancante
- Scanning directory WORKSPACE_DIR per WORKSPACE_PROJECTS
- Rilevamento modelli GGUF

**Impatto:** ~10-100ms al boot. Succede UNA VOLTA.

---

### L2 🟢 BASSO — log_vram_usage() Usa subprocess.run(nvidia-smi)

**File:** `core/llm_engine.py:22-37`

**Problema:** `log_vram_usage()` esegue `subprocess.run(["nvidia-smi", ...])` che fork/un processo.

**Impatto:** ~100-300ms per chiamata. Attualmente chiamato solo 3-4 volte al boot. Se usato in futuro per monitoraggio runtime, migrare a `pynvml`.

---

## 10. Anti-Patterns Generali

### H8 🟠 ALTO — Mem0 Configurato per Chiamare la Propria API (Circolare) ✅ RISOLTO

**File:** `core/config.py:372-374`

**Problema:** Mem0 è configurato con `openai_base_url: "http://127.0.0.1:8000/v1"` — punta a Jarvis stesso. Questo crea una dipendenza circolare: `memory.add()` → Mem0 LLM call → `openai_api/chat.py` → `build_omniscient_prompt()` → contesto RAG/memoria → LLM → `process_response_tags()` → `memory.add()` → loop.

L'anti-pattern è duplice:
1. **Auto-riferimento**: un servizio che chiama se stesso come LLM backend per memoria, creando loop potenziali
2. **Mancanza di isolation layer**: le richieste di sistema (Mem0 extraction) passano attraverso la stessa pipeline delle richieste utente, senza filtri adeguati

**Nota:** OpenAl non ha un LLM provider locale per Mem0 — non supporta llama-cpp-python. L'unica scelta è API OpenAI-compatibile, che per forza punta a Jarvis stesso.

**Fix applicato:**
- ✅ Cortocircuito in `openai_api/chat.py`: rilevamento richieste interne con `is_internal_query()`, bypass di `build_omniscient_prompt()` e `process_response_tags()`
- La richiesta Mem0 viene gestita raw (query → LLM → risposta) senza passare per la pipeline completa
- Non si può cambiare `openai_base_url` in config.py senza una soluzione alternativa per Mem0

**Raccomandazione futura:** Se Mem0 supportasse un provider locale con inference diretta (es. Ollama, llama-cpp-python), si potrebbe eliminare completamente il loop. Monitorare roadmap Mem0.

---

### C5 🔴 CRITICO — TagSafeStream: 862 Righe di State Machine Ultracomplessa

**File:** `agent/tags.py:599-861`

**Problema:** `TagSafeStream` è 862 righe di state machine che gestisce:
- Tag con coppia apertura/chiusura (`<MEMORY>...</MEMORY>`)
- Tag auto-chiudenti (`<THINK_DEEP/>`)
- Thinking blocks con multiple possibili chiusure (`<|end|>`, `</end>`, `</end|>`)
- Gemma format tags (`<start_of_turn>`, `<end_of_turn>`)
- Rilevamento anti-leak di `<` sospetti in chunk
- Escape di tag frammentati su chunk

Il testing esaustivo è quasi impossibile. Già ci sono stati bug (tag leak in streaming, thinking blocks che filtravano). Inoltre, il testo passa attraverso **3 fasi di pulizia ridondanti**:

```
1. TagSafeStream (main.py stream loop — ~908) → state machine durante streaming
2. strip_action_tags() (main.py ~961) → regex su testo completo post-streaming
3. process_all_tags() (main.py ~979) → background task con handler reali
```

**Soluzione proposta:**
- **Semplificare TagSafeStream:** bufferizzare fino a un delimitatore naturale (newline o punto fermo) e processare chunk con regex in blocco
- **Eliminare `strip_action_tags()` finale** (passo 2) e affidarsi solo a `TagSafeStream.flush()` + `process_all_tags()`
- Stato ridotto: non 8 variabili ma un singolo stack depth counter

**Sforzo:** ~3h | **Impatto:** Codice più robusto, -200 righe, bug fix

---

### M5 🟡 MEDIO — Flow Control Tramite Eccezioni

**File:** `agent/tools.py:648-649, 668-669, 692-693`

```python
try:
    approved = await confirmation_mgr.ask(...)
except PendingConfirmation as e:
    return f"⚠️ **Conferma richiesta**: {e.action_desc}"
```

**Problema:** `PendingConfirmation` è un'eccezione usata per FLOW CONTROL — interrompe il flusso normale per richiedere conferma all'utente. Le eccezioni per flow control sono un anti-pattern noto (stesso problema di `StopIteration`).

**Soluzione proposta:** Usare un return type `Union[str, PendingConfirmation]` o un pattern Visitor:

```python
result = await confirmation_mgr.ask(...)
if isinstance(result, PendingConfirmation):
    return f"⚠️ **Conferma richiesta**: ..."
# procedi con approved=True/False
```

**Sforzo:** ~30min | **Impatto:** Codice più leggibile, no eccezioni per logica normale

---

### M6 🟡 MEDIO — TOOLS_SCHEMA Lista Mutabile Globale

**File:** `agent/tools.py:42-269, 994-1065`

**Problema:** `TOOLS_SCHEMA` è una lista modulare globale modificata in momenti diversi:

1. **Definizione statica** (riga 42) — 18 tool base
2. **Estensione import-time** (riga 994-1019) — load_skill, skill_discover
3. **Estensione import-time** (riga 1022-1029) — skill tools
4. **Modifica runtime** (riga 1048-1065) — MCP tools via `refresh_mcp_tools_async()`

Lo stato globale mutabile rende difficile:
- Tracciare quali tool sono disponibili in un dato momento
- Testare (test concorrenti possono interferire)
- Debuggare (strumenti che appaiono/scompaiono)

**Soluzione proposta:** Funzione `get_tools_schema()` che costruisce dinamicamente:

```python
def get_tools_schema():
    schema = list(BASE_TOOLS)
    schema.extend(get_skill_tools())
    schema.extend(get_mcp_tools())
    return schema
```

**Sforzo:** ~30min | **Impatto:** Stato prevedibile, testabile

---

### M7 🟡 MEDIO — _gather_memory() fa 2 Chiamate Mem0 Sequenziali

**File:** `agent/prompt.py:658-683`

**Problema:** Due chiamate a `state.memory.search()` eseguite sequenzialmente via `run_in_executor`:

```python
gen_res = await loop.run_in_executor(state.mem0_executor, gen_search)
if active_project:
    proj_res = await loop.run_in_executor(state.mem0_executor, proj_search)
```

Le due chiamate sono indipendenti e potrebbero essere parallele.

**Soluzione proposta:** `asyncio.gather()`:

```python
searches = [loop.run_in_executor(state.mem0_executor, gen_search)]
if active_project:
    searches.append(loop.run_in_executor(state.mem0_executor, proj_search))
results = await asyncio.gather(*searches)
```

**Sforzo:** ~15min | **Impatto:** Ricerca memoria ~2x più veloce quando progetto attivo

---

### M8 🟡 MEDIO — SessionStore Persistenza su Disco per Ogni Messaggio

**File:** `main.py:644-646`

**Problema:** `state.chat_session_store.persist()` chiamato per ogni turno di messaggio. I/O JSON su disco (open + dump) per ogni interazione.

```python
# Chiamato per ogni messaggio utente e ogni risposta
state.chat_session_store.persist("./data/sessions.json")
```

**Soluzione proposta:**
- Debounce persistenza (salva solo se passati ≥5s dall'ultimo salvataggio)
- O bufferizzare e salvare ogni 5 messaggi
- Perdita massima in caso di crash: 5 messaggi invece di 0

**Sforzo:** ~15min | **Impatto:** ~5-20ms I/O risparmiati per turno

---

### M9 🟡 MEDIO — main.py 1339 Righe Contiene Troppe Responsabilità

**File:** `main.py`

**Problema:** 1339 righe contengono:
- 6 modelli Pydantic (`ChatRequest`, `GenerateRequest`, `EmbeddingRequest`, ...)
- 11+ endpoint API (chat, generate, embeddings, reset, ...)
- Pipeline streaming con tool-calling
- Rate limiting setup (slowapi)
- Embedding handler
- Session store management
- File structure

**Soluzione proposta:**
- Estrarre modelli Pydantic in `api/models.py`
- Endpoint `POST /api/chat` e `POST /api/generate` potrebbero andare in `api/chat_router.py` (parzialmente già iniziato con `routes/profile.py` e `routes/users.py`)

**Sforzo:** ~2h | **Impatto:** Manutenibilità, testabilità

---

## 11. Stima Impatto Cumulativo

| Scenario | Stato Attuale | Con Ottimizzazioni P0-P2 | Delta |
|---|---|---|---|
| **TTFT saluto semplice** | 15-20s | 8-10s (bypass keyword) | -50% |
| **TTFT query progetto semplice** | 20-35s | 10-15s (compressor skip per contesto piccolo) | -50% |
| **TTFT query progetto complessa** | 30-60s | 18-28s (compressor solo per contesto grande) | -40% |
| **TTFT query con tool-calling** | 34-50s | 17-25s (senza doppia generazione) | -50% |
| **TTFT cron reminder** | 30-60s | 0.1s (template diretto, nessun LLM) | -99% |
| **Ingestion 1000 file** | 15-20 min | 8-12 min (batch upsert + lock ottimizzato) | -40% |
| **VRAM** | 1036 MiB (25%) | Invariata | — |
| **RAM Reranker** | ~600 MB | ~0 MB (con lazy import) | -600 MB |
| **Memoria Userbot** | ∞ leak | ≤1000 entry (con cleanup periodico) | Stop leak |
| **Manutenibilità TagSafeStream** | Fragile, 862 righe | Robusto, ~400 righe (con parser semplificato) | -50% codice |

### Diagramma Flusso Richiesta: Prima vs Dopo

**PRIMA (stato attuale):**
```
Utente → main.py → build_omniscient_prompt()
  ├── 1. list_rag_projects()          [Qdrant HTTP ~50ms]
  ├── 2. keyword_bypass()             [0 LLM]
  ├── 3. Gatekeeper()                 [Qwen3.5 CPU 5-10s]
  ├── 4. _gather_memory()             [Mem0 ~100ms]
  ├── 5. _gather_rag()                [Embed ~50ms + Qdrant ~50ms + Reranker ~200ms]
  ├── 6. _gather_synaptiq()           [Synaptiq ~100ms]
  ├── 7. Auto web discovery           [SearXNG ~5-15s se RAG vuoto]
  ├── 8. _allocate_budget()           [~5ms + I/O tasks.json]
  ├── 9. Caveman Compressor           [Qwen3.5 CPU 10-20s]
  └── 10. build_final_prompt()        [~1ms]
       → generate_chat()              [Gemma 4 GPU ~8s TTFT + ~17s gen]
       
TOTALE: ~30-60s prima del primo token
```

**DOPO (Opzioni 1+3+5+7+8 — solo qualità-sicure):**
```
Utente → main.py
  │
  ├── 0. PURE GREETING? → skip build_omniscient_prompt()     [Op3: 0 LLM]
  │     └── Vai direttamente a generate_chat() con messaggi raw
  │
  └── build_omniscient_prompt()
      ├── 1. keyword_bypass()         [0 LLM, pattern espansi Op5]
      │     └── GENERAL/META → EARLY RETURN
      │
      ├── 2. Gatekeeper()             [SOLO se bypass fallisce]
      │     ╔══════════════════════════════════════════╗
      │     ║  PARALLELO: context gathering avviato   ║  [Op7]
      │     ║  CONTEMPORANEAMENTE al gatekeeper       ║
      │     ╚══════════════════════════════════════════╝
      │
      ├── 3. Context gathering (solo per PROJECT intent)
      │     ├── _gather_rag()          [parallelo con gatekeeper via Op7]
      │     ├── _gather_memory()       [parallelo]
      │     └── _gather_synaptiq()     [parallelo]
      │
      ├── 4. DECISORE COMPRESSORE (3 livelli)
      │     │
      │     ├── Livello 1: C'è contenuto da comprimere?   [Op8]
      │     │     ├── NO (RAG+web+mem+synaptiq tutti vuoti)
      │     │     │   → raw fallback, 0 LLM ✅
      │     │     └── SÌ → Livello 2
      │     │
      │     ├── Livello 2: Contesto totale < 2000 chars?  [Op1]
      │     │     ├── SÌ (contesto piccolo, sta in ctx window)
      │     │     │   → raw fallback, 0 LLM ✅
      │     │     └── NO → Livello 3
      │     │
      │     └── Livello 3: Esegui Caveman Compressor      [1 LLM]
      │           (solo per ~25% richieste con contesto grande)
      │
      └── 5. build_final_prompt()
           → generate_chat()           [Gemma 4 GPU ~8s TTFT]

TOTALE per scenario:
  A (saluto):         ~8s   0 LLM call  ← invariato
  B (meta):           ~8s   0 LLM call  ← invariato
  C (prog. semplice): ~10-15s  1 LLM call  ← -50% TTFT ✅
  D (prog. complesso): ~18-28s  2 LLM call  ← -40% TTFT ✅
  E (generale):       ~13-18s  1 LLM call  ← invariato
  F (contesto):       ~13-18s  1 LLM call  ← invariato
  
  Media: ~1.4 → ~0.8 LLM call (-43%) | TTFT medio: ~25s → ~15s (-40%)

```

---

## 12. Priorità d'Intervento

| Priorità | ID | Cosa Fare | File | Sforzo | Impatto |
|---|---|---|---|---|---|
| **P0** | C1-Op8 | Skip compressor se nessun contenuto (RAG/web/mem vuoti) | `agent/prompt.py` | ~5min | -1 LLM call per ~20% richieste progetto |
| **P0** | C1-Op1 | Skip compressor per contesto piccolo (< 2000ch) | `agent/prompt.py` | ~15min | -1 LLM call per ~40% richieste progetto (cumulativo con Op8) |
| **P0** | C3 | Cron reminder senza pipeline LLM completa | `scheduler/cron.py` | ~30min | Latenza 30-60s → 0.1s |
| **P0** | C5 | Semplificare TagSafeStream | `agent/tags.py`, `main.py` | ~3h | Bug fix, -200 righe |
| **P1** | C1-Op3 | Pure greeting check in main.py (before pipeline) | `main.py` | ~10min | -0.1s overhead saluti |
| **P1** | C1-Op5 | Espansione keyword bypass (solo termini tecnici) | `agent/prompt.py` | ~30min | +5-10% bypass rate |
| **P1** | C1-Op7 | Context gathering parallelo con gatekeeper | `agent/prompt.py` | ~2h | -1-2s su query progetto |
| **P1** | C2 | RAG eseguito solo per intent=project ✅ | `agent/prompt.py` | ~1h ✅ | TTFT -30%, CPU -800ms |
| **P1** | H2 | ID deterministici per upsert Qdrant ✅ | `rag/engine.py` | ~30min ✅ | -40% IO ingestion |
| **P1** | H1 | Usare state.http_client singleton per offloading ✅ | `core/llm_engine.py` | ~30min ✅ | -1.5s overhead Worker offline |
| **P1** | H6 | Cleanup periodico userbot_sessions ✅ | `tg_bot/userbot.py` | ~15min ✅ | Memory leak fix |
| **P1** | C6 | Compressione gatekeeper 2048-ctx overflow ✅ **NUOVO** | `core/llm_engine.py` | ~15min ✅ | Elimina ValueError su contesto grande |
| **P1** | C7 | Phantom request loop Mem0 → API ✅ **NUOVO** | `openai_api/chat.py` | ~30min ✅ | CRITICO — interrompe loop infinito |
| **P1** | H8 | Mem0 configurazione circolare ✅ **NUOVO** | `core/config.py` | ~30min ✅ | Architettura, loop prevention |
| **P2** | C4 | Ri-usare prima risposta invece di rigenerare | `main.py` | ~2-3h | Tool-calling -50% latenza |
| **P2** | H4 | Lock separato per stato RAG | `rag/engine.py` | ~1h | -lock contention |
| **P2** | H5 | Entity extraction opzionale + batch Qdrant | `memory/engine.py` | ~1h | -CPU per salvataggio memoria |
| **P2** | H7 | Split dashboard.py in moduli ✅ | `admin/dashboard.py` | ~2h ✅ | Manutenibilità ✅ |
| **P2** | H9 | Cache tasks.json in memoria | `scheduler/tasks.py` | ~30min | -I/O disco per richiesta |
| **P3** | M2-M15 | Vari medi (12 item, M1 ✅ completato) | Multipli | ~4.5h totali | Miglioramenti sparsi |
| **P4** | L1-L5 | Vari bassi (5 item) | Multipli | ~1h totali | Polish |

### Roadmap Suggerita

**Sprint 1a (P0 rapidissimi, ~20min):**
- **C1-Op8**: Skip compressor se niente da comprimere (~5min)
- **C1-Op1**: Skip compressor per contesto piccolo (~15min)

**Sprint 1b (P0 medi, ~1h):**
- **C3**: Cron reminder senza LLM (~30min)
- **C1-Op3**: Pure greeting check in main.py (~10min)
- **C1-Op5**: Espansione keyword bypass (solo tecnici, ~30min)

**Sprint 1c (P1, ~2h):**
- **C1-Op7**: Context gathering parallelo (~2h)

**Sprint 1d (P0 complesso, ~3h):**
- **C5**: TagSafeStream semplificato (~3h)

**Sprint 2 (P1, ~4h): ✅ COMPLETATO**
- C2: RAG condizionale (solo per project intent) ✅
- H2: ID deterministici upsert Qdrant ✅
- H1: http client riutilizzabile ✅
- H6: Cleanup userbot_sessions ✅
- C6: Compressione overflow gatekeeper ✅
- C7: Phantom request loop Mem0 ✅
- H8: Circular Mem0 config ✅

**Sprint 3 (P2, ~7h):**
- C4: Ri-uso prima risposta tool-calling
- H4: Lock separato RAG
- H5: Entity extraction ottimizzata
- H7: Split dashboard.py ✅
- H9: Cache tasks.json

**Sprint 4 (P3, ~4.5h):**
- Tutti i medi restanti (12 item, M1 ✅ completato)

---

*Report generato il 2026-07-25 da Sisyphus — Performance Analysis Agent.*
*Basato su analisi statica di ~12.000 LOC su 25+ moduli del codice sorgente.*
