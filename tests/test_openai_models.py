"""Tests for OpenAI models module — Pydantic models and helpers.

Run: pytest tests/test_openai_models.py -x -v
"""
import os
import sys
import types
import tempfile

import pytest


# ── Mock modules before import ───────────────────────────────────────────────
config_mock = types.ModuleType("config")
config_mock.MODEL_ID = "test-chat-model"
config_mock.LLAMA_MODEL_PATH = ""
config_mock.LLAMA_EMBED_MODEL_PATH = ""
config_mock.EXTERNAL_PROVIDERS = []
sys.modules["config"] = config_mock

# Mock openai package so state can be referenced
_openai_pkg = types.ModuleType("openai")
_openai_pkg.__path__ = []
sys.modules["openai"] = _openai_pkg

from jarvis.openai import models as M


# ── Pydantic model tests ─────────────────────────────────────────────────────

class TestChatCompletionRequestOpenAI:
    def test_defaults(self):
        req = M.ChatCompletionRequestOpenAI(
            model="qwen",
            messages=[M.OpenAIMessage(role="user", content="hi")],
        )
        assert req.model == "qwen"
        assert req.stream is False
        assert req.temperature is None
        assert req.n == 1

    def test_all_fields(self):
        req = M.ChatCompletionRequestOpenAI(
            model="qwen",
            messages=[{"role": "user", "content": "hello"}],
            stream=True,
            temperature=0.7,
            max_tokens=500,
            top_p=0.9,
            stop=["\n"],
            tools=[{"type": "function", "function": {"name": "foo"}}],
            tool_choice="auto",
            response_format={"type": "json_object"},
            seed=42,
            logprobs=True,
            top_logprobs=5,
            n=2,
            user="test-user",
        )
        assert req.stream is True
        assert req.temperature == 0.7
        assert req.max_tokens == 500
        assert req.tool_choice == "auto"
        assert req.seed == 42
        assert req.n == 2

    def test_extra_allowed(self):
        req = M.ChatCompletionRequestOpenAI(
            model="qwen",
            messages=[M.OpenAIMessage(role="user", content="hi")],
            unknown_field="should-not-crash",
        )
        assert req.model == "qwen"

    def test_messages_parsed(self):
        req = M.ChatCompletionRequestOpenAI(
            model="qwen",
            messages=[
                {"role": "system", "content": "be helpful"},
                {"role": "user", "content": "hello"},
            ],
        )
        assert len(req.messages) == 2
        assert req.messages[0].role == "system"
        assert req.messages[1].content == "hello"


class TestOpenAIMessage:
    def test_basic(self):
        msg = M.OpenAIMessage(role="user", content="test")
        assert msg.role == "user"
        assert msg.content == "test"


class TestCompletionRequestOpenAI:
    def test_defaults(self):
        req = M.CompletionRequestOpenAI(model="qwen", prompt="hello")
        assert req.prompt == "hello"
        assert req.stream is False
        assert req.echo is False
        assert req.n == 1

    def test_prompt_list(self):
        req = M.CompletionRequestOpenAI(model="qwen", prompt=["a", "b"])
        assert req.prompt == ["a", "b"]


class TestEmbeddingRequestOpenAI:
    def test_default_encoding(self):
        req = M.EmbeddingRequestOpenAI(model="embed", input="text")
        assert req.encoding_format == "float"

    def test_input_list(self):
        req = M.EmbeddingRequestOpenAI(model="embed", input=["a", "b"])
        assert len(req.input) == 2

    def test_base64(self):
        req = M.EmbeddingRequestOpenAI(model="embed", input="x", encoding_format="base64")
        assert req.encoding_format == "base64"


class TestSpeechRequestOpenAI:
    def test_default_voice(self):
        req = M.SpeechRequestOpenAI(model="tts", input="hello")
        assert req.voice == "alloy"
        assert req.speed == 1.0
        assert req.response_format == "mp3"

    def test_custom_voice(self):
        req = M.SpeechRequestOpenAI(model="tts", input="hi", voice="nova", speed=1.2)
        assert req.voice == "nova"
        assert req.speed == 1.2


class TestModerationRequestOpenAI:
    def test_input_string(self):
        req = M.ModerationRequestOpenAI(input="bad text")
        assert req.input == "bad text"

    def test_input_list(self):
        req = M.ModerationRequestOpenAI(input=["a", "b"])
        assert req.input == ["a", "b"]

    def test_model_optional(self):
        req = M.ModerationRequestOpenAI(input="test")
        assert req.model is None


# ── Helper tests ─────────────────────────────────────────────────────────────

class TestModelEntryHelper:
    def test_basic_entry_structure(self):
        entry = M._model_entry("qwen3", "jarvis")
        assert entry["id"] == "qwen3"
        assert entry["object"] == "model"
        assert entry["owned_by"] == "jarvis"
        assert isinstance(entry["created"], int)
        assert entry["created"] > 0

    def test_different_owned_by(self):
        entry = M._model_entry("embed-model", "jarvis-external")
        assert entry["owned_by"] == "jarvis-external"

    def test_with_file_timestamp(self, tmp_path):
        """When LLAMA_MODEL_PATH points to a real file, use its mtime."""
        model_file = tmp_path / "my-model.gguf"
        model_file.write_text("dummy")
        mtime = int(os.path.getmtime(str(model_file)))

        # Patch config paths temporarily
        old_paths = (config_mock.LLAMA_MODEL_PATH, config_mock.LLAMA_EMBED_MODEL_PATH)
        config_mock.LLAMA_MODEL_PATH = str(model_file)
        config_mock.LLAMA_EMBED_MODEL_PATH = ""
        try:
            entry = M._model_entry("mymodel")
            assert entry["created"] == mtime, f"expected {mtime}, got {entry['created']}"
        finally:
            config_mock.LLAMA_MODEL_PATH = old_paths[0]
            config_mock.LLAMA_EMBED_MODEL_PATH = old_paths[1]

    def test_embeds_first_if_both_paths_exist(self, tmp_path):
        """If both chat and embed exist, the chat path is checked first."""
        chat_file = tmp_path / "chat.gguf"
        embed_file = tmp_path / "embed.gguf"
        chat_file.write_text("c")
        embed_file.write_text("e")

        old_chat = config_mock.LLAMA_MODEL_PATH
        old_embed = config_mock.LLAMA_EMBED_MODEL_PATH
        config_mock.LLAMA_MODEL_PATH = str(chat_file)
        config_mock.LLAMA_EMBED_MODEL_PATH = str(embed_file)
        try:
            entry = M._model_entry("mymodel")
            # Should use chat file's mtime (first in loop)
            expected = int(os.path.getmtime(str(chat_file)))
            assert entry["created"] == expected
        finally:
            config_mock.LLAMA_MODEL_PATH = old_chat
            config_mock.LLAMA_EMBED_MODEL_PATH = old_embed
