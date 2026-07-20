"""Users CRUD API — admin-only endpoints for user management."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import require_admin

router = APIRouter(prefix="/api/users", tags=["users"], dependencies=[Depends(require_admin)])


# ── Pydantic models ─────────────────────────────────────────────────────────

class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"
    display_name: str = ""
    telegram_id: str | None = None
    allowed_projects: list[str] | None = None


class UpdateUserRequest(BaseModel):
    username: str | None = None
    password: str | None = None
    role: str | None = None
    display_name: str | None = None
    telegram_id: str | None = None
    allowed_projects: list[str] | None = None
    is_active: bool | None = None


class TelegramLinkRequest(BaseModel):
    telegram_id: str | None = None


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.get("")
async def list_users(admin: dict = Depends(require_admin)):
    """List all users (no password_hash exposed)."""
    from user_manager import user_manager as um

    return await um.list_users()


@router.post("")
async def create_user(body: CreateUserRequest, admin: dict = Depends(require_admin)):
    """Create a new user with an initial API key."""
    from user_manager import user_manager as um

    try:
        user_row, api_key = await um.create_user(
            username=body.username,
            password=body.password,
            role=body.role,
            display_name=body.display_name,
            telegram_id=body.telegram_id,
            allowed_projects=body.allowed_projects,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return {
        "user": user_row,
        "api_key": api_key,
        "message": "⚠️ Save this API key — it won't be shown again",
    }


@router.get("/{user_id}")
async def get_user(user_id: str, admin: dict = Depends(require_admin)):
    """Get a single user."""
    from user_manager import user_manager as um

    user = await um.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    from auth import _sanitize_user
    return _sanitize_user(user)


@router.put("/{user_id}")
async def update_user(user_id: str, body: UpdateUserRequest, admin: dict = Depends(require_admin)):
    """Update a user. Admin cannot demote themselves to user."""
    from user_manager import user_manager as um

    # Prevent self-demotion
    if admin["id"] == user_id and body.role is not None and body.role != "admin":
        raise HTTPException(status_code=400, detail="You cannot demote yourself")

    fields = body.model_dump(exclude_none=True)

    # Handle explicit null fields that should be stored as None
    # exclude_none=True drops them, so we add them back
    for null_field in ("telegram_id",):
        val = getattr(body, null_field, None)
        if val is None and null_field in body.model_fields_set:
            fields[null_field] = None

    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    try:
        updated = await um.update_user(user_id, **fields)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    if not updated:
        raise HTTPException(status_code=404, detail="User not found")

    from auth import _sanitize_user
    return _sanitize_user(updated)


@router.delete("/{user_id}")
async def delete_user(user_id: str, admin: dict = Depends(require_admin)):
    """Delete a user. Cannot delete the last admin."""
    from user_manager import user_manager as um

    if admin["id"] == user_id:
        raise HTTPException(status_code=400, detail="You cannot delete yourself")

    try:
        deleted = await um.delete_user(user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")

    return {"ok": True}


@router.post("/{user_id}/telegram")
async def set_user_telegram(user_id: str, body: TelegramLinkRequest, admin: dict = Depends(require_admin)):
    """Set or unset Telegram ID for a user."""
    from user_manager import user_manager as um

    try:
        updated = await um.set_telegram_id(user_id, body.telegram_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    if not updated:
        raise HTTPException(status_code=404, detail="User not found")

    return {"ok": True}
