"""File management endpoints — OpenAI-compatible CRUD + content download.

Uses the SQLite state engine for metadata and local filesystem for content.
"""
import os
import uuid
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse

from config import DATA_DIR, logger
from .state import get_db

router = APIRouter()

FILES_DIR = Path(DATA_DIR) / "openai_files"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ensure_files_dir() -> Path:
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    return FILES_DIR


def _openai_file_response(row: dict) -> dict:
    """Shape a DB row into an OpenAI file object response."""
    return {
        "id": row["id"],
        "object": "file",
        "bytes": row["bytes"],
        "created_at": row["created_at"] // 1000,  # ms → s for API
        "filename": row["filename"],
        "purpose": row["purpose"],
        "status": row["status"],
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/v1/files")
async def upload_file(
    file: UploadFile = File(...),
    purpose: str = Form(...),
):
    """Upload a file (multipart/form-data)."""
    db = await get_db()
    file_id = uuid.uuid4().hex
    storage_name = f"{file_id}_{file.filename}"

    # Read content and write to disk
    content = await file.read()
    _ensure_files_dir()
    storage_path = FILES_DIR / storage_name
    storage_path.write_bytes(content)

    # Store metadata
    row = await db.create_file(
        filename=file.filename or "untitled",
        purpose=purpose,
        bytes=len(content),
    )
    # Patch ID to match our generated file_id
    await db.update("files", row["id"], {"id": file_id})
    row["id"] = file_id

    logger.info("File uploaded: %s (%d bytes, purpose=%s)", file_id, len(content), purpose)
    return _openai_file_response(row)


@router.get("/v1/files")
async def list_files(
    purpose: Optional[str] = None,
    limit: int = 20,
    after: Optional[str] = None,
    before: Optional[str] = None,
    order: str = "desc",
):
    """List uploaded files."""
    db = await get_db()
    rows = await db.list_files(
        purpose=purpose, limit=limit,
        after=after, before=before, order=order,
    )
    # Strip the extra row fetched for pagination detection
    has_more = len(rows) > limit
    data = [_openai_file_response(r) for r in rows[:limit]]
    return {"object": "list", "data": data, "has_more": has_more}


@router.get("/v1/files/{file_id}")
async def retrieve_file(file_id: str):
    """Retrieve file metadata."""
    db = await get_db()
    row = await db.get_file(file_id)
    if not row:
        raise HTTPException(404, f"File {file_id} not found")
    return _openai_file_response(row)


@router.delete("/v1/files/{file_id}")
async def delete_file(file_id: str):
    """Delete a file (metadata + disk content)."""
    db = await get_db()
    row = await db.get_file(file_id)
    if not row:
        raise HTTPException(404, f"File {file_id} not found")

    # Delete from disk
    storage_name = f"{file_id}_{row['filename']}"
    storage_path = FILES_DIR / storage_name
    if storage_path.exists():
        storage_path.unlink()

    deleted = await db.delete_file(file_id)
    return {"id": file_id, "object": "file", "deleted": deleted}


@router.get("/v1/files/{file_id}/content")
async def download_file(file_id: str):
    """Download raw file content."""
    db = await get_db()
    row = await db.get_file(file_id)
    if not row:
        raise HTTPException(404, f"File {file_id} not found")

    storage_name = f"{file_id}_{row['filename']}"
    storage_path = FILES_DIR / storage_name
    if not storage_path.exists():
        raise HTTPException(404, "File content not found on disk")

    return FileResponse(
        path=storage_path,
        filename=row["filename"],
        media_type="application/octet-stream",
    )
