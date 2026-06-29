"""Legacy text completions endpoint."""
import json
import uuid
from datetime import datetime, UTC

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse

from config import OLLAMA_MODEL
from llm_engine import engine
from .models import CompletionRequestOpenAI
import state

router = APIRouter()


@router.post("/v1/completions")
async def openai_completions(payload: CompletionRequestOpenAI, request: Request):
    state.total_requests += 1

    body = payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()
    is_stream = body.get("stream", False)

    raw_prompt = body.get("prompt", "")
    if isinstance(raw_prompt, list):
        raw_prompt = " ".join(raw_prompt)
    prompt_str = str(raw_prompt)

    options = {}
    if body.get("temperature") is not None:
        options["temperature"] = body["temperature"]
    if body.get("max_tokens") is not None:
        options["num_predict"] = body["max_tokens"]
    if body.get("top_p") is not None:
        options["top_p"] = body["top_p"]
    if body.get("stop") is not None:
        stop_seq = body["stop"]
        if isinstance(stop_seq, list):
            options["stop"] = stop_seq
        elif isinstance(stop_seq, str):
            options["stop"] = [stop_seq]
    if body.get("suffix"):
        options["suffix"] = body["suffix"]

    messages = [{"role": "user", "content": prompt_str}]

    if not is_stream:
        response = await engine.generate_chat(messages, options=options, stream=False)
        if "error" in response:
            return JSONResponse(status_code=500, content={"error": response["error"]})

        state.total_prompt_tokens += response.get("usage", {}).get("prompt_tokens", 0)
        state.total_completion_tokens += response.get("usage", {}).get("completion_tokens", 0)

        content = response["choices"][0]["message"].get("content", "")
        echo_prefix = prompt_str if body.get("echo") else ""
        return {
            "id": f"cmpl-{uuid.uuid4().hex[:12]}",
            "object": "text_completion",
            "created": int(datetime.now(UTC).timestamp()),
            "model": OLLAMA_MODEL,
            "choices": [
                {
                    "text": echo_prefix + content,
                    "index": 0,
                    "logprobs": None,
                    "finish_reason": "stop"
                }
            ],
            "usage": response.get("usage", {})
        }
    else:
        async def completion_stream_gen():
            gen = await engine.generate_chat(messages, options=options, stream=True)
            if isinstance(gen, dict) and "error" in gen:
                yield f"data: {json.dumps({'error': gen['error']})}\n\n"
                return

            response_id = f"cmpl-{uuid.uuid4().hex[:12]}"
            response_created = int(datetime.now(UTC).timestamp())

            async for chunk in gen:
                if "choices" in chunk and len(chunk["choices"]) > 0:
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    finish_reason = chunk["choices"][0].get("finish_reason")

                    yield f"data: {json.dumps({'id': response_id, 'object': 'text_completion', 'created': response_created, 'model': OLLAMA_MODEL, 'choices': [{'index': 0, 'text': content, 'logprobs': None, 'finish_reason': finish_reason}]})}\n\n"

                    if finish_reason:
                        break

            yield "data: [DONE]\n\n"

        return StreamingResponse(completion_stream_gen(), media_type="text/event-stream")
