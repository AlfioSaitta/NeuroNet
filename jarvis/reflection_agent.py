import logging
import asyncio
import state
from llm_engine import engine
from config import LLM_OPTIONS

logger = logging.getLogger("chameleon.reflection")

async def nightly_memory_consolidation():
    logger.info("Avvio del job notturno di Self-Reflection e Memory Consolidation...")
    if getattr(state, "memory", None) is None:
        logger.warning("Mem0 non inizializzato. Skip consolidamento.")
        return
        
    try:
        # Recupera tutte le memorie usando la sintassi Mem0
        loop = asyncio.get_running_loop()
        from functools import partial
        all_memories = await loop.run_in_executor(state.mem0_executor, partial(state.memory.get_all, user_id="alfio_dev"))
        
        # Gestione compatibilità risultato (lista diretta vs dict con 'results' / 'memories')
        if isinstance(all_memories, dict):
            memory_list = all_memories.get("results", all_memories.get("memories", []))
        else:
            memory_list = all_memories
            
        if not memory_list:
            return
            
        memory_texts = []
        ids_to_delete = []
        for m in memory_list:
            if isinstance(m, dict):
                text = m.get("memory", m.get("text", ""))
                m_id = m.get("id")
            else:
                text = str(m)
                m_id = None
            if text:
                memory_texts.append(text)
                if m_id:
                    ids_to_delete.append(m_id)
                
        if len(memory_texts) < 5:
            logger.info("Troppe poche memorie per il consolidamento (min 5), skip.")
            return
            
        combined_text = "\n- ".join(memory_texts)
        prompt = f"""Sei un processo di memoria subconscia. Analizza i seguenti fatti appresi durante la giornata e condensali in poche frasi essenziali che riassumono le preferenze dell'utente, le abitudini, il contesto e i progetti in corso. Elimina i dettagli effimeri e inutili (es. saluti, domande passeggere). Estrai solo l'essenza utile a lungo termine.

Fatti grezzi:
- {combined_text}

Rispondi SOLO con i fatti condensati, formattati in un elenco puntato. Nessuna introduzione."""
        
        messages = [{"role": "user", "content": prompt}]
        async with state.llm_semaphore:
            response = await engine.generate_chat(
                messages,
                tools=None,
                options=LLM_OPTIONS,
                stream=False
            )

        if "error" in response:
            raise RuntimeError(response["error"])

        consolidated = response["choices"][0]["message"].get("content", "").strip()
        
        if consolidated:
            # Delete old episodic memories
            for m_id in ids_to_delete:
                try:
                    await loop.run_in_executor(state.mem0_executor, partial(state.memory.delete, memory_id=m_id))
                except Exception as e:
                    logger.warning(f"Errore cancellazione memoria {m_id}: {e}")
            
            # Add consolidated one
            await loop.run_in_executor(state.mem0_executor, partial(state.memory.add, f"Sintesi Profilo Utente: \n{consolidated}", user_id="alfio_dev", infer=False))
            logger.info("✅ Consolidamento memoria completato con successo.")
            
    except Exception as e:
        logger.error(f"Errore durante self-reflection: {e}")
