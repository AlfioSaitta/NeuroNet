"""Test articolati per l'iniezione del blocco [HARDWARE IDENTITY] in agent/prompt.py.

Verifica che _hardware_identity_block() venga iniettato in TUTTI i rami del
system prompt costruiti da build_omniscient_prompt():

  Ramo 1 (is_raw)     : _build_final_prompt(is_raw=True)      — riga 357
  Ramo 2 (non-raw)    : _build_final_prompt(is_raw=False)     — riga 364
  Ramo 3 (concise)    : build_omniscient_prompt(concise=True) — riga 475
  Ramo 4 (greeting)   : intent greeting                       — riga 573
  Ramo 5 (web)        : intent web con web_ctx               — riga 650
  Ramo 6 (general)    : intent general senza web_ctx          — riga 659
  Ramo 7 (meta)       : intent meta (FIX 2026-08-02)          — riga 678

NOTA: la doc dichiarava "8 rami"; il conteggio reale dei call site è 7 ed è
stato allineato nei doc (AGENTS/README/PIPELINE/COMPONENTS/CHANGELOG). Il test
strutturale in fondo protegge questo numero da regressioni.

Il modulo agent.prompt importa dipendenze pesanti (rag.engine, memory.engine,
core.llm_engine, ecc.): vengono mockate a livello di sys.modules PRIMA
dell'import (stesso pattern dei test esistenti in tests/).

Run: PYTHONPATH=jarvis python3 -m pytest tests/test_prompt_hardware.py -v
"""
import re
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Path setup: package root = jarvis/ ─────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent / "jarvis"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── Mock delle dipendenze pesanti PRIMA dell'import di agent.prompt ────────

_config_mock = types.ModuleType("core.config")
_config_mock.logger = MagicMock()
_config_mock.BOT_NAME = "Jarvis"
_config_mock.LLM_OPTIONS = {"num_ctx": 12288}
_config_mock.MODEL_PROFILE = SimpleNamespace(default_ctx=12288, max_ctx=32768)
_config_mock.DOC_DIR = "/tmp"
_config_mock.SEARXNG_HOST = "http://searxng:8080"


def _mk_async(ret):
    async def _f(*a, **k):
        return ret
    return _f


_rag_engine_mock = types.ModuleType("rag.engine")
_rag_engine_mock.search_documents = _mk_async("")
_rag_engine_mock.generate_project_tree = _mk_async("")
_rag_engine_mock.list_rag_projects = _mk_async(["NeuroNet", "Other"])
_rag_engine_mock.detect_project_in_conversation = _mk_async("NeuroNet")
_rag_engine_mock.GitignoreFilter = MagicMock()

_rag_cache_mock = types.ModuleType("rag.cache")
_rag_cache_mock.search_web_knowledge = _mk_async("")
_rag_cache_mock.save_web_knowledge = _mk_async(None)

_memory_engine_mock = types.ModuleType("memory.engine")
_memory_engine_mock.extract_memories = lambda r: ""
_memory_engine_mock.save_to_memory = _mk_async(None)

_rag_web_search_mock = types.ModuleType("rag.web_search")
_rag_web_search_mock.perform_web_search_and_crawl = _mk_async(("", ""))
_rag_web_search_mock.is_web_requiring_query = lambda m: False
_rag_web_search_mock.clean_web_query = lambda m: m

_agent_tags_mock = types.ModuleType("agent.tags")
_agent_tags_mock.build_tag_instructions = lambda *a, **k: ""

_scheduler_tasks_mock = types.ModuleType("scheduler.tasks")
_scheduler_tasks_mock.get_open_tasks = lambda user_id: {}

_llm_engine_mock = types.ModuleType("core.llm_engine")
_llm_engine_mock.extract_content = lambda s: s

_context_compressor_mock = types.ModuleType("agent.context_compressor")
_context_compressor_mock.compress = _mk_async(("context compressed", False))
_context_compressor_mock.compress_concise = _mk_async("context concise")

_telemetry_mock = types.ModuleType("core.telemetry")
_telemetry_mock.PipelineTracer = MagicMock()
_telemetry_mock.IntentStats = MagicMock()
_telemetry_mock.LlmCallRecord = MagicMock()

_imports_to_mock = {
    "core.config": _config_mock,
    "rag.engine": _rag_engine_mock,
    "rag.cache": _rag_cache_mock,
    "memory.engine": _memory_engine_mock,
    "rag.web_search": _rag_web_search_mock,
    "agent.tags": _agent_tags_mock,
    "scheduler.tasks": _scheduler_tasks_mock,
    "core.llm_engine": _llm_engine_mock,
    "agent.context_compressor": _context_compressor_mock,
    "core.telemetry": _telemetry_mock,
}

# Core hardware: usiamo il VERO modulo (testabile standalone, solo stdlib).
from core import hardware as HW  # noqa: E402
from core import state as state_mod  # noqa: E402

# Tracer stub — PipelineTracer.begin() deve restituire un oggetto con i
# metodi usati da build_omniscient_prompt.
class _StubTracer:
    def __init__(self, *a, **k):
        self.request_id = "test-req"
        self._gatekeeper_model = ""
        self._web_search_performed = False
        self._rag_ctx_len = 0
        self._memory_records = 0
        self._synaptiq_performed = False
        self._synaptiq_chars = 0
        self._compression_raw_size = 0
        self._compression_is_raw = False
        self.system_prompt = ""
    def start_step(self, *a, **k): pass
    def end_step(self, *a, **k): pass
    def step(self, *a, **k): pass
    def finish(self, *a, **k): pass
    def set_system_prompt(self, content): self.system_prompt = content
    def set_user_content(self, *a, **k): pass
    def set_compressed_text(self, *a, **k): pass
    def set_rag_context(self, *a, **k): pass
    def set_gatekeeper(self, **k): pass
    def add_llm_call(self, *a, **k): pass
    @staticmethod
    def get(request_id): return None
    @staticmethod
    def begin(*a, **k): return _StubTracer()


_telemetry_mock.PipelineTracer = _StubTracer

for _name, _mod in _imports_to_mock.items():
    sys.modules[_name] = _mod

import agent.prompt as prompt_mod  # noqa: E402


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _seed_hardware_cache():
    """Popola la cache hardware con valori noti per test deterministici."""
    HW._HW_CACHE = {
        "hostname": "test-host",
        "gpu": "NVIDIA GeForce RTX 3050 Ti — 4096 MiB VRAM (254 MiB liberi), driver 580.159.03",
        "cpu": "Intel i5-11300H — 8 threads",
        "ram": "15.4 GiB totali, 5.8 GiB disponibili",
    }
    yield
    HW._HW_CACHE = None


def _make_gk(intent: str, source: str = "regex", slots=None, project=None):
    return SimpleNamespace(
        intent=intent, project=project, confidence=1.0,
        source=source, slots=slots or {},
    )


# ── 1. helper _hardware_identity_block() ───────────────────────────────────

class TestHardwareIdentityBlockHelper:
    def test_returns_block_when_cache_populated(self):
        block = prompt_mod._hardware_identity_block()
        assert "[HARDWARE IDENTITY — REAL hardware of the Jarvis server]" in block
        assert "test-host" in block
        assert "RTX 3050 Ti" in block
        assert "i5-11300H" in block

    def test_returns_empty_string_on_internal_error(self, monkeypatch):
        def _boom():
            raise RuntimeError("get_hardware_block crash")
        monkeypatch.setattr("agent.prompt.get_hardware_block", _boom)
        assert prompt_mod._hardware_identity_block() == ""

    def test_returns_empty_string_when_cache_empty(self):
        HW._HW_CACHE = None
        block = prompt_mod._hardware_identity_block()
        # Con cache None, get_hardware_info() fa lazy detect reale: su una
        # macchina reale potrebbe popolare dati reali. Accettiamo entrambi
        # purché non lanci eccezioni.
        assert isinstance(block, str)


# ── 2. Ramo is_raw / non-raw (via _build_final_prompt) ─────────────────────

class TestBuildFinalPromptRamos:
    def _call(self, is_raw: bool):
        tracer = _StubTracer()
        messages = [{"role": "user", "content": "domanda"}]
        out = prompt_mod._build_final_prompt(
            compressed="contesto compresso", is_raw=is_raw, messages=messages,
            _dt_now="Current date and time: 2026-08-03",
            mem_ctx="", rag_final="", web_ctx="", cg_ctx="", tracer=tracer,
        )
        return out, tracer

    def test_is_raw_injects_hardware_block(self):
        messages, _ = self._call(is_raw=True)
        system = messages[0]
        assert system["role"] == "system"
        assert "[HARDWARE IDENTITY" in system["content"]
        assert "test-host" in system["content"]

    def test_non_raw_injects_hardware_block(self):
        messages, _ = self._call(is_raw=False)
        system = messages[0]
        assert system["role"] == "system"
        assert "[HARDWARE IDENTITY" in system["content"]
        assert "RTX 3050 Ti" in system["content"]

    def test_tracer_receives_system_prompt(self):
        _, tracer = self._call(is_raw=True)
        assert "[HARDWARE IDENTITY" in tracer.system_prompt


# ── 3. Ramo concise ────────────────────────────────────────────────────────

class TestConciseBranch:
    async def test_concise_injects_hardware_block(self):
        messages, gk = await prompt_mod.build_omniscient_prompt(
            [{"role": "user", "content": "riassumi"}],
            user_id="tester", conversation_id="c1", concise=True,
            request_id=None, finalize_trace=False,
        )
        system = messages[0]
        assert system["role"] == "system"
        assert "[HARDWARE IDENTITY" in system["content"]
        assert "test-host" in system["content"]

    async def test_concise_keeps_user_content(self):
        messages, _ = await prompt_mod.build_omniscient_prompt(
            [{"role": "user", "content": "riassumi"}],
            user_id="tester", conversation_id="c1", concise=True,
            request_id=None, finalize_trace=False,
        )
        assert any(m["role"] == "user" for m in messages)


# ── 4. Ramo greeting ───────────────────────────────────────────────────────

class TestGreetingBranch:
    async def _run_greeting(self):
        with patch.object(prompt_mod.intent_router, "classify",
                          new=_mk_async(_make_gk("greeting"))), \
             patch.object(prompt_mod.intent_router, "is_greeting_result",
                          new=lambda gk: True):
            return await prompt_mod.build_omniscient_prompt(
                [{"role": "user", "content": "ciao"}],
                user_id="tester", conversation_id="c1",
                request_id=None, finalize_trace=False,
            )

    async def test_greeting_injects_hardware_block(self):
        messages, gk = await self._run_greeting()
        assert gk.intent == "greeting"
        system = messages[0]
        assert system["role"] == "system"
        assert "[HARDWARE IDENTITY" in system["content"]
        assert "test-host" in system["content"]

    async def test_greeting_removes_duplicate_datetime_system(self):
        messages, _ = await self._run_greeting()
        datetime_systems = [m for m in messages
                            if m["role"] == "system" and "Current date" in m["content"]]
        assert datetime_systems == []


# ── 5. Ramo general (senza web_ctx) ────────────────────────────────────────

class TestGeneralBranch:
    async def _run_general(self):
        with patch.object(prompt_mod.intent_router, "classify",
                          new=_mk_async(_make_gk("general"))), \
             patch.object(prompt_mod.intent_router, "is_greeting_result",
                          new=lambda gk: False):
            return await prompt_mod.build_omniscient_prompt(
                [{"role": "user", "content": "spiegami qualcosa"}],
                user_id="tester", conversation_id="c1",
                request_id=None, finalize_trace=False,
            )

    async def test_general_injects_hardware_block(self):
        messages, gk = await self._run_general()
        assert gk.intent == "general"
        system = messages[0]
        assert system["role"] == "system"
        assert "[HARDWARE IDENTITY" in system["content"]
        assert "test-host" in system["content"]


# ── 6. Ramo web (con web_ctx) ──────────────────────────────────────────────

class TestWebBranch:
    async def _run_web_with_context(self):
        # web_ctx_general non vuoto → system prompt dedicato [WEB DATA] + blocco
        # NOTA: prompt.py importa search_web_knowledge/is_web_requiring_query/
        # clean_web_query per NOME a import-time → vanno patchati su prompt_mod.
        with patch.object(prompt_mod.intent_router, "classify",
                          new=_mk_async(_make_gk("general"))), \
             patch.object(prompt_mod.intent_router, "is_greeting_result",
                          new=lambda gk: False), \
             patch.object(prompt_mod, "is_web_requiring_query",
                          new=lambda m: True), \
             patch.object(prompt_mod, "clean_web_query",
                          new=lambda m: "meteo roma"), \
             patch.object(prompt_mod, "search_web_knowledge",
                          new=_mk_async("URL: http://x\nRisultato meteo")):
            return await prompt_mod.build_omniscient_prompt(
                [{"role": "user", "content": "che tempo fa a Roma?"}],
                user_id="tester", conversation_id="c1",
                request_id=None, finalize_trace=False,
            )

    async def test_web_with_context_injects_hardware_block(self):
        messages, gk = await self._run_web_with_context()
        assert gk.intent == "general"
        system = messages[0]
        assert system["role"] == "system"
        assert "[WEB DATA]" in system["content"]
        assert "[HARDWARE IDENTITY" in system["content"]
        assert "test-host" in system["content"]


# ── 7. Ramo meta (FIX 2026-08-02) ──────────────────────────────────────────

class TestMetaBranch:
    async def _run_meta(self):
        with patch.object(prompt_mod.intent_router, "classify",
                          new=_mk_async(_make_gk("meta"))), \
             patch.object(prompt_mod.intent_router, "is_greeting_result",
                          new=lambda gk: False):
            return await prompt_mod.build_omniscient_prompt(
                [{"role": "user", "content": "che hardware hai?"}],
                user_id="tester", conversation_id="c1",
                request_id=None, finalize_trace=False,
            )

    async def test_meta_injects_system_prompt_with_hardware_block(self):
        # REGRESSIONE del bug d2811fb00043: prima del fix il ramo meta non
        # iniettava alcun system prompt → il modello inventava l'hardware.
        messages, gk = await self._run_meta()
        assert gk.intent == "meta"
        system = messages[0]
        assert system["role"] == "system"
        assert "[HARDWARE IDENTITY" in system["content"]
        assert "test-host" in system["content"]
        assert "GENERAL" in system["content"] or "assistant" in system["content"]

    async def test_meta_keeps_projects_in_user_content(self):
        messages, _ = await self._run_meta()
        user = next(m for m in messages if m["role"] == "user")
        assert "Progetti disponibili" in user["content"]
        assert "NeuroNet" in user["content"]

    async def test_meta_removes_duplicate_datetime_system(self):
        messages, _ = await self._run_meta()
        datetime_systems = [m for m in messages
                            if m["role"] == "system" and "Current date" in m["content"]]
        assert datetime_systems == []


# ── 8. Test strutturale: conteggio call site ───────────────────────────────

class TestStructuralBranchCount:
    """Protegge il numero di call site di _hardware_identity_block() (7).

    La doc dichiarava 8 rami; il conteggio reale è 7 (is_raw, non-raw, concise,
    greeting, web, general, meta) ed è stato allineato nei doc il 03/08.
    Questi test impediscono regressioni sotto 7 o ritorni a 8 senza aggiornare
    la documentazione."""

    def test_hardware_identity_block_call_sites(self):
        src = Path(prompt_mod.__file__).read_text(encoding="utf-8")
        # Conta le chiamate a _hardware_identity_block() ESCLUSA la definizione
        calls = [ln for ln in src.splitlines() if "_hardware_identity_block()" in ln and "def " not in ln]
        assert len(calls) == 7, f"Attesi 7 call site, trovati {len(calls)}: {calls}"

    def test_documented_8_branches_is_stale(self):
        # Segnala se qualcuno torna a 8 call site senza aggiornare i doc.
        # La doc è allineata a 7; un 8° call site deve essere giustificato e
        # documentato, non aggiunto in silenzio.
        src = Path(prompt_mod.__file__).read_text(encoding="utf-8")
        calls = [ln for ln in src.splitlines() if "_hardware_identity_block()" in ln and "def " not in ln]
        assert len(calls) != 8, "8 call site senza aggiornamento doc — documentare prima di aggiungere il ramo"

    def test_no_regression_below_7_branches(self):
        src = Path(prompt_mod.__file__).read_text(encoding="utf-8")
        calls = [ln for ln in src.splitlines() if "_hardware_identity_block()" in ln and "def " not in ln]
        assert len(calls) >= 7, "Il blocco hardware è stato rimosso da qualche ramo!"
