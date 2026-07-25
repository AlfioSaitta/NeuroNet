"""Threads + Messages CRUD endpoints — OpenAI-compatible."""
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from .state import get_db

router = APIRouter()


# ── Request models ────────────────────────────────────────────────────────────

class ThreadRequest(BaseModel):
    messages: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[Dict[str, Any]] = None
    model_config = ConfigDict(extra="allow")


class MessageRequest(BaseModel):
    role: str = "user"
    content: str | List[Dict[str, Any]]
    file_ids: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    model_config = ConfigDict(extra="allow")


# ── Response helpers ──────────────────────────────────────────────────────────

def _thread_response(row: dict) -> dict:
    return {
        "id": row["id"],
        "object": "thread",
        "created_at": row["created_at"] // 1000,
        "metadata": row.get("metadata", {}),
    }


def _message_response(row: dict) -> dict:
    return {
        "id": row["id"],
        "object": "thread.message",
        "created_at": row["created_at"] // 1000,
        "thread_id": row["thread_id"],
        "role": row["role"],
        "content": row.get("content", []),
        "assistant_id": row.get("assistant_id"),
        "run_id": row.get("run_id"),
        "file_ids": row.get("file_ids", []),
        "metadata": row.get("metadata", {}),
    }


# ── Thread endpoints ──────────────────────────────────────────────────────────


@router.post("/v1/threads", status_code=201)
async def create_thread(body: ThreadRequest):
    """Create a thread with optional initial messages."""
    db = await get_db()
    row = await db.create_thread(
        messages=body.messages,
        metadata=body.metadata,
    )
    return _thread_response(row)


@router.get("/v1/threads/{thread_id}")
async def retrieve_thread(thread_id: str):
    """Retrieve a thread by ID."""
    db = await get_db()
    row = await db.get_thread(thread_id)
    if not row:
        raise HTTPException(404, f"Thread {thread_id} not found")
    return _thread_response(row)


@router.post("/v1/threads/{thread_id}")
async def modify_thread(thread_id: str, body: ThreadRequest):
    """Modify a thread (only metadata can be updated)."""
    db = await get_db()
    existing = await db.get_thread(thread_id)
    if not existing:
        raise HTTPException(404, f"Thread {thread_id} not found")

    updates: dict = {}
    if body.metadata is not None:
        updates["metadata"] = body.metadata
    row = await db.update_thread(thread_id, updates)
    return _thread_response(row)


@router.delete("/v1/threads/{thread_id}")
async def delete_thread(thread_id: str):
    """Delete a thread and its messages (CASCADE)."""
    db = await get_db()
    deleted = await db.delete_thread(thread_id)
    if not deleted:
        raise HTTPException(404, f"Thread {thread_id} not found")
    return {"id": thread_id, "object": "thread", "deleted": True}


# ── Message endpoints ─────────────────────────────────────────────────────────


@router.post("/v1/threads/{thread_id}/messages", status_code=201)
async def create_message(thread_id: str, body: MessageRequest):
    """Create a message in a thread."""
    db = await get_db()
    # Verify thread exists
    thread = await db.get_thread(thread_id)
    if not thread:
        raise HTTPException(404, f"Thread {thread_id} not found")
    row = await db.create_message(
        thread_id=thread_id,
        role=body.role,
        content=body.content,
        file_ids=body.file_ids,
        metadata=body.metadata,
    )
    return _message_response(row)


@router.get("/v1/threads/{thread_id}/messages")
async def list_messages(
    thread_id: str,
    limit: int = 20,
    after: Optional[str] = None,
    before: Optional[str] = None,
    order: str = "desc",
    run_id: Optional[str] = None,
):
    """List messages in a thread with cursor pagination."""
    db = await get_db()
    thread = await db.get_thread(thread_id)
    if not thread:
        raise HTTPException(404, f"Thread {thread_id} not found")
    rows = await db.list_messages(
        thread_id, limit=limit, after=after,
        before=before, order=order, run_id=run_id,
    )
    has_more = len(rows) > limit
    data = [_message_response(r) for r in rows[:limit]]
    return {"object": "list", "data": data, "first_id": data[0]["id"] if data else None,
            "last_id": data[-1]["id"] if data else None, "has_more": has_more}


@router.get("/v1/threads/{thread_id}/messages/{message_id}")
async def retrieve_message(thread_id: str, message_id: str):
    """Retrieve a specific message."""
    db = await get_db()
    row = await db.get_message(message_id)
    if not row or row["thread_id"] != thread_id:
        raise HTTPException(404, f"Message {message_id} not found in thread {thread_id}")
    return _message_response(row)
