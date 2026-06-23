"""
Gestione della memoria episodica — Mem0 init e helper per estrazione ricordi.
"""

import asyncio
import re
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
        add_func = partial(state.memory.add, text, user_id=user_id, metadata=metadata)
        await loop.run_in_executor(state.mem0_executor, add_func)
        tag = f" [{project}]" if project else ""
        logger.debug(f"🧠 Memoria salvata{tag} ({len(text)} chars, user={user_id})")
        return True
    except Exception as e:
        logger.warning(f"Errore save_to_memory: {e}")
        return False


_STRIP_TAGS_RE = re.compile(r"</?(?:MEMORY|SCHEDULE|NOTIFY_ONCE|NOTIFYONCE|NOTIFY_IN|NOTIFYIN|SSH|TODO_ADD|TODO_DONE)>", re.IGNORECASE)
_MEMORY_TAG_RE = re.compile(r"<MEMORY>(.*?)</MEMORY>", re.DOTALL | re.IGNORECASE)


def strip_action_tags(text):
    """Rimuove tutti i tag d'azione XML dal testo (MEMORY, SCHEDULE, TODO, etc.)"""
    return _STRIP_TAGS_RE.sub("", text).strip()


async def process_response_tags(response_text, user_id="alfio_dev", project=None):
    """Post-processa la risposta del LLM: salva i tag <MEMORY> in Mem0,
    rimuove tutti i tag d'azione, restituisce il testo pulito."""
    if not response_text:
        return ""

    # Salva in Mem0 tutti i tag <MEMORY>
    for match in _MEMORY_TAG_RE.finditer(response_text):
        text = match.group(1).strip()
        if text:
            await save_to_memory(text, user_id, project=project)
            logger.info(f"🧠 MEMORY tag impresso{ ' ['+project+']' if project else '' }: {text[:100]}")

    # Rimuovi tutti i tag d'azione
    return strip_action_tags(response_text)
