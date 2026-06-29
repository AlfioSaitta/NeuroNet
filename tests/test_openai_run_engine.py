"""Tests for the Run execution engine helpers.

Run: pytest tests/test_openai_run_engine.py -x -v
"""
import sys
import types
import logging

import pytest

# ── Mock dependencies before importing run_engine ────────────────────────────
# run_engine.py does: from config import logger
#                    from openai.state import OpenAIDatabase
# We need both available at the top level.

logging.basicConfig(level=logging.DEBUG)

# 1) Mock `config`
config_mock = types.ModuleType("config")
config_mock.logger = logging.getLogger("test_run_engine")
config_mock.logger.addHandler(logging.NullHandler())
config_mock.DATA_DIR = "/tmp"
sys.modules["config"] = config_mock

# 2) Mock `openai` package with `.state` submodule
class _FakeOpenAIDatabase:
    """Minimal stand-in for tests that only need the type hint."""
    pass

_openai_pkg = types.ModuleType("openai")
_openai_pkg.__path__ = []  # mark as package
sys.modules["openai"] = _openai_pkg

_openai_state = types.ModuleType("openai.state")
_openai_state.OpenAIDatabase = _FakeOpenAIDatabase
sys.modules["openai.state"] = _openai_state

# 3) Import the helpers — the type annotation will fail at runtime since
#    OpenAIDatabase in the function signature is resolved lazily (Python 3.13
#    does NOT evaluate annotations at import time when from __future__ import
#    annotations is used — run_engine.py does NOT have that import so
#    OpenAIDatabase IS resolved eagerly.  The mock above satisfies it.)
from jarvis.openai.run_engine import _content_to_text, _messages_from_thread


# ── _content_to_text ─────────────────────────────────────────────────────────

class TestContentToText:
    def test_plain_string(self):
        assert _content_to_text("hello") == "hello"

    def test_empty_string(self):
        assert _content_to_text("") == ""

    def test_list_of_text_parts(self):
        content = [
            {"type": "text", "text": "Hello"},
            {"type": "text", "text": "World"},
        ]
        assert _content_to_text(content) == "Hello\nWorld"

    def test_list_single_part(self):
        content = [{"type": "text", "text": "Only one"}]
        assert _content_to_text(content) == "Only one"

    def test_list_with_non_text_parts(self):
        content = [
            {"type": "text", "text": "Description"},
            {"type": "image_file", "image_file": {"file_id": "xxx"}},
        ]
        assert _content_to_text(content) == "Description"

    def test_list_empty(self):
        assert _content_to_text([]) == ""

    def test_non_string_non_list(self):
        assert _content_to_text(42) == "42"
        assert _content_to_text(None) == "None"

    def test_list_mixed_ordering(self):
        content = [
            {"type": "image_file", "image_file": {"file_id": "a"}},
            {"type": "text", "text": "Caption"},
        ]
        assert _content_to_text(content) == "Caption"


# ── _messages_from_thread ────────────────────────────────────────────────────

class TestMessagesFromThread:
    def test_empty_messages_no_instructions(self):
        result = _messages_from_thread([])
        assert result == []

    def test_empty_messages_with_instructions(self):
        result = _messages_from_thread([], instructions="You are a bot")
        assert result == [{"role": "system", "content": "You are a bot"}]

    def test_single_user_message(self):
        msgs = [{"role": "user", "content": "Hi"}]
        result = _messages_from_thread(msgs)
        assert result == [{"role": "user", "content": "Hi"}]

    def test_user_and_assistant_reversed_order(self):
        # thread stores newest-first
        msgs = [
            {"role": "assistant", "content": "Hello back"},
            {"role": "user", "content": "Hi"},
        ]
        result = _messages_from_thread(msgs)
        assert result == [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello back"},
        ]

    def test_with_instructions_and_messages(self):
        msgs = [
            {"role": "assistant", "content": "Sure!"},
            {"role": "user", "content": "Help me"},
        ]
        result = _messages_from_thread(msgs, instructions="Be helpful")
        assert result == [
            {"role": "system", "content": "Be helpful"},
            {"role": "user", "content": "Help me"},
            {"role": "assistant", "content": "Sure!"},
        ]

    def test_content_as_list_of_parts(self):
        msgs = [
            {"role": "user", "content": [{"type": "text", "text": "Hello via parts"}]},
        ]
        result = _messages_from_thread(msgs)
        assert result == [{"role": "user", "content": "Hello via parts"}]

    def test_tool_role_preserved(self):
        msgs = [
            {"role": "tool", "content": "Result: 42", "name": "calculator"},
        ]
        result = _messages_from_thread(msgs)
        assert result == [{"role": "tool", "content": "Result: 42"}]
