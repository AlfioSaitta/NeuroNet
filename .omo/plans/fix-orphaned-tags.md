# Piano: Correzione Tag Orfani / Non Chiusi dal LLM

## Problema

Il LLM genera tag XML (es. `<MEMORY>...</MEMORY>`) alla fine della risposta, ma
spesso questi risultano **orfani** (aperti senza chiusura), causando:

- Contenuto residuo nel testo visibile all'utente
- Perdita di funzionalità (memoria non salvata, task non creati, ecc.)

## Cause Radice (identificate)

| # | Causa | File | Impatto |
|---|-------|------|---------|
| 1 | Default `LLM_MAX_TOKENS` = 512 — risposte troncate prima della chiusura tag | `llm_engine.py:265` | **ALTO** — causa principale |
| 2 | `build_tag_instructions()` mai chiamata — istruzioni hardcoded e inconsistenti (4 tag vs 12+ tag) | `prompt_builder.py:404-463` | **MEDIO** — LLM non sa tutti i tag |
| 3 | `strip_orphaned_tags()` troppo aggressiva — rimuove tag senza processare il contenuto | `tag_processor.py:629-645` | **BASSO** — contenuto perso |
| 4 | `process_all_tags()` non processa tag non bilanciati | `tag_processor.py:534-598` | **ALTO** — i tag orfani vengono ignorati |

## Modifiche

### Fix 1: `jarvis/llm_engine.py` — Aumentare default LLM_MAX_TOKENS

**Riga 263-266:**

```python
# Before:
_max_tokens_cap = int(os.environ.get("LLM_MAX_TOKENS", "512"))

# After:
_max_tokens_cap = int(os.environ.get("LLM_MAX_TOKENS", "2048"))
```

Cambiare il default da `512` a `2048` (mantenendo override via env var).

### Fix 2: `jarvis/prompt_builder.py` — Usare `build_tag_instructions()` nel prompt

**Aggiungere import** (in cima al file o nel branch condizionale):
```python
from tag_processor import build_tag_instructions
```

**Sostituire** le istruzioni tag hardcoded in entrambe le varianti
(`is_project_query=True` e `is_project_query=False`) con:

```python
# Istruzioni tag auto-generate dal registro
tag_instructions = build_tag_instructions()
```

Rimuovere le istruzioni hardcoded (righe ~419-424 nella variante progetto,
righe ~448-462 nella variante chat) e sostituire con il blocco generato.

### Fix 3: `jarvis/tag_processor.py` — Migliorare `strip_orphaned_tags()`

**Sostituire** l'attuale implementazione (righe 629-645) con una versione che:

1. Rimuove solo tag **veramente orfani** (opening senza closing)
2. **Non tocca** tag completi (già processati da `process_all_tags`)
3. **Recupera il contenuto** del tag orfano invece di buttarlo via

```python
def strip_orphaned_tags(text: str) -> str:
    """
    Rimuove tag d'azione rimasti aperti/orfani (es. <MEMORY> senza </MEMORY>).
    RECUPERA il contenuto del tag orfano invece di buttarlo via.
    
    Strategia:
    - Tag completi (<TAG>...</TAG>) sono già gestiti da process_all_tags, non li tocchiamo
    - Tag solo-opening (<TAG>... senza chiusura) → rimuove il tag ma tiene il contenuto
    - Tag solo-closing (</TAG> senza apertura) → rimuove il closing
    """
    tag_names = "|".join(
        t.name for t in _TAG_REGISTRY.values()
        if not t.is_self_closing and t.visibility in ("hidden", "action")
    )
    if not tag_names:
        return text
    
    # 1. Prima rimuovi solo tag COMPLETI (sono già stati processati da process_all_tags
    #    ma potrebbero essere rimasti se l'handler non li ha tolti)
    complete = re.compile(
        rf"<({tag_names})>(.*?)</\1>", re.DOTALL | re.IGNORECASE
    )
    text = complete.sub(r"\2", text)  # Tiene il contenuto
    
    # 2. Poi rimuovi tag opening orfani (<TAG>... senza </TAG>)
    #    ma tieni il contenuto dopo
    orphan_open = re.compile(rf"<\b(?:{tag_names})\b[^>]*>", re.IGNORECASE)
    text = orphan_open.sub("", text)
    
    # 3. Rimuovi tag closing orfani (</TAG> senza <TAG> prima)
    orphan_close = re.compile(rf"</\b(?:{tag_names})\b[^>]*>", re.IGNORECASE)
    text = orphan_close.sub("", text)
    
    return text.strip()
```

### Fix 4: `jarvis/tag_processor.py` — Aggiungere `close_orphaned_tags()` pre-processing

**Aggiungere** nuova funzione PRIMA di `process_all_tags()` che tenta di chiudere
tag non bilanciati alla fine della risposta:

```python
def close_orphaned_tags(text: str) -> str:
    """
    Pre-processing: rileva tag aperti ma non chiusi alla fine del testo
    e li chiude automaticamente, permettendo a process_all_tags() di 
    processarli correttamente.
    
    Esempio:
      Input: "...<MEMORY>fatto importante"
      Output: "...<MEMORY>fatto importante</MEMORY>"
    
    Funziona solo per tag alla fine del testo (dove il troncamento
    è più probabile).
    """
    tag_names = [
        t.name for t in _TAG_REGISTRY.values()
        if not t.is_self_closing
    ]
    
    for name in tag_names:
        # Cerca <TAG>content senza </TAG> alla fine
        pattern = re.compile(
            rf"<{name}>(.*?)(?:</{name}>)?$", 
            re.DOTALL | re.IGNORECASE
        )
        match = pattern.search(text)
        if match and not text.rstrip().endswith(f"</{name}>"):
            # Aggiungi chiusura mancante
            text = text.rstrip() + f"</{name}>"
    
    return text
```

**Integrare** in `telegram_bot.py` nel flusso principale:
```python
# Prima di process_all_tags:
from tag_processor import ..., close_orphaned_tags
bot_reply = close_orphaned_tags(bot_reply)
```

## Flusso Finale (dopo i fix)

```
bot_reply (grezzo dal LLM)
  │
  ▼
close_orphaned_tags()      ← Fix 4: chiude tag non bilanciati
  │
  ▼
strip_thinking_blocks()     ← esistente: rimuove blocchi thinking
  │
  ▼
process_all_tags()          ← esistente: processa tutti i tag
  │                             Ora trova tag completi grazie a Fix 4
  ▼
strip_orphaned_tags()       ← Fix 3: recupera contenuto da eventuali residui
  │
  ▼
telegram_safe_format()      ← Fix già applicato: adatta Markdown per Telegram
  │
  ▼
parsed_reply → invio a Telegram
```

## Verifica

1. Creare uno script di test che simula output LLM con tag troncati
2. Verificare che `close_orphaned_tags()` + `process_all_tags()` processi
   correttamente tutti i tag
3. Verificare che `strip_orphaned_tags()` recuperi il contenuto residuo
4. Verificare che il prompt generato da `build_tag_instructions()` sia
   completo e consistente con i tag registrati

## Files Modificati

1. `jarvis/llm_engine.py` — 1 riga (default 512 → 2048)
2. `jarvis/tag_processor.py` — ~30 righe (strip_orphaned_tags rewrite + close_orphaned_tags new)
3. `jarvis/prompt_builder.py` — ~20 righe (import + usa build_tag_instructions())
4. `jarvis/telegram_bot.py` — 1 riga (import + chiamata close_orphaned_tags)
