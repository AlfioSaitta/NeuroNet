"""
OpenAI-compatible API endpoints — modular package.
Refactored from openai_router.py for maintainability.

Sub-modules are loaded lazily to avoid triggering heavy import chains
(config, llm_engine, qdrant, etc.) when only ``state.py`` is needed.
Call ``init_openai_routes()`` after the app lifespan to populate the
router with all endpoint modules.
"""
from fastapi import APIRouter

router = APIRouter(tags=["OpenAI API"])
_routes_initialized = False


def init_openai_routes() -> None:
    """Lazy-import and register all endpoint sub-modules.

    Must be called once after all app dependencies are ready
    (i.e. inside the FastAPI lifespan).
    """
    global _routes_initialized
    if _routes_initialized:
        return
    _routes_initialized = True

    # Import sub-modules here — they have heavy dependencies (llm_engine,
    # prompt_builder, qdrant, etc.) that must be available.
    import importlib
    _modules = (
        "chat", "completions", "embeddings", "audio", "moderations",
        "images", "files", "uploads", "models",
        "assistants", "threads", "runs", "vector_stores",
    )
    for name in _modules:
        mod = importlib.import_module(f".{name}", __package__)
        router.include_router(mod.router)

