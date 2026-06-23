import json
import logging
import asyncio
import state

logger = logging.getLogger("chameleon.backup")

async def export_memory_to_json(filepath="/home/alfio/Projects/ai-ecosystem/mem0-proxy/memory_backup.json"):
    if not getattr(state, "memory", None):
        return False, "Mem0 non inizializzato."
        
    try:
        loop = asyncio.get_running_loop()
        from functools import partial
        all_memories = await loop.run_in_executor(state.mem0_executor, partial(state.memory.get_all, user_id="alfio_dev"))
        if isinstance(all_memories, dict):
            memory_list = all_memories.get("results", all_memories.get("memories", []))
        else:
            memory_list = all_memories
            
        with open(filepath, 'w') as f:
            json.dump(memory_list, f, indent=4)
            
        return True, filepath
    except Exception as e:
        logger.error(f"Errore export memoria: {e}")
        return False, str(e)

async def import_memory_from_json(filepath):
    if not getattr(state, "memory", None):
        return False, "Mem0 non inizializzato."
        
    try:
        with open(filepath, 'r') as f:
            memory_list = json.load(f)
            
        if not memory_list:
            return False, "File JSON vuoto o formato non valido."
            
        # Pulisce la memoria attuale
        loop = asyncio.get_running_loop()
        from functools import partial
        existing = await loop.run_in_executor(state.mem0_executor, partial(state.memory.get_all, user_id="alfio_dev"))
        if isinstance(existing, dict):
            existing_list = existing.get("results", existing.get("memories", []))
        else:
            existing_list = existing
            
        for m in existing_list:
            m_id = m.get("id") if isinstance(m, dict) else None
            if m_id:
                await loop.run_in_executor(state.mem0_executor, partial(state.memory.delete, memory_id=m_id))
                
        # Importa la nuova memoria
        count = 0
        for m in memory_list:
            text = m.get("memory", m.get("text", "")) if isinstance(m, dict) else str(m)
            if text:
                await loop.run_in_executor(state.mem0_executor, partial(state.memory.add, text, user_id="alfio_dev", infer=False))
                count += 1
                
        return True, f"Ripristinati {count} ricordi con successo."
    except Exception as e:
        logger.error(f"Errore import memoria: {e}")
        return False, str(e)
