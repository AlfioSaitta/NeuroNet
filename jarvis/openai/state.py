"""
SQLite state engine for OpenAI-compatible entities.
Async CRUD over aiosqlite — no business logic.
"""
import asyncio
import json
import os
import time
import uuid
from typing import Optional

import aiosqlite

from config import DATA_DIR, logger

# ── Schema DDL ──────────────────────────────────────────────────────────────
# Each table stores entities as OpenAI-shaped dicts with a TEXT id PK.
# JSON-serialisable fields (tools, metadata, content, etc.) are stored as TEXT
# and deserialised on read.  `object` is always a static discriminant like
# "assistant" / "thread" / "thread.message" per the OpenAI spec.

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS assistants (
    id          TEXT PRIMARY KEY,
    object      TEXT NOT NULL DEFAULT 'assistant',
    created_at  INTEGER NOT NULL,
    name        TEXT,
    description TEXT,
    model       TEXT NOT NULL,
    instructions TEXT,
    tools       TEXT DEFAULT '[]',
    metadata    TEXT DEFAULT '{}',
    temperature REAL,
    top_p       REAL,
    response_format TEXT
);

CREATE TABLE IF NOT EXISTS threads (
    id          TEXT PRIMARY KEY,
    object      TEXT NOT NULL DEFAULT 'thread',
    created_at  INTEGER NOT NULL,
    metadata    TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    object      TEXT NOT NULL DEFAULT 'thread.message',
    created_at  INTEGER NOT NULL,
    thread_id   TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK(role IN ('user','assistant','tool')),
    content     TEXT NOT NULL DEFAULT '[]',
    assistant_id TEXT,
    run_id      TEXT,
    file_ids    TEXT DEFAULT '[]',
    metadata    TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    object      TEXT NOT NULL DEFAULT 'thread.run',
    created_at  INTEGER NOT NULL,
    thread_id   TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    assistant_id TEXT NOT NULL,
    model       TEXT,
    instructions TEXT,
    tools       TEXT DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'queued',
    started_at  INTEGER,
    completed_at INTEGER,
    expires_at  INTEGER,
    cancelled_at INTEGER,
    failed_at   INTEGER,
    last_error  TEXT,
    required_action TEXT,
    usage       TEXT,
    temperature REAL,
    top_p       REAL,
    max_prompt_tokens INTEGER,
    max_completion_tokens INTEGER,
    truncation_strategy TEXT,
    tool_choice TEXT,
    response_format TEXT,
    metadata    TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS run_steps (
    id          TEXT PRIMARY KEY,
    object      TEXT NOT NULL DEFAULT 'thread.run.step',
    created_at  INTEGER NOT NULL,
    run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    assistant_id TEXT,
    thread_id   TEXT NOT NULL,
    type        TEXT NOT NULL CHECK(type IN ('message_creation','tool_calls')),
    status      TEXT NOT NULL DEFAULT 'in_progress',
    step_details TEXT NOT NULL DEFAULT '{}',
    last_error  TEXT,
    expired_at  INTEGER,
    completed_at INTEGER,
    usage       TEXT
);

CREATE TABLE IF NOT EXISTS files (
    id            TEXT PRIMARY KEY,
    object        TEXT NOT NULL DEFAULT 'file',
    created_at    INTEGER NOT NULL,
    bytes         INTEGER DEFAULT 0,
    filename      TEXT NOT NULL,
    purpose       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'uploaded',
    status_details TEXT
);

CREATE TABLE IF NOT EXISTS vector_stores (
    id            TEXT PRIMARY KEY,
    object        TEXT NOT NULL DEFAULT 'vector_store',
    created_at    INTEGER NOT NULL,
    name          TEXT,
    file_counts   TEXT DEFAULT '{}',
    metadata      TEXT DEFAULT '{}',
    usage_bytes   INTEGER DEFAULT 0,
    expires_after TEXT,
    status        TEXT NOT NULL DEFAULT 'active',
    last_active_at INTEGER
);

CREATE TABLE IF NOT EXISTS vector_store_files (
    id              TEXT PRIMARY KEY,
    object          TEXT NOT NULL DEFAULT 'vector_store.file',
    created_at      INTEGER NOT NULL,
    vector_store_id TEXT NOT NULL REFERENCES vector_stores(id) ON DELETE CASCADE,
    file_id         TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS batches (
    id                TEXT PRIMARY KEY,
    object            TEXT NOT NULL DEFAULT 'batch',
    created_at        INTEGER NOT NULL,
    input_file_id     TEXT,
    endpoint          TEXT,
    completion_window TEXT,
    status            TEXT NOT NULL DEFAULT 'validating',
    request_counts    TEXT DEFAULT '{"total":0,"completed":0,"failed":0}',
    metadata          TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS fine_tuning_jobs (
    id              TEXT PRIMARY KEY,
    object          TEXT NOT NULL DEFAULT 'fine_tuning.job',
    created_at      INTEGER NOT NULL,
    model           TEXT NOT NULL,
    training_file   TEXT,
    validation_file TEXT,
    hyperparameters TEXT DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'validating',
    trained_tokens  INTEGER DEFAULT 0,
    result_files    TEXT DEFAULT '[]',
    metadata        TEXT DEFAULT '{}'
);
"""


class OpenAIDatabase:
    """Async SQLite state engine for OpenAI entities.

    **Usage** — always call via ``get_db()``::

        db = await get_db()
        assistant = await db.create_assistant({"name": "Test", "model": "qwen"})

    ``get_db()`` lazily creates and initialises the singleton on first call
    with a lock to prevent race conditions on concurrent startup requests.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Open connection and run DDL."""
        logger.info("OpenAI state DB: %s", self.db_path)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.executescript(SCHEMA_SQL)
        await self._conn.commit()
        logger.info("OpenAI state DB ready")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if not self._conn:
            raise RuntimeError("OpenAIDatabase not initialised — call .initialize() first")
        return self._conn

    # ── Helpers ────────────────────────────────────────────────────────────

    _last_ts: int = 0

    @staticmethod
    def _now() -> int:
        """Return a strictly-increasing Unix-millisecond timestamp."""
        now = int(time.time() * 1000)
        if now <= OpenAIDatabase._last_ts:
            now = OpenAIDatabase._last_ts + 1
        OpenAIDatabase._last_ts = now
        return now

    @staticmethod
    def _uuid() -> str:
        return uuid.uuid4().hex

    @staticmethod
    def _json(val) -> str:
        return json.dumps(val, ensure_ascii=False) if val is not None else "{}"

    @staticmethod
    def _row_to_dict(row: aiosqlite.Row) -> dict:
        """Convert aiosqlite Row to plain dict, parsing JSON columns."""
        d = dict(row)
        for col in ("tools", "metadata", "file_ids", "content",
                     "step_details", "last_error", "required_action",
                     "usage", "truncation_strategy", "file_counts",
                     "request_counts", "hyperparameters", "result_files",
                     "expires_after", "status_details"):
            if col in d and isinstance(d[col], str):
                try:
                    d[col] = json.loads(d[col])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d

    @staticmethod
    def _rows_to_list(rows: list[aiosqlite.Row]) -> list[dict]:
        return [OpenAIDatabase._row_to_dict(r) for r in rows]

    async def _execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        cur = await self.conn.execute(sql, params)
        return cur

    async def _fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        cur = await self._execute(sql, params)
        row = await cur.fetchone()
        return self._row_to_dict(row) if row else None

    async def _fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        cur = await self._execute(sql, params)
        rows = await cur.fetchall()
        return self._rows_to_list(rows)

    async def _execute_commit(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        cur = await self._execute(sql, params)
        await self.conn.commit()
        return cur

    # ── Generic CRUD ───────────────────────────────────────────────────────

    async def create(self, table: str, data: dict,
                     id_field: str = "id") -> dict:
        """Insert a row and return the full entity."""
        if id_field not in data:
            data[id_field] = self._uuid()
        if "created_at" not in data:
            data["created_at"] = self._now()

        cols = list(data.keys())
        placeholders = ", ".join("?" for _ in cols)
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
        values = [json.dumps(v) if isinstance(v, (dict, list)) else v
                  for v in data.values()]
        await self._execute_commit(sql, tuple(values))
        return await self.get(table, data[id_field])

    async def get(self, table: str, id: str) -> Optional[dict]:
        return await self._fetchone(f"SELECT * FROM {table} WHERE id = ?", (id,))

    async def list_entities(self, table: str, *,
                   limit: int = 20, after: Optional[str] = None,
                   before: Optional[str] = None, order: str = "desc",
                   **filters) -> list[dict]:
        """List entities with optional cursor pagination and field filters."""
        where_clauses = []
        params: list = []

        if after:
            op = "<" if order == "desc" else ">"
            where_clauses.append(f"created_at {op} (SELECT created_at FROM {table} WHERE id = ?)")
            params.append(after)
        if before:
            op = ">" if order == "desc" else "<"
            where_clauses.append(f"created_at {op} (SELECT created_at FROM {table} WHERE id = ?)")
            params.append(before)

        for col, val in filters.items():
            if val is not None:
                where_clauses.append(f"{col} = ?")
                params.append(val)

        where = ""
        if where_clauses:
            where = "WHERE " + " AND ".join(where_clauses)

        dir = "DESC" if order == "desc" else "ASC"
        sql = f"SELECT * FROM {table} {where} ORDER BY created_at {dir}, id {dir} LIMIT ?"
        params.append(limit + 1)  # fetch one extra to detect next page
        return await self._fetchall(sql, tuple(params))

    async def update(self, table: str, id: str,
                     data: dict) -> Optional[dict]:
        """Partial update.  Returns the updated entity or None if not found."""
        if not data:
            return await self.get(table, id)

        set_clauses = []
        params: list = []
        for col, val in data.items():
            if col in ("id", "created_at", "object"):
                continue  # immutable fields
            set_clauses.append(f"{col} = ?")
            params.append(json.dumps(val) if isinstance(val, (dict, list)) else val)

        if not set_clauses:
            return await self.get(table, id)

        params.append(id)
        sql = f"UPDATE {table} SET {', '.join(set_clauses)} WHERE id = ?"
        cur = await self._execute_commit(sql, tuple(params))
        if cur.rowcount == 0:
            return None
        return await self.get(table, id)

    async def delete(self, table: str, id: str) -> bool:
        cur = await self._execute_commit(
            f"DELETE FROM {table} WHERE id = ?", (id,))
        return cur.rowcount > 0

    # ── Entity-specific helpers ─────────────────────────────────────────────

    # ── Assistants ──────────────────────────────────────────────────────────

    async def create_assistant(self, *, model: str,
                               name: Optional[str] = None,
                               description: Optional[str] = None,
                               instructions: Optional[str] = None,
                               tools: Optional[list] = None,
                               metadata: Optional[dict] = None,
                               temperature: Optional[float] = None,
                               top_p: Optional[float] = None,
                               response_format: Optional[str] = None) -> dict:
        return await self.create("assistants", {
            "object": "assistant",
            "model": model,
            "name": name,
            "description": description,
            "instructions": instructions,
            "tools": tools or [],
            "metadata": metadata or {},
            "temperature": temperature,
            "top_p": top_p,
            "response_format": response_format,
        })

    async def get_assistant(self, assistant_id: str) -> Optional[dict]:
        return await self.get("assistants", assistant_id)

    async def list_assistants(self, *, limit: int = 20,
                              after: Optional[str] = None,
                              before: Optional[str] = None,
                              order: str = "desc") -> list[dict]:
        return await self.list_entities("assistants", limit=limit,
                               after=after, before=before, order=order)

    async def update_assistant(self, assistant_id: str,
                               data: dict) -> Optional[dict]:
        return await self.update("assistants", assistant_id, data)

    async def delete_assistant(self, assistant_id: str) -> bool:
        return await self.delete("assistants", assistant_id)

    # ── Threads ─────────────────────────────────────────────────────────────

    async def create_thread(self, *,
                            messages: Optional[list[dict]] = None,
                            metadata: Optional[dict] = None) -> dict:
        thread_id = self._uuid()
        thread = await self.create("threads", {
            "id": thread_id,
            "object": "thread",
            "metadata": metadata or {},
        })
        if messages:
            for msg in messages:
                msg.setdefault("role", "user")
                await self.create("messages", {
                    "thread_id": thread_id,
                    "role": msg["role"],
                    "content": msg.get("content", ""),
                    "file_ids": msg.get("file_ids", []),
                    "metadata": msg.get("metadata", {}),
                    "assistant_id": msg.get("assistant_id"),
                    "run_id": msg.get("run_id"),
                })
        return thread

    async def get_thread(self, thread_id: str) -> Optional[dict]:
        return await self.get("threads", thread_id)

    async def update_thread(self, thread_id: str,
                            data: dict) -> Optional[dict]:
        return await self.update("threads", thread_id, data)

    async def delete_thread(self, thread_id: str) -> bool:
        return await self.delete("threads", thread_id)

    # ── Messages ────────────────────────────────────────────────────────────

    async def create_message(self, *, thread_id: str, role: str,
                             content: str | list,
                             assistant_id: Optional[str] = None,
                             run_id: Optional[str] = None,
                             file_ids: Optional[list] = None,
                             metadata: Optional[dict] = None) -> dict:
        if isinstance(content, str):
            content_parts = [{"type": "text", "text": content}]
        else:
            content_parts = content
        return await self.create("messages", {
            "object": "thread.message",
            "thread_id": thread_id,
            "role": role,
            "content": content_parts,
            "assistant_id": assistant_id,
            "run_id": run_id,
            "file_ids": file_ids or [],
            "metadata": metadata or {},
        })

    async def get_message(self, message_id: str) -> Optional[dict]:
        return await self.get("messages", message_id)

    async def list_messages(self, thread_id: str, *,
                            limit: int = 20,
                            after: Optional[str] = None,
                            before: Optional[str] = None,
                            order: str = "desc",
                            run_id: Optional[str] = None) -> list[dict]:
        return await self.list_entities("messages", limit=limit, after=after,
                               before=before, order=order,
                               thread_id=thread_id, run_id=run_id)

    async def update_message(self, message_id: str,
                             data: dict) -> Optional[dict]:
        return await self.update("messages", message_id, data)

    async def delete_message(self, message_id: str) -> bool:
        return await self.delete("messages", message_id)

    # ── Runs ────────────────────────────────────────────────────────────────

    async def create_run(self, *, thread_id: str, assistant_id: str,
                         model: Optional[str] = None,
                         instructions: Optional[str] = None,
                         tools: Optional[list] = None,
                         temperature: Optional[float] = None,
                         top_p: Optional[float] = None,
                         max_prompt_tokens: Optional[int] = None,
                         max_completion_tokens: Optional[int] = None,
                         truncation_strategy: Optional[dict] = None,
                         tool_choice: Optional[str] = None,
                         response_format: Optional[str] = None,
                         metadata: Optional[dict] = None) -> dict:
        now = self._now()
        return await self.create("runs", {
            "object": "thread.run",
            "thread_id": thread_id,
            "assistant_id": assistant_id,
            "model": model,
            "instructions": instructions,
            "tools": tools or [],
            "status": "queued",
            "created_at": now,
            "expires_at": now + 1800,
            "temperature": temperature,
            "top_p": top_p,
            "max_prompt_tokens": max_prompt_tokens,
            "max_completion_tokens": max_completion_tokens,
            "truncation_strategy": truncation_strategy,
            "tool_choice": tool_choice,
            "response_format": response_format,
            "metadata": metadata or {},
        })

    async def get_run(self, run_id: str) -> Optional[dict]:
        return await self.get("runs", run_id)

    async def list_runs(self, thread_id: str, *,
                        limit: int = 20,
                        after: Optional[str] = None,
                        before: Optional[str] = None,
                        order: str = "desc") -> list[dict]:
        return await self.list_entities("runs", limit=limit, after=after,
                               before=before, order=order,
                               thread_id=thread_id)

    async def update_run(self, run_id: str, data: dict) -> Optional[dict]:
        return await self.update("runs", run_id, data)

    async def delete_run(self, run_id: str) -> bool:
        return await self.delete("runs", run_id)

    # ── Run Steps ──────────────────────────────────────────────────────────

    async def create_run_step(self, *, run_id: str, thread_id: str,
                              assistant_id: Optional[str] = None,
                              type: str = "message_creation",
                              step_details: Optional[dict] = None) -> dict:
        return await self.create("run_steps", {
            "object": "thread.run.step",
            "run_id": run_id,
            "thread_id": thread_id,
            "assistant_id": assistant_id,
            "type": type,
            "status": "in_progress",
            "step_details": step_details or {},
        })

    async def get_run_step(self, step_id: str) -> Optional[dict]:
        return await self.get("run_steps", step_id)

    async def list_run_steps(self, run_id: str, *,
                             limit: int = 20,
                             after: Optional[str] = None,
                             before: Optional[str] = None,
                             order: str = "desc") -> list[dict]:
        return await self.list_entities("run_steps", limit=limit, after=after,
                               before=before, order=order, run_id=run_id)

    async def update_run_step(self, step_id: str,
                              data: dict) -> Optional[dict]:
        return await self.update("run_steps", step_id, data)

    # ── Files ───────────────────────────────────────────────────────────────

    async def create_file(self, *, filename: str, purpose: str,
                          bytes: int = 0) -> dict:
        return await self.create("files", {
            "object": "file",
            "filename": filename,
            "purpose": purpose,
            "bytes": bytes,
            "status": "uploaded",
        })

    async def get_file(self, file_id: str) -> Optional[dict]:
        return await self.get("files", file_id)

    async def list_files(self, *, purpose: Optional[str] = None,
                         limit: int = 20,
                         after: Optional[str] = None,
                         before: Optional[str] = None,
                         order: str = "desc") -> list[dict]:
        return await self.list_entities("files", limit=limit, after=after,
                               before=before, order=order,
                               purpose=purpose)

    async def delete_file(self, file_id: str) -> bool:
        return await self.delete("files", file_id)

    # ── Vector Stores ──────────────────────────────────────────────────────

    async def create_vector_store(self, *, name: Optional[str] = None,
                                  metadata: Optional[dict] = None,
                                  expires_after: Optional[dict] = None) -> dict:
        return await self.create("vector_stores", {
            "object": "vector_store",
            "name": name,
            "metadata": metadata or {},
            "usage_bytes": 0,
            "file_counts": {"in_progress": 0, "completed": 0,
                            "failed": 0, "cancelled": 0, "total": 0},
            "status": "active",
            "last_active_at": self._now(),
            "expires_after": expires_after,
        })

    async def get_vector_store(self, store_id: str) -> Optional[dict]:
        return await self.get("vector_stores", store_id)

    async def list_vector_stores(self, *, limit: int = 20,
                                 after: Optional[str] = None,
                                 before: Optional[str] = None,
                                 order: str = "desc") -> list[dict]:
        return await self.list_entities("vector_stores", limit=limit,
                               after=after, before=before, order=order)

    async def update_vector_store(self, store_id: str,
                                  data: dict) -> Optional[dict]:
        return await self.update("vector_stores", store_id, data)

    async def delete_vector_store(self, store_id: str) -> bool:
        return await self.delete("vector_stores", store_id)

    # ── Vector Store Files ─────────────────────────────────────────────────

    async def add_vector_store_file(self, *, vector_store_id: str,
                                    file_id: str) -> dict:
        return await self.create("vector_store_files", {
            "object": "vector_store.file",
            "vector_store_id": vector_store_id,
            "file_id": file_id,
        })

    async def remove_vector_store_file(self, store_file_id: str) -> bool:
        return await self.delete("vector_store_files", store_file_id)

    async def list_vector_store_files(self, vector_store_id: str, *,
                                      limit: int = 20,
                                      after: Optional[str] = None,
                                      before: Optional[str] = None,
                                      order: str = "desc") -> list[dict]:
        return await self.list_entities("vector_store_files", limit=limit,
                               after=after, before=before, order=order,
                               vector_store_id=vector_store_id)

    # ── Batches ────────────────────────────────────────────────────────────

    async def create_batch(self, *, input_file_id: Optional[str] = None,
                           endpoint: Optional[str] = None,
                           completion_window: Optional[str] = None,
                           metadata: Optional[dict] = None) -> dict:
        return await self.create("batches", {
            "object": "batch",
            "input_file_id": input_file_id,
            "endpoint": endpoint,
            "completion_window": completion_window or "24h",
            "status": "validating",
            "request_counts": {"total": 0, "completed": 0, "failed": 0},
            "metadata": metadata or {},
        })

    async def get_batch(self, batch_id: str) -> Optional[dict]:
        return await self.get("batches", batch_id)

    async def list_batches(self, *, limit: int = 20,
                           after: Optional[str] = None,
                           before: Optional[str] = None,
                           order: str = "desc") -> list[dict]:
        return await self.list_entities("batches", limit=limit,
                               after=after, before=before, order=order)

    async def cancel_batch(self, batch_id: str) -> Optional[dict]:
        return await self.update("batches", batch_id, {"status": "cancelled"})

    # ── Fine-tuning Jobs ───────────────────────────────────────────────────

    async def create_fine_tuning_job(self, *, model: str,
                                     training_file: Optional[str] = None,
                                     validation_file: Optional[str] = None,
                                     hyperparameters: Optional[dict] = None,
                                     metadata: Optional[dict] = None) -> dict:
        return await self.create("fine_tuning_jobs", {
            "object": "fine_tuning.job",
            "model": model,
            "training_file": training_file,
            "validation_file": validation_file,
            "hyperparameters": hyperparameters or {},
            "status": "validating",
            "trained_tokens": 0,
            "result_files": [],
            "metadata": metadata or {},
        })

    async def get_fine_tuning_job(self, job_id: str) -> Optional[dict]:
        return await self.get("fine_tuning_jobs", job_id)

    async def list_fine_tuning_jobs(self, *, limit: int = 20,
                                    after: Optional[str] = None,
                                    before: Optional[str] = None,
                                    order: str = "desc") -> list[dict]:
        return await self.list_entities("fine_tuning_jobs", limit=limit,
                               after=after, before=before, order=order)

    async def cancel_fine_tuning_job(self, job_id: str) -> Optional[dict]:
        return await self.update("fine_tuning_jobs",
                                 job_id, {"status": "cancelled"})


# ── Module-level singleton ────────────────────────────────────────────────────

_db_instance: Optional[OpenAIDatabase] = None
_init_lock = asyncio.Lock()


async def get_db() -> OpenAIDatabase:
    """Return the singleton OpenAIDatabase, initialising it on first call.

    Uses an ``asyncio.Lock`` to prevent race conditions when multiple
    concurrent requests arrive before initialisation completes.
    The DB path is derived from ``DATA_DIR`` so it can be used by any
    endpoint module without manual wiring.
    """
    global _db_instance
    if _db_instance is None:
        async with _init_lock:
            if _db_instance is None:  # double-check after acquiring lock
                from config import DATA_DIR as _data_dir
                instance = OpenAIDatabase(os.path.join(_data_dir, "openai_state.db"))
                await instance.initialize()
                _db_instance = instance
    return _db_instance
