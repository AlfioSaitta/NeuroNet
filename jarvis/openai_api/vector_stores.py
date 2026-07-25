"""Vector Stores + Batch + Fine-tuning endpoints — OpenAI-compatible."""
from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from .state import get_db

router = APIRouter()


# ── Request models ────────────────────────────────────────────────────────────

class VectorStoreRequest(BaseModel):
    name: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    expires_after: Optional[Dict[str, Any]] = None
    model_config = ConfigDict(extra="allow")


class BatchRequest(BaseModel):
    input_file_id: Optional[str] = None
    endpoint: Optional[str] = None
    completion_window: Optional[str] = "24h"
    metadata: Optional[Dict[str, Any]] = None
    model_config = ConfigDict(extra="allow")


class FineTuningJobRequest(BaseModel):
    model: str
    training_file: Optional[str] = None
    validation_file: Optional[str] = None
    hyperparameters: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    model_config = ConfigDict(extra="allow")


# ── Response helpers ──────────────────────────────────────────────────────────

def _vs_response(row: dict) -> dict:
    return {
        "id": row["id"],
        "object": "vector_store",
        "created_at": row["created_at"] // 1000,
        "name": row.get("name"),
        "usage_bytes": row.get("usage_bytes", 0),
        "file_counts": row.get("file_counts", {}),
        "status": row.get("status", "active"),
        "metadata": row.get("metadata", {}),
        "expires_after": row.get("expires_after"),
        "last_active_at": (row["last_active_at"] // 1000) if row.get("last_active_at") else None,
    }


def _vsf_response(row: dict) -> dict:
    return {
        "id": row["id"],
        "object": "vector_store.file",
        "created_at": row["created_at"] // 1000,
        "vector_store_id": row["vector_store_id"],
        "file_id": row["file_id"],
        "status": "completed",
    }


def _batch_response(row: dict) -> dict:
    return {
        "id": row["id"],
        "object": "batch",
        "created_at": row["created_at"] // 1000,
        "input_file_id": row.get("input_file_id"),
        "endpoint": row.get("endpoint"),
        "completion_window": row.get("completion_window"),
        "status": row.get("status", "validating"),
        "request_counts": row.get("request_counts", {}),
        "metadata": row.get("metadata", {}),
    }


def _ftj_response(row: dict) -> dict:
    return {
        "id": row["id"],
        "object": "fine_tuning.job",
        "created_at": row["created_at"] // 1000,
        "model": row["model"],
        "training_file": row.get("training_file"),
        "validation_file": row.get("validation_file"),
        "hyperparameters": row.get("hyperparameters", {}),
        "status": row.get("status", "validating"),
        "trained_tokens": row.get("trained_tokens", 0),
        "result_files": row.get("result_files", []),
        "metadata": row.get("metadata", {}),
    }


# ── Vector Store endpoints ────────────────────────────────────────────────────


@router.post("/v1/vector_stores", status_code=201)
async def create_vector_store(body: VectorStoreRequest):
    db = await get_db()
    row = await db.create_vector_store(
        name=body.name,
        metadata=body.metadata,
        expires_after=body.expires_after,
    )
    return _vs_response(row)


@router.get("/v1/vector_stores")
async def list_vector_stores(
    limit: int = 20,
    after: Optional[str] = None,
    before: Optional[str] = None,
    order: str = "desc",
):
    db = await get_db()
    rows = await db.list_vector_stores(
        limit=limit, after=after, before=before, order=order,
    )
    has_more = len(rows) > limit
    data = [_vs_response(r) for r in rows[:limit]]
    return {"object": "list", "data": data, "first_id": data[0]["id"] if data else None,
            "last_id": data[-1]["id"] if data else None, "has_more": has_more}


@router.get("/v1/vector_stores/{store_id}")
async def retrieve_vector_store(store_id: str):
    db = await get_db()
    row = await db.get_vector_store(store_id)
    if not row:
        raise HTTPException(404, f"Vector store {store_id} not found")
    return _vs_response(row)


@router.post("/v1/vector_stores/{store_id}")
async def modify_vector_store(store_id: str, body: VectorStoreRequest):
    db = await get_db()
    existing = await db.get_vector_store(store_id)
    if not existing:
        raise HTTPException(404, f"Vector store {store_id} not found")
    updates: dict = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.metadata is not None:
        updates["metadata"] = body.metadata
    if updates:
        row = await db.update_vector_store(store_id, updates)
    else:
        row = existing
    return _vs_response(row)


@router.delete("/v1/vector_stores/{store_id}")
async def delete_vector_store(store_id: str):
    db = await get_db()
    deleted = await db.delete_vector_store(store_id)
    if not deleted:
        raise HTTPException(404, f"Vector store {store_id} not found")
    return {"id": store_id, "object": "vector_store", "deleted": True}


# ── Vector Store File endpoints ───────────────────────────────────────────────


@router.post("/v1/vector_stores/{store_id}/files", status_code=201)
async def add_vector_store_file(store_id: str, body: Dict[str, Any]):
    file_id = body.get("file_id")
    if not file_id:
        raise HTTPException(400, "file_id is required")
    db = await get_db()
    store = await db.get_vector_store(store_id)
    if not store:
        raise HTTPException(404, f"Vector store {store_id} not found")
    file_row = await db.get_file(file_id)
    if not file_row:
        raise HTTPException(404, f"File {file_id} not found")
    row = await db.add_vector_store_file(
        vector_store_id=store_id, file_id=file_id,
    )
    return _vsf_response(row)


@router.get("/v1/vector_stores/{store_id}/files")
async def list_vector_store_files(
    store_id: str,
    limit: int = 20,
    after: Optional[str] = None,
    before: Optional[str] = None,
    order: str = "desc",
):
    db = await get_db()
    store = await db.get_vector_store(store_id)
    if not store:
        raise HTTPException(404, f"Vector store {store_id} not found")
    rows = await db.list_vector_store_files(
        store_id, limit=limit, after=after, before=before, order=order,
    )
    has_more = len(rows) > limit
    data = [_vsf_response(r) for r in rows[:limit]]
    return {"object": "list", "data": data, "first_id": data[0]["id"] if data else None,
            "last_id": data[-1]["id"] if data else None, "has_more": has_more}


@router.get("/v1/vector_stores/{store_id}/files/{file_id}")
async def retrieve_vector_store_file(store_id: str, file_id: str):
    """Retrieve a file record inside a vector store.

    The OpenAI spec does not define this exact endpoint, but we expose it
    for symmetry with the file listing workflow.
    """
    db = await get_db()
    store = await db.get_vector_store(store_id)
    if not store:
        raise HTTPException(404, f"Vector store {store_id} not found")
    file_row = await db.get_file(file_id)
    if not file_row:
        raise HTTPException(404, f"File {file_id} not found")
    # Build a response shaped like a vector_store.file object
    return {
        "id": file_id,
        "object": "vector_store.file",
        "created_at": file_row["created_at"] // 1000,
        "vector_store_id": store_id,
        "file_id": file_id,
        "status": "completed",
    }


@router.delete("/v1/vector_stores/{store_id}/files/{file_id}")
async def delete_vector_store_file(store_id: str, file_id: str):
    """Remove a file from a vector store."""
    db = await get_db()
    store = await db.get_vector_store(store_id)
    if not store:
        raise HTTPException(404, f"Vector store {store_id} not found")

    # Find the join row by listing store files and matching file_id
    rows = await db.list_vector_store_files(store_id, limit=100)
    target = next((r for r in rows if r["file_id"] == file_id), None)
    if not target:
        raise HTTPException(404, f"File {file_id} not found in vector store {store_id}")

    deleted = await db.remove_vector_store_file(target["id"])
    return {"id": target["id"], "object": "vector_store.file",
            "deleted": deleted, "vector_store_id": store_id}


# ── Batch endpoints ───────────────────────────────────────────────────────────


@router.post("/v1/batches", status_code=201)
async def create_batch(body: BatchRequest):
    db = await get_db()
    row = await db.create_batch(
        input_file_id=body.input_file_id,
        endpoint=body.endpoint,
        completion_window=body.completion_window,
        metadata=body.metadata,
    )
    return _batch_response(row)


@router.get("/v1/batches")
async def list_batches(
    limit: int = 20,
    after: Optional[str] = None,
    before: Optional[str] = None,
    order: str = "desc",
):
    db = await get_db()
    rows = await db.list_batches(
        limit=limit, after=after, before=before, order=order,
    )
    has_more = len(rows) > limit
    data = [_batch_response(r) for r in rows[:limit]]
    return {"object": "list", "data": data, "first_id": data[0]["id"] if data else None,
            "last_id": data[-1]["id"] if data else None, "has_more": has_more}


@router.get("/v1/batches/{batch_id}")
async def retrieve_batch(batch_id: str):
    db = await get_db()
    row = await db.get_batch(batch_id)
    if not row:
        raise HTTPException(404, f"Batch {batch_id} not found")
    return _batch_response(row)


@router.post("/v1/batches/{batch_id}/cancel")
async def cancel_batch(batch_id: str):
    db = await get_db()
    row = await db.get_batch(batch_id)
    if not row:
        raise HTTPException(404, f"Batch {batch_id} not found")
    row = await db.cancel_batch(batch_id)
    return _batch_response(row)


# ── Fine-tuning endpoints ─────────────────────────────────────────────────────


@router.post("/v1/fine_tuning/jobs", status_code=201)
async def create_fine_tuning_job(body: FineTuningJobRequest):
    db = await get_db()
    row = await db.create_fine_tuning_job(
        model=body.model,
        training_file=body.training_file,
        validation_file=body.validation_file,
        hyperparameters=body.hyperparameters,
        metadata=body.metadata,
    )
    return _ftj_response(row)


@router.get("/v1/fine_tuning/jobs")
async def list_fine_tuning_jobs(
    limit: int = 20,
    after: Optional[str] = None,
    before: Optional[str] = None,
    order: str = "desc",
):
    db = await get_db()
    rows = await db.list_fine_tuning_jobs(
        limit=limit, after=after, before=before, order=order,
    )
    has_more = len(rows) > limit
    data = [_ftj_response(r) for r in rows[:limit]]
    return {"object": "list", "data": data, "first_id": data[0]["id"] if data else None,
            "last_id": data[-1]["id"] if data else None, "has_more": has_more}


@router.get("/v1/fine_tuning/jobs/{job_id}")
async def retrieve_fine_tuning_job(job_id: str):
    db = await get_db()
    row = await db.get_fine_tuning_job(job_id)
    if not row:
        raise HTTPException(404, f"Fine-tuning job {job_id} not found")
    return _ftj_response(row)


@router.post("/v1/fine_tuning/jobs/{job_id}/cancel")
async def cancel_fine_tuning_job(job_id: str):
    db = await get_db()
    row = await db.get_fine_tuning_job(job_id)
    if not row:
        raise HTTPException(404, f"Fine-tuning job {job_id} not found")
    row = await db.cancel_fine_tuning_job(job_id)
    return _ftj_response(row)
