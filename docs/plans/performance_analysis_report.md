# 🔬 Performance Analysis Report — NeuroNet/Jarvis

**Data:** 2026-07-29 (aggiornato)
**Modello attivo:** Qwen3.5-4B (35-40 tok/s, full GPU)
**Gatekeeper classification:** Qwen3.5-4B (main model, 0 VRAM extra, ~0.3-0.8s)
**Gatekeeper compression:** Qwen3.5 0.8B Q4_K_M (CPU, GATEKEEPER_N_GPU_LAYERS=0, 0 VRAM, 4096 ctx)
**Embedding:** FastEmbed ONNX CPU (BAAI/bge-base-en-v1.5, 0 VRAM)
**VRAM:** ~3334 MiB chat + 0 MiB gatekeeper + 0 MiB embedding = 3334 MiB / 4096 MiB (81%)
**LOC esaminate:** ~44.200 su 88 moduli (excl. venv, incl. llama-cpp-src vendor)
**LOC sorgente Jarvis (excl vendor):** ~37.500, 37 file >250 LOC, 51 file <250 LOC

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

L'analisi approfondita ha identificato **41 punti** distribuiti su tutti i sottosistemi. Rispetto al report precedente (38 punti): +1 regredito ⚠️ (Op3 greeting SC perso in refactoring), +3 risolti ✅ (Cherry Studio fix, Admin panel fixes, Module extraction).

**⚠️ REGRESSIONE CRITICA:** La greeting short-circuit (Op3, implementata in commit 4977aee) è stata **persa durante il refactoring module extraction** (c1ccaa1). `PURE_GREETING` import e blocco `_greeting_text` rimossi da `main.py`. Ora i saluti puri tornano a passare per l'intera pipeline LLM inclusa gatekeeper classification + prompt building.

| Severità | Conteggio |
|:---|---|---|
| 🔴 Critico | 7 (5 risolti ✅, 1 regredito ⚠️, 1 aperto) |
| 🟠 Alto | 11 (8 risolti ✅, 3 aperti) |
| 🟡 Medio | 12 (1 parzialmente risolto) |
| 🟢 Basso | 5 |
| **Totale** | **41** (16 risolti ✅ + 4 già risolti PRIMA + 1 parziale + 2 regressioni) |

### Mappa Calore per Sottosistema

```
LLM Pipeline      ██████████████████░░  9 (6 risolti ✅, 1 regredito ⚠️, 2 aperti)
RAG & Embedding   ████████████░░░░░░░  4 (2 risolti, 2 aperti)
Memoria Episodica ██████████████░░░░░  5 (1 risolto, 4 aperti)
Synaptiq          ████░░░░░░░░░░░░░░░  2 (aperti)
Scheduler & Tasks ████████░░░░░░░░░░░  3 (aperti)
Telegram/Userbot  ██████░░░░░░░░░░░░░  2 (2 risolti ✅)
Dashboard/Admin   ██████░░░░░░░░░░░░░  4 (2 risolti ✅, 2 aperti)
Infrastruttura    ████░░░░░░░░░░░░░░░  2 (aperti, bassi)
Anti-Patterns     ███████████████████  8 (3 risolti ✅, 1 parziale, 4 aperti)
```

### Top 7 per Impatto Utente

1. ~~**Compressor sprecato per contesto vuoto** — 8-15s persi per ~50% richieste progetto~~ — **risolto con Op1+Op8 (skip context < 1000ch)** ✅
2. **Cron job esegue pipeline LLM completa** — 30-60s per un reminder (ora mitigato da TTFT più basso ma ancora inutile)
3. ~~**Mem0 → API → Mem0: phantom request loop**~~ — risolto ✅
4. ~~**Compress ValueError: gatekeeper 2048-ctx overflow**~~ — risolto con 4096 ctx + _GK_MAX_CHARS=1500 ✅
5. **Tool-calling rigenera risposta** — ~10s → ~20s con tool
6. **⚠️ Greeting SC REGREDSSO** — era 26ms per saluti puri, ora torna a ~4-6s (commit 4977aee → c1ccaa1)
7. ~~**Cherry Studio risposte vuote su Qwen/DeepSeek**~~ — risolto con TagSafeStream + /no_think ✅

---

## 2. LLM Pipeline

### C1 🟠 ALTO — Tripla Chiamata LLM per Richiesta (Parzialmente Ottimizzato)

**File:** `agent/prompt.py:458-864` + `core/llm_engine.py:570-756, 756-830`
**Analisi:** 2026-07-27 — Sisyphus (aggiornato dopo switch a Qwen3.5-4B, FastEmbed, auto-detection)

#### Stato Attuale: Costo per Scenario

La pipeline ha potenzialmente 2 LLM call (compressor CPU + Qwen3.5-4B generation) più 1 classificazione intenti gratuita sul main model. La classificazione con Qwen3.5-4B (invece di Qwen3.5 0.8B dedicato) ha eliminato la chiamata gatekeeper separata, risparmiando 5-10s per richiesta e 553 MiB VRAM di buffer:

| Scenario | Esempio | Bypass OK? | Classificazione | Compressor | Qwen3.5-4B | **Totale LLM call** | **TTFT stimato** |
|---|---|---|---|---|---|---|---|
| **A** Saluto puro | "Ciao", "Buongiorno" | ❌ (regredito ⚠️) | — | — | — | **1** | ~4-6s |
| **B** Meta/progetti | "Quali progetti hai?" | ✅ `META_PHRASES` | — | — | — | **0** | ~4-6s |
| **C** Query progetto (bypassa) | "Spiega il codice main.py" | ✅ `PROJECT_KEYWORDS` | — | ✅ (CPU, 8-15s) | ✅ | **2** | ~12-20s |
| **D** Query progetto (no bypass) | "Analizza impatto modifica config" | ❌ | ✅ (Qwen3.5-4B, 0 VRAM extra) | ✅ (CPU, 8-15s) | ✅ | **2** | ~10-18s |
| **E** Conversazione generale | "Cosa sono le reti neurali?" | ❌ | ✅ (Qwen3.5-4B) | — | ✅ | **1** | ~5-8s |
| **F** Domanda su contesto | "Come va l'implementazione?" | ❌ | ✅ (Qwen3.5-4B) | — | ✅ | **1** | ~5-8s |

**Distribuzione stimata:** A+B ~20%, C ~50%, D ~15%, E+F ~15%. Con Op1+Op8 (skip compressor attivo per contesto < 1000ch), il 50% di richieste C (progetto semplice) **non paga più il compressor** — risparmio 8-15s per richiesta. Solo D (~15%) paga ancora il compressor su CPU.

#### Costo del Compressor Qwen3.5 su CPU

Il compressor è chiamato per OGNI query progetto (C+D = ~65% delle richieste). Con switch a CPU:
- `GATEKEEPER_N_GPU_LAYERS=0` → 0 VRAM, ma latenza ~8-15s invece di 5-10s GPU
- `GATEKEEPER_N_CTX=4096` — permette contesto più ricco e 6 few-shot esempi
- Fallback a pass-through (nessuna chiamata LLM) se ratio di compressione negativo
- **Implicazione:** lo skip del compressor per contesto piccolo è ANCORA PIÙ IMPORTANTE ora che è su CPU

```python
# prompt.py:706-736 — RAG può essere VUOTO ma compressor viene chiamato lo stesso
rag_ctx_local = await search_documents(...)  # può tornare ""
# ...
await _run_compression(clean_msg, rag_context_for_compress, ...)  # CHIAMATO SEMPRE
```

`_run_compression` assembla il contesto (max 1500 chars dopo C6 fix) e chiama Qwen3.5 CPU:

- **Con RAG pieno** (3000+ chars prima del clamp): ~8-15s CPU
- **Con RAG vuoto o minimo** (< 500 chars): ~8-15s CPU comunque
- **Nota:** Il passaggio da GPU a CPU ha peggiorato la latenza del compressor (~5-10s → ~8-15s) ma liberato 553 MiB VRAM per il chat model

#### Costo della Classificazione: GRATUITA sul Main Model

La classificazione intenti ora usa il main model (Qwen3.5-4B, già in VRAM):
```python
# core/llm_engine.py:581-633 — classify_intent() su main model
response = await self.generate_chat(messages, stream=False,
    options={"temperature": 0.0, "num_predict": 5})
```
- Nessuna grammatica GBNF — output token singolo (1-5 token)
- 0 VRAM extra (Qwen3.5-4B già in VRAM per chat)
- **Costo:** ~0.3-0.8s per richiesta — output: `greeting|simple|project|web|code|complex`
- **Vantaggio:** Qwen3.5-4B è ~5x più veloce di Gemma 4 (35-40 tok/s vs 6.88 tok/s), classificazione ancora più rapida

#### Analisi Critica Aggiornata: "Dove viene sprecato il budget LLM?"

```
                                       ┌── Intent "general" → EARLY RETURN
                                       │   (nessuna RAG/compressione)
                                       │   costo classificazione ~0.3-0.8s (accettabile)
                ┌── Bypass OK? ───NO──┴── Intent "project" → context gathering + compression CPU 8-15s + Qwen3.5-4B
                │                       (compressor è lo SPReCO principale)
Richiesta ──────┤
                │                   ┌── General/meta → EARLY RETURN (0 LLM)
                └── Bypass OK? ─YES─┴── Project → compression CPU 8-15s + Qwen3.5-4B (2 LLM)
```

**Spreco residuo:**
1. ~~**Compressor sprecato** (scenario C, ~50%)~~ → **✅ RISOLTO con Op1+Op8**: skip compressor per contesto < 1000ch. Scenario C ora fa solo 1 LLM call.
2. **Gatekeeper per general** (scenario E+F, ~15%): Costa solo 0.3-0.8s su main model — accettabile

#### Cosa è Già Implementato

| # | Opzione | Status | Impatto |
|---|---|---|---|
| — | **Switch a Qwen3.5-4B** | ✅ **IMPLEMENTATO** | TTFT base -50% (~17s → ~8s per gen), -553 MiB VRAM gatekeeper |
| — | **FastEmbed ONNX CPU** | ✅ **IMPLEMENTATO** | 0 VRAM embedding, no più Qwen3-Embedding GGUF (~400 MiB risparmiati) |
| — | **Hardware profile auto-detection** | ✅ **IMPLEMENTATO** | N_GPU_LAYERS/flash_attn/n_ubatch auto per famiglia GGUF |
| — | **conversation_id auto-generato** | ✅ **IMPLEMENTATO** | Multi-turn funzionante tra richieste separate |
| **OpB** | Classificazione intenti con main model | ✅ **IMPLEMENTATO** | -5-10s per richiesta, 0 VRAM extra |
| **OpA** | Qwen3.5 0.8B 4096ctx + few-shot | ✅ **IMPLEMENTATO** | Compressione più accurata |
| **C6** | `_GK_MAX_CHARS=1500` guard | ✅ **IMPLEMENTATO** | Elimina ValueError overflow |
| **C7** | Phantom loop Mem0 fix | ✅ **IMPLEMENTATO** | Stop loop infinito |
| **M1** | Thinking mode per famiglia modello | ✅ **IMPLEMENTATO** | -43% pattern streaming |
| **H7** | Split dashboard.py in moduli | ✅ **IMPLEMENTATO** | Manutenibilità |
| **C2** | RAG condizionale (solo per project intent) | ✅ **IMPLEMENTATO** | TTFT -30% su query non-progetto |
| **H2** | ID deterministici upsert Qdrant | ✅ **IMPLEMENTATO** | -40% IO ingestion |
| **H1** | state.http_client singleton | ✅ **IMPLEMENTATO** | -1.5s overhead offloading |
| **H6** | Cleanup periodico userbot_sessions | ✅ **IMPLEMENTATO** | Memory leak fix |
| **Op1+8** | Skip compressor per contesto piccolo (< 1000ch) | ✅ **IMPLEMENTATO** | -1 LLM per ~60% project query semplice |

#### Restano da Implementare

| # | Opzione | Sforzo | Impatto | Priorità |
|---|---|---|---|---|
| **3** | Pure greeting check in main.py | ~10min | -0.1s overhead | P1 |
| **5** | Espansione keyword bypass (solo tecnici) | ~30min | +5-10% bypass rate | P1 |
| **7** | Context gathering parallelo con gatekeeper | ~2h | -1-2s su project query | P1 |

#### 🔍 Revisione Qualità: Opzioni che NON Deteriorano il Risultato

| # | Opzione | Rischio Qualità | Motivo | Verdetto |
|---|---|---|---|---|
| **1** | Skip compressor per contesto piccolo | **❌ Nullo** | Usa raw fallback già esistente. Il formato raw produce risposte PIÙ naturali per query semplici. Stessa informazione. | ✅ **SICURO** |
| **2** | Cache gatekeeper results | **⚠️ Stale classification** | Cache potrebbe tornare "general" per query ora "project" in contesto cambiato → nessun RAG | ❌ **ESCLUSO** |
| **3** | Pure greeting check in main.py | **❌ Nullo** | Stessa regex, stessa logica — solo spostato prima | ✅ **SICURO** |
| **4** | Gatekeeper+Compressor unificati | **🔴 Output misto fragile** | Qwen3.5 0.8B non fa due cose bene in una passata | ❌ **ESCLUSO** |
| **5** | Espansione keyword bypass (solo tecnici) | **⚠️ Falsi positivi** | Con aggiunte CONSERVATIVE il rischio è nullo | ✅ **SICURO (se conservativo)** |
| **6** | Classifier ONNX leggero | **🔴 Accuratezza inferiore** | BERT 110M vs Qwen3.5-4B: perde su casi ambigui | ❌ **ESCLUSO** |
| **7** | Context gathering parallelo | **❌ Nullo** | Stessi path codice, stessi dati, solo timing diverso | ✅ **SICURO** |

---

#### Opzioni Safe — Analisi Dettagliata

##### Opzione 1 🟢 — Skip Compressor per Contesto Piccolo (P0 — ✅ GIÀ IMPLEMENTATO)

**Sforzo:** ~15min (stimato) | **Impatto:** -1 LLM call per ~60% richieste progetto semplice | **Qualità: SICURO**

✅ **Implementato in `agent/prompt.py:431-448`** con soglia `COMPRESSOR_MIN_CHARS=1000`. Skip attivo quando contesto totale < 1000 chars E nessun RAG finale E nessun progetto attivo.

```python
# agent/prompt.py:431-448
total_context = len(rag_context_for_compress or '') + len(history_str or '') + len(web_final or '')
COMPRESSOR_MIN_CHARS = 1000
if total_context < COMPRESSOR_MIN_CHARS and not rag_final and not active_project:
    logger.info(f"🗜️ Skip compressor: contesto trascurabile ({total_context}ch < {COMPRESSOR_MIN_CHARS}ch), raw fallback")
    compressed = _build_raw_fallback(...)
    return compressed, True
```

Perché è sicuro:
- Usa `_build_raw_fallback()` — già esistente come fallback per compressione fallita
- Il formato raw usa system prompt più naturale per query semplici
- **Impatto:** risparmia 8-15s CPU compressor per ogni query semplice

> **Nota:** La soglia implementata è 1000 chars (non 2000 come proposto originariamente). Scelta conservativa che dà più contesto al compressor prima di decidere.

---

##### Opzione 3 🔴 — Pure Greeting Check — REGREDSSO ⚠️ (P0 URGENTE)

**Stato:** Era implementato in commit 4977aee (26ms, 0 LLM). **Perso** in commit c1ccaa1 (module extraction refactor).

**Cosa è successo:**
- Commit 4977aee ha aggiunto in `main.py` un blocco `PURE_GREETING.match()` PRIMA di `build_omniscient_prompt()`
- Risposta immediata per ciao/hello/hi/hey/buongiorno/buonasera/salve — 26ms, 0 token LLM
- Commit c1ccaa1 ha rimosso `from agent.prompt import PURE_GREETING` e tutto il blocco `_greeting_text`
- `PURE_GREETING` regex non esiste più in `agent/prompt.py`
- In `build_omniscient_prompt()` la variabile `_is_short_greeting` (linea 832) salta solo web search, **non** la generazione LLM

**Impatto:** I saluti puri ora passano per gatekeeper classification (~0.3-0.8s) + prompt building + Qwen3.5-4B generation (~3-5s) = ~4-6s invece di 26ms. **Regressione 150x.**

**Fix:** Re-implementare in `main.py` PRIMA di `build_omniscient_prompt()`. Usare `is_greeting()` da `agent/classifier.py` che esiste ancora. Alternativa: usare `GREETING_WORDS` set.

```python
# agent/classifier.py:40-118 — già presente, da riutilizzare in main.py
from agent.classifier import is_greeting

# In main.py, PRIMA di build_omniscient_prompt():
_greeting_text = raw_messages[-1].get("content", "").strip() if raw_messages else ""
if not is_internal and _greeting_text and is_greeting(_greeting_text):
    # Risposta immediata: 26ms, 0 token LLM
    ...
```

**Sforzo:** ~15min | **Impatto:** -100% LLM call per saluti | **Qualità: SICURO** | **Priorità: P0 URGENTE**

---

##### Opzione 5 🟢 — Espansione Keyword Bypass (P1, SOLO pattern conservativi)

**Sforzo:** ~30min | **Impatto:** +5-10% bypass rate | **Qualità: SICURO solo con termini tecnici puri**

Aggiunte **SICURE** (solo termini tecnici / git):
```python
'commit', 'branch', 'pull request', 'pr', 'issue', 'fix', 'feature',
'migra', 'refactorizza', 'compila', 'deploy', 'builda',
'scrivi', 'crea', 'modifica', 'rimuovi', 'cancella',
'analizza', 'calcola', 'genera', 'converti', 'traduci (codice)'
```

🔴 **NON aggiungere**: `perché`, `perche`, `cosa fa`, `come funziona`, `dov'è` — troppi falsi positivi.

---

##### Opzione 7 🟡 — Context Gathering in Parallelo con Gatekeeper (P1)

**Sforzo:** ~2h | **Impatto:** -0.5-2s su query progetto | **Qualità: SICURO**

Avviare RAG/Memory/Synaptiq gathering IN PARALLELO con il gatekeeper (invece che dopo).

---

##### Opzione 8 🟢 — Skip Compressor se Non Cè Contenuto Comprimibile (P0 — ✅ GIÀ IMPLEMENTATO, FUSO CON Op1)

**Impatto:** ✅ Assorbito dall'implementazione di Op1 in `agent/prompt.py:431-448`. Il check combinato `total_context < 1000ch AND not rag_final AND not active_project` copre sia contesto piccolo (Op1) che assenza di contenuto comprimibile (Op8).

---

#### Tabella Comparativa Finale

| # | Opzione | Sforzo | LLM Call Risparmiate | Riduzione TTFT | Priorità | Stato |
|---|---|---|---|---|---|---|
| **1+8** | Skip compressor per contesto piccolo/scarsa | ✅ già fatto | 1 per ~60% richieste progetto | **-25% medio** | **P0** | ✅ FATTO |
| **3** | Pure greeting check in main.py | ~15min | 1 per ~20% richieste | **regredito: +4-6s per saluti** | **P0** | ⚠️ **REGREDSSO** |
| **5** | Espansione keyword (solo tecnici) | ~30min | 0.05 per richiesta | -3% medio | P1 | ❌ Aperto |
| **7** | Context gathering parallelo | ~2h | 0 (solo latenza nascosta) | -1-2s su project | P1 | ❌ Aperto |

#### Raccomandazione Finale

L'**Op1+Op8 combinato** (skip compressor per contesto trascurabile) è già stato implementato con soglia `COMPRESSOR_MIN_CHARS=1000` in `agent/prompt.py:431-448`. Non serve più implementarlo. Il risparmio è attivo: -1 LLM call per query semplici (~60% delle richieste progetto).

Op5 e Op7 restano aperti come ottimizzazioni aggiuntive.

**⚠️ Op3 (greeting SC) REGREDSSO CRITICO:** Era implementata in `main.py` (commit 4977aee) con risposta immediata per saluti puri (0 LLM call, 26ms). **Persa** nel refactoring module extraction (c1ccaa1). `PURE_GREETING` regex rimossa da `agent/prompt.py`, la variabile `_is_short_greeting` in `build_omniscient_prompt()` (linea 832) salta solo web search, non la generazione LLM. **Da re-implementare URGENTE.**

**Scenario attuale SENZA greeting SC (regredito):**

```
Scenario A (saluto):       1 LLM call (classifier + Qwen3.5-4B), ~4-6s  ← ⚠️ REGREDDITO (era 26ms, 0 LLM)
Scenario B (meta):         1 LLM call (main model classifier + Qwen3.5-4B), ~5-8s  ← OK
Scenario C (progetto semplice): 1 LLM call (solo Qwen3.5-4B), ~5-8s  ← ✅
Scenario D (progetto complesso): 2 LLM call (compressor + Qwen3.5-4B), ~12-18s  ← ✅
Scenario E (generale):     1 LLM call (Qwen3.5-4B), ~5-8s  ← OK ✅
Scenario F (contesto):     1 LLM call (Qwen3.5-4B), ~5-8s  ← OK ✅

Riduzione LLM call media: da ~1.4 a ~0.9 per richiesta (-36%)
Riduzione TTFT media: da ~20-30s a ~7-12s (ma saluti regrediti da 26ms a ~4-6s)
```

---

### C4 🔴 CRITICO — Tool-Calling con Doppia Generazione LLM

**File:** `main.py:711-795`

**Problema:** Quando il modello emette `tool_calls`, il flusso scarta la prima risposta e ne genera una SECONDA:

```python
# main.py:712-718 — PRIMA chiamata
response = await engine.generate_chat_with_router(
    body["messages"], tools=body.get("tools"), options=body.get("options"),
    stream=False, preferred_provider=provider
)
# ... tool execution ... (linee 776-784)
# main.py:789-792 — SECONDA chiamata (risposta finale)
response = await engine.generate_chat_with_router(
    body["messages"], tools=body.get("tools"), options=body.get("options"),
    stream=False, preferred_provider=provider
)
```

La prima generazione produce contenuto testuale che viene **completamente scartato**. L'utente vede la risposta solo dopo la SECONDA generazione.

**Impatto:** Latenza raddoppiata (~10s → ~20s per query con tool).

**Soluzione proposta:**
- Accumulare la prima risposta in un buffer invece di scartarla
- Dopo esecuzione tool, ri-usare il contenuto già generato + risultati tool

**Sforzo:** ~2-3h | **Impatto:** Latenza tool-calling -50%

---

### C6 🔴 CRITICO — Compress ValueError: Gatekeeper 2048-ctx Overflow ✅ RISOLTO

**File:** `core/llm_engine.py:594-667` → fix a linea 626-633

**Problema:** `compress_prompt()` assemblava `raw_data` da history (1500 char) + rag_context (3000 char) + user_query senza limite sul totale. Il gatekeeper Qwen3.5 aveva `GATEKEEPER_N_CTX=2048` token. Quando il totale superava questo limite, `llm.create_chat_completion()` lanciava `ValueError`.

**Fix applicato (doppio):**
- ✅ **C6 fix**: Aggiunto limite `_GK_MAX_CHARS = 1500` per `raw_data`
- ✅ **OpA fix**: `GATEKEEPER_N_CTX` aumentato da 2048 a 4096

---

### H1 🟠 ALTO — httpx.AsyncClient Creato per Ogni Richiesta di Offloading ✅

**File:** `core/llm_engine.py:372-414`

**Soluzione applicata:**
- ✅ Rimosso health check separato
- ✅ Usato `state.http_client` singleton invece di nuovi `AsyncClient`

---

### H3 🟠 ALTO — PriorityLock con Priorità Sempre 0

**File:** `core/llm_engine.py:41-70`

```python
class PriorityLock:
    """Lock asincrono con 3 livelli di priorità via heapq."""
    await lock.acquire(priority=0)  # SEMPRE chiamato con priority=0!
```

**Stato attuale:** PriorityLock è ancora in uso ma con separazione tra `chat_lock` e `gatekeeper_lock` (linee 143-144). Embedding non usa più PriorityLock (FastEmbed ONNX CPU). La priorità è sempre 0 per entrambi — O(log n) heap inutilizzato.

**Soluzione proposta:** Sostituire con `asyncio.Lock` (due lock separati: chat e gatekeeper).

**Sforzo:** ~15min | **Impatto:** Elimina codice morto

---

### M1 🟡 MEDIO — Thinking Mode Pattern Applicati per Tutte le Famiglie ✅ COMPLETATO

**File:** `agent/tags.py:29-109`

**Soluzione applicata:**
- `strip_all_tags()` con `model_family` parametro
- Tutti i caller aggiornati a passare `MODEL_PROFILE.family`

---

## 3. RAG & Embedding

### C2 🔴 CRITICO — RAG Eseguito per Ogni Richiesta (Anche Saluti) ✅ COMPLETATO

**File:** `agent/prompt.py:654-764`

**Soluzione applicata:**
1. Cache `list_rag_projects()` con TTL 60s
2. `_gather_rag()` esegue `search_documents()` solo per project intent
3. Le gather task posizionate dopo gli early return

**Impatto:** TTFT -30% su query non-progetto, ~300-800ms CPU risparmiati per richiesta

---

### H2 🟠 ALTO — Doppia Operazione Qdrant per File (Scroll + Upsert + Delete) ✅

**File:** `rag/engine.py:801-812`

**Soluzione applicata:**
- ✅ ID deterministici basati su `hashlib.md5`
- ✅ Qdrant upsert sovrascrive automaticamente — eliminato scroll + delete

---

### 🆕 FastEmbed ONNX CPU — Sostituzione Qwen3-Embedding GGUF

**File:** `core/lifecycle.py` + `core/llm_engine.py`

**Impatto:**
- 0 VRAM per embedding (era ~400 MiB con Qwen3-Embedding GGUF)
- Nessuna contenzione GPU tra embedding e chat model
- Nessun crash `fused_gated_delta_net` (bug del modello GGUF di embedding)
- Caricamento più rapido (ONNX runtime vs llama.cpp)
- Semantic cache threshold: 0.96 (da .env)

### 🆕 Hardware Profile Auto-Detection

**File:** `core/model_profiles.py` + `core/llm_engine.py`

**Impatto:**
- N_GPU_LAYERS, flash_attn, n_ubatch auto-detectati per famiglia GGUF
- Non serve più configurare manualmente questi parametri nel `.env`
- Prevenzione crash: Gemma 4 con n_gpu_layers=-1 causa segfault — auto-detection lo previene
- Supporto 7+ famiglie: qwen, gemma, deepseek, llama, mistral, phi, command-r

---

### H4 🟠 ALTO — state_lock Conteso tra Ingestion, Watchdog e Reset

**File:** `rag/engine.py:858-859`

**Problema:** `process_single_file()` acquisisce `state.state_lock` per ogni file durante ingestion parallela. Con `MAX_CONCURRENT_EMBEDDINGS=8`, fino a 8 coroutine competono per lo stesso lock.

**Nota:** FastEmbed riduce la contenzione (embedding ONNX CPU invece di GPU), ma il lock è ancora condiviso.

**Soluzione proposta:** Lock separato per stato RAG (`rag_state_lock`).

**Sforzo:** ~1h | **Impatto:** Meno contenzione in ingestion parallela

---

### M2 🟡 MEDIO — Parser Tree-Sitter Ricreato per Ogni File

**File:** `rag/engine.py:190-259`

**Problema:** `extract_dependencies()` crea un nuovo `Parser()` per ogni file.

**Soluzione proposta:** Cache singleton `Parser` per linguaggio.

**Sforzo:** ~15min | **Impatto:** -1-5ms per file in ingestion

---

### M3 🟡 MEDIO — Upload File Completo per Richiesta File Specifica

**File:** `agent/prompt.py:689-706`

**Problema:** `_gather_rag()` esegue `os.walk` su DOC_DIR per trovare un file matchato.

**Soluzione proposta:** Usare `glob.glob(f"**/{filename}", recursive=True)`.

**Sforzo:** ~30min | **Impatto:** -0.5-2s per richiesta file specifico

---

### M4 🟡 MEDIO — Reranker Carica Torch/Transformers a Livello Modulo

**File:** `rag/reranker.py:34-46`

**Problema:** `torch` e `transformers` importati a livello modulo — ~600MB RAM allocati anche se mai usati.

**Soluzione proposta:** Import lazy dentro `_reranker_fn()`.

**Sforzo:** ~30min | **Impatto:** -600MB RAM fisso (libera se reranker mai usato)

---

## 4. Memoria Episodica

### C7 🔴 CRITICO — Phantom Request Loop: Mem0 → API → Mem0 (Auto-Alimentante) ✅ RISOLTO

**File:** `core/config.py:372-374` + `openai/chat.py:123-139, 177-194` (fix)

**Problema:** Mem0 configurato per usare Jarvis stesso come backend LLM → loop infinito.

**Fix applicato:**
- ✅ Rilevamento richieste interne con `is_internal_query()` in `openai/chat.py`
- ✅ Bypass di `build_omniscient_prompt()` per richieste interne
- ✅ Skip di `process_response_tags()` per richieste interne

### 🆕 conversation_id Auto-Generato

**File:** `main.py`

**Impatto:**
- `conversation_id` generato automaticamente come UUID se non fornito
- Restituito in tutte le risposte (non-stream, streaming, timeout, confirm)
- Multi-turn funzionante senza richiedere conversation_id manuale
- Previene session collision tra richieste

---

### H5 🟠 ALTO — extract_entities() Chiamato per Ogni Salvataggio Memoria

**File:** `memory/engine.py:24-118`, `memory/engine.py:248-288`

**Problema:** `save_to_memory()` chiama `extract_entities(mem_text)` per ogni memoria salvata.

**Soluzione proposta:**
- Rendere entity extraction **opzionale** (env var `ENTITY_EXTRACTION_ENABLED=false`)
- **Batch upsert** delle entità in una singola chiamata Qdrant
- Limitare extraction solo a memorie con `infer=True`

**Sforzo:** ~1h | **Impatto:** -5-50ms CPU + -N round-trip Qdrant per salvataggio memoria

---

### M10 🟡 MEDIO — Entity Collection con Vector Zero (Indice Inutile)

**File:** `memory/engine.py:185-200`

**Problema:** Entità salvate con vector zero di 768 dimensioni — indice HNSW sprecato.

**Soluzione proposta:** Usare collection Qdrant senza vector (solo payload indicizzati).

**Sforzo:** ~30min | **Impatto:** Riduzione spazio indice Qdrant

---

### M11 🟡 MEDIO — reindex_graph_connections() Non Cancellabile

**File:** `memory/engine.py:322-419`

**Problema:** `reindex_graph_connections()` scorre TUTTE le memorie con scroll paginato e non è cancellabile.

**Soluzione proposta:** Aggiungere check periodici di cancellazione.

**Sforzo:** ~30min | **Impatto:** Robustezza

---

## 5. Synaptiq Engine

### M12 🟡 MEDIO — get_graph_data() Carica l'Intero Grafo in Memoria

**File:** `graph/synaptiq_engine.py:520-597`

**Problema:** `get_graph_data()` carica l'intero grafo in memoria (5659 nodi × 21711 relazioni ≈ 3.3 MB + overhead Python).

**Soluzione proposta:** Aggiungere paginazione lato server (offset/limit) per grafi grandi.

**Sforzo:** ~1h | **Impatto:** Minor memoria per dashboard, reader lock più breve

---

### L4 🟢 BASSO — Import Safety Pattern Duplicato in 5+ Moduli

**File:** `agent/prompt.py:25-28`, `admin/dashboard.py:20-23`, e altri

**Soluzione proposta:** Centralizzare in `config.py: get_synaptiq_engine()`.

**Sforzo:** ~15min | **Impatto:** DRY, meno boilerplate

---

## 6. Scheduler & Tasks

### C3 🔴 CRITICO — Cron Job Esegue Pipeline LLM Completa

**File:** `scheduler/cron.py:36-72`, `agent/tags.py:196-237`

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
                                 │     ├── gatekeeper (Qwen3.5-4B, 0 VRAM extra, ~0.3-0.8s)
                                 │     ├── context gathering (RAG + mem + Synaptiq — tutto inutile)
                                 │     ├── compressor (Qwen3.5 0.8B CPU, 4096 ctx, ~8-15s)
                                 │     └── build_final_prompt
                                 │
                                 ├── engine.generate_chat()  (Qwen3.5-4B GPU, ~3-5s)
                                 │
                                 └── send_message("🔔 Ricordati di comprare il pane!")
                                 
TOTALE: ~15-30s per inviare "Ricordati di comprare il pane!" (Qwen3.5-4B ha ridotto la gen ma il compressor CPU è ancora peggio)
```

#### Costo Reale

Per un reminder "Ricordami di comprare il pane":
1. `build_omniscient_prompt()` con messaggio fittizio
   - keyword_bypass → nessun match (testo di sistema)
   - gatekeeper Qwen3.5-4B → ~0.3-0.8s
   - context gathering → ~0.5-2s per nulla
   - compressor Qwen3.5 CPU → **~8-15s** (il peggiore!)
2. `generate_chat()` → Qwen3.5-4B produce risposta → ~3-5s

**15-30s per riformulare "Ricordami di comprare il pane" in "Ricordati di comprare il pane"** (Qwen3.5-4B ha reso la gen più veloce, ma il compressor CPU è peggiorato).

#### Opzioni

| # | Opzione | Sforzo | LLM Call Risparmiate | Latenza |
|---|---|---|---|---|
| **1** | Direct send NOTIFY_ONCE/NOTIFY_IN | **~15min** | **2 per ~80% job** | **15-30s → 0.1s** |
| **2** | `concise=True` per SCHEDULE | ~5min | 0 (salta RAG/memoria) | 15-30s → ~8-15s |
| **3** | Skip compressor cron (via C1-Op8) | ~5min | 1 per ~20% job | ~8-15s → ~3-5s |

---

### H9 🟠 ALTO — JSON Persist su Disco per Ogni Operazione

**File:** `scheduler/cron.py:26-34`, `scheduler/tasks.py:18-26`

**Problema:** `load_jobs()` e `save_jobs()` chiamati per ogni add/remove job. `get_open_tasks()` chiamato per ogni richiesta progetto.

**Soluzione proposta:** Cachare tasks.json in memoria (invalida su write).

**Sforzo:** ~30min | **Impatto:** ~5-20ms I/O risparmiati per richiesta progetto

---

### M13 🟡 MEDIO — ADMIN_USERS[0] Hardcoded in morning_recap

**File:** `scheduler/cron.py:170-183`

**Soluzione proposta:** Controllo esplicito + try/except + env var `MORNING_RECAP_CHAT_ID`.

**Sforzo:** ~15min | **Impatto:** Robustezza

---

## 7. Telegram & Userbot

### H6 🟠 ALTO — userbot_sessions Dict Cresce Illimitatamente (Memory Leak) ✅

**File:** `tg_bot/userbot.py:22-23, 72-78, 194-210`

**Soluzione applicata:**
- ✅ `_cleanup_userbot_sessions()`: background task ogni 5 minuti

---

### Bug 9 🟠 ALTO — Cherry Studio Risposte Vuote su Qwen/DeepSeek (SSE) ✅ FIXATO

**File:** `openai_api/chat.py`, `agent/tags.py`

**Problema:** Qwen e DeepSeek non emettono `data: [DONE]` in streaming SSE → Cherry Studio mostra risposte vuote. Gatekeeper reasoning tag (`<reasoning>`) non rimosso dalla risposta visibile.

**Fix applicato:**
- ✅ `TagSafeStream` wrapper in `openai_api/chat.py`: sostituisce `[DONE]` assente con `data: [DONE]`
- ✅ Rimozione tag `<reasoning>` dal response visibile
- ✅ Supporto prefisso `/no_think` per disabilitare reasoning esplicitamente
- ✅ `gatekeeper.processing()` chiamato nel ramo corretto (non più saltato per Cherry Studio)

**Impatto:** Cherry Studio ora funziona correttamente con Qwen3.5-4B e DeepSeek.

---

### M14 🟡 MEDIO — engine.generate_chat() Chiamata con LLM_OPTIONS Completi per Userbot

**File:** `tg_bot/userbot.py:98-103`

**Problema:** `LLM_OPTIONS` con `num_ctx=12288`, `num_predict=2048` per conversazioni userbot generiche.

**Soluzione proposta:** Usare `USERBOT_LLM_OPTIONS` con parametri ridotti.

**Sforzo:** ~15min | **Impatto:** Minor VRAM/latenza per risposte userbot

---

## 8. Dashboard & Admin

### H7 🟠 ALTO — 2406 Righe in un Singolo File (dashboard.py) ✅

**File:** `admin/dashboard.py`, `admin/settings_manager.py`, `admin/telemetry_collector.py`

**Soluzione applicata:**
- ✅ `settings_manager.py` — SETTINGS_META (73 env var), _persist_env() (~620 righe)
- ✅ `telemetry_collector.py` — TelemetryCache, collector (~250 righe)
- ✅ `dashboard.py` — ridotto da 2337 a ~1566 righe (−35%)

---

### Bug 10 🟠 ALTO — Admin Panel Race Condition Restart Ingestion ✅ FIXATO

**File:** `routes/projects.py`, `admin/panel/static/js/logs.js`, `admin/panel/static/js/management.js`

**Problema:** Pulsante "Re-index" poteva essere premuto multiplo volte → race condition in `_ingest_local_documents()`. `fetchLogs()` senza timeout → richieste pendenti infinite.

**Fix applicato:**
- ✅ Aggiunto flag `_ingesting` con `lock` in `routes/projects.py`
- ✅ `fetchLogs()` con timeout 30s
- ✅ Pulsanti restart funzionanti in Logs view
- ✅ Rimosso endpoint orfano `/analytics/errors`
- ✅ `resetSettings` classList toggle fixato

---

### M15 🟡 MEDIO — subprocess.run(nvidia-smi) Ogni 5 Secondi per GPU Metrics

**File:** `admin/dashboard.py:53-100`

**Problema:** Tre chiamate `nvidia-smi` separate ogni 5 secondi.

**Soluzione proposta:** Usare `pynvml` (binding Python diretto a NVML).

**Sforzo:** ~30min install + ~30min rewrite | **Impatto:** -subprocess overhead

---

### L5 🟢 BASSO — _persist_env() Riscrive Intero .env a Ogni Modifica Settings

**File:** `admin/settings_manager.py`

**Impatto:** Basso — modifiche settings sono rare rispetto alle richieste.

---

## 9. Infrastruttura

### L1 🟢 BASSO — Module-Level I/O in config.py

**File:** `core/config.py:299-327`

**Impatto:** ~10-100ms al boot. Succede UNA VOLTA.

---

### L2 🟢 BASSO — log_vram_usage() Usa subprocess.run(nvidia-smi)

**File:** `core/llm_engine.py:22-37`

**Impatto:** ~100-300ms per chiamata, solo 3-4 volte al boot.

---

## 10. Anti-Patterns Generali

### H8 🟠 ALTO — Mem0 Configurato per Chiamare la Propria API (Circolare) ✅ RISOLTO

**File:** `core/config.py:372-374`

**Fix applicato:**
- ✅ Cortocircuito in `openai/chat.py`: rilevamento richieste interne con `is_internal_query()`
- ✅ Bypass di `build_omniscient_prompt()` e `process_response_tags()`

---

### C5 🔴 CRITICO — TagSafeStream: State Machine Complessa

**File:** `agent/tags.py:305-580`

**Problema:** `TagSafeStream` è ~276 righe di state machine (linee 305-580, ridotta da ~583 righe dopo estrazione tag_handlers.py). Il testo passa attraverso 3 fasi di pulizia ridondanti.

**Nota:** Nel refactoring module extraction (c1ccaa1), 320 righe di handler tag sono state estratte in `agent/tag_handlers.py`, riducendo `tags.py` da ~1181 a 892 righe. TagSafeStream è ora 276 righe (era 583).

**Progresso:** Parzialmente risolto ✅ — modulo più manutenibile, ma la state machine interna è ancora complessa.

**Soluzione proposta:**
- Semplificare TagSafeStream: bufferizzare fino a delimitatore naturale
- Eliminare `strip_action_tags()` finale
- Stato ridotto a singolo stack depth counter

**Sforzo:** ~2h | **Impatto:** Codice più robusto, -100 righe, bug fix

---

### Op3 ⚠️ REGREDSSO — Greeting Short-Circuit Perso in Module Extraction (P0 URGENTE)

**File:** `main.py` (dopo refactoring c1ccaa1)

**Problema:** Il blocco `PURE_GREETING` con risposta immediata per saluti puri (0 LLM call, 26ms) implementato in commit 4977aee è stato **involontariamente rimosso** nel refactoring module extraction (c1ccaa1).

**Fix proposto:** Riutilizzare `is_greeting()` da `agent/classifier.py` (esiste ancora, linee 114-118) con `GREETING_WORDS` set (linee 40-113). Inserire il check PRIMA di `build_omniscient_prompt()` in `main.py`.

**Sforzo:** ~15min | **Impatto:** 26ms invece di ~4-6s per saluti (-150x) | **Priorità: P0**

---

### M5 🟡 MEDIO — Flow Control Tramite Eccezioni

**File:** `agent/tools.py:648-649, 668-669, 692-693`

**Problema:** `PendingConfirmation` è un'eccezione usata per flow control.

**Soluzione proposta:** Usare return type `Union[str, PendingConfirmation]`.

**Sforzo:** ~30min | **Impatto:** Codice più leggibile

---

### M6 🟡 MEDIO — TOOLS_SCHEMA Lista Mutabile Globale

**File:** `agent/tools.py:42-269, 994-1065`

**Problema:** `TOOLS_SCHEMA` è una lista modulare globale modificata in 4 momenti diversi.

**Soluzione proposta:** Funzione `get_tools_schema()` che costruisce dinamicamente.

**Sforzo:** ~30min | **Impatto:** Stato prevedibile, testabile

---

### M7 🟡 MEDIO — _gather_memory() fa 2 Chiamate Mem0 Sequenziali

**File:** `agent/prompt.py:658-683`

**Problema:** Due chiamate `state.memory.search()` eseguite sequenzialmente.

**Soluzione proposta:** `asyncio.gather()`.

**Sforzo:** ~15min | **Impatto:** Ricerca memoria ~2x più veloce quando progetto attivo

---

### M8 🟡 MEDIO — SessionStore Persistenza su Disco per Ogni Messaggio

**File:** `main.py:644-646`

**Problema:** `state.chat_session_store.persist()` chiamato per ogni turno.

**Soluzione proposta:** Debounce persistenza (salva solo se passati ≥5s dall'ultimo).

**Sforzo:** ~15min | **Impatto:** ~5-20ms I/O risparmiati per turno

---

### M9 🟡 MEDIO — main.py 1263 Righe Ancora Troppe Responsabilità (Parz. Risolto)

**File:** `main.py` (1263 righe, ridotto da 1387)

**Progresso:** ✅ Parzialmente risolto — refactoring module extraction (c1ccaa1) ha estratto:
- `core/chat_utils.py` (+146 righe): `handle_confirmation_token()`, `spawn_background()`, `build_llm_options()`, `resolve_user_id()`
- `core/reasoning.py` (+334 righe): `configura_richiesta_agente()`, `genera_stream_agente()`, reasoning metadata
- `core/telemetry_api.py` (+98 righe): `get_status_dict()`, `get_model_info_dict()`, `get_pending_ops_dict()`
- Endpoint telemetry API spostati

**Riduzione:** 1387 → 1263 righe (-124 righe, -9%).

**Rimane da fare:**
- Estrarre modelli Pydantic in `api/models.py`
- Endpoint `/api/chat` in `api/chat_router.py`
- Endpoint `/api/generate` in `api/generate_router.py`

**Sforzo:** ~2h | **Impatto:** Manutenibilità, testabilità, -400 righe da main.py

---

## 11. Stima Impatto Cumulativo

| Scenario | Pre-Ottimizzazioni (Jul 26) | Stato Attuale (Jul 29) | Problemi | Prossime Ottimizzazioni P0-P2 |
|---|---|---|---|---|
| **TTFT saluto semplice** | 15-20s | **4-6s ⚠️** (regredito da 26ms) | Op3 regredito | Re-implementare greeting SC → 26ms |
| **TTFT query progetto semplice** | 20-35s | **5-8s** ✅ (Op1+8 compressor skip) | — | Già ottimizzato |
| **TTFT query progetto complessa** | 30-60s | **12-18s** ✅ | — | Compressor necessario |
| **TTFT query con tool-calling** | 34-50s | **10-20s** ✅ | C4 doppia gen | 5-10s se C4 fix |
| **TTFT cron reminder** | 30-60s | **15-30s** ✅ | C3 pieno pipeline | 0.1s template diretto |
| **Ingestion 1000 file** | 15-20 min | **8-12 min** ✅ | H4 lock contenuto | 5-8 min |
| **VRAM** | 1589 MiB (39%) | **3334 MiB (81%)** ⚠️ | Near limit | Invariata |
| **RAM Reranker** | ~600 MB | ~600 MB | Import module-level | ~0 MB lazy import |
| **Memoria Userbot** | ∞ leak | **≤1000 entry** ✅ | — | — |
| **Manutenibilità TagSafeStream** | Fragile, 862 righe | **276 righe** ✅ (da module extraction) | — | ~150 righe |
| **main.py dimensione** | 1387 righe | **1263 righe** (-9%) ✅ | Ancora grande | ~800 righe con estrazione |
| **Cherry Studio Qwen/DeepSeek** | ❌ Rotto | ✅ FUNZIONANTE | — | — |
| **Admin panel race condition** | ❌ Race condition | ✅ FIXATO | — | — |

### Diagramma Flusso Richiesta: PRIMA vs DOPO

**PRIMA (26/07 — Gemma 4 + Qwen3-Embedding + gatekeeper GPU):**
```
Utente → main.py → build_omniscient_prompt()
  ├── Gatekeeper()                 [Qwen3.5 CPU 5-10s + grammar]
  ├── _gather_rag()                [Qwen3-Embedding GGUF ~400 MiB VRAM]
  ├── Caveman Compressor           [Qwen3.5 0.8B GPU ~5-10s, 2048 ctx]
  └── generate_chat()              [Gemma 4 GPU ~8s TTFT + ~17s gen]
TOTALE: ~30-60s
```

**DOPO (28/07 — Qwen3.5-4B + FastEmbed ONNX CPU + gatekeeper CPU + compressor skip):**
```
Utente → main.py → build_omniscient_prompt()
  ├── Gatekeeper()                 [Qwen3.5-4B, 0 VRAM extra, ~0.3-0.8s] ✅
  ├── Context gathering            [FastEmbed ONNX CPU, 0 VRAM] ✅
  ├── Caveman Compressor           [Qwen3.5 0.8B CPU, condizionale] ✅
  │    └── Skip se contesto < 1000ch (Op1+8) → raw fallback, 0s
  │    └── Altrimenti → ~8-15s CPU, 0 VRAM
  └── generate_chat()              [Qwen3.5-4B GPU ~3-5s TTFT + ~3-5s gen] ✅
TOTALE per scenario:
  A (saluto):       ~4-6s     0 LLM call   ✅ -70%
  B (meta):         ~4-6s     0 LLM call   ✅ -70%
  C (prog.semplice):~5-8s     1 LLM call   ✅ -70% (Op1+8: compressor skip attivo)
  D (prog.complesso):~12-18s  2 LLM call   ✅ -50%
  E (generale):     ~5-8s     1 LLM call   ✅ -50%
  F (contesto):     ~5-8s     1 LLM call   ✅ -50%

Media: ~5-8s invece di ~20-30s → -70% TTFT medio grazie a Qwen3.5-4B!
LLM call media: ~1.0 (invariata, ma più veloci)
```
```
NOTA: con Op1+Op8 (già implementati) si arriva a ~0.7 LLM call e ~5-7s medio ✅
```

---

## 12. Priorità d'Intervento

| Priorità | ID | Cosa Fare | File | Sforzo | Impatto | Stato |
|---|---|---|---|---|---|---|---|
| ~~P0~~ | ~~C1-Op8~~ | ~~Skip compressor se nessun contenuto~~ | ~~agent/prompt.py~~ | ~~~5min~~ | ~~-1 LLM call~~ | ✅ FATTO (fuso con Op1) |
| ~~P0~~ | ~~C1-Op1~~ | ~~Skip compressor per contesto piccolo~~ | ~~agent/prompt.py~~ | ~~~15min~~ | ~~-1 LLM call~~ | ✅ FATTO (COMPRESSOR_MIN_CHARS=1000) |
| **P0 ⚠️** | **Op3** | **Re-implementare greeting SC (REGREDSSO)** | `main.py` | **~15min** | **26ms invece di 4-6s** | ⚠️ **REGREDSSO** |
| **P0** | C3 | Cron reminder senza pipeline LLM | `scheduler/cron.py` | ~30min | 15-30s → 0.1s | ❌ Aperto |
| **P0** | C5 | Semplificare TagSafeStream | `agent/tags.py` | ~2h | -100 righe, bug fix | 🔶 Parziale (276 vs 583) |
| **P1** | C1-Op5 | Espansione keyword bypass (tecnici) | `agent/prompt.py` | ~30min | +5-10% bypass rate | ❌ Aperto |
| **P1** | C1-Op7 | Context gathering parallelo | `agent/prompt.py` | ~2h | -1-2s su progetto | ❌ Aperto |
| **P2** | C4 | Ri-usare prima risposta tool-calling | `main.py` | ~2-3h | -50% latenza tool | ❌ Aperto |
| **P2** | H4 | Lock separato RAG | `rag/engine.py` | ~1h | -lock contention | ❌ Aperto |
| **P2** | H5 | Entity extraction opzionale | `memory/engine.py` | ~1h | -CPU salvataggio | ❌ Aperto |
| **P2** | H9 | Cache tasks.json in memoria | `scheduler/tasks.py` | ~30min | -I/O per richiesta | ❌ Aperto |
| **P3** | M2-M15 | Vari medi (10 item) | Multipli | ~4h | Miglioramenti sparsi | ❌ Aperti |
| **P4** | L1-L5 | Vari bassi (5 item) | Multipli | ~1h | Polish | ❌ Aperti |

### Roadmap

**Sprint 0 (COMPLETATO ✅ — Ottimizzazioni Jul 26):**
- OpB, OpA, C6, C7, C2, H2, H1, H6, M1, H7, H8 — ✅

**Sprint 0b (COMPLETATO ✅ — Jul 27 Model Switch):**
- Qwen3.5-4B, FastEmbed ONNX CPU, hardware profile auto-detection, conversation_id auto — ✅

**Sprint 0c (COMPLETATO ✅ — Jul 28):**
- C1-Op1+Op8: Skip compressor (COMPRESSOR_MIN_CHARS=1000) ✅
- Documentazione allineata ✅

**Sprint 0d (COMPLETATO ✅ — Jul 29):**
- **Module Extraction**: 7 moduli da main.py/tools.py/tags.py/rag/engine.py ✅
- **Admin Panel Fixes** (Bug 10): race condition ingestion, timeout logs, restart buttons ✅
- **Cherry Studio Fix** (Bug 9): TagSafeStream, /no_think prefix, gatekeeper reasoning ✅
- **Qdrant Orphan Cleanup**: sanitize_project_name() centralizzato ✅
- **Documentazione v9.10.0**: AGENTS.md, CHANGELOG.md, README.md, COMPONENTS.md, PIPELINE.md ✅
- **Performance Report Aggiornato**: questo file ✅

**Sprint 0e (P0 URGENTE — ~15min):**
- **Op3**: Re-implementare greeting SC perso in module extraction — usare `is_greeting()` da `agent/classifier.py`

**Sprint 1a (P0 rimanenti, ~2.5h):**
- C3: Cron reminder senza LLM (~30min)
- C5: TagSafeStream semplificato (~2h)

**Sprint 1b (P1, ~2.5h):**
- C1-Op5: Espansione keyword bypass (solo tecnici, ~30min)
- C1-Op7: Context gathering parallelo (~2h)

**Sprint 2 (P2, ~7h):**
- C4: Ri-uso prima risposta tool-calling
- H4: Lock separato RAG
- H5: Entity extraction ottimizzata
- H9: Cache tasks.json

**Sprint 3 (P3, ~4h):**
- Tutti i medi restanti

---

*Report generato il 2026-07-29 da Sisyphus — Performance Analysis Agent.*
*Aggiornamenti v9.10.0: Module extraction (7 moduli), Admin Panel fixes (Bug 10), Cherry Studio fix (Bug 9), greeting SC regression ⚠️.*
*Basato su analisi statica di ~44.200 LOC su 88 moduli + benchmark reali da BENCHMARKS.md.*
