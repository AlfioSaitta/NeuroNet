"""Profile self-service API — change password, API key management, Telegram link.

All endpoints require authentication (any role).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import require_auth

router = APIRouter(prefix="/api/auth", tags=["profile"], dependencies=[Depends(require_auth)])


# ── Pydantic models ─────────────────────────────────────────────────────────

class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class CreateApiKeyRequest(BaseModel):
    name: str = ""
    rotate: bool = False


class TelegramLinkRequest(BaseModel):
    telegram_id: str | None = None


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/change-password")
async def change_password(body: ChangePasswordRequest, user: dict = Depends(require_auth)):
    """Change own password. Requires current password verification."""
    if not body.old_password or not body.new_password:
        raise HTTPException(status_code=400, detail="old_password and new_password required")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    from user_manager import user_manager as um

    verified = await um.verify_password(user["username"], body.old_password)
    if not verified:
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    await um.update_user(user["id"], password=body.new_password)
    return {"ok": True}


@router.get("/api-key")
async def list_my_api_keys(user: dict = Depends(require_auth)):
    """List own API keys (prefix only, never full key or hash)."""
    from user_manager import user_manager as um

    keys = await um.get_user_api_keys(user["id"])
    return {"keys": keys}


@router.post("/api-key")
async def create_api_key(body: CreateApiKeyRequest, user: dict = Depends(require_auth)):
    """Generate a new API key. Optionally revoke all previous keys."""
    from user_manager import user_manager as um

    full_key, key_obj = await um.generate_api_key(user["id"], name=body.name)

    if body.rotate:
        all_keys = await um.get_user_api_keys(user["id"])
        for k in all_keys:
            if k["id"] != key_obj["id"] and k["is_active"]:
                await um.revoke_api_key(k["id"])

    return {
        "key": full_key,
        "key_id": key_obj["id"],
        "prefix": key_obj["key_prefix"],
        "message": "⚠️ Save this key — it won't be shown again",
    }


@router.post("/api-key/{key_id}/revoke")
async def revoke_api_key(key_id: str, user: dict = Depends(require_auth)):
    """Revoke one of own API keys."""
    from user_manager import user_manager as um

    keys = await um.get_user_api_keys(user["id"])
    if not any(k["id"] == key_id for k in keys):
        raise HTTPException(status_code=404, detail="API key not found")

    await um.revoke_api_key(key_id)
    return {"ok": True}


@router.get("/api-key/{key_id}/reveal")
async def reveal_api_key(key_id: str, user: dict = Depends(require_auth)):
    """Reveal a recently generated API key (within 5 minutes of creation).
    
    The full key is only stored temporarily in memory after generation.
    Once expired, it cannot be recovered — generate a new key instead.
    """
    from user_manager import user_manager as um, _get_recent_key

    keys = await um.get_user_api_keys(user["id"])
    if not any(k["id"] == key_id for k in keys):
        raise HTTPException(status_code=404, detail="API key not found")

    full_key = _get_recent_key(key_id)
    if not full_key:
        raise HTTPException(
            status_code=404,
            detail="API key has expired and cannot be recovered. Generate a new key.",
        )

    return {"key": full_key, "key_id": key_id}


@router.post("/telegram")
async def set_telegram(body: TelegramLinkRequest, user: dict = Depends(require_auth)):
    """Link or unlink Telegram ID to own profile."""
    from user_manager import user_manager as um

    if body.telegram_id is None or body.telegram_id == "":
        await um.update_user(user["id"], telegram_id=None)
        return {"ok": True, "telegram_id": None}

    if not body.telegram_id.strip().isdigit():
        raise HTTPException(status_code=400, detail="Telegram ID must be numeric")

    try:
        await um.set_telegram_id(user["id"], body.telegram_id.strip())
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return {"ok": True, "telegram_id": body.telegram_id.strip()}
