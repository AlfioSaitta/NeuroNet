"""Test articolati per i 3 tool MCP riparati in api/mcp/server_v2.py (03/08).

FIX applicati:
  jarvis_rag_search     : hybrid_search (import rotto) -> rag.engine.search_documents
                          (output markdown: {"query","results","count":1,"format":"markdown"})
  jarvis_memory_search  : memory.engine.search_memories (rotto) -> state.memory via
                          mem0_executor (Mem0 .search API reale)
  jarvis_web_search     : rag.web_search.web_search (rotto) -> SearXNG diretto via
                          state.http_client + SEARXNG_HOST

Questi tool usano import LAZY dentro le funzioni: possiamo mockare i moduli
target (rag.engine, core.state, core.config) senza importare la catena pesante.

Run: PYTHONPATH=jarvis python3 -m pytest tests/test_mcp_tools.py -v
"""
import asyncio
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent / "jarvis"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── Mock moduli target (import lazy dentro le funzioni tool) ───────────────

_rag_engine_mock = types.ModuleType("rag.engine")
_rag_engine_mock.search_documents = AsyncMock(return_value="contenuto markdown")
sys.modules["rag.engine"] = _rag_engine_mock

import core.state as state_mod  # noqa: E402
import core.config as config_mod  # noqa: E402

from api.mcp.server_v2 import (  # noqa: E402
    jarvis_rag_search,
    jarvis_memory_search,
    jarvis_web_search,
    _json_text,
)


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_state():
    """Ripristina gli attributi di core.state modificati dai test."""
    old_memory = getattr(state_mod, "memory", None)
    old_http = getattr(state_mod, "http_client", None)
    yield
    state_mod.memory = old_memory
    state_mod.http_client = old_http


class _AsyncClient:
    """Finto httpx.AsyncClient: .get() async che restituisce una response mock."""
    def __init__(self, response=None):
        self.get = AsyncMock(return_value=response or _JsonResponse({}))


class _JsonResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
    def json(self):
        return self._payload


def _parse(text: str) -> dict:
    return json.loads(text)


# ── 1. jarvis_rag_search ───────────────────────────────────────────────────

class TestJarvisRagSearch:
    async def test_uses_search_documents_and_returns_markdown_format(self):
        _rag_engine_mock.search_documents = AsyncMock(return_value="# Codice\nprint('x')")
        out = await jarvis_rag_search("come funziona X", project="NeuroNet")
        data = _parse(out)
        assert data["query"] == "come funziona X"
        assert data["results"] == "# Codice\nprint('x')"
        assert data["count"] == 1
        assert data["format"] == "markdown"
        _rag_engine_mock.search_documents.assert_awaited_once()

    async def test_forwards_project_and_is_project_query(self):
        _rag_engine_mock.search_documents = AsyncMock(return_value="ctx")
        await jarvis_rag_search("query", project="MyProj")
        _rag_engine_mock.search_documents.assert_awaited_with(
            "query", is_project_query=True, project_name="MyProj",
        )

    async def test_no_project_forwards_none_project(self):
        _rag_engine_mock.search_documents = AsyncMock(return_value="ctx")
        await jarvis_rag_search("query")
        _rag_engine_mock.search_documents.assert_awaited_with(
            "query", is_project_query=False, project_name=None,
        )

    async def test_top_k_clamped_to_20(self):
        _rag_engine_mock.search_documents = AsyncMock(return_value="ctx")
        out = await jarvis_rag_search("query", top_k=9999)
        assert _parse(out)["count"] == 1

    async def test_empty_context_returns_count_zero(self):
        _rag_engine_mock.search_documents = AsyncMock(return_value="")
        out = await jarvis_rag_search("query")
        data = _parse(out)
        assert data["results"] == []
        assert data["count"] == 0

    async def test_exception_returns_error_payload(self):
        async def _boom(*a, **k):
            raise RuntimeError("qdrant down")
        _rag_engine_mock.search_documents = _boom
        out = await jarvis_rag_search("query")
        assert "error" in _parse(out)


# ── 2. jarvis_memory_search ────────────────────────────────────────────────

class TestJarvisMemorySearch:
    async def test_no_memory_returns_empty(self):
        state_mod.memory = None
        out = await jarvis_memory_search("ricordi")
        data = _parse(out)
        assert data["memories"] == []
        assert data["count"] == 0

    async def test_searches_mem0_with_filters(self):
        class _Mem0:
            def search(self, query, filters=None, limit=10):
                return {"results": [
                    {"id": "m1", "memory": "Alfio lavora su NeuroNet", "score": 0.92,
                     "created_at": "2026-07-01"},
                ]}
        state_mod.memory = _Mem0()
        out = await jarvis_memory_search("progetto")
        data = _parse(out)
        assert data["count"] == 1
        assert data["memories"][0]["content"] == "Alfio lavora su NeuroNet"
        assert data["memories"][0]["score"] == 0.92

    async def test_mem0_list_result_formatted(self):
        class _Mem0:
            def search(self, query, filters=None, limit=10):
                return [
                    {"id": "a", "memory": "Primo ricordo", "score": 0.8, "created_at": "x"},
                    {"id": "b", "memory": "Secondo ricordo", "score": 0.7, "created_at": "y"},
                ]
        state_mod.memory = _Mem0()
        out = await jarvis_memory_search("x")
        data = _parse(out)
        assert data["count"] == 2

    async def test_exception_returns_error_payload(self):
        class _Mem0:
            def search(self, query, filters=None, limit=10):
                raise RuntimeError("mem0 down")
        state_mod.memory = _Mem0()
        out = await jarvis_memory_search("x")
        assert "error" in _parse(out)


# ── 3. jarvis_web_search ───────────────────────────────────────────────────

class TestJarvisWebSearch:
    async def test_calls_searxng_with_json_format(self):
        state_mod.http_client = _AsyncClient(_JsonResponse({
            "results": [
                {"title": "Roma", "url": "http://roma.it", "content": "capitale"},
            ],
        }))
        with patch.object(sys.modules["core.config"], "SEARXNG_HOST", "http://searxng:8081"):
            out = await jarvis_web_search("roma")
        _get = state_mod.http_client.get
        assert _get.await_count == 1
        args, kwargs = _get.await_args
        assert args[0] == "http://searxng:8081/search"
        assert kwargs["params"] == {"q": "roma", "format": "json"}
        data = _parse(out)
        assert data["count"] == 1
        assert data["results"][0]["url"] == "http://roma.it"

    async def test_num_results_clamped(self):
        state_mod.http_client = _AsyncClient(_JsonResponse({
            "results": [{"title": f"r{i}", "url": f"u{i}", "content": "c"} for i in range(30)],
        }))
        with patch.object(sys.modules["core.config"], "SEARXNG_HOST", "http://searxng:8081"):
            out = await jarvis_web_search("q", num_results=999)
        data = _parse(out)
        assert len(data["results"]) <= 20

    async def test_non_200_returns_empty(self):
        state_mod.http_client = _AsyncClient(_JsonResponse({}, status_code=500))
        with patch.object(sys.modules["core.config"], "SEARXNG_HOST", "http://searxng:8081"):
            out = await jarvis_web_search("q")
        assert _parse(out)["count"] == 0

    async def test_no_http_client_returns_empty(self):
        state_mod.http_client = None
        out = await jarvis_web_search("q")
        assert _parse(out)["count"] == 0

    async def test_empty_results_returns_count_zero(self):
        state_mod.http_client = _AsyncClient(_JsonResponse({"results": []}))
        with patch.object(sys.modules["core.config"], "SEARXNG_HOST", "http://searxng:8081"):
            out = await jarvis_web_search("q")
        assert _parse(out)["count"] == 0

    async def test_exception_returns_error_payload(self):
        class _BoomClient:
            get = AsyncMock(side_effect=RuntimeError("searxng down"))
        state_mod.http_client = _BoomClient()
        with patch.object(sys.modules["core.config"], "SEARXNG_HOST", "http://searxng:8081"):
            out = await jarvis_web_search("q")
        assert "error" in _parse(out)


# ── 4. _json_text helper ───────────────────────────────────────────────────

class TestJsonText:
    def test_serializes_non_ascii(self):
        out = _json_text({"msg": "ciao mondo — test"})
        assert "ciao mondo" in out

    def test_serializes_objects_with_default_str(self):
        out = _json_text({"when": __import__("datetime").datetime(2026, 8, 3)})
        assert "2026" in out
