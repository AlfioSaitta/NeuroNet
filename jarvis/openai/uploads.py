"""Uploads API — OpenAI-compatible multipart upload for large files.

Lifecycle:
  POST /v1/uploads           → create (returns upload_id)
  POST /v1/uploads/{id}/parts → upload a chunk
  POST /v1/uploads/{id}/complete → assemble chunks → file record
  POST /v1/uploads/{id}/cancel → clean up

Files are stored in ``DATA_DIR/uploads/{upload_id}/`` during the upload
and moved to ``DATA_DIR/files/{file_id}/{filename}`` on completion.
"""
import os
import shutil
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from config import DATA_DIR
from .state import get_db

router = APIRouter()

# In-memory registry: upload_id → upload state
_uploads: dict[str, dict] = {}

UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")
FILES_DIR = os.path.join(DATA_DIR, "files")


# ── Request models ────────────────────────────────────────────────────────────


class CreateUploadRequest(BaseModel):
    filename: str
    purpose: str
    bytes: int


class CompleteUploadRequest(BaseModel):
    part_ids: Optional[list[str]] = None
    md5: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/v1/uploads", status_code=201)
async def create_upload(body: CreateUploadRequest):
    """Create a new upload session for a large file."""
    upload_id = uuid.uuid4().hex
    upload_dir = os.path.join(UPLOADS_DIR, upload_id)
    os.makedirs(upload_dir, exist_ok=True)

    _uploads[upload_id] = {
        "id": upload_id,
        "filename": body.filename,
        "purpose": body.purpose,
        "bytes": body.bytes,
        "status": "pending",
        "parts": [],
        "dir": upload_dir,
    }

    return {
        "id": upload_id,
        "object": "upload",
        "status": "pending",
        "filename": body.filename,
        "purpose": body.purpose,
        "bytes": body.bytes,
        "expires_at": None,
    }


@router.post("/v1/uploads/{upload_id}/parts", status_code=201)
async def upload_part(upload_id: str, data: UploadFile = File(...)):
    """Upload a single chunk of the file."""
    upload = _uploads.get(upload_id)
    if not upload:
        raise HTTPException(404, f"Upload {upload_id} not found")
    if upload["status"] != "pending":
        raise HTTPException(400, f"Upload {upload_id} is in status '{upload['status']}'")

    part_id = uuid.uuid4().hex
    part_path = os.path.join(upload["dir"], part_id)

    content = await data.read()
    with open(part_path, "wb") as f:
        f.write(content)

    part_info = {
        "id": part_id,
        "object": "upload.part",
        "created_at": int(time.time()),
        "upload_id": upload_id,
        "size": len(content),
    }
    upload["parts"].append(part_info)

    return part_info


@router.post("/v1/uploads/{upload_id}/complete")
async def complete_upload(upload_id: str, body: CompleteUploadRequest):
    """Assemble uploaded parts into a final file record."""
    upload = _uploads.get(upload_id)
    if not upload:
        raise HTTPException(404, f"Upload {upload_id} not found")
    if upload["status"] != "pending":
        raise HTTPException(400, f"Upload {upload_id} is in status '{upload['status']}'")

    parts = upload["parts"]
    if body.part_ids:
        # Only use specified parts
        parts = [p for p in parts if p["id"] in body.part_ids]

    if not parts:
        raise HTTPException(400, "No parts to assemble")

    # Sort parts by creation order
    parts.sort(key=lambda p: p["created_at"])

    # Assemble into final file
    file_id = uuid.uuid4().hex
    file_dir = os.path.join(FILES_DIR, file_id)
    os.makedirs(file_dir, exist_ok=True)
    dest_path = os.path.join(file_dir, upload["filename"])

    total_bytes = 0
    with open(dest_path, "wb") as dest:
        for part in parts:
            part_path = os.path.join(upload["dir"], part["id"])
            if os.path.exists(part_path):
                with open(part_path, "rb") as src:
                    chunk = src.read()
                    dest.write(chunk)
                    total_bytes += len(chunk)

    # Clean up upload directory
    shutil.rmtree(upload["dir"], ignore_errors=True)
    del _uploads[upload_id]

    # Create file record in DB
    db = await get_db()
    file_row = await db.create_file(
        filename=upload["filename"],
        purpose=upload["purpose"],
        bytes=total_bytes,
    )
    # Override the auto-generated ID with our deterministic one
    await db.update("files", file_row["id"], {"id": file_id})
    file_row["id"] = file_id

    # Mark file as uploaded
    await db.update("files", file_id, {"status": "uploaded"})

    return {
        "id": file_id,
        "object": "file",
        "created_at": file_row["created_at"] // 1000,
        "filename": upload["filename"],
        "purpose": upload["purpose"],
        "bytes": total_bytes,
        "status": "uploaded",
    }


@router.post("/v1/uploads/{upload_id}/cancel")
async def cancel_upload(upload_id: str):
    """Cancel an upload and clean up temporary files."""
    upload = _uploads.get(upload_id)
    if not upload:
        raise HTTPException(404, f"Upload {upload_id} not found")

    # Clean up temp directory
    shutil.rmtree(upload["dir"], ignore_errors=True)
    del _uploads[upload_id]

    return {
        "id": upload_id,
        "object": "upload",
        "status": "cancelled",
    }
