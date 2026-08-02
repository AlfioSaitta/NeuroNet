"""Test E2E contratto agentic — Fase 6 (client agentici: OpenCode, Cline, Continue, Roo).

Verifica il contratto OpenAI del tool-calling agentico in openai_api/chat.py:

  6.1  OpenAIMessage content array + tool_calls/tool_call_id/name preservati
  6.2  Rilevamento flusso agentic: "tools" in body (nessuna env di modalità)
  6.3  Flusso agentic → tool_calls emessi al client, MAI eseguiti server-side
       (execute_tool_call non chiamato); flusso chat → loop server-side invariato
  6.4  <CLIENT_TOOLS> iniettato nel system prompt (solo agentic), filtro mcp__*
  6.7  reasoning_effort: high|medium → thinking ON (override dopo apply_reasoning_config)
  6.8  stream_options.include_usage → chunk finale con usage prima di [DONE]

Il modulo openai_api.chat importa dipendenze pesanti (rag.engine, memory.engine,
core.llm_engine): vengono mockate a livello di sys.modules PRIMA dell'import
(stesso pattern di test_prompt_hardware.py). L'engine LLM è un fake che restituisce
chunk/response configurati per-test — nessun modello reale caricato.

Run: PYTHONPATH=jarvis python3 -m pytest tests/test_agentic_contract.py -v
"""
import asyncio
import json
import re
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── Path setup: package root = jarvis/ ─────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent / "jarvis"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ── Mock delle dipendenze pesanti PRIMA dell'import di openai_api.chat ──────
#
# NOTA: core.config e core.state NON vanno mockati. Sono importabili
# nell'ambiente test (vedi test_mcp_tools.py) e i test file che girano dopo
# questo (ordine alfabetico) si aspettano i moduli REALI in sys.modules.
# A fine import ripristiniamo sys.modules per i moduli che abbiamo mockato,
# così i file successivi ri-importano i moduli reali (o i propri mock).

# agent.prompt: mock completo — build_omniscient_prompt restituisce un system
# + i messaggi ricevuti (il ramo 6.4 cerca il messaggio system per iniettare
# <CLIENT_TOOLS>).
async def _build_omniscient_prompt(messages, **kwargs):
    return ([{"role": "system", "content": "SYSTEM"}, *list(messages)], None)

_prompt_mock = types.ModuleType("agent.prompt")
_prompt_mock.build_omniscient_prompt = _build_omniscient_prompt

# agent.tags: TagSafeStream stub che strippa i blocchi <tool_call>...</tool_call>
# completi (fedele al comportamento reale: TOOL_CALL è un tag hidden).
_TC_RE = re.compile(r"(?s)<tool_call[^>]*>.*?</tool_call\s*>\s*")

class _TagSafeStreamStub:
    def __init__(self, model_family="all"):
        self._buf = ""
        self._flushed = ""
    def process(self, chunk):
        self._buf += chunk
        out = _TC_RE.sub("", self._buf)
        new = out[len(self._flushed):]
        self._flushed = out
        return new
    def flush(self):
        return ""

_tags_mock = types.ModuleType("agent.tags")
_tags_mock.strip_action_tags = lambda s, **k: s
_tags_mock.TagSafeStream = _TagSafeStreamStub

# agent.confirmation: ConfirmationManager + ApiTokenProvider stub
_confirmation_mock = types.ModuleType("agent.confirmation")
_confirmation_mock.ConfirmationManager = MagicMock()
_confirmation_mock.ApiTokenProvider = SimpleNamespace(resolve=staticmethod(lambda *a, **k: None))

# agent.classifier
_classifier_mock = types.ModuleType("agent.classifier")
_classifier_mock.is_internal_query = lambda s: False
_classifier_mock.classify_confirmation = lambda s: None

# agent.tools: execute_tool_call spy (asserito MAI chiamato in agentic)
_execute_tool_call = AsyncMock(return_value="tool result")
_tools_mock = types.ModuleType("agent.tools")
_tools_mock.execute_tool_call = _execute_tool_call

# memory.engine: process_response_tags spy (stub async, mai eseguito in agentic)
_memory_mock = types.ModuleType("memory.engine")
_memory_mock.process_response_tags = AsyncMock(return_value="")

# core.llm_engine: engine fake + parse_qwen_tool_calls minimale (branch JSON)
def _parse_qwen_tool_calls(text):
    if not text:
        return []
    out = []
    for m in re.finditer(r"(?s)<tool_call[^>]*>(.*?)</tool_call\s*>", text):
        inner = m.group(1).strip()
        if inner.startswith("{") and inner.endswith("}"):
            try:
                parsed = json.loads(inner)
            except (json.JSONDecodeError, TypeError):
                continue
            fn_name = parsed.get("name", parsed.get("function", ""))
            fn_args = parsed.get("arguments", {})
            if isinstance(fn_args, str):
                try:
                    fn_args = json.loads(fn_args)
                except (json.JSONDecodeError, TypeError):
                    pass
            if not fn_args and fn_name:
                fn_args = {k: v for k, v in parsed.items() if k not in ("name", "function", "type")}
            out.append({
                "id": f"call_{len(out)}",
                "type": "function",
                "function": {"name": fn_name, "arguments": json.dumps(fn_args)},
            })
    return out


class _FakeEngine:
    """Engine fake: restituisce le response configurate per-test, registra le call."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def generate_chat_with_router(self, messages, tools=None, options=None,
                                        stream=False, grammar=None,
                                        preferred_provider=None, force_cloud=False):
        self.calls.append({
            "messages": list(messages), "tools": tools, "options": options, "stream": stream,
        })
        idx = min(len(self.calls) - 1, len(self.responses) - 1)
        r = self.responses[idx]
        if asyncio.iscoroutine(r):
            return await r
        if callable(r) and not isinstance(r, types.AsyncGeneratorType):
            r = r()
        return r


async def _agen(chunks):
    for c in chunks:
        yield c
        await asyncio.sleep(0)


_llm_mock = types.ModuleType("core.llm_engine")
_llm_mock.engine = _FakeEngine([])
_llm_mock.parse_qwen_tool_calls = _parse_qwen_tool_calls

# core.config e core.state NON sono mockati (moduli reali, importabili).
_imports_to_mock = {
    "agent.prompt": _prompt_mock,
    "agent.tags": _tags_mock,
    "agent.confirmation": _confirmation_mock,
    "agent.classifier": _classifier_mock,
    "agent.tools": _tools_mock,
    "memory.engine": _memory_mock,
    "core.llm_engine": _llm_mock,
}
# Salviamo lo stato precedente: a fine import ripristiniamo sys.modules per non
# inquinare gli altri test file eseguiti nello stesso processo pytest (ordine
# alfabetico: test_mcp_tools.py e test_prompt_hardware.py importano i moduli
# REALI). openai_api.chat conserva i riferimenti ai mock risolti all'import.
_saved_modules = {_name: sys.modules.get(_name) for _name in _imports_to_mock}
for _name, _mod in _imports_to_mock.items():
    sys.modules[_name] = _mod

# core.chat_utils reale (build_llm_options con reasoning_effort è ciò che testiamo)
from core.chat_utils import build_llm_options  # noqa: E402

from openai_api.chat import (  # noqa: E402
    _build_client_tools_block,
    _normalize_content,
    _estimate_usage,
    openai_chat_completions,
)
import openai_api.chat as chat_mod  # noqa: E402
from openai_api.models import OpenAIMessage, ChatCompletionRequestOpenAI  # noqa: E402

import core.state as state_mod  # noqa: E402

# Ripristina sys.modules: i mock servivano SOLO all'import di openai_api.chat.
# Gli altri test file eseguiti nello stesso processo pytest (ordine alfabetico:
# test_mcp_tools.py, test_prompt_hardware.py) importano i moduli REALI
# (core.config, core.state, agent.prompt) — non devono ricevere i nostri mock.
for _name, _prev in _saved_modules.items():
    if _prev is None:
        sys.modules.pop(_name, None)
    else:
        sys.modules[_name] = _prev


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _install_runtime_mocks():
    """Re-installa i mock in sys.modules per la durata di ogni test.

    pytest importa TUTTI i moduli test in fase di raccolta; i file che girano
    dopo questo (test_mcp_tools.py, test_prompt_hardware.py) installano i
    PROPRI mock per core.llm_engine/rag.engine/... in sys.modules. Al runtime
    openai_api.chat fa import LAZY (es. riga 302: parse_qwen_tool_calls): se in
    sys.modules c'è il mock di un altro file senza quei nomi, i test falliscono.
    Questo fixture ripristina i NOSTRI mock per ogni test e ripristina lo stato
    precedente al teardown (gli altri file non vengono inquinati).
    """
    _prev = {name: sys.modules.get(name) for name in _imports_to_mock}
    for _name, _mod in _imports_to_mock.items():
        sys.modules[_name] = _mod
    yield
    for _name, _prev_mod in _prev.items():
        if _prev_mod is None:
            sys.modules.pop(_name, None)
        else:
            sys.modules[_name] = _prev_mod


@pytest.fixture(autouse=True)
def _reset_engine_and_state():
    """Ripristina engine fake, execute_tool_call spy e state tra i test."""
    _execute_tool_call.reset_mock()
    _execute_tool_call.side_effect = None
    _execute_tool_call.return_value = "tool result"
    chat_mod.engine = _FakeEngine([])
    state_mod.total_requests = 0
    state_mod.total_prompt_tokens = 0
    state_mod.total_completion_tokens = 0
    state_mod.background_tasks = set()
    yield


class _FakeRequest:
    """Request minimale: state.user=None + header opzionali."""
    def __init__(self, headers=None):
        self.state = SimpleNamespace(user=None)
        self.headers = headers or {}


def _tc_tool(name="bash", desc="Run a shell command", params=None, required=None):
    """Costruisce un tool OpenAI-style (formato function)."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": params or {"command": {"type": "string"}},
                "required": required or ["command"],
            },
        },
    }


def _xml_tool_call(name="bash", args=None):
    """Risposta LLM con tool call XML in content (formato Qwen3.5)."""
    return f'<tool_call>{{"name": "{name}", "arguments": {json.dumps(args or {"command": "ls"})}}}</tool_call>'


async def _run(payload_dict, headers=None):
    """Costruisce il payload pydantic e invoca l'endpoint direttamente."""
    payload = ChatCompletionRequestOpenAI(**payload_dict)
    return await openai_chat_completions(payload, _FakeRequest(headers))


async def _collect_sse(resp):
    """Itera il body_iterator di una StreamingResponse, restituisce le linee."""
    lines = []
    async for line in resp.body_iterator:
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        lines.append(line)
    return lines


def _parse_data_lines(lines):
    """Estrae i payload JSON dalle righe SSE (esclude la sentinella [DONE])."""
    payloads = []
    for l in lines:
        if not l.startswith("data: "):
            continue
        raw = l[len("data: "):].strip()
        if not raw or raw == "[DONE]":
            continue
        payloads.append(json.loads(raw))
    return payloads


# ── 6.1 — Modelli pydantic (contratto esteso) ──────────────────────────────

class TestOpenAIMessageContract:
    def test_content_array_of_blocks_accepted(self):
        msg = OpenAIMessage(
            role="user",
            content=[{"type": "text", "text": "leggi il file X"}, {"type": "text", "text": " e riassumi"}],
        )
        assert isinstance(msg.content, list)
        assert msg.content[0]["text"] == "leggi il file X"

    def test_tool_calls_tool_call_id_name_preserved(self):
        msg = OpenAIMessage(
            role="assistant",
            content=None,
            tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}],
        )
        assert msg.tool_calls[0]["id"] == "call_1"
        tool_msg = OpenAIMessage(role="tool", content="ok", tool_call_id="call_1", name="bash")
        assert tool_msg.tool_call_id == "call_1"
        assert tool_msg.name == "bash"

    def test_extra_fields_allowed(self):
        msg = OpenAIMessage(role="user", content="x", unknown_field=42)
        assert msg.unknown_field == 42

    def test_request_reasoning_effort_and_stream_options(self):
        req = ChatCompletionRequestOpenAI(
            model="local",
            messages=[{"role": "user", "content": "hi"}],
            reasoning_effort="high",
            stream_options={"include_usage": True},
        )
        assert req.reasoning_effort == "high"
        assert req.stream_options == {"include_usage": True}


# ── 6.4 — _build_client_tools_block ────────────────────────────────────────

class TestClientToolsBlock:
    def test_returns_empty_without_tools(self):
        assert _build_client_tools_block([]) == ""
        assert _build_client_tools_block(None) == ""

    def test_filters_mcp_tools(self):
        block = _build_client_tools_block([
            _tc_tool("bash"),
            _tc_tool("mcp__github__create_issue"),
        ])
        assert "bash" in block
        assert "mcp__" not in block

    def test_includes_name_desc_and_params(self):
        block = _build_client_tools_block([
            _tc_tool("bash", "Run a shell command", {"command": {"type": "string"}, "cwd": {"type": "string"}}),
        ])
        assert "[CLIENT_TOOLS]" in block
        assert "bash" in block
        assert "Run a shell command" in block
        assert "command" in block

    def test_budget_respected(self):
        tools = [_tc_tool(f"tool_{i}", "desc " * 50) for i in range(200)]
        block = _build_client_tools_block(tools)
        assert len(block) < 2000  # budget ~800 + header/tail margine


# ── _normalize_content ─────────────────────────────────────────────────────

class TestNormalizeContent:
    def test_str_passthrough(self):
        assert _normalize_content("ciao") == "ciao"

    def test_list_of_blocks_concatenated(self):
        content = [
            {"type": "text", "text": "parte uno"},
            {"type": "text", "text": "parte due"},
        ]
        assert _normalize_content(content) == "parte uno\nparte due"

    def test_none_returns_empty(self):
        assert _normalize_content(None) == ""


# ── 6.8 — _estimate_usage ──────────────────────────────────────────────────

class TestEstimateUsage:
    def test_returns_usage_dict(self):
        usage = _estimate_usage([{"role": "user", "content": "hello"}], "world")
        assert set(usage) == {"prompt_tokens", "completion_tokens", "total_tokens"}
        assert usage["prompt_tokens"] >= 1
        assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


# ── 6.7 — build_llm_options reasoning_effort ───────────────────────────────

class TestReasoningEffortExtraction:
    def test_normalized_to_lower(self):
        options = build_llm_options({"reasoning_effort": "High"})
        assert options["reasoning_effort"] == "high"

    def test_absent_omitted(self):
        assert "reasoning_effort" not in build_llm_options({})


# ── 6.2 / 6.3 — Non-stream: agentic vs chat ────────────────────────────────

class TestNonStreamAgentic:
    async def test_agentic_emits_tool_calls_never_executes(self):
        chat_mod.engine = _FakeEngine([{
            "choices": [{"message": {"role": "assistant", "content": _xml_tool_call()}, "index": 0}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }])
        resp = await _run({
            "model": "local",
            "messages": [{"role": "user", "content": "esegui ls"}],
            "tools": [_tc_tool("bash")],
            "stream": False,
        })
        msg = resp["choices"][0]["message"]
        assert resp["choices"][0]["finish_reason"] == "tool_calls"
        assert msg["tool_calls"][0]["function"]["name"] == "bash"
        assert msg["tool_calls"][0]["function"]["arguments"] == '{"command": "ls"}'
        _execute_tool_call.assert_not_awaited()

    async def test_chat_without_tools_keeps_server_side_loop(self):
        # Prima call: tool call XML → esecuzione server-side → seconda call: risposta
        chat_mod.engine = _FakeEngine([
            {
                "choices": [{"message": {"role": "assistant", "content": _xml_tool_call()}, "index": 0}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
            {
                "choices": [{"message": {"role": "assistant", "content": "Eseguito."}, "index": 0}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23},
            },
        ])
        resp = await _run({
            "model": "local",
            "messages": [{"role": "user", "content": "esegui ls"}],
            "stream": False,
        })
        assert resp["choices"][0]["finish_reason"] == "stop"
        assert resp["choices"][0]["message"]["content"] == "Eseguito."
        _execute_tool_call.assert_awaited_once()

    async def test_agentic_injects_client_tools_in_system_prompt(self):
        chat_mod.engine = _FakeEngine([{
            "choices": [{"message": {"role": "assistant", "content": "ok"}, "index": 0}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }])
        await _run({
            "model": "local",
            "messages": [{"role": "user", "content": "leggi file"}],
            "tools": [_tc_tool("bash", "Run a shell command")],
            "stream": False,
        })
        sent_system = [m["content"] for m in chat_mod.engine.calls[0]["messages"] if m.get("role") == "system"]
        assert sent_system and "[CLIENT_TOOLS]" in sent_system[0]

    async def test_chat_flow_does_not_inject_client_tools(self):
        chat_mod.engine = _FakeEngine([{
            "choices": [{"message": {"role": "assistant", "content": "ok"}, "index": 0}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }])
        await _run({
            "model": "local",
            "messages": [{"role": "user", "content": "ciao"}],
            "stream": False,
        })
        sent_system = [m["content"] for m in chat_mod.engine.calls[0]["messages"] if m.get("role") == "system"]
        assert "[CLIENT_TOOLS]" not in sent_system[0]

    async def test_agentic_content_array_normalized(self):
        chat_mod.engine = _FakeEngine([{
            "choices": [{"message": {"role": "assistant", "content": "ok"}, "index": 0}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }])
        await _run({
            "model": "local",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "leggi il file"}]}],
            "tools": [_tc_tool("bash")],
            "stream": False,
        })
        # L'ultimo messaggio user inoltrato al modello è testo piatto, non array
        last_user = [m["content"] for m in chat_mod.engine.calls[0]["messages"] if m.get("role") == "user"][-1]
        assert last_user == "leggi il file"


# ── 6.2 / 6.3 / 6.8 — Streaming agentic ────────────────────────────────────

class TestStreamingAgentic:
    async def test_agentic_streams_tool_calls_and_terminates(self):
        chat_mod.engine = _FakeEngine([
            _agen([
                {"choices": [{"delta": {"role": "assistant", "content": "Controllo."}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": _xml_tool_call()}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": ""}, "finish_reason": "stop"}]},
            ]),
        ])
        resp = await _run({
            "model": "local",
            "messages": [{"role": "user", "content": "esegui ls"}],
            "tools": [_tc_tool("bash")],
            "stream": True,
        })
        lines = await _collect_sse(resp)
        payloads = _parse_data_lines(lines)
        tc_payloads = [p for p in payloads if p.get("choices") and p["choices"][0].get("delta", {}).get("tool_calls")]
        assert tc_payloads, "attesi delta.tool_calls nello stream agentic"
        first_tc = tc_payloads[0]["choices"][0]["delta"]["tool_calls"][0]
        assert first_tc["function"]["name"] == "bash"
        assert first_tc["function"]["arguments"] == '{"command": "ls"}'
        final = [p for p in payloads if p.get("choices") and p["choices"][0].get("finish_reason") == "tool_calls"]
        assert final, "atteso finish_reason=tool_calls"
        assert any(l.strip() == "data: [DONE]" for l in lines)
        _execute_tool_call.assert_not_awaited()
        # Nessuna seconda chiamata LLM (T2) in flusso agentic
        assert len(chat_mod.engine.calls) == 1

    async def test_include_usage_final_chunk(self):
        chat_mod.engine = _FakeEngine([
            _agen([
                {"choices": [{"delta": {"role": "assistant", "content": "Hello"}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": ""}, "finish_reason": "stop"}]},
            ]),
        ])
        resp = await _run({
            "model": "local",
            "messages": [{"role": "user", "content": "ciao"}],
            "stream": True,
            "stream_options": {"include_usage": True},
        })
        lines = await _collect_sse(resp)
        payloads = _parse_data_lines(lines)
        usage_payloads = [p for p in payloads if p.get("usage")]
        assert usage_payloads, "atteso chunk finale con usage (include_usage)"
        assert set(usage_payloads[0]["usage"]) == {"prompt_tokens", "completion_tokens", "total_tokens"}
        # Il chunk usage precede [DONE]
        assert "data: [DONE]" in lines[-1]

    async def test_reasoning_effort_high_enables_thinking(self):
        chat_mod.engine = _FakeEngine([{
            "choices": [{"message": {"role": "assistant", "content": "ok"}, "index": 0}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }])
        await _run({
            "model": "local",
            "messages": [{"role": "user", "content": "ragiona"}],
            "reasoning_effort": "high",
            "stream": False,
        })
        opts = chat_mod.engine.calls[0]["options"]
        assert opts["chat_template_kwargs"]["enable_thinking"] is True
        assert "reasoning_effort" not in opts  # non propagato al motore

    async def test_tool_message_roundtrip_preserved(self):
        # Secondo giro del loop agentic: client rimanda role:"tool" con tool_call_id
        chat_mod.engine = _FakeEngine([{
            "choices": [{"message": {"role": "assistant", "content": "Usando il risultato: 3 file."}, "index": 0}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
        }])
        await _run({
            "model": "local",
            "messages": [
                {"role": "user", "content": "esegui ls"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "bash", "arguments": '{"command": "ls"}'}}],
                },
                {"role": "tool", "content": "3 file", "tool_call_id": "call_1", "name": "bash"},
                {"role": "user", "content": "cosa c'è?"},
            ],
            "tools": [_tc_tool("bash")],
            "stream": False,
        })
        sent = chat_mod.engine.calls[0]["messages"]
        # tool_calls dell'assistant e tool_call_id del tool preservati nello storico
        assert any(m.get("tool_calls") for m in sent)
        assert any(m.get("role") == "tool" and m.get("tool_call_id") == "call_1" for m in sent)
        assert resp_ok(sent)


def resp_ok(sent) -> bool:
    return bool(sent)
