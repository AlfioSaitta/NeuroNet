"""
Run execution engine — async Runner backed by LlamaEngine.

Orchestrates the Run lifecycle:
  queued → in_progress → (LLM call)
    ├── tool_calls? → requires_action → (client submit_tool_outputs) → in_progress → LLM call...
    └── no tool_calls → completed

Each LLM invocation creates a run_step (message_creation or tool_calls).
"""
import traceback
from typing import Optional

from config import logger
from .state import OpenAIDatabase


def _content_to_text(content) -> str:
    """Normalise thread-message content (str or list of parts) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(part.get("text", ""))
        return "\n".join(texts)
    return str(content)


def _messages_from_thread(thread_messages: list[dict],
                          instructions: Optional[str] = None) -> list[dict]:
    """Convert thread messages to LLM conversation format.

    If *instructions* (from the run or assistant) is provided it is prepended
    as a ``system`` message.
    """
    msgs: list[dict] = []
    if instructions:
        msgs.append({"role": "system", "content": instructions})

    for m in reversed(thread_messages):  # thread stores newest-first
        role = m.get("role", "user")
        content = _content_to_text(m.get("content", ""))
        msgs.append({"role": role, "content": content})

    return msgs


# ── Run execution ─────────────────────────────────────────────────────────────


async def execute_run(run_id: str, db: OpenAIDatabase) -> None:
    """Execute a Run asynchronously.

    Intended to be spawned as ``asyncio.create_task()`` so the HTTP handler
    returns immediately while the run executes in the background.
    """
    # Lazy-import heavy modules that depend on full app initialisation.
    from llm_engine import engine

    try:
        run = await db.get_run(run_id)
        if not run:
            logger.error("execute_run: run %s not found", run_id)
            return

        # ── Resolve assistant ──────────────────────────────────────────────
        assistant = await db.get_assistant(run["assistant_id"])
        if not assistant:
            await db.update_run(run_id, {
                "status": "failed",
                "last_error": {"code": "assistant_not_found",
                               "message": f"Assistant {run['assistant_id']} not found"},
                "failed_at": db._now(),
            })
            return

        instructions = run.get("instructions") or assistant.get("instructions")
        tools = run.get("tools") or assistant.get("tools", [])

        # ── Fetch thread messages ──────────────────────────────────────────
        thread_messages = await db.list_messages(
            run["thread_id"], limit=200, order="desc")
        messages = _messages_from_thread(thread_messages, instructions)

        # ── Transition run → in_progress ───────────────────────────────────
        now = db._now()
        run = await db.update_run(run_id, {
            "status": "in_progress",
            "started_at": now,
        })

        # ── LLM call options ───────────────────────────────────────────────
        options = {}
        if run.get("temperature") is not None:
            options["temperature"] = run["temperature"]
        if run.get("top_p") is not None:
            options["top_p"] = run["top_p"]
        if run.get("max_completion_tokens") is not None:
            options["num_predict"] = run["max_completion_tokens"]

        # ── Main loop — LLM call → tool_calls → submit → repeat ───────────
        await _run_llm_loop(run_id, db, messages, tools, options)

    except Exception as exc:
        logger.error("execute_run(%s) failed: %s\n%s",
                      run_id, exc, traceback.format_exc())
        try:
            now = db._now()
            await db.update_run(run_id, {
                "status": "failed",
                "last_error": {"code": "server_error",
                               "message": str(exc)},
                "failed_at": now,
            })
        except Exception:
            logger.error("execute_run: failed to persist error for %s", run_id)


async def _run_llm_loop(
    run_id: str,
    db: OpenAIDatabase,
    messages: list[dict],
    tools: list[dict],
    options: dict,
) -> None:
    """Inner LLM-call loop — may iterate multiple times via submit_tool_outputs."""
    from llm_engine import engine

    # Create a message_creation step for this LLM invocation.
    step = await db.create_run_step(
        run_id=run_id,
        thread_id=(await db.get_run(run_id))["thread_id"],
        type="message_creation",
        step_details={"type": "message_creation",
                       "message_creation": {"message_id": None}},  # filled after
    )
    step_id = step["id"]

    # ── Call the LLM ───────────────────────────────────────────────────────
    response = await engine.generate_chat_with_router(
        messages, tools=tools if tools else None, options=options, stream=False)

    if "error" in response:
        now = db._now()
        await db.update_run(run_id, {
            "status": "failed",
            "last_error": {"code": "llm_error",
                           "message": response["error"]},
            "failed_at": now,
        })
        # Mark step failed too.
        await db.update_run_step(step_id, {
            "status": "failed",
            "last_error": {"code": "llm_error", "message": response["error"]},
            "completed_at": now,
        })
        return

    choice = response["choices"][0]["message"]
    content = choice.get("content", "")
    tool_calls = choice.get("tool_calls")

    # ── Create assistant message on the thread ─────────────────────────────
    thread_id = (await db.get_run(run_id))["thread_id"]
    assistant_id = (await db.get_run(run_id))["assistant_id"]

    msg = await db.create_message(
        thread_id=thread_id,
        role="assistant",
        content=content or "",
        assistant_id=assistant_id,
        run_id=run_id,
    )

    # Update step with the created message.
    now = db._now()
    await db.update_run_step(step_id, {
        "status": "completed",
        "completed_at": now,
        "step_details": {
            "type": "message_creation",
            "message_creation": {"message_id": msg["id"]},
        },
        "usage": response.get("usage"),
    })

    # ── Handle tool_calls ──────────────────────────────────────────────────
    if tool_calls:
        # Transition run → requires_action
        required_action = {
            "type": "submit_tool_outputs",
            "submit_tool_outputs": {
                "tool_calls": tool_calls,
            },
        }
        await db.update_run(run_id, {
            "status": "requires_action",
            "required_action": required_action,
        })

        # Create a tool_calls step detailing each call.
        tool_step = await db.create_run_step(
            run_id=run_id,
            thread_id=thread_id,
            type="tool_calls",
            step_details={
                "type": "tool_calls",
                "tool_calls": tool_calls,
            },
        )
        # tool_calls step stays "in_progress" until submit_tool_outputs.
        _ = tool_step  # keep reference
        return  # Wait for client to submit tool outputs.

    # ── No tool_calls → completed ──────────────────────────────────────────
    await db.update_run(run_id, {
        "status": "completed",
        "completed_at": now,
        "usage": response.get("usage"),
    })


async def continue_run_with_tool_outputs(
    run_id: str,
    db: OpenAIDatabase,
) -> None:
    """Continue execution of a run that is in ``requires_action`` state.

    Called after the client POSTs ``submit_tool_outputs``.  Appends the
    submitted tool results as ``tool`` role messages and calls the LLM again.
    """
    from llm_engine import engine

    run = await db.get_run(run_id)
    if not run or run["status"] != "queued":  # submit_tool_outputs transitions back to queued
        logger.warning("continue_run_with_tool_outputs: run %s not in expected state", run_id)
        return

    # Put it back in_progress.
    now = db._now()
    await db.update_run(run_id, {"status": "in_progress"})

    # ── Reconstruct messages with tool results ─────────────────────────────
    assistant = await db.get_assistant(run["assistant_id"])
    instructions = run.get("instructions") or (assistant.get("instructions") if assistant else None)
    tools = run.get("tools") or (assistant.get("tools", []) if assistant else [])

    thread_messages = await db.list_messages(
        run["thread_id"], limit=200, order="desc")
    messages = _messages_from_thread(thread_messages, instructions)

    options = {}
    if run.get("temperature") is not None:
        options["temperature"] = run["temperature"]
    if run.get("top_p") is not None:
        options["top_p"] = run["top_p"]
    if run.get("max_completion_tokens") is not None:
        options["num_predict"] = run["max_completion_tokens"]

    # ── Create a new message_creation step for the follow-up LLM call ──────
    step = await db.create_run_step(
        run_id=run_id,
        thread_id=run["thread_id"],
        type="message_creation",
        step_details={"type": "message_creation",
                       "message_creation": {"message_id": None}},
    )
    step_id = step["id"]

    # ── LLM call ───────────────────────────────────────────────────────────
    response = await engine.generate_chat_with_router(
        messages, tools=tools if tools else None, options=options, stream=False)

    if "error" in response:
        now = db._now()
        await db.update_run(run_id, {
            "status": "failed",
            "last_error": {"code": "llm_error",
                           "message": response["error"]},
            "failed_at": now,
        })
        await db.update_run_step(step_id, {
            "status": "failed",
            "last_error": {"code": "llm_error", "message": response["error"]},
            "completed_at": now,
        })
        return

    choice = response["choices"][0]["message"]
    content = choice.get("content", "")
    tool_calls = choice.get("tool_calls")

    # ── Create assistant response message ──────────────────────────────────
    thread_id = run["thread_id"]
    assistant_id = run["assistant_id"]
    msg = await db.create_message(
        thread_id=thread_id,
        role="assistant",
        content=content or "",
        assistant_id=assistant_id,
        run_id=run_id,
    )

    now = db._now()
    await db.update_run_step(step_id, {
        "status": "completed",
        "completed_at": now,
        "step_details": {
            "type": "message_creation",
            "message_creation": {"message_id": msg["id"]},
        },
        "usage": response.get("usage"),
    })

    # ── Handle tool_calls again ────────────────────────────────────────────
    if tool_calls:
        required_action = {
            "type": "submit_tool_outputs",
            "submit_tool_outputs": {
                "tool_calls": tool_calls,
            },
        }
        await db.update_run(run_id, {
            "status": "requires_action",
            "required_action": required_action,
        })

        tool_step = await db.create_run_step(
            run_id=run_id,
            thread_id=thread_id,
            type="tool_calls",
            step_details={
                "type": "tool_calls",
                "tool_calls": tool_calls,
            },
        )
        _ = tool_step
        return

    # ── Completed ──────────────────────────────────────────────────────────
    await db.update_run(run_id, {
        "status": "completed",
        "completed_at": now,
        "usage": response.get("usage"),
    })
