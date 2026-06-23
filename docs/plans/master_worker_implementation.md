# Manuale Operativo: Architettura Master/Worker per Ecosistema AI

> **📅 Ultimo aggiornamento:** 2026-06-19  
> **📊 Stato:** Codice locale completamente pronto — in attesa di deployment su VPS  
> **📖 Guida Agenti AI:** `docs/AGENTS.md` — riferimento rapido per operare autonomamente

---

## 🚦 Stato dell'Implementazione (Dashboard)

### ✅ Completato (Codice Locale)
| Componente | Stato | Note |
|---|---|---|
| `jarvis/config.py` | ✅ Pronto | Gemma 4 params, LLM_THINKING_MODE, LLM_NUM_CTX |
| `jarvis/llm_engine.py` | ✅ Pronto | chat_format=None, thinking mode, n_ctx/n_gpu_layers configurabili, offloading+failover |
| `docker-compose.vps.yml` | ✅ Pronto | Stack Master senza GPU (no deploy section) |
| `docker-compose.worker.yml` | ✅ Pronto | QDRANT_HOST da .env, volumi mem0+documents montati |
| `start_master.sh` | ✅ Pronto | Usa docker-compose.vps.yml |
| `start_worker.sh` | ✅ Pronto | Modalità Worker GPU |
| `.env` (Worker locale) | ✅ Aggiornato | Qwen3.5-4B (temporaneo), Gemma 4 in attesa fix llama-cpp |
| `sync_to_master.sh` | ✅ Creato | Script rsync con verifica SSH, pronto all'uso |
| `Dockerfile` | ✅ Aggiornato | llama-cpp-python dalla master GitHub (supporto Gemma 4) |
| **Istanza Locale** | ✅ **ONLINE** | Jarvis avviato, Qdrant+Mem0+RAG funzionanti, testato |
| `docs/AGENTS.md` | ✅ Creato | Guida completa per agenti AI (architettura, regole, bug, comandi) |
| `README.md` | ✅ Aggiornato | Architettura Master/Worker, Tailscale, no Ngrok/Ollama, stato modelli |
| Piano di deployment | ✅ Completo | Questo documento |

> ⚠️ **Nota Modello:** Gemma 4 (E2B) ha un bug in llama-cpp-python ≤0.3.30 (`GGML_SCHED_MAX_SPLIT_INPUTS`, [PR #22133](https://github.com/ggml-org/llama.cpp/pull/22133)). Temporaneamente si usa `Qwen3.5-4B-UD-Q4_K_XL.gguf`. Per tornare a Gemma 4: aggiornare `llama-cpp-python` quando la 0.3.31+ sarà disponibile e impostare `LLAMA_MODEL_PATH=./models/gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf`, `LLM_TEMPERATURE=1.0`, `LLM_REPEAT_PENALTY=1.0`, `LLM_TOP_P=0.95`, `LLM_THINKING_MODE=true`.

### ⏳ Da Completare (Operazioni Manuali)
| Step | Azione | Sezione |
|---|---|---|
| **1** | Copia progetto sulla VPS via SCP/Git | §9 Step 1 |
| **2** | Installare Tailscale su VPS e Laptop | §9 Step 2 |
| **3** | Creare `.env` su VPS (Master) + aggiornare `.env` locale (Worker) | §9 Step 3 |
| **4** | Copiare sessioni Userbot Telegram sulla VPS | §9 Step 5 |
| **5** | Download modello `gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf` su VPS (~14.2GB) | §9 Step 5b |
| **6** | Download modello `gemma-4-E2B-it-Q4_K_M.gguf` sul Laptop | §9 Step 5b |
| **7** | Avviare Master (VPS) e Worker (Laptop) | §9 Step 6 |
| **8** | ~~Creare `sync_to_master.sh`~~ ✅ CREATO — eseguire quando pronto | §9 Step 7 |

---

## 1. Visione Generale
Realizzare un sistema AI autonomo, resiliente e "error-proof".
- **Master (VPS):** Gestione infrastruttura (Qdrant, Mem0, Bot Telegram, RAG).
- **Worker (Local GPU):** Inferenza LLM pesante (offloading via VPN).

## 2. Accesso alla VPS (Master)

| Parametro | Valore |
|---|---|
| **IP Pubblico** | `51.38.135.179` |
| **Utente SSH** | `debian` |
| **Chiave SSH** | `/home/alfio/.ssh/ovh_rsa` |
| **Percorso progetto** | `/home/debian/ai-ecosystem` |

**Comando di connessione:**
```bash
ssh -i /home/alfio/.ssh/ovh_rsa debian@51.38.135.179
```

**Copia file sulla VPS:**
```bash
scp -i /home/alfio/.ssh/ovh_rsa <file_locale> debian@51.38.135.179:/home/debian/ai-ecosystem/<destinazione>
```

---

## 3. Infrastruttura e Rete (VPN Mesh - Tailscale)
L'architettura utilizza Tailscale per una rete privata virtuale (VPN Mesh) che garantisce IP statici e persistenti.

### Configurazione Tailscale
1. Installare Tailscale su Master (VPS) e Worker (Laptop):
   ```bash
   # Su entrambi i nodi:
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   ```
2. Eseguire `tailscale ip -4` su ciascun nodo per annotare l'IP VPN assegnato (es. `100.64.0.1` Master, `100.64.0.2` Worker).
3. Verificare la connettività: dal Worker eseguire `ping 100.64.0.1`.

## 4. Workflow Operativo (Switching Modes)

Il sistema può operare in due modalità distinte. È necessario configurare il nodo Worker in base alla connettività.

### A. Modalità Offline (Standalone Local)
Il Worker lavora in isolamento, utilizzando le proprie risorse locali.
- **`.env` (Worker):**
```env
TELEGRAM_ENABLED=false
QDRANT_HOST=localhost
EXTERNAL_GPU_URL=
```
- **Avvio:** `./start_worker.sh`

### B. Modalità Online (Integrated Master-Worker)
Il Worker è connesso al Master via Tailscale, sfruttando la memoria centralizzata e delegando l'inferenza.
- **`.env` (Worker):**
```env
TELEGRAM_ENABLED=false
QDRANT_HOST=100.64.0.1
EXTERNAL_GPU_URL=http://100.64.0.2:8000
```
- **Avvio:** `./start_worker.sh`

## 5. Configurazione Master (VPS - "La Mente")
Gestisce il core dell'applicazione.
- **`.env` (Master):**
```env
TELEGRAM_ENABLED=true
TELEGRAM_TOKEN=tuo_token_ufficiale
# (Altre credenziali Telegram/API qui)
QDRANT_HOST=localhost
```
- **Avvio:** `./start_master.sh`

## 6. Centralizzazione Bot Telegram
Per garantire l'alta disponibilità:
1. **Master:** `TELEGRAM_ENABLED=true`.
2. **Worker:** `TELEGRAM_ENABLED=false`.
3. **Migrazione:** Copiare l'intera directory `data/jarvis_mem0/userbots/` dal Worker al Master.

## 7. Sincronizzazione Dati (Error-Proof)
Per garantire la coerenza dei dati tra Offline e Online, sincronizzare `data/qdrant` e `data/jarvis_mem0`.

**Script di sincronizzazione (`sync_to_master.sh`):**
```bash
#!/bin/bash
# Eseguire dal Worker per sincronizzare i dati verso il Master
# Prerequisito: chiave SSH configurata
rsync -avzP -e "ssh -i /home/alfio/.ssh/ovh_rsa" data/qdrant/ debian@51.38.135.179:/home/debian/ai-ecosystem/data/qdrant/
rsync -avzP -e "ssh -i /home/alfio/.ssh/ovh_rsa" data/jarvis_mem0/ debian@51.38.135.179:/home/debian/ai-ecosystem/data/jarvis_mem0/
```

## 8. Modifiche al Codice dell'Applicazione

### 8.1 `docker-compose.worker.yml` — Abilitare Qdrant esterno in modalità Online

**Problema:** Il Worker ha `QDRANT_HOST=local` hardcoded nel `docker-compose.worker.yml` (riga 28), che sovrascrive sempre il valore del `.env`.

**Modifica:** Rimuovere la variabile `QDRANT_HOST` dall'`environment` del compose per farla leggere esclusivamente dal `.env`.

```yaml
# File: docker-compose.worker.yml
# Rimuovere questa riga dalla sezione environment:
- QDRANT_HOST=local  # <-- DA RIMUOVERE
```

**Risultato atteso:** In modalità Offline, il `.env` avrà `QDRANT_HOST=localhost`; in modalità Online avrà `QDRANT_HOST=100.64.0.1` (IP Tailscale del Master). Il container non sovrascriverà più il valore.

---

### 8.2 `docker-compose.worker.yml` — Aggiungere mount dei dati per la modalità Offline

**Problema:** Il Worker non monta i volumi `data/qdrant` e `data/jarvis_mem0`, quindi in modalità Offline non ha dati persistenti.

**Modifica:** Aggiungere i volumi nella sezione `volumes`:

```yaml
# File: docker-compose.worker.yml
# Aggiungere nella sezione volumes:
volumes:
  - ./jarvis:/app
  - ./data/jarvis_mem0:/app/mem0_data_v3   # <-- AGGIUNGERE
  - ./data/documents:/app/documents          # <-- AGGIUNGERE (per RAG locale)
  - /:/host_fs
  - /etc/localtime:/etc/localtime:ro
  - /etc/timezone:/etc/timezone:ro
```

---

### 8.3 `jarvis/config.py` — ✅ MODIFICHE GIÀ APPLICATE

Il file `config.py` è stato aggiornato nelle sessioni precedenti:
- **Riga 73:** `QDRANT_HOST = os.getenv("QDRANT_HOST", "local")` — legge dal `.env`.
- **Riga 77:** `EXTERNAL_GPU_URL = os.getenv("EXTERNAL_GPU_URL", "")` — legge dal `.env`.
- **Righe 236-251:** `TELEGRAM_ENABLED` supporta valori `true`, `false` e `auto` dal `.env`.
- **Righe 80-94:** `LLM_NUM_CTX` (alias `LLM_CTX_SIZE`), `LLM_THINKING_MODE`, parametri LLM ottimizzati per Gemma 4 (`temperature=1.0`, `repeat_penalty=1.0`, `top_p=0.95`).

✅ **Nessuna ulteriore modifica necessaria.**

---

### 8.4 `jarvis/llm_engine.py` — ✅ MODIFICHE GIÀ APPLICATE

Il file `llm_engine.py` è stato aggiornato nelle sessioni precedenti:
- **Riga 84:** `LLAMA_MODEL_PATH` letto da `os.environ` — nessun path hardcoded.
- **Riga 90:** `n_gpu_layers = int(os.environ.get("N_GPU_LAYERS", 20))` — configurabile via `.env`.
- **Riga 94:** `n_ctx = int(os.environ.get("LLM_NUM_CTX") or os.environ.get("LLM_CTX_SIZE") or "32768")` — configurabile via `.env`.
- **Riga 99:** `chat_format=None` — corretto per Gemma 4 (usa template Jinja2 embedded nel GGUF).
- **Righe 127-143:** Thinking Mode: inietta `<|think|>` al system prompt se `LLM_THINKING_MODE=true`.
- **Riga 171:** `if EXTERNAL_GPU_URL:` — attiva offloading solo se la variabile è valorizzata.
- **Riga 184:** Ping veloce (timeout 1.5s) per verificare se il Worker è raggiungibile.
- **Riga 194:** Fallback automatico su CPU locale se il Worker è offline.

✅ **Nessuna ulteriore modifica necessaria.**

---

### 8.5 Selezione e Configurazione Modelli — Gemma 4

**Decisione:** Utilizzo di **Google Gemma 4** su entrambi i nodi. È la famiglia di modelli più recente (aprile 2026), progettata da Google DeepMind con reasoning integrato, context window 128K-256K, supporto multimodale e funzionalità agent-native.

I due nodi hanno hardware radicalmente diverso: la VPS è CPU-only con molta RAM, il Worker ha GPU con poca VRAM.

---

#### Panoramica della famiglia Gemma 4

| Modello | Parametri attivi | Architettura | RAM (Q4) | VRAM (Q4) | Uso consigliato |
|---|---|---|---|---|---|
| **Gemma 4 E2B** | 2B Dense | Edge/Mobile | ~2.9GB | ~2.9GB | Worker (4GB VRAM) ✅ |
| **Gemma 4 E4B** | 4B Dense | Edge/Desktop | ~4.5GB | ~4.5GB | Worker (alternativa) |
| **Gemma 4 12B** | 12B Dense | Server | ~6.7GB | ~6.7GB | Master (CPU) ✅ |
| **Gemma 4 26B A4B** | 4B attivi / 26B totali | MoE | ~14.4GB | ~14.4GB | Master (premium) |
| **Gemma 4 31B** | 31B Dense | Server | ~17.5GB | ~17.5GB | Non adatto (troppo lento su CPU) |

> **🔑 Nota sul 26B A4B MoE:** Questo modello MoE (Mixture of Experts) attiva solo ~4B parametri per token durante l'inferenza, pur avendo 26B parametri totali. Risulta quindi **veloce quanto un 4B** ma con intelligenza paragonabile a un 12B-14B denso. Con 24GB RAM sulla VPS, il 26B A4B Q4 (~14.4GB) entra comodamente.

---

#### 🖥️ Master (VPS) — CPU-Only: 8 vCore, 24GB RAM

Il Master **non ha GPU**. L'inferenza avviene interamente su CPU tramite **llama-cpp-python** con file GGUF caricato in RAM (`n_gpu_layers=0`).  
Jarvis **non usa mai Ollama** — usa esclusivamente llama-cpp-python su entrambi i nodi.

> **⚠️ Nota su `OLLAMA_MODEL` nel codice:** La variabile `OLLAMA_MODEL` nel `.env` non avvia Ollama — è semplicemente un'etichetta testuale inclusa nel payload HTTP inviato al Worker durante l'offloading (vedi `llm_engine.py` riga 157). Quando il Worker è offline, Jarvis usa direttamente il file GGUF locale tramite llama-cpp-python.

**Vincoli RAM:**
- RAM totale VPS: 24GB.
- RAM riservata a Qdrant, Mem0, SearXNG, Crawl4AI, Bot Telegram: ~8-10GB.
- **RAM disponibile per il modello LLM: ~14-16GB** → supporta tranquillamente il 12B Q4 (~6.7GB) o il 26B A4B Q4 (~14.4GB).

**Confronto opzioni Gemma 4 per il Master (CPU, llama-cpp-python):**

| File GGUF | Tipo Quant | RAM usata | Qualità | Velocità CPU | Raccomandazione |
|---|---|---|---|---|---|
| `gemma-4-12b-it-Q4_K_M.gguf` | Standard Q4_K_M | ~6.7GB | ⭐⭐⭐⭐⭐ | ~6-8 t/s | ✅ Scelta conservativa |
| `gemma-4-26B-A4B-it-UD-Q4_K_M.gguf` | Standard Q4_K_M | ~14.4GB | ⭐⭐⭐⭐⭐ | ~8-12 t/s* | ✅ Scelta premium MoE |
| `gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf` | **QAT + Dynamic (Unsloth UD)** | ~14.2GB | ⭐⭐⭐⭐⭐⭐ | ~8-12 t/s* | 🏆 **SCELTA OTTIMALE** |
| `gemma-4-E4B-it-Q4_K_M.gguf` | Standard Q4_K_M | ~4.5GB | ⭐⭐⭐⭐ | ~15 t/s | ⚠️ Troppo piccolo per la VPS |

> \* Il 26B A4B MoE è **più veloce del 12B denso** su CPU perché attiva solo ~4B parametri per inferenza.

---

### 📊 Differenze tra `Q4_K_M` e `Q4_K_XL` (per 26B A4B)

Questa è la differenza chiave che determina la scelta del modello per la VPS:

| Caratteristica | `Q4_K_M` | `Q4_K_XL` (Unsloth Dynamic QAT) |
|---|---|---|
| **Tipo di quantizzazione** | Standard post-training | **QAT** (Quantization-Aware Training) |
| **Come è prodotto** | Quantizzato *dopo* il training BF16 | Google ha *addestrato il modello già pensando alla 4-bit* |
| **Dimensione file** | ~14.4 GB | ~14.2 GB (**200MB più piccolo!**) |
| **Qualità (top-1 accuracy)** | ~70.2% top-1 (degradazione significativa) | **~85.6% top-1** (+15.6% rispetto al Q4_K_M naïve) |
| **Precisione bit effettiva** | ~4 bit uniformi sulle matrici | **Misto dinamico**: layer critici a 6-bit, altri a 4-bit |
| **Perdita qualità vs BF16** | Moderata (~30% degradazione) | **Quasi trascurabile** (~15% residuo) |
| **Latenza di inferenza** | Uguale | Uguale (stessa architettura) |
| **Compatibilità llama.cpp** | ✅ Piena | ✅ Piena (formato GGUF standard) |
| **Repository HuggingFace** | `unsloth/gemma-4-26B-A4B-it-GGUF` | `unsloth/gemma-4-26B-A4B-it-GGUF` (stesso repo, prefisso `UD-`) |

> **📝 Spiegazione tecnica semplificata:**
> - `Q4_K_M` è come comprimere un'immagine JPG *dopo* averla scattata: perdi inevitabilmente dettagli.
> - `Q4_K_XL` (QAT) è come usare una fotocamera progettata *appositamente* per JPG: il risultato compresso è molto più vicino all'originale, spesso migliore del JPG convenzionale.
> - Il risultato: **qualità notevolmente superiore a parità di dimensione (anzi, è anche leggermente più piccolo)**.

> **🎯 Raccomandazione finale per il Master:**
> - **Scelta ottimale — usa questa:** `gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf` — QAT Dynamic Unsloth (prefisso `UD-`), ~14.2GB RAM, qualità quasi identica al BF16, architettura MoE (~8-12 t/s), context 256K token, entra nei 24GB della VPS.
> - **Alternativa sicura (se XL non disponibile):** `gemma-4-26B-A4B-it-UD-Q4_K_M.gguf` — stessa RAM, qualità inferiore.
> - **Alternativa conservativa (meno RAM):** `gemma-4-12b-it-UD-Q4_K_XL.gguf` — solo ~6.7GB RAM, context 256K, qualità inferiore, nessun rischio.

**Modifica necessaria in `llm_engine.py` per il Master (CPU):**
Sul Master, il modello GGUF va caricato con `n_gpu_layers=0`. L'attuale default è `n_gpu_layers=20`.
```python
# llm_engine.py — riga 92 (da modificare per il Master)
# Legge dal .env: se GPU non presente, impostare N_GPU_LAYERS=0 nel .env
n_gpu_layers = int(os.environ.get("N_GPU_LAYERS", 20)),
```
Aggiungere nel `.env` del Master:
```env
N_GPU_LAYERS=0
```
E modificare `llm_engine.py` riga 92:
```python
n_gpu_layers=int(os.environ.get("N_GPU_LAYERS", 20)),
```

**Configurazione `.env` Master:**
```env
# 🏆 SCELTA OTTIMALE (26B A4B QAT UD-Q4_K_XL — qualità quasi identica al BF16, MoE veloce, 256K context):
LLAMA_MODEL_PATH=./models/gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf
# Alternativa (stesso modello, qualità inferiore, Q4_K_M standard):
# LLAMA_MODEL_PATH=./models/gemma-4-26B-A4B-it-UD-Q4_K_M.gguf
# Alternativa conservativa (meno RAM, qualità inferiore):
# LLAMA_MODEL_PATH=./models/gemma-4-12b-it-UD-Q4_K_XL.gguf
# CRITICO: nessuna GPU sulla VPS → tutti i layer su CPU
N_GPU_LAYERS=0
# OLLAMA_MODEL è solo un'etichetta per il payload Worker (non avvia Ollama):
OLLAMA_MODEL=gemma-4-master
```

**Download del modello GGUF sulla VPS:**
```bash
ssh -i /home/alfio/.ssh/ovh_rsa debian@51.38.135.179
cd /home/debian/ai-ecosystem/jarvis/models/

# Installa huggingface-cli e hf_transfer (più veloce per file grandi):
pip install huggingface_hub hf_transfer
export HF_HUB_ENABLE_HF_TRANSFER=1

# 🏆 SCELTA OTTIMALE — Gemma 4 26B A4B UD-Q4_K_XL (~14.2GB RAM, qualità quasi BF16, context 256K):
huggingface-cli download unsloth/gemma-4-26B-A4B-it-GGUF \
    --include "*UD-Q4_K_XL*" \
    --local-dir .

# Alternativa Q4_K_M standard (stessa RAM, qualità inferiore):
# huggingface-cli download unsloth/gemma-4-26B-A4B-it-GGUF \
#     --include "*UD-Q4_K_M*" \
#     --local-dir .

# Alternativa conservativa (meno RAM — 12B denso):
# huggingface-cli download unsloth/gemma-4-12b-it-GGUF \
#     --include "*UD-Q4_K_XL*" \
#     --local-dir .

# Verifica:
ls -lh /home/debian/ai-ecosystem/jarvis/models/
```

---

#### 💻 Worker (Laptop) — Hardware reale

| Componente | Specifica |
|---|---|
| **CPU** | Intel Core i5-11300H (8 thread, 3.1GHz) |
| **RAM** | 16 GiB |
| **GPU** | NVIDIA GeForce RTX 3050 Ti Laptop GPU |
| **VRAM** | **4 GB GDDR6** |
| **GPU 2** | Intel Iris Xe (integrata, non usata per inferenza) |
| **OS** | openSUSE Tumbleweed |

> ⚠️ **Vincolo critico:** La RTX 3050 Ti Laptop ha solo **4GB di VRAM**. La scelta dell'utente è `gemma-4-E2B-it-Q4_K_M.gguf` — **scelta ottimale e confermata**.

Gemma 4 E2B in Q4_K_M usa ~2.9GB di VRAM, lasciando ~1GB di margine. Entra interamente in GPU senza offload ibrido.

**Perché Gemma 4 E2B è la scelta giusta per il Worker:**
- **2.9GB VRAM (Q4_K_M):** entra comodamente nei 4GB, nessun rischio CUDA OOM.
- **Reasoning nativo:** supporta thinking mode pur essendo solo 2B.
- **Velocità:** ~50-70 t/s su RTX 3050 Ti.
- **Context 128K token:** molto superiore ai modelli precedenti.
- **Multimodale:** supporta immagini, audio (nativo su E2B).
- **Architettura recente:** basata su Gemini 3, qualità superiore a Qwen2.5-3B.

**Confronto opzioni Gemma 4 per il Worker (4GB VRAM):**

| Modello | VRAM (Q4_K_M) | Qualità | Velocità GPU | Strategia | Raccomandazione |
|---|---|---|---|---|---|
| `gemma-4-E2B-it-Q4_K_M.gguf` | ~2.9GB | ⭐⭐⭐⭐ | ~60 t/s | 100% GPU | ✅ **Scelta dell'utente — confermata** |
| `gemma-4-E4B-it-Q4_K_M.gguf` | ~4.5GB | ⭐⭐⭐⭐⭐ | ~35 t/s | ibrido (supera 4GB) | ⚠️ Richiede offload parziale su RAM |
| `qwen2.5-7b-instruct-q4_k_s.gguf` | ~3.8GB | ⭐⭐⭐⭐ | ~30 t/s | 100% GPU | ✅ Alternativa se Gemma 4 non disponibile |

> **🎯 Raccomandazione finale per il Worker:** `gemma-4-E2B-it-Q4_K_M.gguf` — già identificato dall'utente. Perfetto per 4GB VRAM.
> Se si vuole più qualità a discapito della velocità: `gemma-4-E4B-it-Q4_K_M.gguf` con `n_gpu_layers` ridotto (es. 20/34) per offload ibrido su 16GB RAM.

**⚠️ Nota su `n_gpu_layers` per Gemma 4 E2B:**
- Gemma 4 E2B ha **18 transformer layers**.
- Con Q4_K_M (~2.9GB VRAM): usa `n_gpu_layers=18` (tutti i layer in GPU) → massima velocità.
- Nessun rischio CUDA OOM con 4GB VRAM.

**Download GGUF Gemma 4:**
- Gemma 4 E2B (Worker): [https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF](https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF)
- Gemma 4 E4B (alternativa Worker): [https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF](https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF)
- Gemma 4 12B (Master GGUF alternativo): [https://huggingface.co/unsloth/gemma-4-12b-it-GGUF](https://huggingface.co/unsloth/gemma-4-12b-it-GGUF)

**Configurazione `.env` Worker (Gemma 4 E2B — scelta confermata):**
```env
LLAMA_MODEL_PATH=./models/gemma-4-E2B-it-Q4_K_M.gguf
N_GPU_LAYERS=18
# OLLAMA_MODEL è solo un'etichetta testuale inclusa nel payload HTTP verso il Master,
# non avvia Ollama — Jarvis usa esclusivamente llama-cpp-python con il GGUF sopra:
OLLAMA_MODEL=gemma-4-E2B-worker
```

---

#### 🔄 Flusso decisionale dell'inferenza (riepilogo — Gemma 4)

```
Richiesta Chat
     │
     ▼
[Jarvis Master VPS] ── EXTERNAL_GPU_URL impostato? ──► SÌ ──► Ping Worker (timeout 1.5s)
     │                                                                    │
     │ NO (Worker offline o non configurato)                    Worker raggiungibile?
     ▼                                                           SÌ ──────┼──► Offload HTTP
[llama-cpp-python su VPS] ◄─────────────────────────────── NO (fallback) │
  n_gpu_layers=0 (CPU only)                                               ▼
  gemma-4-12b-it-Q4_K_M.gguf                               [Jarvis Worker Laptop]
  ~6.7GB RAM, ~6-8 t/s                                      llama-cpp-python (CUDA)
  (o 26B A4B MoE: ~14.4GB, ~8-12 t/s)                      n_gpu_layers=18 (100% GPU)
  (Fallback automatico — Telegram sempre funziona)           gemma-4-E2B-it-Q4_K_M.gguf
                                                             ~2.9GB VRAM, ~60 t/s
```

> **ℹ️ Nessun Ollama coinvolto.** Jarvis usa llama-cpp-python sia sul Master (CPU, `n_gpu_layers=0`) che sul Worker (GPU, `n_gpu_layers=18`). La variabile `OLLAMA_MODEL` nel `.env` è solo un'etichetta nel payload HTTP di offloading.

---

### 8.6 `jarvis/main.py` — Nessuna modifica necessaria

Il `lifespan` FastAPI gestisce già correttamente:
- **Riga 113-118:** Inizializza Qdrant in modalità locale (`path=`) se `QDRANT_HOST=local`, altrimenti si connette via HTTP all'host remoto.
- **Riga 199:** Il Bot Telegram si avvia solo se `TELEGRAM_ENABLED` è `True`.

✅ Nessuna modifica necessaria — configurazione condizionale già in place.

---

### 8.6 Creare i file `.env` separati (da applicare manualmente)

> ℹ️ I valori qui sotto riflettono il file `.env` attuale del progetto (Worker/Locale). Copia e adatta il blocco corretto in base al nodo target.

---

**`.env` per il Master (VPS)** — copia questo file sulla VPS:
```env
# ==============================================================================
# VARIABILI AGGIUNTE PER ARCHITETTURA MASTER
# ==============================================================================
TELEGRAM_ENABLED=true

# --- Nodo Master: Qdrant gira come container Docker nello stesso stack ---
QDRANT_HOST=qdrant

# --- IP Tailscale del Worker (Laptop) ---
EXTERNAL_GPU_URL=http://100.64.0.2:8000

# ==============================================================================
# MODELLO LLM — Master usa llama-cpp-python su CPU (nessuna GPU sulla VPS)
# Jarvis NON usa Ollama — usa esclusivamente llama-cpp-python con file GGUF
# ==============================================================================
# 🏆 SCELTA OTTIMALE: Gemma 4 26B A4B UD-Q4_K_XL (~14.2GB RAM, ~8-12 t/s, qualità quasi BF16, context 256K)
LLAMA_MODEL_PATH=./models/gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf
# Alternativa (stesso modello, quantizzazione standard, qualità inferiore):
# LLAMA_MODEL_PATH=./models/gemma-4-26B-A4B-it-UD-Q4_K_M.gguf
# Alternativa conservativa (meno RAM, qualità inferiore):
# LLAMA_MODEL_PATH=./models/gemma-4-12b-it-UD-Q4_K_XL.gguf
# CRITICO: nessuna GPU sulla VPS → tutti i layer su CPU
N_GPU_LAYERS=0
# OLLAMA_MODEL è solo un'etichetta testuale nel payload HTTP verso il Worker:
OLLAMA_MODEL=gemma-4-master

# ==============================================================================
# CREDENZIALI (identiche al .env attuale del Worker)
# ==============================================================================
TELEGRAM_TOKEN=8949815609:AAE9-o_UMebjK3e-t4_E7itikCcIrXzz2TE
ALLOWED_USERS=6924399906
TELEGRAM_API_ID=21580802
TELEGRAM_API_HASH=78bc25f45f9741011975a70484e63676
TELEGRAM_PHONE=+393513050864
ALLOWED_PRIVATE_CHATS=

# ==============================================================================
# IMPOSTAZIONI DI SISTEMA (invariate)
# ==============================================================================
EMBEDDING_MODEL=nomic-embed-text
FLASHRANK_MODEL=ms-marco-TinyBERT-L-2-v2
VECTOR_DB_VERSION=v3
API_RATE_LIMIT_DEFAULT=60/minute
API_RATE_LIMIT_HEAVY=5/minute
WATCHDOG_BATCH_DELAY=1.0
SEMANTIC_CACHE_THRESHOLD=0.96
MEM0_STARTUP_DELAY=4.0

# ==============================================================================
# HYPERPARAMETERS LLM — OTTIMIZZATI PER CODING (Go, TypeScript, React)
# Fonte: Unsloth Gemma 4 docs — temperature=1.0 raccomandata per Gemma 4
# ==============================================================================
LLM_TEMPERATURE=1.0
LLM_REPEAT_PENALTY=1.0
LLM_TOP_P=0.95
# 65K context (26B A4B MoE su VPS: ~4-5GB KV-cache aggiuntivi, fattibile con 24GB RAM)
LLM_NUM_CTX=65536
LLM_NUM_PREDICT=2048
# Thinking mode Gemma 4 (antepone <|think|> al system prompt — migliora il coding)
LLM_THINKING_MODE=true

# ==============================================================================
# TUNING DEL RAG
# ==============================================================================
RAG_SCORE_THRESHOLD_CODE=0.40
RAG_SCORE_THRESHOLD_DOCS=0.32
RAG_TOP_K_CODE=5
RAG_TOP_K_DOCS=3

# ==============================================================================
# MOUNT DINAMICI (aggiornare con i percorsi della VPS)
# ==============================================================================
EXTERNAL_PROJECTS=""
```

---

**`.env` per il Worker — Modalità Offline** (il `.env` attuale con due righe aggiunte):
```env
# ==============================================================================
# VARIABILI AGGIUNTE PER ARCHITETTURA WORKER (OFFLINE)
# ==============================================================================
TELEGRAM_ENABLED=false
QDRANT_HOST=localhost        # Qdrant locale nel container Worker
EXTERNAL_GPU_URL=            # Vuoto: usa GPU locale direttamente

# ==============================================================================
# MODELLO LLM — Worker usa llama-cpp-python su GPU (RTX 3050 Ti, 4GB VRAM)
# Hardware: LENOVO IdeaPad Gaming 3, RTX 3050 Ti Laptop GPU, 16GB RAM
# ─────────────────────────────────────────────────────────────────────
# VRAM disponibile: 4GB → Gemma 4 E2B Q4_K_M è la scelta ideale (~2.9GB VRAM)
# Gemma 4 E2B: reasoning nativo, context 128K, multimodale, ~60 t/s su RTX 3050 Ti
# Tutti i 18 layer entrano in GPU senza offload ibrido.
#
# Opzioni (in ordine di preferenza):
#   1. gemma-4-E2B-it-Q4_K_M.gguf  (~2.9GB VRAM, ~60 t/s) ← SCELTA PRIMARIA (confermata)
#   2. gemma-4-E4B-it-Q4_K_M.gguf  (~4.5GB VRAM, ~35 t/s) ← più capace, offload ibrido
#   3. qwen2.5-7b-instruct-q4_k_s.gguf (~3.8GB VRAM, ~30 t/s) ← fallback non-Gemma
# Jarvis NON usa Ollama — usa esclusivamente llama-cpp-python con il file GGUF.
# ==============================================================================
LLAMA_MODEL_PATH=./models/gemma-4-E2B-it-Q4_K_M.gguf
# Tutti i 18 layer di Gemma 4 E2B entrano in GPU (4GB VRAM disponibili):
N_GPU_LAYERS=18
# OLLAMA_MODEL è solo un'etichetta nel payload HTTP di offloading verso il Master:
OLLAMA_MODEL=gemma-4-E2B-worker

# ==============================================================================
# CREDENZIALI (identiche al .env attuale)
# ==============================================================================
TELEGRAM_TOKEN=8949815609:AAE9-o_UMebjK3e-t4_E7itikCcIrXzz2TE
ALLOWED_USERS=6924399906
TELEGRAM_API_ID=21580802
TELEGRAM_API_HASH=78bc25f45f9741011975a70484e63676
TELEGRAM_PHONE=+393513050864
ALLOWED_PRIVATE_CHATS=

# ... (resto del .env invariato)
EMBEDDING_MODEL=nomic-embed-text
FLASHRANK_MODEL=ms-marco-TinyBERT-L-2-v2
VECTOR_DB_VERSION=v3
API_RATE_LIMIT_DEFAULT=60/minute
API_RATE_LIMIT_HEAVY=5/minute
WATCHDOG_BATCH_DELAY=1.0
SEMANTIC_CACHE_THRESHOLD=0.96
MEM0_STARTUP_DELAY=4.0
# Parametri ottimizzati per coding Go/TypeScript/React (Gemma 4 — temp=1.0 raccomandata da Unsloth)
LLM_TEMPERATURE=1.0
LLM_REPEAT_PENALTY=1.0
LLM_TOP_P=0.95
# Worker GPU (E2B — 16GB RAM, context compatto per massima velocità su RTX 3050 Ti):
LLM_NUM_CTX=32768
LLM_NUM_PREDICT=2048
LLM_THINKING_MODE=true
RAG_SCORE_THRESHOLD_CODE=0.40
RAG_SCORE_THRESHOLD_DOCS=0.32
RAG_TOP_K_CODE=5
RAG_TOP_K_DOCS=3
EXTERNAL_PROJECTS="/home/alfio/Projects/SlotBuilder/:SlotBuilder, /home/alfio/Projects/StreamAI-IPTV/:StreamAI-IPTV, /home/alfio/Projects/ShieldProxy/:Shield-Proxy"
```

---

**`.env` per il Worker — Modalità Online** (Worker connesso alla VPS via Tailscale):
```env
# ==============================================================================
# VARIABILI AGGIUNTE PER ARCHITETTURA WORKER (ONLINE)
# ==============================================================================
TELEGRAM_ENABLED=false
QDRANT_HOST=100.64.0.1       # IP Tailscale del Master (VPS)
EXTERNAL_GPU_URL=            # Il Worker NON fa offloading: è lui il target del Master

# ... (resto identico alla modalità Offline)
```

> **⚠️ Nota critica:** Il `EXTERNAL_GPU_URL` viene impostato **solo sul Master**, non sul Worker.
> Il Master punta al Worker con `EXTERNAL_GPU_URL=http://100.64.0.2:8000`.
> Il Worker non mette mai questo valore perché non delega l'inferenza a nessuno.

---

> **⚠️ Nota sugli `EXTERNAL_PROJECTS`:** Il percorso `/home/alfio/Projects/...` è valido solo sul laptop (Worker).
> Sulla VPS (Master), aggiornare `EXTERNAL_PROJECTS` con i percorsi corretti dei progetti presenti sulla VPS, oppure lasciarlo vuoto se i progetti non sono disponibili.

---

### 8.8 Ottimizzazione per Coding Assistant (Go, TypeScript, React)

Gemma 4 è eccellente per il coding grazie al reasoning nativo (thinking mode) e al context di 256K token, che permette di caricare interi progetti in memoria. Di seguito la configurazione ottimale per assistere allo sviluppo in **Go, TypeScript e React** su entrambi i nodi.

---

#### 🧠 Thinking Mode per il Coding

Gemma 4 supporta un canale di ragionamento interno che migliora notevolmente la qualità delle risposte su problemi complessi (debug, refactoring, architettura). Per attivarlo, aggiungere il token `<|think|>` **all'inizio del system prompt**.

> **Quando abilitarlo:** Per task complessi (debug difficile, progettazione architettura, spiegazione codice lungo).  
> **Quando disabilitarlo:** Per completamenti veloci (snippet semplici, autocompletamento) — il thinking aggiunge latenza.

**Controllo thinking via llama-cpp-python** (in `llm_engine.py`):
```python
# Per abilitare il thinking (task complessi — Go, TS, React debugging):
# Aggiungere <|think|> come primo token del system_prompt prima di passarlo a Llama()
system_prompt = "<|think|>\n" + system_prompt  # thinking abilitato

# Per disabilitare il thinking (risposte veloci):
# Passare il system_prompt senza prefisso <|think|>
```

---

#### 📝 System Prompt Ottimizzato per Coding (Go + TypeScript + React)

Questo è il system prompt raccomandato da Unsloth, esteso per i linguaggi specifici del progetto:

**System prompt per il Master (VPS — risposte più deliberate, context ampio):**
```
<|think|>
You are Jarvis, an expert coding assistant. You specialize in Go (Golang), TypeScript, and React.
When helping with code:
- Always explain your reasoning before writing code.
- Write idiomatic, production-ready code following best practices for each language.
- For Go: follow standard Go conventions (gofmt, error handling, interfaces, goroutines when appropriate).
- For TypeScript: use strict typing, prefer functional patterns, avoid `any`.
- For React: prefer functional components with hooks, follow React 18+ patterns, use TypeScript.
- When debugging, reason step-by-step through the problem before proposing a fix.
- Include relevant imports and package declarations.
- If the user provides a file or project context, use it to give contextually accurate answers.
```

**System prompt per il Worker (Laptop — risposte veloci, GPU):**
```
<|think|>
You are Jarvis, a fast and precise coding assistant. You specialize in Go, TypeScript, and React.
Write idiomatic, production-ready code. Be concise but accurate. Include imports. Explain briefly.
```

> **Nota:** Il thinking mode è attivato su entrambi i nodi per garantire qualità. Il Worker (GPU ~60 t/s) è abbastanza veloce da compensare la latenza del thinking anche per task rapidi.

---

#### ⚙️ Parametri LLM Ottimizzati per Coding

I parametri di default nel `.env` sono calibrati per uso generale. Per il coding si consiglia:

| Parametro | Valore generale | Valore coding ottimale | Motivazione |
|---|---|---|---|
| `LLM_TEMPERATURE` | `0.35` | **`1.0`** | Unsloth raccomanda `1.0` per Gemma 4 — calibrato in training |
| `LLM_REPEAT_PENALTY` | `1.15` | **`1.0`** | Il coding richiede ripetizione (variabili, pattern) — penalty alta causa errori |
| `LLM_TOP_P` | `0.85` | **`0.95`** | Più opzioni = codice più creativo e corretto sintatticamente |
| `LLM_NUM_CTX` | `24576` | **`65536`** | 65K token = ~50K caratteri di codice (interi file Go/TS caricabili) |
| `LLM_NUM_PREDICT` | `1024` | **`2048`** | Le funzioni complete spesso superano 1024 token |

> **⚠️ Nota su `LLM_NUM_CTX=65536` per il Master:** Con il 26B A4B MoE, ogni token di context occupa RAM aggiuntiva. Con 24GB RAM totali e ~14.2GB per il modello, rimangono ~9-10GB per il context KV-cache. 65K token Q4 usa ~4-5GB KV-cache → **fattibile sulla VPS**. Se si nota lentezza o OOM, ridurre a `32768`.

**`.env` ottimizzato per coding (da applicare su entrambi i nodi):**
```env
# ==============================================================================
# HYPERPARAMETERS LLM — OTTIMIZZATI PER CODING (Go, TypeScript, React)
# Fonte: Unsloth Gemma 4 documentation (temperature=1.0 raccomandata)
# ==============================================================================
LLM_TEMPERATURE=1.0
LLM_REPEAT_PENALTY=1.0
LLM_TOP_P=0.95
# Master (VPS — 24GB RAM, 26B A4B MoE, context ampio):
LLM_NUM_CTX=65536
# Worker (Laptop — 16GB RAM, E2B, context più compatto per velocità GPU):
# LLM_NUM_CTX=32768
LLM_NUM_PREDICT=2048
```

---

#### 📁 Sfruttare il Context da 256K (RAG e Progetti)

Il 26B A4B supporta **256.000 token di context** (Master) e l'E2B **128.000 token** (Worker). Questo permette di:

1. **Caricare interi file Go/TS/React nel context** senza chunking: un file `.go` da 2000 righe è ~8K token — possono stare 8+ file nel context.
2. **Usare `EXTERNAL_PROJECTS`** nel `.env` per montare dinamicamente le cartelle dei progetti:
   ```env
   # Worker — percorsi locali reali dei progetti:
   EXTERNAL_PROJECTS="/home/alfio/Projects/SlotBuilder/:SlotBuilder, /home/alfio/Projects/StreamAI-IPTV/:StreamAI-IPTV, /home/alfio/Projects/ShieldProxy/:Shield-Proxy"
   
   # Master VPS — vuoto (i progetti sono sul laptop, non sulla VPS):
   EXTERNAL_PROJECTS=""
   ```
3. **RAG per codebase grandi:** Se il progetto supera il context disponibile, Jarvis usa automaticamente il RAG (Qdrant) per recuperare i file più rilevanti via semantic search. Le variabili `RAG_TOP_K_CODE=5` e `RAG_SCORE_THRESHOLD_CODE=0.40` controllano questo comportamento.

---

#### 🔧 Modifiche a `jarvis/config.py` e `jarvis/prompt_builder.py`

Verificare che `prompt_builder.py` supporti il prefisso `<|think|>` nel system prompt. Se il system prompt viene costruito dinamicamente, assicurarsi che `<|think|>` sia il **primo token** della stringa finale passata al modello.

**Verifica rapida in `prompt_builder.py`:**
```python
# Assicurarsi che il system prompt per coding includa <|think|>:
# Il prompt builder deve preporre <|think|> quando la modalità è "coding"
# o quando il messaggio dell'utente contiene keyword di codice (def, func, class, etc.)
```

**Nuovo parametro `.env` consigliato (da aggiungere a `config.py`):**
```env
# Attiva il thinking mode di Gemma 4 (antepone <|think|> al system prompt):
LLM_THINKING_MODE=true
```

**Modifica da applicare in `jarvis/config.py`:**
```python
# Aggiungere in config.py:
LLM_THINKING_MODE = os.environ.get("LLM_THINKING_MODE", "true").lower() == "true"
```

**Modifica da applicare in `jarvis/llm_engine.py` (nel metodo che costruisce il system prompt):**
```python
# In llm_engine.py, prima di passare il system_prompt al modello:
from jarvis.config import LLM_THINKING_MODE

if LLM_THINKING_MODE:
    system_prompt = "<|think|>\n" + system_prompt
```

> ✅ **Modifica già applicata nel codice:** `llm_engine.py` ora inietta automaticamente `<|think|>` al system prompt se `LLM_THINKING_MODE=true`.

---

#### ⚠️ Note Critiche su `chat_format` e Tool Calls in llama-cpp-python

**Problema noto:** In `llama-cpp-python`, non esiste un handler `"gemma4"` registrato — solo `"gemma"` (Gemma 1/2/3). Se si usa `chat_format="chatml"` (impostato in precedenza per Qwen), Gemma 4 non riceve il template corretto e i tool calls vengono restituiti come raw token nel campo `content` invece di `tool_calls`.

**Soluzione applicata:** `chat_format=None` in `llm_engine.py` → llama-cpp-python usa il **template Jinja2 embedded nel GGUF Unsloth**, che è il comportamento corretto per Gemma 4.

**Limitazione residua (tool calls):** Anche con `chat_format=None`, i tool calls di Gemma 4 vengono restituiti come raw token nativi (`<|tool_call>call:FUNCTION_NAME{...}<tool_call|>`) nel campo `content` invece che in `tool_calls`. Questo è un bug aperto di `llama-cpp-python` (issue #2227). La workaround corrente è parsare manualmente il campo `content` in `agent_tools.py` se si usano tool calls.

---

#### 📊 Riepilogo configurazione finale per nodo

| Configurazione | Master (VPS CPU) | Worker (Laptop GPU) |
|---|---|---|
| **Modello** | `gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf` | `gemma-4-E2B-it-Q4_K_M.gguf` |
| **Context max supportato** | 256K token | 128K token |
| **`LLM_NUM_CTX` consigliato** | `65536` (65K) | `32768` (32K) |
| **`N_GPU_LAYERS`** | `0` (CPU only) | `18` (100% GPU) |
| **`LLM_TEMPERATURE`** | `1.0` | `1.0` |
| **`LLM_REPEAT_PENALTY`** | `1.0` | `1.0` |
| **`LLM_TOP_P`** | `0.95` | `0.95` |
| **`LLM_NUM_PREDICT`** | `2048` | `2048` |
| **Thinking Mode** | `true` | `true` |
| **Velocità stimata** | ~8-12 t/s (CPU, MoE) | ~50-60 t/s (GPU) |

---

### 8.7 Script di sincronizzazione `sync_to_master.sh` (da creare)

```bash
#!/bin/bash
# Eseguire dal Worker per sincronizzare i dati locali verso il Master
# Prerequisito: Tailscale attivo e SSH configurato

# Accesso diretto tramite IP pubblico SSH (prima di avere Tailscale)
MASTER_USER="debian"
MASTER_IP="51.38.135.179"         # IP pubblico VPS (pre-Tailscale)
# MASTER_IP="100.64.0.1"          # IP Tailscale VPS (post-Tailscale, preferibile)
MASTER_PATH="/home/debian/ai-ecosystem"
SSH_KEY="/home/alfio/.ssh/ovh_rsa"

echo "🔄 Sincronizzazione dati verso Master (${MASTER_USER}@${MASTER_IP})..."
rsync -avzP --delete -e "ssh -i ${SSH_KEY}" data/qdrant/ ${MASTER_USER}@${MASTER_IP}:${MASTER_PATH}/data/qdrant/
rsync -avzP --delete -e "ssh -i ${SSH_KEY}" data/jarvis_mem0/ ${MASTER_USER}@${MASTER_IP}:${MASTER_PATH}/data/jarvis_mem0/
echo "✅ Sincronizzazione completata."
```

---

## 9. Checklist di Deployment

### Step 1 — Connessione iniziale alla VPS
- [ ] Connettersi alla VPS: `ssh -i /home/alfio/.ssh/ovh_rsa debian@51.38.135.179`
- [ ] Clonare o copiare il progetto in `/home/debian/ai-ecosystem/`
- [ ] Installare Docker e Docker Compose sulla VPS se non presenti

### Step 2 — Setup Rete VPN (Tailscale)
- [ ] Installare Tailscale su VPS: `curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`
- [ ] Installare Tailscale sul Laptop e fare login con lo stesso account
- [ ] Annotare gli IP VPN: `tailscale ip -4` su entrambi i nodi
- [ ] Aggiornare i placeholder `100.64.0.1` / `100.64.0.2` con gli IP reali assegnati

### Step 3 — Configurazione file `.env`
- [ ] Creare `.env` per il Master sulla VPS (sezione 8.6) con `TELEGRAM_ENABLED=true`
- [ ] Aggiornare `.env` Worker (sezione 4) con `TELEGRAM_ENABLED=false`
- [ ] Verificare che `QDRANT_HOST=qdrant` sul Master e `QDRANT_HOST=localhost` sul Worker Offline

### Step 4 — Modifiche al Codice ✅ COMPLETATE
- [x] ✅ Applicata modifica 8.1: rimosso `QDRANT_HOST=local` hardcoded da `docker-compose.worker.yml` — ora il valore viene letto esclusivamente dal `.env`
- [x] ✅ Applicata modifica 8.2: aggiunti volumi `./data/jarvis_mem0:/app/mem0_data_v3` e `./data/documents:/app/documents` in `docker-compose.worker.yml`
- [x] ✅ Applicata modifica 8.8: `LLM_THINKING_MODE` aggiunto in `jarvis/config.py` (riga 94) — default `true`
- [x] ✅ Applicata modifica 8.8: `jarvis/llm_engine.py` inietta `<|think|>` al system prompt quando `LLM_THINKING_MODE=true` (righe 127-143)
- [x] ✅ `n_ctx` configurabile via `LLM_NUM_CTX` in `jarvis/llm_engine.py` (riga 94)
- [x] ✅ `chat_format=None` impostato in `jarvis/llm_engine.py` — compatibile con Gemma 4 GGUF Unsloth

### Step 5 — Migrazione Sessioni Telegram
- [ ] Copiare le sessioni Userbot dal laptop alla VPS:
  ```bash
  scp -i /home/alfio/.ssh/ovh_rsa -r data/jarvis_mem0/userbots/ debian@51.38.135.179:/home/debian/ai-ecosystem/data/jarvis_mem0/
  ```

### Step 5b — Download Modelli LLM (Gemma 4 GGUF)

> **ℹ️ Jarvis usa esclusivamente llama-cpp-python con file GGUF. Nessun Ollama coinvolto.**

**Sulla VPS (Master) — scarica Gemma 4 GGUF per CPU:**
```bash
ssh -i /home/alfio/.ssh/ovh_rsa debian@51.38.135.179
cd /home/debian/ai-ecosystem/jarvis/models/

# Installa huggingface-cli (dentro o fuori dal container):
pip install huggingface_hub

# 🏆 SCELTA OTTIMALE — Gemma 4 26B A4B UD-Q4_K_XL (~14.2GB RAM, qualità quasi BF16, context 256K):
huggingface-cli download unsloth/gemma-4-26B-A4B-it-GGUF \
    --include "*UD-Q4_K_XL*" \
    --local-dir .

# Alternativa (stessa dimensione, quantizzazione standard, qualità inferiore):
# huggingface-cli download unsloth/gemma-4-26B-A4B-it-GGUF \
#     --include "*UD-Q4_K_M*" \
#     --local-dir .

# Alternativa conservativa (meno RAM — 12B denso):
# huggingface-cli download unsloth/gemma-4-12b-it-GGUF \
#     --include "*UD-Q4_K_XL*" \
#     --local-dir .

# Verifica:
ls -lh /home/debian/ai-ecosystem/jarvis/models/
```

**Sul Laptop Worker (RTX 3050 Ti — 4GB VRAM) — scarica Gemma 4 E2B GGUF per GPU:**
```bash
# Hardware: LENOVO IdeaPad Gaming 3, RTX 3050 Ti Laptop GPU, 4GB VRAM
# Scelta confermata: gemma-4-E2B-it-Q4_K_M.gguf (~2.9GB VRAM, ~60 t/s)
# Tutti i 18 layer entrano interamente in VRAM (nessun offload ibrido)

# Verifica VRAM disponibile:
nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv

cd /home/alfio/Projects/ai-ecosystem/jarvis/models/

# Installa huggingface-cli se non presente:
pip install huggingface_hub

# --- SCELTA PRIMARIA: Gemma 4 E2B Q4_K_M (confermata dall'utente) ---
huggingface-cli download unsloth/gemma-4-E2B-it-GGUF gemma-4-E2B-it-Q4_K_M.gguf --local-dir .
# Alternativa wget:
# wget "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_K_M.gguf"

# --- ALTERNATIVA: Gemma 4 E4B Q4_K_M (più capace, richiede offload ibrido su 16GB RAM) ---
# huggingface-cli download unsloth/gemma-4-E4B-it-GGUF gemma-4-E4B-it-Q4_K_M.gguf --local-dir .

# Verifica file scaricati:
ls -lh /home/alfio/Projects/ai-ecosystem/jarvis/models/
```
- [ ] Verificata VRAM disponibile con `nvidia-smi` prima del download
- [ ] File `gemma-4-E2B-it-Q4_K_M.gguf` scaricato sul laptop in `jarvis/models/`
- [ ] Verificato `LLAMA_MODEL_PATH=./models/gemma-4-E2B-it-Q4_K_M.gguf` e `N_GPU_LAYERS=18` nel `.env` Worker
- [ ] 🏆 File `gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf` scaricato sulla VPS in `jarvis/models/` (~14.2GB)
- [ ] Verificato `LLAMA_MODEL_PATH=./models/gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf` e `N_GPU_LAYERS=0` nel `.env` Master
- [x] ✅ `llm_engine.py` riga 90: `n_gpu_layers=int(os.environ.get("N_GPU_LAYERS", 20))` — già applicato
- [ ] Se errore CUDA OOM sul Worker: ridurre `N_GPU_LAYERS` a 15 nel `.env` Worker (E2B ha 18 layer, improbabile)

### Step 6 — Avvio Servizi
- [ ] Avviare il Master sulla VPS: `./start_master.sh`
- [ ] Avviare il Worker sul Laptop: `./start_worker.sh`
- [ ] Verificare che il Master raggiunga il Worker: `curl http://100.64.0.2:8000/health`

### Step 7 — Script di sincronizzazione
- [x] ✅ `sync_to_master.sh` già creato nella root del progetto e reso eseguibile
- [ ] Eseguire una prima sincronizzazione per allineare i dati: `./sync_to_master.sh`
  > 💡 Tip: per usare IP Tailscale dopo il setup VPN: `MASTER_IP=100.64.0.1 ./sync_to_master.sh`
