"""Assistants CRUD endpoints — OpenAI-compatible."""
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from .state import get_db

router = APIRouter()


# ── Request / Response models ─────────────────────────────────────────────────

class AssistantRequest(BaseModel):
    model: str
    name: Optional[str] = None
    description: Optional[str] = None
    instructions: Optional[str] = None
    tools: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[Dict[str, Any]] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    response_format: Optional[str] = None
    model_config = ConfigDict(extra="allow")


def _assistant_response(row: dict) -> dict:
    """Shape a DB row into an OpenAI assistant object."""
    return {
        "id": row["id"],
        "object": "assistant",
        "created_at": row["created_at"] // 1000,  # ms → s for API
        "name": row.get("name"),
        "description": row.get("description"),
        "model": row["model"],
        "instructions": row.get("instructions"),
        "tools": row.get("tools", []),
        "metadata": row.get("metadata", {}),
        "temperature": row.get("temperature"),
        "top_p": row.get("top_p"),
        "response_format": row.get("response_format"),
        "file_ids": [],   # stub — file binding not yet implemented
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/v1/assistants", status_code=201)
async def create_assistant(body: AssistantRequest):
    """Create an assistant."""
    db = await get_db()
    row = await db.create_assistant(
        model=body.model,
        name=body.name,
        description=body.description,
        instructions=body.instructions,
        tools=body.tools,
        metadata=body.metadata,
        temperature=body.temperature,
        top_p=body.top_p,
        response_format=body.response_format,
    )
    return _assistant_response(row)


@router.get("/v1/assistants")
async def list_assistants(
    limit: int = 20,
    after: Optional[str] = None,
    before: Optional[str] = None,
    order: str = "desc",
):
    """List assistants with cursor pagination."""
    db = await get_db()
    rows = await db.list_assistants(
        limit=limit, after=after, before=before, order=order,
    )
    has_more = len(rows) > limit
    data = [_assistant_response(r) for r in rows[:limit]]
    return {"object": "list", "data": data, "first_id": data[0]["id"] if data else None,
            "last_id": data[-1]["id"] if data else None, "has_more": has_more}


@router.get("/v1/assistants/{assistant_id}")
async def retrieve_assistant(assistant_id: str):
    """Retrieve an assistant by ID."""
    db = await get_db()
    row = await db.get_assistant(assistant_id)
    if not row:
        raise HTTPException(404, f"Assistant {assistant_id} not found")
    return _assistant_response(row)


@router.post("/v1/assistants/{assistant_id}")
async def modify_assistant(assistant_id: str, body: AssistantRequest):
    """Modify an assistant (partial update)."""
    db = await get_db()
    existing = await db.get_assistant(assistant_id)
    if not existing:
        raise HTTPException(404, f"Assistant {assistant_id} not found")

    # Build update dict from non-None fields
    updates: dict = {}
    for field in ("model", "name", "description", "instructions",
                  "tools", "metadata", "temperature", "top_p",
                  "response_format"):
        val = getattr(body, field, None)
        if val is not None:
            updates[field] = val

    row = await db.update_assistant(assistant_id, updates)
    return _assistant_response(row)


@router.delete("/v1/assistants/{assistant_id}")
async def delete_assistant(assistant_id: str):
    """Delete an assistant."""
    db = await get_db()
    deleted = await db.delete_assistant(assistant_id)
    if not deleted:
        raise HTTPException(404, f"Assistant {assistant_id} not found")
    return {"id": assistant_id, "object": "assistant", "deleted": True}
