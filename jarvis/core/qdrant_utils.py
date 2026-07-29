"""
Helper Qdrant condivisi — sanitizzazione, creazione collezioni, operazioni comuni.
Consolida pattern Qdrant sparsi in rag/engine.py, routes/projects.py, lifecycle.py.
"""

import re
import logging
from typing import Optional

from core.config import EMBEDDING_DIMS, VECTOR_DB_VERSION
import core.state as state

logger = logging.getLogger(__name__)


def sanitize_project_name(name: str) -> str:
    """Previene path-traversal e crea un nome valido per collezioni Qdrant.
    
    Sostituisce tutti i caratteri non alfanumerici con underscore.
    Allineato tra rag/engine.py e routes/projects.py per consistenza.
    """
    return re.sub(r'[^a-zA-Z0-9_]', '_', name)


def get_project_col_name(project_name: str) -> str:
    """Restituisce il nome della collezione Qdrant per un dato project name."""
    sanitized = sanitize_project_name(project_name)
    return f"collateral_docs_{sanitized}_{VECTOR_DB_VERSION}"


def get_file_profile_col_name() -> str:
    return f"file_profiles_{VECTOR_DB_VERSION}"


async def ensure_collection(
    collection_name: str,
    dims: int = EMBEDDING_DIMS,
) -> bool:
    """Crea una collezione Qdrant se non esiste già. Return True se creata."""
    try:
        await state.qdrant.get_collection(collection_name)
        return False  # esiste già
    except Exception:
        from qdrant_client.models import VectorParams, Distance
        await state.qdrant.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=dims, distance=Distance.COSINE),
        )
        state.created_collections.add(collection_name)
        logger.info("📦 Creata collezione Qdrant: %s (dims=%d)", collection_name, dims)
        return True
