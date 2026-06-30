"""Embeddings endpoint."""
import base64
import struct

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config import MODEL_ID
from llm_engine import engine
from .models import EmbeddingRequestOpenAI

router = APIRouter()


@router.post("/v1/embeddings")
async def openai_embeddings(payload: EmbeddingRequestOpenAI, request: Request):
    body = payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()
    inputs = body.get("input", [])
    if isinstance(inputs, str):
        inputs = [inputs]

    encoding_format = body.get("encoding_format", "float")

    result = await engine.get_embeddings(inputs, priority=0)
    if "error" in result:
        return JSONResponse(status_code=500, content={"error": result["error"]})

    data_list = result.get("data", [])
    embeddings_data = []
    total_tokens = 0
    for idx, d in enumerate(data_list):
        emb = d.get("embedding", [])
        if encoding_format == "base64":
            emb_bytes = struct.pack(f'{len(emb)}f', *emb)
            emb_b64 = base64.b64encode(emb_bytes).decode("utf-8")
            embeddings_data.append({
                "object": "embedding",
                "embedding": emb_b64,
                "index": idx
            })
        else:
            embeddings_data.append({
                "object": "embedding",
                "embedding": emb,
                "index": idx
            })
        total_tokens += len(inputs[idx]) // 4 if idx < len(inputs) else 0

    return {
        "object": "list",
        "data": embeddings_data,
        "model": body.get("model", MODEL_ID),
        "usage": {
            "prompt_tokens": total_tokens or len(data_list),
            "total_tokens": total_tokens or len(data_list)
        }
    }
