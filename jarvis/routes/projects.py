"""
Projects management API — list, reindex, delete, register RAG projects.

All endpoints under /api/projects with JWT auth via Depends().
Uses state.qdrant methods (not REST API) per QDRANT_HOST=="local" safety.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from auth import require_auth, require_admin
from config import WORKSPACE_PROJECTS, EXTERNAL_PROJECTS, HOST_FS_PREFIX, SYNAPTIQ_ENABLED, parse_external_projects
from dashboard import _persist_env
from rag import (
    list_rag_projects, get_project_col_name, get_project_path,
    get_project_last_indexed, ingest_local_documents, _registered_project_paths,
    _save_state_unsafe,
)
import state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])


# ── Helpers ────────────────────────────────────────────────────────────────

def _resolve_register_path(raw_path: str) -> str | None:
    """Tenta di risolvere il path considerando HOST_FS_PREFIX (Docker)."""
    if os.path.isdir(raw_path):
        return raw_path
    if HOST_FS_PREFIX:
        prefixed = os.path.join(HOST_FS_PREFIX, raw_path.lstrip("/"))
        if os.path.isdir(prefixed):
            return prefixed
    return None


def _sanitize_name(name: str) -> str:
    """Previene path-traversal: sostituisce con underscore tutti i caratteri non alfanumerici.
    Allineato a get_project_col_name() in rag.py per consistenza."""
    return re.sub(r'[^a-zA-Z0-9_]', '_', name)


async def _get_project_stats(name: str) -> dict[str, Any]:
    """Recupera stats Qdrant per un progetto usando AsyncQdrantClient (non REST)."""
    col_name = get_project_col_name(name)
    points = 0
    dims = None
    status = "unknown"
    try:
        col_info = await state.qdrant.get_collection(col_name)
        pts = col_info.points_count or 0
        points = pts
        # Estrai dimensione dal config
        vc = col_info.config.params.vectors if col_info.config and col_info.config.params else None
        if isinstance(vc, dict):
            first = list(vc.values())[0]
            dims = first.size if hasattr(first, 'size') else None
        elif hasattr(vc, 'size'):
            dims = vc.size
        status = "green" if pts > 0 else "yellow"
    except Exception:
        status = "red"
    return {"points": points, "dimension": dims, "status": status}


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("")
async def list_projects(user: dict = Depends(require_auth)):
    """Lista progetti RAG con metadati. Utente normale vede solo i suoi (ACL)."""
    project_names = await list_rag_projects(user)
    results = []
    for name in project_names:
        col_name = get_project_col_name(name)
        stats = await _get_project_stats(name)
        last_idx = get_project_last_indexed(name)
        path = get_project_path(name)

        # Determina source
        if path and path in WORKSPACE_PROJECTS:
            source = "workspace"
        elif path:
            source = "external"
        else:
            source = "orphan"

        results.append({
            "name": name,
            "collection_name": col_name,
            "points": stats["points"],
            "dimension": stats["dimension"],
            "last_indexed": last_idx,
            "path": path,
            "source": source,
            "status": stats["status"],
        })

    return {"projects": results}


@router.get("/available")
async def available_projects(_: dict = Depends(require_admin)):
    """Scansiona WORKSPACE_DIR per progetti non ancora indicizzati."""
    from config import WORKSPACE_DIR
    candidates = []

    if WORKSPACE_DIR and os.path.isdir(WORKSPACE_DIR):
        existing_projects = await list_rag_projects()
        existing_lower = {p.lower() for p in existing_projects}
        try:
            for d in sorted(os.listdir(WORKSPACE_DIR)):
                full_path = os.path.join(WORKSPACE_DIR, d)
                if os.path.isdir(full_path) and not d.startswith('.'):
                    # Verifica se ha collezione Qdrant
                    if d.lower() not in existing_lower:
                        candidates.append({
                            "name": d,
                            "path": full_path,
                            "source": "workspace",
                        })
        except OSError as e:
            raise HTTPException(500, f"Error scanning {WORKSPACE_DIR}: {e}")

    return {"candidates": candidates}


@router.get("/{name}")
async def get_project(name: str, _: dict = Depends(require_admin)):
    """Dettaglio di un progetto specifico."""
    name = _sanitize_name(name)
    col_name = get_project_col_name(name)
    stats = await _get_project_stats(name)
    last_idx = get_project_last_indexed(name)
    path = get_project_path(name)

    # Conta files in rag_state per questo progetto
    prefix = name.replace(' ', '_').replace('-', '_') + "/"
    files_count = sum(1 for rp in state.rag_state if rp.startswith(prefix))

    if path and path in WORKSPACE_PROJECTS:
        source = "workspace"
    elif path:
        source = "external"
    else:
        source = "orphan"

    return {
        "name": name,
        "collection_name": col_name,
        "points": stats["points"],
        "dimension": stats["dimension"],
        "last_indexed": last_idx,
        "files_count": files_count,
        "path": path,
        "source": source,
        "status": stats["status"],
    }


@router.post("/reindex")
async def reindex_project(body: dict, _: dict = Depends(require_admin)):
    """Re-indicizza un progetto specifico."""
    name = body.get("name", "")
    if not name:
        raise HTTPException(400, "Missing 'name' in body")
    name = _sanitize_name(name)

    project_path = get_project_path(name)
    if not project_path:
        # Controlla se la collezione Qdrant esiste
        try:
            await state.qdrant.get_collection(get_project_col_name(name))
            raise HTTPException(404,
                "Collection exists but project path not found. "
                "Use DELETE to remove collection."
            )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(404, f"Project '{name}' not found")

    if not os.path.isdir(project_path):
        raise HTTPException(400, f"Project path {project_path} not accessible")

    # Lancia ingest in background
    task = asyncio.create_task(ingest_local_documents(single_project_path=project_path))
    state.background_tasks.add(task)
    task.add_done_callback(state.background_tasks.discard)

    # Triggera Synaptiq analysis per questo progetto
    synaptiq_triggered = False
    if SYNAPTIQ_ENABLED:
        try:
            from synaptiq_engine import synaptiq_engine
            if synaptiq_engine and synaptiq_engine.is_initialized:
                # Analisi immediata (non debounced) — usa _analyze_one
                # che aggiorna _last_project_path e serializza accessi
                async def _trigger_synaptiq():
                    try:
                        await synaptiq_engine._analyze_one(project_path)
                    except Exception as e:
                        logger.warning("Synaptiq reindex analyze fallito: %s", e)
                st = asyncio.create_task(_trigger_synaptiq())
                state.background_tasks.add(st)
                st.add_done_callback(state.background_tasks.discard)
                synaptiq_triggered = True
        except Exception as e:
            logger.debug("Synaptiq trigger skipped: %s", e)

    msg = f"Re-index started for {name}"
    if synaptiq_triggered:
        msg += " (Synaptiq analysis triggered)"

    return {"status": "ok", "message": msg}


@router.delete("/{name}/collection")
async def delete_project_collection(name: str, _: dict = Depends(require_admin)):
    """Elimina la collezione Qdrant di un progetto. NON modifica .env."""
    name = _sanitize_name(name)
    col_name = get_project_col_name(name)

    try:
        await state.qdrant.get_collection(col_name)
    except Exception:
        raise HTTPException(404, f"Collection '{col_name}' not found")

    # Cancella via AsyncQdrantClient (funziona in local e remote mode)
    await state.qdrant.delete_collection(col_name)

    # Pulisce rag_state per i file del progetto
    prefix = name.replace(' ', '_').replace('-', '_') + "/"
    async with state.state_lock:
        keys_to_delete = [rp for rp in state.rag_state if rp.startswith(prefix)]
        for k in keys_to_delete:
            del state.rag_state[k]
        _save_state_unsafe()

    # Rimuove dal runtime cache se presente
    if name in _registered_project_paths:
        del _registered_project_paths[name]

    return {"status": "ok", "message": f"Collection for {name} deleted"}


@router.post("/register")
async def register_project(body: dict, _: dict = Depends(require_admin)):
    """Registra un nuovo progetto: path in EXTERNAL_PROJECTS, crea collezione, avvia ingest."""
    raw_path = body.get("path", "")
    name = body.get("name", "")
    if not raw_path or not name:
        raise HTTPException(400, "Missing 'path' or 'name' in body")
    name = _sanitize_name(name)

    # Risolvi il path
    resolved = _resolve_register_path(raw_path)
    if not resolved:
        msg = (
            f"Path {raw_path} not accessible in container."
        )
        if HOST_FS_PREFIX:
            msg += (
                f" On Docker, the host path must be mounted and accessible "
                f"via HOST_FS_PREFIX ({HOST_FS_PREFIX}/...). "
                f"Add the mount to docker-compose.worker.yml first."
            )
        raise HTTPException(400, msg)

    # Verifica duplicati
    name_lower = name.lower()
    for proj_path in WORKSPACE_PROJECTS:
        if os.path.basename(proj_path).lower() == name_lower:
            raise HTTPException(400, f"Project '{name}' already registered in WORKSPACE")
    for ep_path in parse_external_projects():
        if os.path.basename(ep_path).lower() == name_lower:
            raise HTTPException(400, f"Project '{name}' already registered in EXTERNAL_PROJECTS")
    existing_projects = await list_rag_projects()
    if name in existing_projects or name_lower in {p.lower() for p in existing_projects}:
        raise HTTPException(400, f"Qdrant collection for '{name}' already exists")

    # Aggiorna .env
    current_ext = EXTERNAL_PROJECTS.strip()
    new_value = f"{current_ext},{raw_path}:{name}" if current_ext else f"{raw_path}:{name}"
    persisted = _persist_env("EXTERNAL_PROJECTS", new_value)
    if not persisted:
        raise HTTPException(500, "Failed to persist configuration to .env")

    # Crea collezione Qdrant se non esiste
    col_name = get_project_col_name(name)
    try:
        await state.qdrant.get_collection(col_name)
    except Exception:
        from qdrant_client.models import VectorParams, Distance
        from config import EMBEDDING_DIMS
        await state.qdrant.create_collection(
            collection_name=col_name,
            vectors_config=VectorParams(size=EMBEDDING_DIMS, distance=Distance.COSINE)
        )
        state.created_collections.add(col_name)

    # Runtime cache: rende il progetto trovabile da get_project_path() subito
    _registered_project_paths[name] = resolved

    # Avvia ingest in background
    task = asyncio.create_task(ingest_local_documents(single_project_path=resolved))
    state.background_tasks.add(task)
    task.add_done_callback(state.background_tasks.discard)

    return {
        "status": "ok",
        "message": f"Project {name} registered and indexing started",
        "needs_restart": True,
    }


@router.get("/{name}/synaptiq/graph")
async def get_synaptiq_project_graph(name: str, _: dict = Depends(require_admin)):
    """Restituisce nodi e relazioni del grafo Synaptiq per visualizzazione Sigma.js.

    Poiché LadybugBackend.bulk_load() sovrascrive l'intero DB a ogni analisi,
    il grafo contiene sempre l'ultimo progetto analizzato. Se non corrisponde
    al progetto ``name`` richiesto, la risposta include ``project_match: false``
    e un messaggio di avviso.
    """
    if not SYNAPTIQ_ENABLED:
        return JSONResponse(
            content={"error": "Synaptiq non abilitato", "nodes": [], "edges": []},
            status_code=400,
        )

    try:
        from synaptiq_engine import synaptiq_engine
    except ImportError:
        return JSONResponse(
            content={"error": "Synaptiq non installato", "nodes": [], "edges": []},
            status_code=400,
        )

    if not synaptiq_engine or not synaptiq_engine.is_initialized:
        return JSONResponse(
            content={"error": "Synaptiq non inizializzato", "nodes": [], "edges": []},
            status_code=400,
        )

    graph_data = await synaptiq_engine.get_graph_data()
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    g_project = graph_data.get("project_name", "") or ""

    # Caso 1: nessun dato — analisi mai eseguita
    if not nodes:
        return {
            "nodes": [],
            "edges": [],
            "project_path": "",
            "project_name": "",
            "project_match": False,
            "warning": (
                f"Synaptiq non ha ancora dati. "
                f"L'analisi iniziale è in corso o non è mai partita per '{name}'. "
                f"Usa l'endpoint POST /api/synaptiq/analyze per triggerarla."
            ),
        }

    # Caso 2: dati presenti — verifica corrispondenza progetto
    project_match = g_project.lower() == name.lower()
    result = {
        "nodes": nodes,
        "edges": edges,
        "project_path": graph_data.get("project_path", ""),
        "project_name": g_project,
        "project_match": project_match,
    }

    if not project_match:
        result["warning"] = (
            f"Synaptiq contiene il grafo di '{g_project}', "
            f"non '{name}'. Re-index per aggiornare."
        )

    return result
