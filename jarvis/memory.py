"""
Gestione della memoria episodica — Mem0 init e helper per estrazione ricordi.

La logica di parsing dei tag è stata delegata a tag_processor.py.
Questo modulo si occupa solo di: init Mem0, salvataggio, estrazione ricordi.
"""

import asyncio
from functools import partial
from mem0 import Memory

from config import logger, MEM0_CONFIG, MEM0_STARTUP_DELAY
import state


async def init_mem0_delayed():
    """Inizializza Mem0 con un ritardo per permettere al loopback proxy di avviarsi e riprova in caso di fallimento."""
    await asyncio.sleep(MEM0_STARTUP_DELAY)
    while True:
        try:
            loop = asyncio.get_running_loop()
            state.memory = await loop.run_in_executor(state.mem0_executor, Memory.from_config, MEM0_CONFIG)
            logger.info("🧠 Mem0 collegato con successo al Loopback Proxy.")
            break
        except Exception as e:
            logger.error(f"⚠️ Inizializzazione Mem0 Fallita ({e}). Ritento tra 5 secondi...")
            await asyncio.sleep(5)
    # Warmup: forza la lazy init di spaCy e BM25 (10-30s sulla prima richiesta altrimenti)
    try:
        logger.info(f"🔄 Mem0 warmup (spaCy/BM25 lazy init)...")
        _ = await loop.run_in_executor(
            state.mem0_executor,
            partial(state.memory.search, query="warmup", filters={"user_id": "alfio_dev"}, limit=1)
        )
        logger.info(f"✅ Mem0 warmup completato")
    except Exception as e:
        logger.warning(f"⚠️ Mem0 warmup fallito (non critico): {e}")


def extract_memories(relevant_memories):
    """Estrae testo leggibile da risultati Mem0 (lista o dict)."""
    extracted = []
    try:
        if isinstance(relevant_memories, list):
            for m in relevant_memories:
                if isinstance(m, dict):
                    extracted.append(m.get("memory", m.get("text", str(m))))
                else:
                    extracted.append(m)
        elif isinstance(relevant_memories, dict):
            for m in relevant_memories.get("results", relevant_memories.get("memories", [])):
                if isinstance(m, dict):
                    extracted.append(m.get("memory", m.get("text", str(m))))
                else:
                    extracted.append(m)
    except Exception as e:
        logger.warning(f"Errore extract_memories: {e}")
    return "\n".join(extracted)


async def save_to_memory(text, user_id="alfio_dev", project=None):
    """Salva un testo nella memoria Mem0 in modo sicuro, con metadati opzionali di progetto."""
    if not text or not state.memory:
        return False
    try:
        loop = asyncio.get_running_loop()
        metadata = {"project": project} if project is not None else None
        add_func = partial(state.memory.add, text, user_id=user_id, metadata=metadata, infer=False)
        await loop.run_in_executor(state.mem0_executor, add_func)
        tag = f" [{project}]" if project else ""
        logger.debug(f"🧠 Memoria salvata{tag} ({len(text)} chars, user={user_id})")
        return True
    except Exception as e:
        logger.warning(f"Errore save_to_memory: {e}")
        return False


async def process_response_tags(response_text, user_id="alfio_dev", project=None, model_family=None):
    """
    Post-processa la risposta del LLM: delega a tag_processor.process_all_tags().
    Mantiene la stessa firma per retrocompatibilità con main.py e altri chiamanti.
    
    Args:
        model_family: Famiglia modello per filtrare thinking patterns.
                      None = auto-detect da MODEL_PROFILE, "all" = legacy (tutti i pattern)
    """
    if not response_text:
        return ""

    # Auto-detect model_family se non specificato
    if model_family is None:
        try:
            from config import MODEL_PROFILE
            model_family = MODEL_PROFILE.family
        except Exception:
            model_family = "all"

    from tag_processor import process_all_tags, TagContext
    ctx = TagContext(user_id=user_id, project=project)
    cleaned, feedback = await process_all_tags(response_text, ctx, model_family=model_family)

    if feedback:
        for msg in feedback:
            logger.info(f"📢 Tag feedback: {msg}")

    return cleaned
