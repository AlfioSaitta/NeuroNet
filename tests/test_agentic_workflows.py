"""Test E2E workflow di sviluppo reale — flusso agentic (Fase 6).

Simulazioni realistiche del lavoro quotidiano di sviluppo con un client
agentico (OpenCode & Co.) collegato a Jarvis via /v1/chat/completions:

  - Feature development multi-giro: read → edit → test → risposta finale,
    con history cumulativa che il client rimanda a ogni turno (come farebbe
    OpenCode). Jarvis deve PRESERVARE l'intero storico agentico (tool_calls
    dell'assistant, role:"tool" con tool_call_id, name) e NON eseguire MAI
    i tool del client lato server.
  - Tool failure: l'errore restituito dal tool (es. "file not found") deve
    arrivare intatto al modello nel turno successivo — Jarvis non deve
    scartarlo né interpretarlo.
  - Tool calls paralleli: una risposta del modello con PIÙ tool_call XML
    emette più delta (indice 0, 1, ...) e i risultati multipli tornano al
    modello con i rispettivi tool_call_id.
  - Streaming: tool_calls frammentati su più chunk (arguments sparsi) vengono
    ricostruiti correttamente; round-trip tool → testo finale in streaming.
  - Comportamento client: tool_choice propagato alle options, errore motore
    → 500 JSON (non-stream) / chunk di errore (stream), tool aziendali custom
    nel blocco <CLIENT_TOOLS>.
  - Isolamento: conversazioni diverse non contaminano state né storico.

Stesso pattern di test_agentic_contract.py: dipendenze pesanti mockate in
sys.modules PRIMA dell'import; il modulo openai_api.chat viene re-importato
forzatamente con i mock di QUESTO file (se test_agentic_contract è stato
raccolto prima, il modulo cached punterebbe ai suoi mock). Engine LLM = fake
con response configurate per-test.

Run: PYTHONPATH=jarvis python3 -m pytest tests/test_agentic_workflows.py -v
Suite completa (ordine vincolato — mcp_tools per ultimo, il suo mock
rag.engine deve vincere):
  PYTHONPATH=jarvis python3 -m pytest tests/test_agentic_contract.py \
      tests/test_agentic_workflows.py tests/test_hardware.py \
      tests/test_prompt_hardware.py tests/test_mcp_tools.py -q
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
# (stesso pattern di test_agentic_contract.py / test_prompt_hardware.py).
# core.config e core.state NON sono mockati (moduli reali, importabili).

async def _build_omniscient_prompt(messages, **kwargs):
    return ([{"role": "system", "content": "SYSTEM"}, *list(messages)], None)

_prompt_mock = types.ModuleType("agent.prompt")
_prompt_mock.build_omniscient_prompt = _build_omniscient_prompt

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

_confirmation_mock = types.ModuleType("agent.confirmation")
_confirmation_mock.ConfirmationManager = MagicMock()
_confirmation_mock.ApiTokenProvider = SimpleNamespace(resolve=staticmethod(lambda *a, **k: None))

_classifier_mock = types.ModuleType("agent.classifier")
_classifier_mock.is_internal_query = lambda s: False
_classifier_mock.classify_confirmation = lambda s: None

# execute_tool_call spy: asserito MAI chiamato in flusso agentic
_execute_tool_call = AsyncMock(return_value="tool result")
_tools_mock = types.ModuleType("agent.tools")
_tools_mock.execute_tool_call = _execute_tool_call

_memory_mock = types.ModuleType("memory.engine")
_memory_mock.process_response_tags = AsyncMock(return_value="")


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

_imports_to_mock = {
    "agent.prompt": _prompt_mock,
    "agent.tags": _tags_mock,
    "agent.confirmation": _confirmation_mock,
    "agent.classifier": _classifier_mock,
    "agent.tools": _tools_mock,
    "memory.engine": _memory_mock,
    "core.llm_engine": _llm_mock,
}
_saved_modules = {_name: sys.modules.get(_name) for _name in _imports_to_mock}
for _name, _mod in _imports_to_mock.items():
    sys.modules[_name] = _mod

# Forza il re-import di openai_api.chat con i NOSTRI mock: se
# test_agentic_contract.py è stato raccolto prima (ordine alfabetico), il
# modulo cached in sys.modules punta ai SUOI mock (execute_tool_call spy,
# engine fake) — il re-import garantisce l'isolamento tra i due file.
for _m in ("openai_api.chat", "openai_api"):
    sys.modules.pop(_m, None)

from core.chat_utils import build_llm_options  # noqa: E402

from openai_api.chat import (  # noqa: E402
    _build_client_tools_block,
    _normalize_content,
    openai_chat_completions,
)
import openai_api.chat as chat_mod  # noqa: E402
from openai_api.models import ChatCompletionRequestOpenAI  # noqa: E402

import core.state as state_mod  # noqa: E402

# Ripristina sys.modules: i mock servivano SOLO all'import. Gli altri test file
# eseguiti nello stesso processo pytest importano i moduli REALI.
for _name, _prev in _saved_modules.items():
    if _prev is None:
        sys.modules.pop(_name, None)
    else:
        sys.modules[_name] = _prev

# Rimuovi openai_api.chat/openai_api da sys.modules: questo modulo conserva i
# propri riferimenti (chat_mod) per l'esecuzione dei test, ma i file test che
# girano dopo (o prima) devono re-importare openai_api.chat con i PROPRI mock
# (test_agentic_contract.py importa i suoi). Senza questo pop, il primo file
# raccolto lascerebbe il SUO modulo in sys.modules e l'altro lo riuserebbe con
# i mock sbagliati → assert su spy mai chiamato (ordine inverso).
for _m in ("openai_api.chat", "openai_api"):
    sys.modules.pop(_m, None)


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _install_runtime_mocks():
    """Re-installa i mock in sys.modules per la durata di ogni test.

    openai_api.chat fa import LAZY a runtime (es. parse_qwen_tool_calls da
    core.llm_engine): durante l'esecuzione dei test, sys.modules deve contenere
    i NOSTRI mock, non quelli di un altro test file raccolto nel processo.
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


def _assistant_tool_msg(tool_calls):
    """Messaggio assistant con tool_calls (come emesso da Jarvis in agentic)."""
    return {"role": "assistant", "content": None, "tool_calls": tool_calls}


def _tool_result_msg(tool_call_id, content, name="bash"):
    """Messaggio role:'tool' con tool_call_id (come rimandato dal client)."""
    return {"role": "tool", "content": content, "tool_call_id": tool_call_id, "name": name}


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


def _sent_to_model():
    """Messaggi inoltrati al modello nell'ultima call dell'engine fake."""
    return chat_mod.engine.calls[-1]["messages"]


# ── Feature development multi-giro (simulazione di sviluppo reale) ──────────

class TestWorkflowFeatureDevelopment:
    """Ciclo completo di sviluppo: read → edit → test → risposta finale.

    Ogni turno simula una richiesta HTTP separata del client (OpenCode), che
    rimanda la history cumulativa: i messaggi assistant con tool_calls, i
    risultati role:"tool" con tool_call_id e i nuovi messaggi user.
    """

    async def test_three_turn_feature_implementation(self):
        tools = [_tc_tool("read_file"), _tc_tool("edit_file"), _tc_tool("run_tests")]

        # Turno 1: "Leggi il file" → il modello decide read_file
        chat_mod.engine = _FakeEngine([{
            "choices": [{"message": {"role": "assistant", "content": _xml_tool_call("read_file", {"path": "utils.py"})}, "index": 0}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }])
        r1 = await _run({
            "model": "local",
            "messages": [{"role": "user", "content": "Leggi il file utils.py"}],
            "tools": tools, "stream": False,
        })
        assert r1["choices"][0]["finish_reason"] == "tool_calls"
        tc1 = r1["choices"][0]["message"]["tool_calls"][0]
        assert tc1["function"]["name"] == "read_file"
        assert json.loads(tc1["function"]["arguments"]) == {"path": "utils.py"}
        _execute_tool_call.assert_not_awaited()

        # Turno 2: client rimanda risultato + nuova richiesta → edit_file
        chat_mod.engine = _FakeEngine([{
            "choices": [{"message": {"role": "assistant", "content": _xml_tool_call("edit_file", {"path": "utils.py", "code": "def validate_email(e): ..."})}, "index": 0}],
            "usage": {"prompt_tokens": 30, "completion_tokens": 5, "total_tokens": 35},
        }])
        r2 = await _run({
            "model": "local",
            "messages": [
                {"role": "user", "content": "Leggi il file utils.py"},
                _assistant_tool_msg(r1["choices"][0]["message"]["tool_calls"]),
                _tool_result_msg(tc1["id"], "def foo():\n    pass\n", name="read_file"),
                {"role": "user", "content": "Aggiungi una funzione validate_email"},
            ],
            "tools": tools, "stream": False,
        })
        assert r2["choices"][0]["finish_reason"] == "tool_calls"
        tc2 = r2["choices"][0]["message"]["tool_calls"][0]
        assert tc2["function"]["name"] == "edit_file"
        # Il contenuto del file è arrivato al modello nel secondo turno
        sent = _sent_to_model()
        tool_msgs = [m for m in sent if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["content"] == "def foo():\n    pass\n"
        assert tool_msgs[0]["tool_call_id"] == tc1["id"]
        assert tool_msgs[0]["name"] == "read_file"
        # L'ultimo user è la nuova richiesta
        assert sent[-1]["role"] == "user"
        assert "validate_email" in sent[-1]["content"]
        _execute_tool_call.assert_not_awaited()

        # Turno 3: risultato edit + "esegui i test" → risposta testuale finale
        chat_mod.engine = _FakeEngine([{
            "choices": [{"message": {"role": "assistant", "content": "Fatto. validate_email aggiunta e test passati (3/3)."}, "index": 0}],
            "usage": {"prompt_tokens": 45, "completion_tokens": 8, "total_tokens": 53},
        }])
        r3 = await _run({
            "model": "local",
            "messages": [
                {"role": "user", "content": "Leggi il file utils.py"},
                _assistant_tool_msg(r1["choices"][0]["message"]["tool_calls"]),
                _tool_result_msg(tc1["id"], "def foo():\n    pass\n", name="read_file"),
                {"role": "user", "content": "Aggiungi una funzione validate_email"},
                _assistant_tool_msg(r2["choices"][0]["message"]["tool_calls"]),
                _tool_result_msg(tc2["id"], "File aggiornato", name="edit_file"),
                {"role": "user", "content": "Esegui i test"},
            ],
            "tools": tools, "stream": False,
        })
        assert r3["choices"][0]["finish_reason"] == "stop"
        assert "Fatto" in r3["choices"][0]["message"]["content"]
        # Intero storico agentico arrivato al modello (nessun campo scartato)
        sent3 = _sent_to_model()
        assert sum(1 for m in sent3 if m.get("role") == "tool") == 2
        assert sum(1 for m in sent3 if m.get("tool_calls")) == 2
        assert all(m.get("tool_call_id") for m in sent3 if m.get("role") == "tool")
        _execute_tool_call.assert_not_awaited()

    async def test_tool_error_content_fed_back_to_model(self):
        """Un tool che fallisce (es. file not found) → l'errore arriva al modello."""
        tools = [_tc_tool("read_file"), _tc_tool("bash")]
        chat_mod.engine = _FakeEngine([{
            "choices": [{"message": {"role": "assistant", "content": _xml_tool_call("read_file", {"path": "missing.py"})}, "index": 0}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }])
        r1 = await _run({
            "model": "local",
            "messages": [{"role": "user", "content": "Leggi missing.py"}],
            "tools": tools, "stream": False,
        })
        tc1 = r1["choices"][0]["message"]["tool_calls"][0]
        assert tc1["function"]["name"] == "read_file"

        # Il client esegue il tool e rimanda l'ERRORE come content del tool
        chat_mod.engine = _FakeEngine([{
            "choices": [{"message": {"role": "assistant", "content": "Provo con un glob: cerco file .py simili."}, "index": 0}],
            "usage": {"prompt_tokens": 25, "completion_tokens": 6, "total_tokens": 31},
        }])
        await _run({
            "model": "local",
            "messages": [
                {"role": "user", "content": "Leggi missing.py"},
                _assistant_tool_msg([tc1]),
                _tool_result_msg(tc1["id"], "Error: file not found: missing.py", name="read_file"),
                {"role": "user", "content": "Riprova"},
            ],
            "tools": tools, "stream": False,
        })
        # Jarvis NON ha scartato l'errore: il messaggio tool arriva intatto al modello
        tool_msgs = [m for m in _sent_to_model() if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert "file not found" in tool_msgs[0]["content"]
        assert tool_msgs[0]["tool_call_id"] == tc1["id"]
        _execute_tool_call.assert_not_awaited()

    async def test_parallel_tool_calls_emitted_and_results_preserved(self):
        """Risposta con PIÙ tool_call XML → tutti emessi al client, tutti i risultati tornano."""
        tools = [_tc_tool("read_file")]
        multi = (_xml_tool_call("read_file", {"path": "a.py"})
                 + _xml_tool_call("read_file", {"path": "b.py"}))
        chat_mod.engine = _FakeEngine([{
            "choices": [{"message": {"role": "assistant", "content": multi}, "index": 0}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
        }])
        r1 = await _run({
            "model": "local",
            "messages": [{"role": "user", "content": "Leggi a.py e b.py"}],
            "tools": tools, "stream": False,
        })
        tcs = r1["choices"][0]["message"]["tool_calls"]
        assert len(tcs) == 2
        assert [t["function"]["name"] for t in tcs] == ["read_file", "read_file"]
        assert json.loads(tcs[0]["function"]["arguments"]) == {"path": "a.py"}
        assert json.loads(tcs[1]["function"]["arguments"]) == {"path": "b.py"}
        assert r1["choices"][0]["finish_reason"] == "tool_calls"
        _execute_tool_call.assert_not_awaited()

        # Secondo giro: client rimanda DUE risultati tool con i rispettivi id
        chat_mod.engine = _FakeEngine([{
            "choices": [{"message": {"role": "assistant", "content": "Analizzo entrambi i file."}, "index": 0}],
            "usage": {"prompt_tokens": 35, "completion_tokens": 5, "total_tokens": 40},
        }])
        await _run({
            "model": "local",
            "messages": [
                {"role": "user", "content": "Leggi a.py e b.py"},
                _assistant_tool_msg(tcs),
                _tool_result_msg(tcs[0]["id"], "file a", name="read_file"),
                _tool_result_msg(tcs[1]["id"], "file b", name="read_file"),
                {"role": "user", "content": "Cosa contengono?"},
            ],
            "tools": tools, "stream": False,
        })
        tool_msgs = [m for m in _sent_to_model() if m.get("role") == "tool"]
        assert len(tool_msgs) == 2
        assert {m["content"] for m in tool_msgs} == {"file a", "file b"}
        assert {m["tool_call_id"] for m in tool_msgs} == {tcs[0]["id"], tcs[1]["id"]}
        _execute_tool_call.assert_not_awaited()

    async def test_five_turn_history_fully_preserved(self):
        """Conversazione lunga (5 turni) → l'intero storico arriva al modello in ordine."""
        tools = [_tc_tool("bash")]
        history = [
            {"role": "user", "content": "Setup progetto"},
            _assistant_tool_msg([{"id": "call_0", "type": "function", "function": {"name": "bash", "arguments": '{"command": "npm init"}'}}]),
            _tool_result_msg("call_0", "package.json creato"),
            {"role": "user", "content": "Installa le dipendenze"},
            _assistant_tool_msg([{"id": "call_1", "type": "function", "function": {"name": "bash", "arguments": '{"command": "npm i"}'}}]),
            _tool_result_msg("call_1", "node_modules pronti"),
            {"role": "user", "content": "Avvia i test"},
            _assistant_tool_msg([{"id": "call_2", "type": "function", "function": {"name": "bash", "arguments": '{"command": "npm test"}'}}]),
            _tool_result_msg("call_2", "12 passed"),
            {"role": "user", "content": "Scrivi un README"},
            _assistant_tool_msg([{"id": "call_3", "type": "function", "function": {"name": "bash", "arguments": '{"command": "cat README.md"}'}}]),
            _tool_result_msg("call_3", "# Progetto"),
            {"role": "user", "content": "Qual è lo stato?"},
        ]
        chat_mod.engine = _FakeEngine([{
            "choices": [{"message": {"role": "assistant", "content": "Tutto pronto: dipendenze installate, 12 test passati."}, "index": 0}],
            "usage": {"prompt_tokens": 60, "completion_tokens": 8, "total_tokens": 68},
        }])
        resp = await _run({
            "model": "local", "messages": history, "tools": tools, "stream": False,
        })
        assert resp["choices"][0]["message"]["content"].startswith("Tutto pronto")
        sent = _sent_to_model()
        # Ruoli in ordine, nessun messaggio scartato
        roles = [m["role"] for m in sent if m.get("role") != "system"]
        assert roles == ["user", "assistant", "tool", "user", "assistant", "tool",
                         "user", "assistant", "tool", "user", "assistant", "tool", "user"]
        # Tutti i tool_calls e tool_call_id preservati
        assert [m["tool_calls"][0]["id"] for m in sent if m.get("tool_calls")] == [
            "call_0", "call_1", "call_2", "call_3"]
        assert [m["tool_call_id"] for m in sent if m.get("role") == "tool"] == [
            "call_0", "call_1", "call_2", "call_3"]
        _execute_tool_call.assert_not_awaited()


# ── Round-trip agentico in streaming ───────────────────────────────────────

class TestWorkflowAgenticStreaming:
    async def test_streaming_fragmented_parallel_tool_calls_reconstructed(self):
        """Tool_calls frammentati su più chunk (arguments sparsi) → ricostruiti integri."""
        tools = [_tc_tool("read_file")]
        chat_mod.engine = _FakeEngine([
            _agen([
                {"choices": [{"delta": {"role": "assistant", "content": "Leggo."}, "finish_reason": None}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_10", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "'}}]}, "finish_reason": None}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "a.py"}}]}, "finish_reason": None}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"}'}}]}, "finish_reason": None}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 1, "id": "call_11", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "'}}]}, "finish_reason": None}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 1, "function": {"arguments": "b.py"}}]}, "finish_reason": None}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 1, "function": {"arguments": '"}'}}]}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": ""}, "finish_reason": "stop"}]},
            ]),
        ])
        resp = await _run({
            "model": "local",
            "messages": [{"role": "user", "content": "Leggi a.py e b.py"}],
            "tools": tools, "stream": True,
        })
        lines = await _collect_sse(resp)
        payloads = _parse_data_lines(lines)
        tc_payloads = [p for p in payloads
                       if p.get("choices") and p["choices"][0].get("delta", {}).get("tool_calls")]
        assert len(tc_payloads) == 2, "attesi 2 delta.tool_calls (un tool per file)"
        by_index = {}
        for p in tc_payloads:
            tc = p["choices"][0]["delta"]["tool_calls"][0]
            by_index[tc["index"]] = tc
        assert by_index[0]["function"]["name"] == "read_file"
        assert by_index[0]["function"]["arguments"] == '{"path": "a.py"}'
        assert by_index[1]["function"]["arguments"] == '{"path": "b.py"}'
        assert by_index[0]["id"] == "call_10"
        assert by_index[1]["id"] == "call_11"
        final = [p for p in payloads if p.get("choices") and p["choices"][0].get("finish_reason") == "tool_calls"]
        assert final, "atteso finish_reason=tool_calls"
        assert any(l.strip() == "data: [DONE]" for l in lines)
        _execute_tool_call.assert_not_awaited()
        assert len(chat_mod.engine.calls) == 1  # nessuna T2

    async def test_streaming_text_after_tool_result(self):
        """Secondo giro (tool eseguito dal client): streaming di testo finale."""
        tools = [_tc_tool("bash")]
        chat_mod.engine = _FakeEngine([
            _agen([
                {"choices": [{"delta": {"role": "assistant", "content": "Risultato "}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": "ricevuto: 42 righe."}, "finish_reason": "stop"}]},
            ]),
        ])
        resp = await _run({
            "model": "local",
            "messages": [
                {"role": "user", "content": "Conta le righe"},
                _assistant_tool_msg([{"id": "call_7", "type": "function", "function": {"name": "bash", "arguments": '{"command": "wc -l"}'}}]),
                _tool_result_msg("call_7", "42"),
                {"role": "user", "content": "Quante sono?"},
            ],
            "tools": tools, "stream": True,
        })
        lines = await _collect_sse(resp)
        payloads = _parse_data_lines(lines)
        text = "".join(p.get("choices", [{}])[0].get("delta", {}).get("content", "")
                       for p in payloads if p.get("choices"))
        assert "42 righe" in text
        assert any(l.strip() == "data: [DONE]" for l in lines)
        # Nessun delta.tool_calls (il modello ha risposto in testo)
        assert not any(p.get("choices") and p["choices"][0].get("delta", {}).get("tool_calls")
                       for p in payloads)
        _execute_tool_call.assert_not_awaited()


# ── Comportamento client ───────────────────────────────────────────────────

class TestWorkflowClientBehavior:
    async def test_tool_choice_propagated_to_options(self):
        """tool_choice esplicito del client arriva alle options del motore."""
        chat_mod.engine = _FakeEngine([{
            "choices": [{"message": {"role": "assistant", "content": "ok"}, "index": 0}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        }])
        await _run({
            "model": "local",
            "messages": [{"role": "user", "content": "ciao"}],
            "tools": [_tc_tool("bash")],
            "tool_choice": "none", "stream": False,
        })
        assert chat_mod.engine.calls[0]["options"].get("tool_choice") == "none"

    async def test_engine_error_returns_500_non_stream(self):
        """Errore del motore (non-stream) → JSONResponse 500, non crash."""
        chat_mod.engine = _FakeEngine([{"error": "CUDA out of memory"}])
        resp = await _run({
            "model": "local",
            "messages": [{"role": "user", "content": "analizza"}],
            "tools": [_tc_tool("bash")], "stream": False,
        })
        assert resp.status_code == 500
        assert "CUDA out of memory" in resp.body.decode()
        _execute_tool_call.assert_not_awaited()

    async def test_engine_error_yields_error_chunk_stream(self):
        """Errore del motore (streaming) → chunk di errore SSE, non crash."""
        chat_mod.engine = _FakeEngine([{"error": "model not loaded"}])
        resp = await _run({
            "model": "local",
            "messages": [{"role": "user", "content": "analizza"}],
            "tools": [_tc_tool("bash")], "stream": True,
        })
        lines = await _collect_sse(resp)
        assert any("model not loaded" in l for l in lines)
        _execute_tool_call.assert_not_awaited()

    async def test_company_tools_in_client_block_and_roundtrip(self):
        """Tool aziendali custom → blocco <CLIENT_TOOLS> + nome preservato nel round-trip."""
        tools = [
            _tc_tool("create_jira_issue", "Create a Jira issue", {"summary": {"type": "string"}}, ["summary"]),
            _tc_tool("run_pytest", "Run pytest suite", {"path": {"type": "string"}}),
            _tc_tool("git_commit", "Create a git commit", {"message": {"type": "string"}}, ["message"]),
            _tc_tool("mcp__github__create_issue", "runtime mcp tool"),
        ]
        chat_mod.engine = _FakeEngine([{
            "choices": [{"message": {"role": "assistant", "content": _xml_tool_call("git_commit", {"message": "feat: add validate_email"})}, "index": 0}],
            "usage": {"prompt_tokens": 15, "completion_tokens": 5, "total_tokens": 20},
        }])
        resp = await _run({
            "model": "local",
            "messages": [{"role": "user", "content": "Committa la modifica"}],
            "tools": tools, "stream": False,
        })
        # Blocco <CLIENT_TOOLS> nel system prompt: tool aziendali sì, mcp__ no
        system = next(m["content"] for m in _sent_to_model() if m.get("role") == "system")
        assert "[CLIENT_TOOLS]" in system
        assert "create_jira_issue" in system
        assert "run_pytest" in system
        assert "git_commit" in system
        assert "mcp__" not in system
        # Round-trip: nome del tool custom emesso al client
        tc = resp["choices"][0]["message"]["tool_calls"][0]
        assert tc["function"]["name"] == "git_commit"
        assert json.loads(tc["function"]["arguments"]) == {"message": "feat: add validate_email"}
        _execute_tool_call.assert_not_awaited()

    async def test_tool_result_content_array_normalized(self):
        """Content array nei risultati tool (AI SDK) → normalizzato senza crash."""
        tools = [_tc_tool("bash")]
        chat_mod.engine = _FakeEngine([{
            "choices": [{"message": {"role": "assistant", "content": "ok"}, "index": 0}],
            "usage": {"prompt_tokens": 15, "completion_tokens": 3, "total_tokens": 18},
        }])
        await _run({
            "model": "local",
            "messages": [
                {"role": "user", "content": "esegui"},
                _assistant_tool_msg([{"id": "call_1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]),
                _tool_result_msg("call_1", [{"type": "text", "text": "completato"}]),
                {"role": "user", "content": "dimmi"},
            ],
            "tools": tools, "stream": False,
        })
        tool_msgs = [m for m in _sent_to_model() if m.get("role") == "tool"]
        assert tool_msgs[0]["content"] == "completato"
        _execute_tool_call.assert_not_awaited()


# ── Isolamento tra conversazioni ───────────────────────────────────────────

class TestWorkflowStateIsolation:
    async def test_two_conversations_do_not_mix_state_or_history(self):
        """Conversazioni diverse: state incrementato, storie non contaminate."""
        chat_mod.engine = _FakeEngine([
            {
                "choices": [{"message": {"role": "assistant", "content": _xml_tool_call("bash", {"command": "ls"})}, "index": 0}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            },
            {
                "choices": [{"message": {"role": "assistant", "content": "Nessuna side effect."}, "index": 0}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
            },
        ])
        # Conversazione A: task agentico (tools)
        r_a = await _run({
            "model": "local",
            "messages": [{"role": "user", "content": "lista i file"}],
            "tools": [_tc_tool("bash")], "stream": False,
            "conversation_id": "conv-proj-a",
        })
        # Conversazione B: chat semplice (no tools)
        await _run({
            "model": "local",
            "messages": [{"role": "user", "content": "buongiorno"}],
            "stream": False,
            "conversation_id": "conv-proj-b",
        })
        assert state_mod.total_requests == 2
        # A è agentica (tool_calls), B no (nessuna iniezione CLIENT_TOOLS)
        assert r_a["choices"][0]["finish_reason"] == "tool_calls"
        sys_a = [m["content"] for m in chat_mod.engine.calls[0]["messages"] if m.get("role") == "system"]
        sys_b = [m["content"] for m in chat_mod.engine.calls[1]["messages"] if m.get("role") == "system"]
        assert "[CLIENT_TOOLS]" in sys_a[0]
        assert "[CLIENT_TOOLS]" not in sys_b[0]
        # Le storie non si mescolano
        assert len(chat_mod.engine.calls[0]["messages"]) == 2  # system + user
        assert len(chat_mod.engine.calls[1]["messages"]) == 2
        _execute_tool_call.assert_not_awaited()
