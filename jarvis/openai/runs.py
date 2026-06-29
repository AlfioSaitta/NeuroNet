"""Runs API endpoints — OpenAI-compatible (sync, no streaming).

Implements the full Runs lifecycle: create, retrieve, list, cancel,
submit_tool_outputs, and run-step retrieval.
"""
import asyncio
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from .state import get_db
from .run_engine import execute_run, continue_run_with_tool_outputs

router = APIRouter()


# ── Request models ────────────────────────────────────────────────────────────

class CreateRunRequest(BaseModel):
    assistant_id: str
    model: Optional[str] = None
    instructions: Optional[str] = None
    tools: Optional[List[Dict[str, Any]]] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_prompt_tokens: Optional[int] = None
    max_completion_tokens: Optional[int] = None
    truncation_strategy: Optional[Dict[str, Any]] = None
    tool_choice: Optional[str] = None
    response_format: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    model_config = ConfigDict(extra="allow")


class SubmitToolOutputsRequest(BaseModel):
    tool_outputs: List[Dict[str, Any]]


# ── Response helpers ──────────────────────────────────────────────────────────

def _run_response(row: dict) -> dict:
    return {
        "id": row["id"],
        "object": "thread.run",
        "created_at": row["created_at"] // 1000,
        "thread_id": row["thread_id"],
        "assistant_id": row["assistant_id"],
        "status": row.get("status", "queued"),
        "required_action": row.get("required_action"),
        "last_error": row.get("last_error"),
        "expires_at": (row.get("expires_at") // 1000) if row.get("expires_at") else None,
        "started_at": (row.get("started_at") // 1000) if row.get("started_at") else None,
        "completed_at": (row.get("completed_at") // 1000) if row.get("completed_at") else None,
        "cancelled_at": (row.get("cancelled_at") // 1000) if row.get("cancelled_at") else None,
        "failed_at": (row.get("failed_at") // 1000) if row.get("failed_at") else None,
        "model": row.get("model"),
        "instructions": row.get("instructions"),
        "tools": row.get("tools", []),
        "metadata": row.get("metadata", {}),
        "usage": row.get("usage"),
        "temperature": row.get("temperature"),
        "top_p": row.get("top_p"),
        "max_prompt_tokens": row.get("max_prompt_tokens"),
        "max_completion_tokens": row.get("max_completion_tokens"),
        "truncation_strategy": row.get("truncation_strategy"),
        "tool_choice": row.get("tool_choice"),
        "response_format": row.get("response_format"),
    }


def _run_step_response(row: dict) -> dict:
    return {
        "id": row["id"],
        "object": "thread.run.step",
        "created_at": row["created_at"] // 1000,
        "run_id": row["run_id"],
        "assistant_id": row.get("assistant_id"),
        "thread_id": row["thread_id"],
        "type": row.get("type"),
        "status": row.get("status", "in_progress"),
        "step_details": row.get("step_details", {}),
        "last_error": row.get("last_error"),
        "completed_at": (row.get("completed_at") // 1000) if row.get("completed_at") else None,
        "expired_at": (row.get("expired_at") // 1000) if row.get("expired_at") else None,
        "usage": row.get("usage"),
    }


# ── Run endpoints ─────────────────────────────────────────────────────────────


@router.post("/v1/threads/{thread_id}/runs", status_code=201)
async def create_run(thread_id: str, body: CreateRunRequest):
    """Create a run for a thread."""
    db = await get_db()
    thread = await db.get_thread(thread_id)
    if not thread:
        raise HTTPException(404, f"Thread {thread_id} not found")

    assistant = await db.get_assistant(body.assistant_id)
    if not assistant:
        raise HTTPException(404, f"Assistant {body.assistant_id} not found")

    row = await db.create_run(
        thread_id=thread_id,
        assistant_id=body.assistant_id,
        model=body.model,
        instructions=body.instructions,
        tools=body.tools,
        temperature=body.temperature,
        top_p=body.top_p,
        max_prompt_tokens=body.max_prompt_tokens,
        max_completion_tokens=body.max_completion_tokens,
        truncation_strategy=body.truncation_strategy,
        tool_choice=body.tool_choice,
        response_format=body.response_format,
        metadata=body.metadata,
    )
    # Spawn async execution in the background.
    asyncio.create_task(execute_run(row["id"], await get_db()))
    return _run_response(row)


@router.get("/v1/threads/{thread_id}/runs")
async def list_runs(
    thread_id: str,
    limit: int = 20,
    after: Optional[str] = None,
    before: Optional[str] = None,
    order: str = "desc",
):
    """List runs for a thread with cursor pagination."""
    db = await get_db()
    thread = await db.get_thread(thread_id)
    if not thread:
        raise HTTPException(404, f"Thread {thread_id} not found")

    rows = await db.list_runs(
        thread_id, limit=limit, after=after,
        before=before, order=order,
    )
    has_more = len(rows) > limit
    data = [_run_response(r) for r in rows[:limit]]
    return {"object": "list", "data": data, "first_id": data[0]["id"] if data else None,
            "last_id": data[-1]["id"] if data else None, "has_more": has_more}


@router.get("/v1/threads/{thread_id}/runs/{run_id}")
async def retrieve_run(thread_id: str, run_id: str):
    """Retrieve a specific run."""
    db = await get_db()
    row = await db.get_run(run_id)
    if not row or row["thread_id"] != thread_id:
        raise HTTPException(404, f"Run {run_id} not found in thread {thread_id}")
    return _run_response(row)


@router.post("/v1/threads/{thread_id}/runs/{run_id}")
async def modify_run(thread_id: str, run_id: str, body: Dict[str, Any]):
    """Modify a run (only metadata is updatable per OpenAI spec)."""
    db = await get_db()
    row = await db.get_run(run_id)
    if not row or row["thread_id"] != thread_id:
        raise HTTPException(404, f"Run {run_id} not found in thread {thread_id}")

    updates: dict = {}
    if "metadata" in body:
        updates["metadata"] = body["metadata"]
    if updates:
        row = await db.update_run(run_id, updates)
    return _run_response(row)


@router.post("/v1/threads/{thread_id}/runs/{run_id}/cancel")
async def cancel_run(thread_id: str, run_id: str):
    """Cancel a queued/in-progress run."""
    db = await get_db()
    row = await db.get_run(run_id)
    if not row or row["thread_id"] != thread_id:
        raise HTTPException(404, f"Run {run_id} not found in thread {thread_id}")

    if row["status"] in ("queued", "in_progress"):
        now = db._now()
        row = await db.update_run(run_id, {
            "status": "cancelled",
            "cancelled_at": now,
        })
    return _run_response(row)


@router.post("/v1/threads/{thread_id}/runs/{run_id}/submit_tool_outputs")
async def submit_tool_outputs(thread_id: str, run_id: str,
                               body: SubmitToolOutputsRequest):
    """Submit outputs for tool calls — transitions run to 'queued'."""
    db = await get_db()
    row = await db.get_run(run_id)
    if not row or row["thread_id"] != thread_id:
        raise HTTPException(404, f"Run {run_id} not found in thread {thread_id}")

    if row["status"] != "requires_action":
        raise HTTPException(400, f"Run {run_id} is not in requires_action state (status={row['status']})")

    # Append tool output messages to the thread.
    for to in body.tool_outputs:
        await db.create_message(
            thread_id=thread_id,
            role="tool",
            content=to.get("output", ""),
            run_id=run_id,
            metadata={"tool_call_id": to.get("tool_call_id", "")},
        )

    # Store submitted outputs and transition run back to queued
    row = await db.update_run(run_id, {
        "status": "queued",
        "required_action": None,
    })
    # Spawn continuation in the background.
    asyncio.create_task(continue_run_with_tool_outputs(run_id, await get_db()))
    return _run_response(row)


# ── Run Step endpoints ────────────────────────────────────────────────────────


@router.get("/v1/threads/{thread_id}/runs/{run_id}/steps")
async def list_run_steps(
    thread_id: str,
    run_id: str,
    limit: int = 20,
    after: Optional[str] = None,
    before: Optional[str] = None,
    order: str = "desc",
):
    """List steps for a run with cursor pagination."""
    db = await get_db()
    run = await db.get_run(run_id)
    if not run or run["thread_id"] != thread_id:
        raise HTTPException(404, f"Run {run_id} not found in thread {thread_id}")

    rows = await db.list_run_steps(
        run_id, limit=limit, after=after, before=before, order=order,
    )
    has_more = len(rows) > limit
    data = [_run_step_response(r) for r in rows[:limit]]
    return {"object": "list", "data": data, "first_id": data[0]["id"] if data else None,
            "last_id": data[-1]["id"] if data else None, "has_more": has_more}


@router.get("/v1/threads/{thread_id}/runs/{run_id}/steps/{step_id}")
async def retrieve_run_step(thread_id: str, run_id: str, step_id: str):
    """Retrieve a specific run step."""
    db = await get_db()
    row = await db.get_run_step(step_id)
    if not row or row["run_id"] != run_id:
        raise HTTPException(404, f"Step {step_id} not found in run {run_id}")
    return _run_step_response(row)
