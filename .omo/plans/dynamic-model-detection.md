# Piano: Rilevamento Dinamico Modello dai Metadati GGUF

## Problema

Il sistema rileva la famiglia del modello dal **nome del file GGUF** (es. "qwen"), 
ma questo non fornisce informazioni accurate come:
- Nome reale del modello (es. "Qwen3.5-4B-UD")
- Architettura esatta (es. "qwen2" vs "qwen2.5")
- `chat_format` corretto per il modello
- Supporto effettivo a funzioni/thinking

## Modifiche

### 1. `jarvis/model_profiles.py` — Aggiungere rilevamento da metadati

**Aggiungere** campi a `ModelProfile`:
```python
@dataclass
class ModelProfile:
    model_name: str = ""         # Nome estratto dai metadati GGUF
    chat_format: Optional[str] = None  # Formato chat per llama-cpp-python
    # ... campi esistenti ...
```

**Aggiungere** `detect_from_metadata(metadata, fallback_profile)`:
```python
def detect_from_metadata(metadata: dict, fallback: ModelProfile) -> ModelProfile:
    """
    Rileva profilo modello dai metadati GGUF (dopo caricamento).
    Usa general.architecture e general.name per maggior precisione.
    """
    arch = (metadata.get("general.architecture") or "").lower()
    name = metadata.get("general.name") or fallback.model_name or ""
    unsloth = fallback.unsloth_optimized  # preserva dal filename
    
    # Architecture → famiglia
    arch_map = {
        "qwen2":   ("qwen",    "qwen2",   False),
        "qwen2.5": ("qwen",    "qwen2.5", False),
        "gemma2":  ("gemma",   "gemma2",  True),
        "gemma":   ("gemma",   "gemma",   True),
        "llama":   ("llama",   "llama",   False),
        "mistral": ("mistral", "mistral", False),
        "deepseek2": ("deepseek", "deepseek2", True),
        "phi3":    ("phi",     "phi3",    False),
    }
    
    for arch_key, (family, variant, thinking) in arch_map.items():
        if arch_key in arch:
            return ModelProfile(
                model_name=name,
                family=family,
                variant=variant,
                chat_format=family_to_chat_format(family, variant),
                thinking_support=thinking,
                unsloth_optimized=unsloth,
                # defaults ragionevoli per la famiglia
                ...
            )
    
    # Fallback su filename se architettura non riconosciuta
    return ModelProfile(
        model_name=name,
        chat_format=None,
        **fallback.__dict__
    )

def family_to_chat_format(family: str, variant: str = "") -> Optional[str]:
    """Mappa famiglia modello a chat_format per llama-cpp-python."""
    fmt_map = {
        "qwen": "chatml",
        "gemma": "gemma",
        "llama": "llama-2",
        "mistral": "llama-2",  # mistral segue formato llama
        "deepseek": "chatml",
        "phi": "phi-3",
    }
    return fmt_map.get(family)
```

### 2. `jarvis/llm_engine.py` — Estrarre metadati dopo caricamento

**Dopo** il caricamento del modello in `load_models()`:
```python
# Dopo self.chat_model = Llama(...)
self.model_metadata = {}
self.model_name = chat_model_path
try:
    if hasattr(self.chat_model, 'metadata'):
        self.model_metadata = dict(self.chat_model.metadata)
        self.model_name = self.model_metadata.get("general.name", self.model_name)
except Exception:
    pass

# Aggiorna ModelProfile
try:
    from model_profiles import detect_from_metadata
    from config import MODEL_PROFILE
    new_profile = detect_from_metadata(self.model_metadata, MODEL_PROFILE)
    import config as _cfg
    _cfg.MODEL_PROFILE = new_profile
    logger.info(f"🧠 Modello rilevato: {new_profile.model_name} "
                f"({new_profile.family}/{new_profile.variant}) "
                f"chat_format={new_profile.chat_format}")
except Exception as e:
    logger.warning(f"Rilevamento metadati fallito: {e}")
```

### 3. `jarvis/config.py` — Rendere MODEL_PROFILE aggiornabile

```python
# MODEL_PROFILE è già una variabile di modulo, aggiornabile via import
# Aggiungere solo:
model_profile_lock = threading.Lock()  # per thread safety
```

**Usare `chat_format` in `load_models()`**:
```python
# Invece di chat_format=None:
from config import MODEL_PROFILE
chat_format = os.environ.get("LLM_CHAT_FORMAT")  # override esplicito
if not chat_format:
    chat_format = MODEL_PROFILE.chat_format  # auto dal profilo

self.chat_model = Llama(
    ...
    chat_format=chat_format,
    ...
)
```

## Flusso Finale

```
1. start_worker.sh → llama_cpp.py → load_models()
2.   Carica modello GGUF → self.chat_model = Llama(...)
3.   Legge self.chat_model.metadata → dict con general.architecture, general.name
4.   detect_from_metadata() → ModelProfile aggiornato
5.   config.MODEL_PROFILE aggiornato globalmente
6.   prompt_builder / llm_engine usano MODEL_PROFILE aggiornato
```

## Files Modificati

1. `jarvis/model_profiles.py` — + `detect_from_metadata()`, + `family_to_chat_format()`, + campi `model_name` e `chat_format` in `ModelProfile`
2. `jarvis/llm_engine.py` — estrazione metadati in `load_models()`, aggiornamento `config.MODEL_PROFILE`
3. `jarvis/config.py` — `model_profile_lock` per thread safety (opzionale)
