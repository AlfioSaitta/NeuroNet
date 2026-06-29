"""Tests for OpenAI SQLite state engine.

Run: pytest tests/test_openai_state.py -x -v
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

# ── Import OpenAIDatabase without triggering the full package import chain ──
# In Docker the package import works; otherwise we use importlib to bypass
# heavy sub-module dependencies (qdrant, prompt_builder, etc.).
try:
    from jarvis.openai.state import OpenAIDatabase
except ImportError:
    import importlib.util
    import importlib.machinery
    import types

    # Mock the `config` module that state.py imports
    import logging
    config_mock = types.ModuleType("config")
    config_mock.DATA_DIR = "/tmp"
    config_mock.logger = logging.getLogger("test_openai_state")
    config_mock.logger.addHandler(logging.NullHandler())
    sys.modules["config"] = config_mock

    _state_path = Path(__file__).resolve().parent.parent / "jarvis" / "openai" / "state.py"
    _loader = importlib.machinery.SourceFileLoader("openai_state", str(_state_path))
    _spec = importlib.util.spec_from_loader("openai_state", _loader,
                                            origin=str(_state_path))
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["openai_state"] = _mod
    _loader.exec_module(_mod)
    OpenAIDatabase = _mod.OpenAIDatabase


# ── Helpers ──────────────────────────────────────────────────────────────────

@pytest.fixture
async def db():
    """Create a fresh OpenAIDatabase in a temp directory for each test."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test_openai.db")
        _db = OpenAIDatabase(db_path)
        await _db.initialize()
        yield _db
        await _db.close()


def _check_openai_entity(entity: dict, expected_object: str):
    """Validate common OpenAI entity fields."""
    assert entity is not None
    assert "id" in entity
    assert entity["object"] == expected_object
    assert entity["created_at"] > 0


# ── Assistant CRUD ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_assistant(db):
    a = await db.create_assistant(
        model="qwen3.5",
        name="Test Assistant",
        description="A test assistant",
        instructions="Help the user.",
        tools=[{"type": "code_interpreter"}],
        metadata={"project": "test"},
        temperature=0.7,
        top_p=0.9,
    )
    _check_openai_entity(a, "assistant")
    assert a["model"] == "qwen3.5"
    assert a["name"] == "Test Assistant"
    assert a["description"] == "A test assistant"
    assert a["instructions"] == "Help the user."
    assert a["tools"] == [{"type": "code_interpreter"}]
    assert a["metadata"] == {"project": "test"}
    assert a["temperature"] == 0.7
    assert a["top_p"] == 0.9


@pytest.mark.asyncio
async def test_get_assistant(db):
    a = await db.create_assistant(model="qwen3.5", name="get-test")
    a_id = a["id"]

    got = await db.get_assistant(a_id)
    assert got is not None
    assert got["id"] == a_id
    assert got["name"] == "get-test"

    # Non-existent returns None
    assert await db.get_assistant("nonexistent") is None


@pytest.mark.asyncio
async def test_list_assistants(db):
    # Create 3 assistants with small delays so created_at differs
    import asyncio
    ids = []
    for i in range(3):
        a = await db.create_assistant(model="qwen3.5", name=f"a{i}")
        ids.append(a["id"])
        await asyncio.sleep(0.01)

    lst = await db.list_assistants(limit=10)
    assert len(lst) >= 3
    # Most recent first (desc order)
    assert lst[0]["id"] == ids[-1]


@pytest.mark.asyncio
async def test_update_assistant(db):
    a = await db.create_assistant(model="qwen3.5", name="original")
    a_id = a["id"]

    updated = await db.update_assistant(a_id, {"name": "renamed", "temperature": 1.0})
    assert updated["name"] == "renamed"
    assert updated["temperature"] == 1.0
    # Immutable fields preserved
    assert updated["id"] == a_id
    assert updated["model"] == "qwen3.5"


@pytest.mark.asyncio
async def test_delete_assistant(db):
    a = await db.create_assistant(model="qwen3.5", name="delete-me")
    a_id = a["id"]

    assert await db.delete_assistant(a_id) is True
    assert await db.get_assistant(a_id) is None
    # Double delete returns False
    assert await db.delete_assistant(a_id) is False


# ── Thread + Message CRUD ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_thread(db):
    t = await db.create_thread(messages=[
        {"role": "user", "content": "Hello!"},
        {"role": "assistant", "content": "Hi there!"},
    ], metadata={"channel": "test"})
    _check_openai_entity(t, "thread")
    assert t["metadata"] == {"channel": "test"}

    # Messages should be created
    msgs = await db.list_messages(t["id"])
    assert len(msgs) == 2
    assert msgs[0]["role"] == "assistant"  # desc order = most recent first
    assert msgs[1]["role"] == "user"
    assert msgs[0]["content"] == "Hi there!"


@pytest.mark.asyncio
async def test_create_message(db):
    t = await db.create_thread()
    m = await db.create_message(
        thread_id=t["id"],
        role="user",
        content="What is AI?",
        metadata={"source": "test"},
    )
    _check_openai_entity(m, "thread.message")
    assert m["thread_id"] == t["id"]
    assert m["role"] == "user"
    assert m["content"][0]["text"] == "What is AI?"
    assert m["metadata"] == {"source": "test"}


@pytest.mark.asyncio
async def test_list_messages_pagination(db):
    t = await db.create_thread()
    ids = []
    for i in range(5):
        m = await db.create_message(thread_id=t["id"], role="user",
                                    content=f"msg {i}")
        ids.append(m["id"])

    # Default list (desc, limit 20) — newest first
    all_msgs = await db.list_messages(t["id"])
    assert len(all_msgs) == 5
    assert all_msgs[0]["id"] == ids[-1]

    # Pagination with limit (returns limit+1 for pagination detection)
    page = await db.list_messages(t["id"], limit=2)
    assert len(page) == 3  # limit+1 = 3
    assert page[0]["id"] == ids[-1]

    # Pagination with after
    page2 = await db.list_messages(t["id"], limit=2, after=page[-1]["id"])
    assert len(page2) >= 1


# ── Run CRUD ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_run(db):
    a = await db.create_assistant(model="qwen3.5", name="run-test")
    t = await db.create_thread()
    await db.create_message(thread_id=t["id"], role="user", content="Hello")

    r = await db.create_run(
        thread_id=t["id"],
        assistant_id=a["id"],
        model="qwen3.5",
        instructions="Be helpful",
        temperature=0.5,
    )
    _check_openai_entity(r, "thread.run")
    assert r["thread_id"] == t["id"]
    assert r["assistant_id"] == a["id"]
    assert r["status"] == "queued"
    assert r["temperature"] == 0.5
    assert r["expires_at"] > r["created_at"]


@pytest.mark.asyncio
async def test_run_status_transitions(db):
    a = await db.create_assistant(model="qwen3.5")
    t = await db.create_thread()
    r = await db.create_run(thread_id=t["id"], assistant_id=a["id"])
    r_id = r["id"]

    # queued -> in_progress -> completed
    await db.update_run(r_id, {"status": "in_progress", "started_at": db._now()})
    await db.update_run(r_id, {"status": "completed", "completed_at": db._now()})
    done = await db.get_run(r_id)
    assert done["status"] == "completed"
    assert done["started_at"] is not None
    assert done["completed_at"] > done["created_at"]


@pytest.mark.asyncio
async def test_run_steps(db):
    a = await db.create_assistant(model="qwen3.5")
    t = await db.create_thread()
    r = await db.create_run(thread_id=t["id"], assistant_id=a["id"])
    r_id = r["id"]

    # Create a step
    s = await db.create_run_step(
        run_id=r_id,
        thread_id=t["id"],
        type="message_creation",
        step_details={"message_id": "msg-abc"},
    )
    _check_openai_entity(s, "thread.run.step")
    assert s["run_id"] == r_id
    assert s["type"] == "message_creation"
    assert s["step_details"] == {"message_id": "msg-abc"}

    # List steps
    steps = await db.list_run_steps(r_id)
    assert len(steps) == 1

    # Update step
    s2 = await db.update_run_step(s["id"], {"status": "completed"})
    assert s2["status"] == "completed"


# ── File CRUD ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_file_crud(db):
    f = await db.create_file(filename="test.txt", purpose="assistants", bytes=42)
    _check_openai_entity(f, "file")
    assert f["filename"] == "test.txt"
    assert f["purpose"] == "assistants"
    assert f["bytes"] == 42
    f_id = f["id"]

    got = await db.get_file(f_id)
    assert got["id"] == f_id

    files = await db.list_files(purpose="assistants")
    assert len(files) >= 1

    await db.delete_file(f_id)
    assert await db.get_file(f_id) is None


# ── Vector Store CRUD ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vector_store_crud(db):
    vs = await db.create_vector_store(
        name="test-store",
        metadata={"env": "test"},
    )
    _check_openai_entity(vs, "vector_store")
    assert vs["name"] == "test-store"
    assert vs["status"] == "active"
    vs_id = vs["id"]

    got = await db.get_vector_store(vs_id)
    assert got["name"] == "test-store"

    listed = await db.list_vector_stores()
    assert len(listed) >= 1

    updated = await db.update_vector_store(vs_id, {"name": "renamed"})
    assert updated["name"] == "renamed"

    await db.delete_vector_store(vs_id)
    assert await db.get_vector_store(vs_id) is None


# ── Batch CRUD ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_batch_crud(db):
    b = await db.create_batch(
        endpoint="/v1/chat/completions",
        input_file_id="file-abc",
        metadata={"env": "test"},
    )
    _check_openai_entity(b, "batch")
    assert b["status"] == "validating"
    assert b["completion_window"] == "24h"
    b_id = b["id"]

    await db.cancel_batch(b_id)
    assert (await db.get_batch(b_id))["status"] == "cancelled"


# ── Fine-tuning Job CRUD ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fine_tuning_job_crud(db):
    fj = await db.create_fine_tuning_job(
        model="qwen3.5",
        training_file="file-train",
        hyperparameters={"n_epochs": 3},
    )
    _check_openai_entity(fj, "fine_tuning.job")
    assert fj["status"] == "validating"
    fj_id = fj["id"]

    await db.cancel_fine_tuning_job(fj_id)
    assert (await db.get_fine_tuning_job(fj_id))["status"] == "cancelled"


# ── Cascade delete behaviour ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cascade_delete_thread(db):
    """Deleting a thread should cascade-delete its messages."""
    t = await db.create_thread(messages=[{"role": "user", "content": "Hello"}])
    t_id = t["id"]

    msgs_before = await db.list_messages(t_id)
    assert len(msgs_before) == 1

    await db.delete_thread(t_id)
    msgs_after = await db.list_messages(t_id)
    assert len(msgs_after) == 0


# ── Edge cases ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_nonexistent_returns_none(db):
    assert await db.get_assistant("does-not-exist") is None
    assert await db.get_thread("does-not-exist") is None
    assert await db.get_message("does-not-exist") is None
    assert await db.get_run("does-not-exist") is None
    assert await db.get_file("does-not-exist") is None
    assert await db.get_vector_store("does-not-exist") is None


@pytest.mark.asyncio
async def test_update_nonexistent_returns_none(db):
    result = await db.update_assistant("no-such-id", {"name": "x"})
    assert result is None
