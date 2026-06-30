"""Pydantic models + model listing endpoints for OpenAI-compatible API."""
import os
from datetime import datetime, UTC
from typing import List, Optional, Dict, Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from config import MODEL_ID, LLAMA_MODEL_PATH, LLAMA_EMBED_MODEL_PATH

router = APIRouter()


class OpenAIMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequestOpenAI(BaseModel):
    model: str
    messages: List[OpenAIMessage]
    stream: Optional[bool] = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    stop: Optional[List[str]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[str | Dict[str, Any]] = None
    parallel_tool_calls: Optional[bool] = None
    response_format: Optional[Dict[str, Any]] = None
    seed: Optional[int] = None
    logprobs: Optional[bool] = None
    top_logprobs: Optional[int] = None
    n: Optional[int] = 1
    user: Optional[str] = None
    confirmation_token: Optional[str] = None
    model_config = ConfigDict(extra="allow")


class CompletionRequestOpenAI(BaseModel):
    model: str
    prompt: str | List[str]
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    stop: Optional[List[str]] = None
    stream: Optional[bool] = False
    echo: Optional[bool] = False
    n: Optional[int] = 1
    suffix: Optional[str] = None
    best_of: Optional[int] = None
    logprobs: Optional[int] = None
    user: Optional[str] = None
    model_config = ConfigDict(extra="allow")


class EmbeddingRequestOpenAI(BaseModel):
    model: str
    input: str | List[str]
    encoding_format: Optional[str] = "float"
    user: Optional[str] = None
    model_config = ConfigDict(extra="allow")


class SpeechRequestOpenAI(BaseModel):
    model: str
    input: str
    voice: Optional[str] = "alloy"
    speed: Optional[float] = 1.0
    response_format: Optional[str] = "mp3"
    model_config = ConfigDict(extra="allow")


class ModerationRequestOpenAI(BaseModel):
    input: str | List[str]
    model: Optional[str] = None


# ── Helpers ──────────────────────────────────────────────────────────────────────


def _model_entry(model_id: str, owned_by: str = "jarvis",
                 model_family: str = "") -> dict:
    """Build a single model object for the /v1/models response.

    Attempts to read the model file's mtime for a realistic ``created``
    timestamp; falls back to current time if the file is not found.
    """
    created = int(datetime.now(UTC).timestamp())
    # Try to get a meaningful timestamp from config
    for candidate in (LLAMA_MODEL_PATH, LLAMA_EMBED_MODEL_PATH):
        if candidate and os.path.exists(candidate):
            try:
                created = int(os.path.getmtime(candidate))
            except Exception:
                pass
            break
    return {
        "id": model_id,
        "object": "model",
        "created": created,
        "owned_by": owned_by,
    }


# ── Model listing endpoints ──


@router.get("/v1/models")
async def openai_models():
    """List available models — reads from LlamaEngine at runtime."""
    models = []

    # Primary chat model
    models.append(_model_entry(MODEL_ID, "jarvis"))

    # Embedding model (infer name from path)
    embed_name = os.path.splitext(os.path.basename(LLAMA_EMBED_MODEL_PATH))[0]
    models.append(_model_entry(embed_name, "jarvis"))

    # External provider models (Gemini, if configured)
    try:
        from config import EXTERNAL_PROVIDERS
        for provider_id in EXTERNAL_PROVIDERS:
            models.append(_model_entry(provider_id, f"jarvis-{provider_id}"))
    except (ImportError, AttributeError):
        pass

    # Chat model variations from config (if a different model string is set)
    try:
        from config import LLAMA_MODEL_PATH as _lm_path
        if _lm_path and os.path.exists(_lm_path):
            basename = os.path.splitext(os.path.basename(_lm_path))[0]
            if basename != MODEL_ID and basename != embed_name:
                models.append(_model_entry(basename, "jarvis"))
    except Exception:
        pass

    return {"object": "list", "data": models}


@router.get("/v1/models/{model_name}")
async def openai_models_detail(model_name: str):
    """Detail for a specific model."""
    return {
        "id": model_name,
        "object": "model",
        "created": int(datetime.now(UTC).timestamp()),
        "owned_by": "jarvis",
        "permissions": [],
    }
