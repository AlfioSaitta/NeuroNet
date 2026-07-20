"""User Manager — SQLite user database with bcrypt passwords and API key management.

Singleton pattern matching OpenAIDatabase in openai/state.py.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
import uuid
from typing import Any

# Forward reference for cache invalidation (telegram_bot is loaded lazily)
_telegram_cache_invalidator = None


def _set_telegram_cache_invalidator(fn):
    """Set the function to invalidate telegram user cache.
    Called from telegram_bot on import."""
    global _telegram_cache_invalidator
    _telegram_cache_invalidator = fn

import aiosqlite
import bcrypt

logger = logging.getLogger(__name__)

# ── SQL Schema ──────────────────────────────────────────────────────────────

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    username        TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'user',
    display_name    TEXT DEFAULT '',
    telegram_id     TEXT UNIQUE,
    allowed_projects TEXT DEFAULT '[]',
    is_active       INTEGER DEFAULT 1,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_prefix      TEXT NOT NULL,
    key_hash        TEXT NOT NULL,
    name            TEXT DEFAULT '',
    is_active       INTEGER DEFAULT 1,
    last_used_at    INTEGER,
    created_at      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys(key_hash);
"""


# ── Serialization helpers ──────────────────────────────────────────────────

def _serialize_projects(projects: list | None) -> str:
    if projects is None:
        return "[]"
    return json.dumps(projects)


def _deserialize_projects(projects_str: str) -> list:
    if not projects_str:
        return []
    if isinstance(projects_str, list):
        return projects_str
    try:
        return json.loads(projects_str)
    except (json.JSONDecodeError, TypeError):
        return []


def _sanitize_user(user: dict) -> dict:
    """Strip sensitive fields before API exposure."""
    sensitive = {"password_hash", "key_hash"}
    return {k: v for k, v in user.items() if k not in sensitive}


# ── API key generation ─────────────────────────────────────────────────────

API_KEY_PREFIX = "sk-jarvis-"


def _generate_api_key() -> tuple[str, str, str]:
    """Return (full_key, prefix, sha256_hash)."""
    raw = secrets.token_bytes(32)
    full_key = API_KEY_PREFIX + base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    prefix = full_key[:18]  # "sk-jarvis-" (10) + first 8 random chars
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    return full_key, prefix, key_hash


# ── UserManager Singleton ──────────────────────────────────────────────────

class UserManager:
    """Async SQLite user database with bcrypt auth and API key management.

    Usage::

        mgr = UserManager(db_path)
        await mgr.initialize()
        user = await mgr.verify_password("admin", "secret")
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Open connection and run DDL."""
        async with self._lock:
            if self._conn is None:
                logger.info("User DB: %s", self.db_path)
                self._conn = await aiosqlite.connect(self.db_path)
                self._conn.row_factory = aiosqlite.Row
                await self._conn.execute("PRAGMA foreign_keys = ON")
                await self._conn.executescript(CREATE_TABLES_SQL)
                await self._conn.commit()
                logger.info("User DB ready")

    async def close(self) -> None:
        async with self._lock:
            if self._conn:
                await self._conn.close()
                self._conn = None

    # ── Low-level helpers ──────────────────────────────────────────────────

    async def _execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        async with self._lock:
            cur = await self._conn.execute(sql, params)
            await self._conn.commit()
            return cur

    async def _fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        async with self._lock:
            cur = await self._conn.execute(sql, params)
            row = await cur.fetchone()
            return self._row_to_dict(row) if row else None

    async def _fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        async with self._lock:
            cur = await self._conn.execute(sql, params)
            rows = await cur.fetchall()
            return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(row: aiosqlite.Row) -> dict:
        d = dict(row)
        # Parse JSON columns
        for col in ("allowed_projects",):
            if col in d and isinstance(d[col], str):
                try:
                    d[col] = json.loads(d[col])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d

    @staticmethod
    def _now() -> int:
        return int(time.time())

    @staticmethod
    def _uuid() -> str:
        return uuid.uuid4().hex

    # ── User CRUD ──────────────────────────────────────────────────────────

    async def create_user(
        self,
        username: str,
        password: str,
        role: str = "user",
        display_name: str = "",
        telegram_id: str | None = None,
        allowed_projects: list | None = None,
    ) -> tuple[dict, str]:
        """Create user. Returns (user_dict_without_hash, api_key_plaintext).

        Raises ValueError if username or telegram_id already exists.
        """
        # Uniqueness checks
        existing = await self._fetchone(
            "SELECT id FROM users WHERE username = ?", (username,)
        )
        if existing:
            raise ValueError(f"Username '{username}' already exists")

        if telegram_id is not None:
            existing_tg = await self._fetchone(
                "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
            )
            if existing_tg:
                raise ValueError(f"Telegram ID '{telegram_id}' already linked")

        now = self._now()
        user_id = self._uuid()
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        projects_str = _serialize_projects(allowed_projects)

        await self._execute(
            """INSERT INTO users (id, username, password_hash, role, display_name,
               telegram_id, allowed_projects, is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (user_id, username, pw_hash, role, display_name,
             telegram_id, projects_str, now, now),
        )

        user_row = await self._fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
        assert user_row is not None  # just inserted

        # Create initial API key
        api_key_plaintext, _ = await self.generate_api_key(user_id, name="default")

        return _sanitize_user(user_row), api_key_plaintext

    async def get_user(self, user_id: str) -> dict | None:
        return await self._fetchone("SELECT * FROM users WHERE id = ?", (user_id,))

    async def get_user_by_username(self, username: str) -> dict | None:
        return await self._fetchone(
            "SELECT * FROM users WHERE username = ?", (username,)
        )

    async def get_user_by_telegram_id(self, telegram_id: str) -> dict | None:
        return await self._fetchone(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        )

    async def list_users(self, role: str | None = None) -> list[dict]:
        if role:
            rows = await self._fetchall(
                "SELECT * FROM users WHERE role = ? ORDER BY created_at ASC",
                (role,),
            )
        else:
            rows = await self._fetchall(
                "SELECT * FROM users ORDER BY created_at ASC"
            )
        return [_sanitize_user(r) for r in rows]

    async def update_user(self, user_id: str, **fields: Any) -> dict | None:
        """Update user fields. Returns updated user or None if not found.

        Special handling:
        - 'password' → bcrypt rehash before save
        - 'allowed_projects' → JSON.dumps before save
        - 'telegram_id' → uniqueness check before save
        - 'role' → prevent self-demotion (handled at API level)
        """
        existing = await self.get_user(user_id)
        if not existing:
            return None

        # Handle special fields
        if "password" in fields:
            pw = fields.pop("password")
            fields["password_hash"] = (
                bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
            )

        if "allowed_projects" in fields:
            fields["allowed_projects"] = _serialize_projects(fields["allowed_projects"])

        if "telegram_id" in fields:
            new_tg = fields["telegram_id"]
            # Invalidate cache for old telegram_id if it changed
            if existing.get("telegram_id") and isinstance(existing["telegram_id"], str):
                if _telegram_cache_invalidator:
                    _telegram_cache_invalidator(existing["telegram_id"])
            if new_tg is not None:
                existing_tg = await self._fetchone(
                    "SELECT id FROM users WHERE telegram_id = ? AND id != ?",
                    (new_tg, user_id),
                )
                if existing_tg:
                    raise ValueError(f"Telegram ID '{new_tg}' already linked to another user")
                # Preemptively invalidate new telegram_id cache
                if _telegram_cache_invalidator:
                    _telegram_cache_invalidator(new_tg)

        if not fields:
            return existing

        now = self._now()
        fields["updated_at"] = now

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = tuple(fields.values()) + (user_id,)
        await self._execute(
            f"UPDATE users SET {set_clause} WHERE id = ?", values
        )

        return await self.get_user(user_id)

    async def delete_user(self, user_id: str) -> bool:
        """Delete user. Returns True if deleted.

        Raises ValueError if this is the last admin remaining.
        """
        user = await self.get_user(user_id)
        if not user:
            return False

        if user["role"] == "admin":
            admin_count = await self._fetchone(
                "SELECT COUNT(*) as cnt FROM users WHERE role = 'admin' AND is_active = 1"
            )
            if admin_count and admin_count["cnt"] <= 1:
                raise ValueError("Cannot delete the last admin")

        await self._execute("DELETE FROM users WHERE id = ?", (user_id,))
        return True

    async def verify_password(self, username: str, password: str) -> dict | None:
        """Verify credentials. Returns user dict (sanitized) or None."""
        user = await self.get_user_by_username(username)
        if not user:
            return None
        stored_hash = user.get("password_hash", "")
        if not stored_hash:
            return None
        try:
            if bcrypt.checkpw(password.encode(), stored_hash.encode()):
                return _sanitize_user(user)
        except (ValueError, TypeError):
            pass
        return None

    async def set_telegram_id(self, user_id: str, telegram_id: str | None) -> dict | None:
        """Set or unset telegram_id for a user.

        Raises ValueError if telegram_id is already linked to another user.
        """
        if telegram_id is not None:
            existing = await self._fetchone(
                "SELECT id FROM users WHERE telegram_id = ? AND id != ?",
                (telegram_id, user_id),
            )
            if existing:
                raise ValueError(f"Telegram ID '{telegram_id}' already linked")
        return await self.update_user(user_id, telegram_id=telegram_id)

    # ── API Key methods ────────────────────────────────────────────────────

    async def generate_api_key(self, user_id: str, name: str = "") -> tuple[str, dict]:
        """Generate a new API key for user. Returns (full_key, key_row_dict)."""
        user = await self.get_user(user_id)
        if not user:
            raise ValueError(f"User {user_id} not found")

        full_key, prefix, key_hash = _generate_api_key()
        key_id = self._uuid()
        now = self._now()

        await self._execute(
            """INSERT INTO api_keys (id, user_id, key_prefix, key_hash, name,
               is_active, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (key_id, user_id, prefix, key_hash, name, now),
        )

        key_row = await self._fetchone("SELECT * FROM api_keys WHERE id = ?", (key_id,))
        assert key_row is not None
        # Never return hash
        safe_row = {k: v for k, v in key_row.items() if k != "key_hash"}
        return full_key, safe_row

    async def resolve_api_key(self, raw_key: str) -> tuple[dict, dict] | None:
        """Resolve API key to (api_key_row, user_row) or None.

        Updates last_used_at on successful match.
        Uses self._conn singleton — NEVER opens a new connection.
        """
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        key_row = await self._fetchone(
            "SELECT * FROM api_keys WHERE key_hash = ? AND is_active = 1",
            (key_hash,),
        )
        if not key_row:
            return None

        # Update last_used_at
        now = self._now()
        await self._execute(
            "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
            (now, key_row["id"]),
        )

        user_row = await self._fetchone(
            "SELECT * FROM users WHERE id = ? AND is_active = 1",
            (key_row["user_id"],),
        )
        if not user_row:
            return None

        return (key_row, user_row)

    async def get_user_api_keys(self, user_id: str) -> list[dict]:
        """List user's API keys. Never exposes key_hash or full key."""
        rows = await self._fetchall(
            """SELECT id, key_prefix, name, is_active, last_used_at, created_at
               FROM api_keys WHERE user_id = ? ORDER BY created_at DESC""",
            (user_id,),
        )
        return rows

    async def revoke_api_key(self, key_id: str) -> bool:
        """Set API key is_active=0. Returns True if found and revoked."""
        cur = await self._execute(
            "UPDATE api_keys SET is_active = 0 WHERE id = ? AND is_active = 1",
            (key_id,),
        )
        return cur.rowcount > 0

    async def regenerate_api_key(self, user_id: str, old_key_id: str) -> tuple[str, dict]:
        """Revoke old key and generate a new one. Returns (full_key, key_row)."""
        await self.revoke_api_key(old_key_id)
        return await self.generate_api_key(user_id, name="regenerated")


# ── Module-level singleton ─────────────────────────────────────────────────

user_manager: UserManager | None = None


async def init_user_manager(db_path: str) -> UserManager:
    """Initialize the global UserManager singleton."""
    global user_manager
    if user_manager is None:
        user_manager = UserManager(db_path)
        await user_manager.initialize()
    return user_manager


async def close_user_manager() -> None:
    """Close the global UserManager singleton."""
    global user_manager
    if user_manager:
        await user_manager.close()
        user_manager = None


# ── Lazy bootstrap ────────────────────────────────────

_DEFAULT_DB_PATH: str | None = None


def _get_default_db_path() -> str:
    """Get or compute the default user DB path."""
    global _DEFAULT_DB_PATH
    if _DEFAULT_DB_PATH is None:
        data_dir = os.getenv("DATA_DIR", "/app/mem0_data_v3")
        _DEFAULT_DB_PATH = os.path.join(data_dir, "users.db")
    return _DEFAULT_DB_PATH


async def ensure_admin_exists(db_path: str | None = None) -> str | None:
    """Lazy-init UserManager and create default admin if none exists.

    Returns the initial API key plaintext if admin was created, None otherwise.
    Can be called safely even if UserManager is already initialized.

    This is a safety net: the lifespan should normally seed the admin first,
    but if something goes wrong at import time (missing dependency, etc.)
    before the lifespan runs, this ensures the system is still usable on
    the first API call.
    """
    global user_manager
    if user_manager is None:
        path = db_path or _get_default_db_path()
        await init_user_manager(path)

    try:
        admins = await user_manager.list_users(role="admin")
        if admins:
            return None  # Admin already exists

        logger.warning("⚠️ Safety net: no admin found — creating default admin...")
        _user, api_key = await user_manager.create_user(
            username="admin",
            password="neuronet",
            role="admin",
            display_name="Default Admin",
            allowed_projects=["*"],
        )
        logger.info("✅ Default admin created (safety net): admin / neuronet")
        logger.info("🔑 Initial API key: %s", api_key)
        logger.warning("⚠️ CHANGE THE DEFAULT PASSWORD ON FIRST LOGIN!")
        return api_key
    except Exception as exc:
        logger.error("❌ Safety net admin creation failed: %s", exc)
        return None
