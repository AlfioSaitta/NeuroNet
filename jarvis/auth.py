"""JWT authentication module — token creation/verification, FastAPI dependencies, and auth endpoints.

Usage::

    from auth import router as auth_router
    app.include_router(auth_router)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import JWT_ALGORITHM, JWT_SECRET, ACCESS_TOKEN_EXPIRE_MINUTES

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── Pydantic models ─────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    user: dict[str, Any]
    access_token: str


# ── Token functions ─────────────────────────────────────────────────────────

def create_access_token(user_id: str, role: str) -> str:
    """Create a JWT with sub=user_id, role=role, exp=now+expire_minutes."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> dict[str, Any] | None:
    """Decode JWT, return payload or None if expired/invalid."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


# ── Helper ──────────────────────────────────────────────────────────────────

def _sanitize_user(user: dict) -> dict:
    """Remove sensitive fields before API exposure."""
    sensitive = {"password_hash", "key_hash"}
    return {k: v for k, v in user.items() if k not in sensitive}


# ── FastAPI dependencies ────────────────────────────────────────────────────

async def get_current_user(request: Request) -> dict | None:
    """Extract JWT from cookie or Authorization header → verify → get user from DB."""
    token = request.cookies.get("access_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        return None

    payload = verify_token(token)
    if not payload:
        return None

    from user_manager import user_manager, ensure_admin_exists  # defer import

    if user_manager is None:
        # Safety net: bootstrap if lifespan never ran
        logger.info("🛟 UserManager not initialized — lazy bootstrapping in get_current_user")
        await ensure_admin_exists()
        from user_manager import user_manager  # re-import after init
        if user_manager is None:
            return None
    user = await user_manager.get_user(payload["sub"])
    return _sanitize_user(user) if user else None


async def require_admin(user: dict | None = Depends(get_current_user)) -> dict:
    """FastAPI dependency — require admin role."""
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=401, detail="Not authorized")
    return user


async def require_auth(user: dict | None = Depends(get_current_user)) -> dict:
    """FastAPI dependency — require any authenticated user."""
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/login")
async def login(body: LoginRequest):
    """Verify credentials → create JWT → set httpOnly cookie.

    Safeguard: if UserManager was not initialized (e.g. lifespan never ran
    due to an import-time crash), bootstrap it on first login attempt.
    """
    from user_manager import user_manager, ensure_admin_exists

    if user_manager is None:
        logger.info("🛟 UserManager not initialized — lazy bootstrapping on login")
        await ensure_admin_exists()
        from user_manager import user_manager  # re-import after init

        if user_manager is None:
            raise HTTPException(
                status_code=503,
                detail="User database could not be initialized. Check server logs.",
            )

    user = await user_manager.verify_password(body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token(user["id"], user["role"])
    safe_user = _sanitize_user(user)
    response = JSONResponse(
        content={"user": safe_user, "access_token": token},
    )
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax",
        secure=False,  # Set True in production behind HTTPS
    )
    return response


@router.post("/logout")
async def logout():
    """Delete access_token cookie."""
    response = JSONResponse(content={"ok": True})
    response.delete_cookie("access_token")
    return response


@router.get("/me")
async def auth_me(user: dict | None = Depends(get_current_user)):
    """Return current user profile (sanitized)."""
    if not user:
        raise HTTPException(status_code=401)
    return user
