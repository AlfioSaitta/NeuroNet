"""
Cache semantica e Web Knowledge persistence per RAG.
Estratto da rag.py per modularizzazione.
"""

import uuid
import hashlib
import asyncio
import logging

from qdrant_client.models import PointStruct, VectorParams, Distance, Filter, FieldCondition, MatchValue

import state
from config import (
    SEMANTIC_CACHE_THRESHOLD,
    EMBEDDING_DIMS,
    VECTOR_DB_VERSION,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
)

logger = logging.getLogger(__name__)


# ==============================================================================
# CACHE SEMANTICA
# ==============================================================================

async def semantic_cache_search(prompt: str, threshold: float = SEMANTIC_CACHE_THRESHOLD):
    try:
        from rag import get_embedding  # lazy: evita circular import
        vector = await get_embedding(prompt, priority=0, is_query=True)
        if not vector: return None
        res = await state.qdrant.query_points(
            collection_name=f"semantic_cache_{VECTOR_DB_VERSION}",
            query=vector,
            limit=1,
            score_threshold=threshold,
            with_payload=True
        )
        if res and res.points:
            return res.points[0].payload.get("response")
    except Exception as e:
        logger.warning(f"Errore silenziato: {e}")
    return None


async def semantic_cache_store(prompt: str, response: str):
    try:
        from rag import get_embedding  # lazy: evita circular import
        vector = await get_embedding(prompt, is_query=True)
        if vector:
            await state.qdrant.upsert(
                collection_name=f"semantic_cache_{VECTOR_DB_VERSION}",
                points=[PointStruct(
                    id=str(uuid.uuid4()),
                    vector=vector,
                    payload={"prompt": prompt, "response": response}
                )]
            )
    except Exception as e:
        logger.warning(f"Errore silenziato: {e}")


async def semantic_cache_clear():
    """Cancella tutta la cache semantica ricreando la collezione."""
    try:
        col_name = f"semantic_cache_{VECTOR_DB_VERSION}"
        try:
            await state.qdrant.delete_collection(col_name)
        except Exception:
            pass  # Non esiste ancora
        await state.qdrant.create_collection(
            collection_name=col_name,
            vectors_config=VectorParams(size=EMBEDDING_DIMS, distance=Distance.COSINE)
        )
        logger.info(f"🗑️ Cache semantica resettata: {col_name}")
        return True
    except Exception as e:
        logger.warning(f"Errore semantic_cache_clear: {e}")
        return False


# ==============================================================================
# WEB KNOWLEDGE PERSISTENCE (Qdrant)
# ==============================================================================

async def ensure_web_knowledge_collection():
    """Crea la collezione web_knowledge in Qdrant se non esiste."""
    col_name = f"web_knowledge_{VECTOR_DB_VERSION}"
    if col_name not in state.created_collections:
        async with state.state_lock:
            if col_name not in state.created_collections:
                try:
                    exists = await state.qdrant.collection_exists(collection_name=col_name)
                    if not exists:
                        await state.qdrant.create_collection(
                            collection_name=col_name,
                            vectors_config=VectorParams(size=EMBEDDING_DIMS, distance=Distance.COSINE)
                        )
                except Exception as e:
                    logger.warning(f"Errore creazione web knowledge collection: {e}")
                state.created_collections.add(col_name)


async def save_web_knowledge(query: str, context: str, sources: list[str] | None = None):
    """Salva conoscenza da web in Qdrant per future ricerche semantiche."""
    try:
        await ensure_web_knowledge_collection()
        col_name = f"web_knowledge_{VECTOR_DB_VERSION}"

        try:
            await state.qdrant.delete(
                collection_name=col_name,
                points_selector=Filter(must=[FieldCondition(key="query_hash", match=MatchValue(value=hashlib.md5(query.encode()).hexdigest()[:16]))])
            )
        except Exception:
            pass

        chunks = [context[i:i+CHUNK_SIZE] for i in range(0, len(context), CHUNK_SIZE - CHUNK_OVERLAP)]
        valid_chunks = [c for c in chunks if len(c.strip()) >= 50]
        if not valid_chunks:
            return

        from rag import get_embedding  # lazy: evita circular import
        texts_to_embed = [f"QUERY: {query} | WEB: {chunk}" for chunk in valid_chunks]
        vectors = []
        for i in range(0, len(texts_to_embed), 3):
            batch = texts_to_embed[i:i+3]
            batch_vectors = await get_embedding(batch)
            vectors.extend(batch_vectors)
            await asyncio.sleep(0)

        points = []
        for chunk, vector in zip(valid_chunks, vectors):
            if vector:
                points.append(PointStruct(
                    id=str(uuid.uuid4()),
                    vector=vector,
                    payload={
                        "query": query[:300],
                        "query_hash": hashlib.md5(query.encode()).hexdigest()[:16],
                        "text": chunk,
                        "sources": sources[:5] if sources else [],
                        "type": "web_knowledge"
                    }
                ))

        if points:
            await state.qdrant.upsert(collection_name=col_name, points=points)
            logger.info(f"🌐 Web knowledge salvata in Qdrant: '{query[:60]}...' ({len(points)} chunks)")
    except Exception as e:
        logger.warning(f"Errore save_web_knowledge: {e}")


async def search_web_knowledge(query: str) -> str:
    """Cerca nella knowledge base web Qdrant. Ritorna contesto se trovato, stringa vuota altrimenti."""
    try:
        col_name = f"web_knowledge_{VECTOR_DB_VERSION}"
        try:
            exists = await state.qdrant.collection_exists(collection_name=col_name)
            if not exists:
                return ""
        except Exception:
            return ""

        from rag import get_embedding  # lazy: evita circular import
        vector = await get_embedding(query, is_query=True)
        if not vector:
            return ""

        res = await state.qdrant.query_points(
            collection_name=col_name,
            query=vector,
            limit=3,
            score_threshold=0.35,
            with_payload=True
        )
        if res and res.points:
            results = []
            for p in res.points:
                text = p.payload.get("text", "")
                if text:
                    results.append(text)
            if results:
                return "\n\n---\n\n".join(results)
        return ""
    except Exception as e:
        logger.warning(f"Errore search_web_knowledge: {e}")
        return ""
