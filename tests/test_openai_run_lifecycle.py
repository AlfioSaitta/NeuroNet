"""Poll-based run lifecycle test — exercises execute_run with a mocked LLM.

Verifies the full pipeline:
  queued → in_progress → (LLM call) → completed
  + messages created, steps created, correct content

Run:  pytest tests/test_openai_run_lifecycle.py -x -v  (DOES NOT require Docker/LLM)
"""
import os
import sys
import json
import types
import asyncio
import logging
import tempfile

import pytest


# ── Module-level setup (BEFORE any imports of the modules under test) ────────────

# 1) Mock ``config`` — required by jarvis.openai.state and jarvis.openai.run_engine
_config_mock = types.ModuleType("config")
_config_mock.logger = logging.getLogger("test_lifecycle")
_config_mock.logger.addHandler(logging.NullHandler())
_config_mock.DATA_DIR = tempfile.gettempdir()
sys.modules["config"] = _config_mock

# 2) Mock ``llm_engine`` — required by run_engine at runtime (imported inside functions)
class _MockEngine:
    def __init__(self):
        self.response = {
            "choices": [{
                "message": {
                    "content": "Hello from mock LLM!",
                }
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    async def generate_chat_with_router(self, messages, tools=None,
                                        options=None, stream=False):
        """Return the pre-configured response."""
        return self.response

_mock_engine = _MockEngine()
_mock_llm_pkg = types.ModuleType("llm_engine")
_mock_llm_pkg.engine = _mock_engine
sys.modules["llm_engine"] = _mock_llm_pkg


# Now import the modules under test — via jarvis.openai (normal package path).
from jarvis.openai.state import OpenAIDatabase
from jarvis.openai.run_engine import execute_run


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def mock_engine(request):
    """Return the controllable mock LLM engine — mutate ``.response`` per test.

    Automatically resets to the default response after each test so mutations
    from one test (e.g. ``test_llm_error_fails_run``) don't leak into the next.
    """
    # Before test: ensure default response
    _mock_engine.response = {
        "choices": [{
            "message": {
                "content": "Hello from mock LLM!",
            }
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    yield _mock_engine
    # After test: reset to default
    _mock_engine.response = {
        "choices": [{
            "message": {
                "content": "Hello from mock LLM!",
            }
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
async def db(tmp_path):
    """Fresh in-memory SQLite database for each test."""
    db_path = str(tmp_path / "test_openai_state.db")
    d = OpenAIDatabase(db_path)
    await d.initialize()

    # Create minimal seed data: assistant + thread + one user message
    assistant = await d.create_assistant(
        model="test-model",
        name="Test Bot",
        instructions="You are a helpful test assistant.",
        tools=[],
    )
    thread = await d.create_thread(
        messages=[{"role": "user", "content": "Hello, test bot!"}],
    )
    yield d, assistant, thread

    await d.close()
    if os.path.exists(db_path):
        os.unlink(db_path)


# ── Helper ───────────────────────────────────────────────────────────────────

async def _poll_run(db, run_id, timeout=10.0, interval=0.05):
    """Poll DB until run reaches a terminal state or timeout."""
    terminal = {"completed", "failed", "cancelled", "expired"}
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        run = await db.get_run(run_id)
        if run and run["status"] in terminal:
            return run
        if asyncio.get_event_loop().time() >= deadline:
            pytest.fail(f"Run {run_id} did not reach terminal state within {timeout}s "
                        f"(last status: {run['status'] if run else 'None'})")
        await asyncio.sleep(interval)


# ── Lifecycle Tests ──────────────────────────────────────────────────────────

class TestRunLifecycle:

    @pytest.mark.asyncio
    async def test_simple_chat_completes(self, db, mock_engine):
        """A basic run with no tools → LLM responds → run completes."""
        d, assistant, thread = db
        assistant_id = assistant["id"]
        thread_id = thread["id"]

        # Create run
        run = await d.create_run(
            thread_id=thread_id,
            assistant_id=assistant_id,
            instructions="Answer concisely.",
        )
        run_id = run["id"]
        assert run["status"] == "queued"

        # Spawn execution (background)
        task = asyncio.create_task(execute_run(run_id, d))

        # Poll until terminal
        completed_run = await _poll_run(d, run_id)
        await task

        assert completed_run["status"] == "completed", (
            f"Expected completed, got {completed_run['status']}: "
            f"{completed_run.get('last_error')}")

        # ── Verify assistant message was created ─────────────────────────────
        msgs = await d.list_messages(thread_id, limit=10, order="desc")
        # The newest message should be the assistant response
        assert len(msgs) >= 2, f"Expected ≥2 messages, got {len(msgs)}"

        assistant_msg = msgs[0]  # newest
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["run_id"] == run_id
        assert assistant_msg["assistant_id"] == assistant_id

        # Extract text from content (list of parts)
        content = assistant_msg["content"]
        if isinstance(content, list):
            full = "".join(
                p["text"] for p in content if p.get("type") == "text" and p.get("text")
            )
        else:
            full = str(content)
        assert "Hello from mock LLM" in full

        # ── Verify run steps ─────────────────────────────────────────────────
        steps = await d.list_run_steps(run_id, limit=10)
        assert len(steps) >= 1
        step = steps[0]  # newest step
        assert step["type"] == "message_creation"
        assert step["status"] == "completed"
        assert step["run_id"] == run_id

        # ── Verify usage recorded on run ─────────────────────────────────────
        final = await d.get_run(run_id)
        usage = final.get("usage")
        assert usage is not None
        assert usage["total_tokens"] == 15

    @pytest.mark.asyncio
    async def test_tool_call_requires_action(self, db, mock_engine):
        """When LLM returns tool_calls, run transitions to requires_action."""
        mock_engine.response = {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": '{"city": "Rome"}',
                            },
                        }
                    ],
                }
            }],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        }

        d, assistant, thread = db
        assistant_id = assistant["id"]
        thread_id = thread["id"]

        # Create run with tools defined
        run = await d.create_run(
            thread_id=thread_id,
            assistant_id=assistant_id,
            tools=[{
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather for a city",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }],
        )
        run_id = run["id"]
        assert run["status"] == "queued"

        task = asyncio.create_task(execute_run(run_id, d))

        # Poll — should land on requires_action (NOT completed)
        terminal = {"requires_action", "completed", "failed", "cancelled", "expired"}
        deadline = asyncio.get_event_loop().time() + 10.0
        while True:
            r = await d.get_run(run_id)
            assert r is not None
            if r["status"] in terminal:
                break
            if asyncio.get_event_loop().time() >= deadline:
                pytest.fail("Run did not reach terminal/requires_action state")
            await asyncio.sleep(0.05)

        await task

        assert r["status"] == "requires_action", (
            f"Expected requires_action, got {r['status']}")

        required = r.get("required_action")
        assert required is not None
        assert required["type"] == "submit_tool_outputs"
        tcs = required["submit_tool_outputs"]["tool_calls"]
        assert len(tcs) == 1
        assert tcs[0]["function"]["name"] == "get_weather"

        # ── Tool-call step should exist ──────────────────────────────────────
        steps = await d.list_run_steps(run_id, limit=10)
        tool_steps = [s for s in steps if s["type"] == "tool_calls"]
        assert len(tool_steps) == 1
        assert tool_steps[0]["status"] == "in_progress"

        # ── An assistant message should ALSO exist (the one that triggered tool_calls)
        msgs = await d.list_messages(thread_id, limit=10, order="desc")
        assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
        assert len(assistant_msgs) >= 1

    @pytest.mark.asyncio
    async def test_assistant_not_found_fails(self, db):
        """A run referencing a deleted assistant → failed status."""
        d, assistant, thread = db

        # Delete the assistant
        await d.delete_assistant(assistant["id"])

        # Create run referencing now-deleted assistant
        run = await d.create_run(
            thread_id=thread["id"],
            assistant_id=assistant["id"],
        )
        run_id = run["id"]

        task = asyncio.create_task(execute_run(run_id, d))
        failed_run = await _poll_run(d, run_id)
        await task

        assert failed_run["status"] == "failed"
        err = failed_run.get("last_error", {})
        assert err.get("code") == "assistant_not_found"

    @pytest.mark.asyncio
    async def test_llm_error_fails_run(self, db, mock_engine):
        """When the LLM returns an error, the run should fail."""
        mock_engine.response = {"error": "Model overloaded"}

        d, assistant, thread = db
        run = await d.create_run(
            thread_id=thread["id"],
            assistant_id=assistant["id"],
        )
        run_id = run["id"]

        task = asyncio.create_task(execute_run(run_id, d))
        failed_run = await _poll_run(d, run_id)
        await task

        assert failed_run["status"] == "failed"
        err = failed_run.get("last_error", {})
        assert err.get("code") == "llm_error"
        assert "overloaded" in err.get("message", "")

    @pytest.mark.asyncio
    async def test_concurrent_runs_independent(self, db, mock_engine):
        """Two concurrent runs on different threads should both complete."""
        d, assistant, thread = db

        # Create a second thread
        thread2 = await d.create_thread(
            messages=[{"role": "user", "content": "Run 2 message"}],
        )

        run1 = await d.create_run(thread_id=thread["id"], assistant_id=assistant["id"])
        run2 = await d.create_run(thread_id=thread2["id"], assistant_id=assistant["id"])

        task1 = asyncio.create_task(execute_run(run1["id"], d))
        task2 = asyncio.create_task(execute_run(run2["id"], d))

        r1 = await _poll_run(d, run1["id"])
        r2 = await _poll_run(d, run2["id"])
        await task1
        await task2

        assert r1["status"] == "completed", (
            f"Run 1 failed: {r1.get('last_error')}")
        assert r2["status"] == "completed", (
            f"Run 2 failed: {r2.get('last_error')}")
