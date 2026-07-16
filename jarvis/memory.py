"""
Gestione della memoria episodica — Mem0 init e helper per estrazione ricordi.

La logica di parsing dei tag è stata delegata a tag_processor.py.
Questo modulo si occupa solo di: init Mem0, salvataggio, estrazione ricordi.
"""

import asyncio
import re
from functools import partial
from mem0 import Memory

from config import logger, MEM0_CONFIG, MEM0_STARTUP_DELAY, QDRANT_HOST, VECTOR_DB_VERSION
import state

MEMORY_COLLECTION = f"collateral_memories_{VECTOR_DB_VERSION}"
ENTITY_COLLECTION = f"{MEMORY_COLLECTION}_entities"


# ──────────────────────────────────────────────
# Entity extraction helpers (no spaCy dependency)
# ──────────────────────────────────────────────

def extract_entities(text: str) -> dict[str, str]:
    """Estrae entità dal testo usando pattern euristici (regex).

    Cattura entità in inglese e italiano: nomi propri, organizzazioni,
    prodotti/progetti, luoghi, tecnologie. Non richiede modelli spaCy.
    Restituisce dict {nome_entità: tipo_entità} con tipi compatibili spaCy.
    """
    entities: dict[str, str] = {}

    if not text or not isinstance(text, str):
        return entities

    # ── Filtra stopword / parole generiche da non candidare mai ──
    _stop_entities = {
        "User", "Users", "Project", "Projects", "File", "Files",
        "Code", "Data", "Info", "Information", "Details", "Detail",
        "List", "Lists", "Status", "Output", "Input", "Error", "Errors",
        "Message", "Messages", "Text", "Value", "Values", "Result",
        "Results", "Summary", "Update", "Updates", "Change", "Changes",
        "Fix", "Fixes", "Add", "Adds", "Remove", "Removes",
        "Please", "Hello", "Hi", "Ciao", "Buongiorno", "Buonasera",
        "Grazie", "Thanks", "Thank", "Yes", "No", "Ok", "OK",
        "Help", "Support", "Question", "Request", "Issue",
        "Hi", "Hey", "Salve", "Saluti",
    }

    # ── 1) Multi-word capitalized names (PERSON / ORG) ──
    #    Cattura ANY sequence of 2+ capitalized words (not just after sentence start)
    for match in re.finditer(
        r'\b([A-Z][a-zàèéìòùæœ]+(?:\s+[A-Z][a-zàèéìòùæœ]+)+)\b', text
    ):
        name = match.group(1).strip()
        if not (3 < len(name) < 80) or name in entities:
            continue
        if name in _stop_entities:
            continue

        # Skip entities that start with generic words (e.g. "User Mario Rossi")
        first_word = name.split()[0]
        if first_word in _stop_entities:
            continue

        # Organization indicators
        if re.search(
            r'\b(Team|Inc|Corp|Srl|Ltd|LLC|SA|SPA|GmbH|Company|Azienda|'
            r'Gruppo|Divisione|Department|Agency|Studio|Studios|Labs|'
            r'Foundation|Association|Authority|Club|Organisation|Organization|'
            r'Universit[àa]|University|Istituto|Institute|Scuola|School|'
            r'College|Politecnico|Facolt[àa]|Dipartimento)\b',
            name, re.IGNORECASE
        ):
            entities[name] = "ORG"
        elif name[0].isupper() and name.split()[-1][0].isupper():
            # Proper multi-word name → PERSON (e.g. "Mario Rossi")
            entities[name] = "PERSON"

    # ── 2) Acronyms 2-6 UPPER letters (standalone, not inside words) ──
    for match in re.finditer(r'(?<![A-Za-z])([A-Z]{2,6})(?![A-Za-z])', text):
        acronym = match.group(1)
        if 2 <= len(acronym) <= 6 and acronym not in entities and acronym not in _stop_entities:
            # Skip common English words that happen to be uppercase
            if acronym not in {"I", "A", "AN", "IN", "ON", "AT", "TO", "OF", "BY", "IS", "IT", "BE", "HE", "SHE", "WE", "THE", "AND", "OR", "FOR", "NOT", "ARE", "WAS", "HAD", "HAS", "CAN", "WILL", "MAY", "ALL", "ANY", "PER", "VIA", "DUE", "PER", "NON", "CHE"}:
                entities[acronym] = "ORG"

    # ── 3) CamelCase / PascalCase technical terms (Product/Project/Tech names) ──
    for match in re.finditer(r'\b([A-Z][a-z]{1,10}[A-Z][a-zA-Z0-9]{1,30})\b', text):
        term = match.group(1)
        if 3 < len(term) < 45 and term not in entities:
            if term not in _stop_entities:
                entities[term] = "PRODUCT"

    # ── 4) Geographic / Place entities ──
    #     Capitalized single word after Italian/English prepositions
    geo_preps = r'\b(a|ad|in|da|dalla|dal|nel|nella|verso|per|su|sul|sulla|'
    geo_preps += r'to|from|at|in|into|toward|near|by|for|of)\s+'
    geo_preps += r'([A-Z][a-zàèéìòù]{2,})\b'
    for match in re.finditer(geo_preps, text, re.IGNORECASE):
        place = match.group(2)
        if place not in entities and len(place) > 2 and place not in _stop_entities:
            entities[place] = "GPE"

    # ── 5) snake_case identifiers (technical references) ──
    for match in re.finditer(r'\b([a-z]+(?:_[a-z][a-z0-9]*){1,5})\b', text):
        term = match.group(1)
        if 4 < len(term) < 50 and term not in entities:
            entities[term] = "PRODUCT"

    # ── 6) Package / domain identifiers ──
    #     e.g. package_name, module.name, file-name.ext
    for match in re.finditer(r'\b([a-z][a-z0-9]*[-.][a-z][a-z0-9]+)\b', text):
        term = match.group(1)
        if 4 < len(term) < 50 and term not in entities:
            entities[term] = "PRODUCT"

    return entities


async def _ensure_entity_collection():
    """Crea la Qdrant collection per le entità se non esiste."""
    if not state.qdrant:
        logger.warning("Qdrant non disponibile, skip entity collection")
        return

    try:
        from qdrant_client import models as qdrant_models

        collections = await state.qdrant.get_collections()
        col_names = [c.name for c in collections.collections]

        if ENTITY_COLLECTION in col_names:
            return  # already exists

        await state.qdrant.create_collection(
            collection_name=ENTITY_COLLECTION,
            vectors_config=qdrant_models.VectorParams(
                size=768, distance=qdrant_models.Distance.COSINE
            ),
            optimizers_config=qdrant_models.OptimizersConfigDiff(
                default_segment_number=2
            ),
        )
        logger.info(f"✅ Created entity collection '{ENTITY_COLLECTION}'")
    except Exception as e:
        logger.warning(f"⚠️ Cannot create entity collection: {e}")


async def _store_entities_for_memory(mem_id: str, user_id: str, entities: dict[str, str]):
    """Salva le entità estratte nella Qdrant entity collection."""
    if not entities or not state.qdrant:
        return

    import hashlib
    import time as time_mod
    from qdrant_client import models as qdrant_models

    now = time_mod.time()

    for ent_name, ent_type in entities.items():
        # Deterministic ID from entity name (consistent across reindexes)
        ent_id = hashlib.md5(f"entity:{user_id}:{ent_name}".encode()).hexdigest()

        # Check if entity point already exists (to merge linked_memory_ids)
        existing_ids = []
        try:
            existing = await state.qdrant.retrieve(
                collection_name=ENTITY_COLLECTION,
                ids=[ent_id],
                with_payload=True,
                with_vectors=False,
            )
            if existing:
                old_payload = existing[0].payload or {}
                old_linked = old_payload.get("linked_memory_ids", []) or []
                existing_ids = [lid for lid in old_linked if lid != mem_id]
        except Exception:
            pass

        # Merge existing + new linked memory IDs
        all_linked = existing_ids + [mem_id]

        # Upsert via Qdrant client library
        await state.qdrant.upsert(
            collection_name=ENTITY_COLLECTION,
            points=[
                qdrant_models.PointStruct(
                    id=ent_id,
                    vector=[0.0] * 768,
                    payload={
                        "entity_name": ent_name,
                        "entity_type": ent_type,
                        "user_id": user_id,
                        "linked_memory_ids": all_linked,
                        "updated_at": now,
                    },
                )
            ],
        )


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
    """Salva un testo nella memoria Mem0 in modo sicuro, con metadati opzionali di progetto.

    infer=True: abilita l'estrazione LLM di fatti chiave + entity extraction (spaCy) per
    creare connessioni tra i nodi di memoria nel Graph Network. Le entità condivise tra
    memorie diverse vengono linkate nella entity store, permettendo al search
    di usare entity boosting per risultati più intelligenti.

    Oltre a Mem0, estrae entità localmente e le upserta direttamente nella Qdrant
    entity collection (``collateral_memories_{VECTOR_DB_VERSION}_entities``),
    cosicché il Memory Graph della dashboard sia sempre popolato.
    """
    if not text or not state.memory:
        return False

    mem_id = None
    try:
        loop = asyncio.get_running_loop()
        metadata = {"project": project} if project is not None else None
        add_func = partial(state.memory.add, text, user_id=user_id, metadata=metadata, infer=True)
        result = await loop.run_in_executor(state.mem0_executor, add_func)

        # Extract memory ID from mem0 result
        if isinstance(result, dict):
            mem_id = result.get("id") or result.get("memory_id")

        tag = f" [{project}]" if project else ""
        logger.debug(f"🧠 Memoria salvata{tag} ({len(text)} chars, user={user_id})")

        # Also extract entities and store directly in Qdrant entity collection
        if mem_id:
            entities = extract_entities(text)
            if entities:
                await _ensure_entity_collection()
                await _store_entities_for_memory(mem_id, user_id, entities)
                logger.debug(f"🧩 Entity linking: {len(entities)} entità per memoria {mem_id[:12]}")

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


async def reindex_graph_connections(user_id: str = "alfio_dev") -> dict:
    """
    Scansione retroattiva di tutti i nodi di memoria esistenti per ricreare
    le connessioni (entity linking) nel Memory Graph della dashboard.

    Legge le memorie direttamente da Qdrant, estrae le entità con
    ``extract_entities()`` e le upserta nella entity store
    (``collateral_memories_{VECTOR_DB_VERSION}_entities``).

    Args:
        user_id: ID utente di cui processare le memorie.

    Returns:
        dict con chiavi ``success``, ``total``, ``linked``, ``errors``.
    """
    if not state.qdrant:
        return {"success": False, "error": "Qdrant non inizializzato"}

    await _ensure_entity_collection()
    from qdrant_client import models as qdrant_models

    # Recupera TUTTE le memorie da Qdrant via scroll paginato
    memory_list = []
    try:
        all_points = []
        next_offset = None
        while True:
            points, next_offset = await state.qdrant.scroll(
                collection_name=MEMORY_COLLECTION,
                limit=1000,
                with_payload=True,
                with_vectors=False,
                scroll_filter=qdrant_models.Filter(
                    must=[qdrant_models.FieldCondition(
                        key="user_id",
                        match=qdrant_models.MatchValue(value=user_id),
                    )]
                ),
                offset=next_offset,
            )
            all_points.extend(points)
            if next_offset is None or not points:
                break
            if len(all_points) >= 5000:
                break

        for p in all_points:
            memory_list.append({
                "id": str(p.id),
                "payload": p.payload or {},
            })
    except Exception as e:
        return {"success": False, "error": str(e)}

    if not memory_list:
        msg = f"Nessuna memoria trovata per user={user_id}"
        logger.info(f"📭 {msg}")
        return {"success": True, "total": 0, "linked": 0, "errors": 0, "message": msg}

    logger.info(f"📊 Trovate {len(memory_list)} memorie per user={user_id}. Avvio entity linking...")

    linked = 0
    errors = 0
    skipped = 0

    for idx, entry in enumerate(memory_list, 1):
        mem_id = entry["id"]
        payload = entry["payload"]
        mem_text = payload.get("data", "") or payload.get("memory", "") or payload.get("text", "")

        if not mem_id or not mem_text:
            skipped += 1
            continue

        try:
            entities = extract_entities(mem_text)
            if entities:
                await _store_entities_for_memory(mem_id, user_id, entities)
                linked += 1
            else:
                skipped += 1
        except Exception as e:
            errors += 1
            logger.warning(f"Errore entity linking [{idx}/{len(memory_list)}] memoria {mem_id}: {e}")

        if idx % 50 == 0 or idx == len(memory_list):
            logger.info(f"  📎 [{idx}/{len(memory_list)}] linked={linked}, errors={errors}, skipped={skipped}")

    result = {
        "success": True,
        "total": len(memory_list),
        "linked": linked,
        "errors": errors,
        "skipped": skipped,
        "message": f"Entity linking completato: {linked}/{len(memory_list)} memorie collegate ({errors} errori, {skipped} saltate).",
    }
    logger.info(f"✅ {result['message']}")
    return result
