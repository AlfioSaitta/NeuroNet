"""
OpenAI-compatible API endpoints per Jarvis Cognitive Proxy.
Estratto da main.py per modularizzazione.
"""

import json
import os
import re
import uuid
import base64
import struct
import tempfile
import logging
from datetime import datetime, UTC
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse, Response
from pydantic import BaseModel, Field, ConfigDict

from config import (
    logger, OLLAMA_MODEL, API_RATE_LIMIT_DEFAULT,
    API_RATE_LIMIT_HEAVY, API_RATE_LIMIT_EMBED,
)
from llm_engine import engine
from prompt_builder import build_omniscient_prompt
from memory import process_response_tags
from agent_tools import execute_tool_call
from confirmation_manager import ApiTokenProvider, ConfirmationManager
from classificatore import classify_confirmation
import state

router = APIRouter(tags=["OpenAI API"])


# ==============================================================================
# Pydantic Models
# ==============================================================================

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


# ==============================================================================
# Whisper model (lazy singleton)
# ==============================================================================

_whisper_model = None


# ==============================================================================
# Handlers
# ==============================================================================

@router.get("/v1/models")
async def openai_models():
    return {
        "object": "list",
        "data": [
            {
                "id": OLLAMA_MODEL,
                "object": "model",
                "created": 1710000000,
                "owned_by": "ollama"
            },
            {
                "id": "nomic-embed-text:latest",
                "object": "model",
                "created": 1710000000,
                "owned_by": "ollama"
            }
        ]
    }


@router.post("/v1/chat/completions")
async def openai_chat_completions(payload: ChatCompletionRequestOpenAI, request: Request):
    state.total_requests += 1

    body = payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()
    is_stream = body.get("stream", False)
    raw_messages = body.get("messages", [])

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

    ollama_messages = [{"role": m["role"], "content": m["content"]} for m in raw_messages]

    current_user_id = body.get("user_id") or "alfio_dev"
    conversation_id = body.get("conversation_id") or request.headers.get("X-Conversation-Id", "default")
    concise = body.get("concise", False)

    # ── Confirmation token handling ──
    confirmation_mgr = None
    confirmation_token = body.get("confirmation_token") or payload.confirmation_token
    if confirmation_token:
        resolved = ApiTokenProvider.resolve(confirmation_token, approved=True)
        if resolved:
            return {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(datetime.now(UTC).timestamp()),
                "model": OLLAMA_MODEL,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "✅ Conferma ricevuta. Operazione autorizzata."}, "finish_reason": "stop"}],
                "usage": {}
            }
    elif raw_messages:
        last_msg = raw_messages[-1] if isinstance(raw_messages[-1], dict) else {}
        if last_msg.get("role") == "user":
            msg_text = str(last_msg.get("content", ""))
            result = classify_confirmation(msg_text)
            if result:
                token, approved = result
                api_resolved = ApiTokenProvider.resolve(token, approved=approved)
                if api_resolved:
                    status_text = "✅ Conferma ricevuta. Operazione autorizzata." if approved else "❌ Operazione rifiutata."
                    return {
                        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                        "object": "chat.completion",
                        "created": int(datetime.now(UTC).timestamp()),
                        "model": OLLAMA_MODEL,
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": status_text}, "finish_reason": "stop"}],
                        "usage": {}
                    }
                else:
                    return {
                        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                        "object": "chat.completion",
                        "created": int(datetime.now(UTC).timestamp()),
                        "model": OLLAMA_MODEL,
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": "⚠️ Token di conferma non valido o scaduto."}, "finish_reason": "stop"}],
                        "usage": {}
                    }

    enriched = await build_omniscient_prompt(
        ollama_messages, user_id=current_user_id,
        conversation_id=str(conversation_id), concise=concise
    )
    tools = body.get("tools")
    if not is_stream:
        response = await engine.generate_chat_with_router(enriched, tools=tools, options=options, stream=False, preferred_provider=body.get("provider"))
        if "error" in response:
            return JSONResponse(status_code=500, content={"error": response["error"]})

        state.total_prompt_tokens += response.get("usage", {}).get("prompt_tokens", 0)
        state.total_completion_tokens += response.get("usage", {}).get("completion_tokens", 0)

        choice = response["choices"][0]["message"]

        # ── Tool calling loop (non-stream) ──
        tool_calls = choice.get("tool_calls", [])
        if tool_calls:
            if confirmation_mgr is None:
                confirmation_mgr = ConfirmationManager.from_request(request_id=conversation_id)
            enriched.append(dict(choice))
            for tc in tool_calls:
                tool_res = await execute_tool_call(tc, confirmation_mgr=confirmation_mgr)
                enriched.append({
                    "role": "tool", "content": tool_res,
                    "name": tc.get("function", {}).get("name", "unknown")
                })
            response = await engine.generate_chat_with_router(enriched, tools=tools, options=options, stream=False, preferred_provider=body.get("provider"))
            if "error" in response:
                return JSONResponse(status_code=500, content={"error": response["error"]})
            choice = response["choices"][0]["message"]

        content = choice.get("content", "")
        cleaned = await process_response_tags(content, user_id=current_user_id)
        if not cleaned and content:
            cleaned = content
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(datetime.now(UTC).timestamp()),
            "model": OLLAMA_MODEL,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": choice.get("role", "assistant"),
                        "content": cleaned
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": response.get("usage", {})
        }
    else:
        async def openai_stream_gen():
            gen = await engine.generate_chat_with_router(enriched, tools=tools, options=options, stream=True, preferred_provider=body.get("provider"))
            if isinstance(gen, dict) and "error" in gen:
                yield f"data: {json.dumps({'error': gen['error']})}\n\n"
                return

            response_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
            response_created = int(datetime.now(UTC).timestamp())

            full_chunks = []
            role_sent = False
            async for chunk in gen:
                if "choices" in chunk and len(chunk["choices"]) > 0:
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    finish_reason = chunk["choices"][0].get("finish_reason")

                    if not role_sent:
                        role_sent = True
                        yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': response_created, 'model': OLLAMA_MODEL, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
                        if content:
                            yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': response_created, 'model': OLLAMA_MODEL, 'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': None}]})}\n\n"
                    else:
                        delta_dict = {"content": content} if content else {}
                        yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': response_created, 'model': OLLAMA_MODEL, 'choices': [{'index': 0, 'delta': delta_dict, 'finish_reason': finish_reason}]})}\n\n"

                    if content:
                        full_chunks.append(content)
                    if finish_reason:
                        break

            full_text = "".join(full_chunks)
            if full_text:
                await process_response_tags(full_text, user_id=current_user_id)
            yield "data: [DONE]\n\n"

        return StreamingResponse(openai_stream_gen(), media_type="text/event-stream")


@router.post("/v1/completions")
async def openai_completions(payload: CompletionRequestOpenAI, request: Request):
    """Endpoint text completions in formato OpenAI (legacy)."""
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


@router.post("/v1/embeddings")
async def openai_embeddings(payload: EmbeddingRequestOpenAI, request: Request):
    """Endpoint embeddings in formato OpenAI."""
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
        "model": body.get("model", OLLAMA_MODEL),
        "usage": {
            "prompt_tokens": total_tokens or len(data_list),
            "total_tokens": total_tokens or len(data_list)
        }
    }


@router.post("/v1/audio/transcriptions")
async def openai_audio_transcriptions(request: Request):
    """Trascrizione audio tramite faster-whisper in formato OpenAI."""
    global _whisper_model

    form = await request.form()
    audio_file = form.get("file")
    if not audio_file:
        return JSONResponse(status_code=400, content={"error": "Missing 'file' field"})

    language = form.get("language", None)
    response_format = form.get("response_format", "json")
    prompt_text = form.get("prompt", None)

    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")

    audio_bytes = await audio_file.read()
    suffix = os.path.splitext(str(audio_file.filename))[1] or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        segments, info = _whisper_model.transcribe(
            tmp_path,
            language=language or None,
            initial_prompt=prompt_text or None,
            beam_size=5
        )
        segments_list = list(segments)
        full_text = " ".join(seg.text for seg in segments_list)
    except Exception as e:
        logger.error(f"Whisper transcription error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    if response_format == "text":
        return Response(content=full_text, media_type="text/plain")

    return {"text": full_text}


@router.post("/v1/audio/speech")
async def openai_audio_speech(payload: SpeechRequestOpenAI, request: Request):
    """Text-to-speech tramite gTTS in formato OpenAI."""
    from gtts import gTTS
    import io as _io

    tts = gTTS(text=payload.input, lang="it", slow=False)
    audio_buf = _io.BytesIO()
    tts.write_to_fp(audio_buf)
    audio_buf.seek(0)

    return Response(content=audio_buf.read(), media_type="audio/mpeg")


@router.get("/v1/models/{model_name}")
async def openai_models_detail(model_name: str):
    """Dettaglio modello in formato OpenAI."""
    return {
        "id": model_name,
        "object": "model",
        "created": 1710000000,
        "owned_by": "ollama",
        "permissions": []
    }


# ── Moderation fallback ──

def _moderation_fallback(input_text: str) -> dict:
    """Fallback per moderazione quando il modello locale non risponde."""
    text_lower = input_text.lower()
    flagged = False
    categories = {}
    for category, keywords in {
        "hate": ["odio", "uccidi", "ammazza", "brucia"],
        "sexual": ["sesso", "porno", "xxx"],
        "violence": ["violento", "omicidio", "strage"],
        "self-harm": ["suicidio", "ammazzarmi"],
    }.items():
        found = any(kw in text_lower for kw in keywords)
        categories[category] = found
        if found:
            flagged = True
    return {"flagged": flagged, "categories": categories}


@router.post("/v1/moderations")
async def openai_moderations(payload: ModerationRequestOpenAI, request: Request):
    """Endpoint moderazione contenuti in formato OpenAI."""
    state.total_requests += 1

    inputs = payload.input if isinstance(payload.input, list) else [payload.input]
    results = []

    try:
        for text in inputs:
            messages = [
                {"role": "system", "content": "Sei un moderatore di contenuti. Rispondi SOLO con un JSON valido con chiavi 'flagged' (bool) e 'categories' (dict string->bool)."},
                {"role": "user", "content": f"Modera questo contenuto: {text[:2000]}"}
            ]
            response = await engine.generate_chat(messages, options={"temperature": 0.1, "num_predict": 100}, stream=False)
            content = response["choices"][0]["message"].get("content", "")

            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                mod_result = json.loads(json_match.group())
                flagged = mod_result.get("flagged", False)
                categories = mod_result.get("categories", {})
            else:
                raise ValueError("Nessun JSON nella risposta")
            results.append({
                "flagged": flagged,
                "categories": categories,
            })
    except Exception:
        for text in inputs:
            results.append(_moderation_fallback(text))

    return {
        "id": f"modr-{uuid.uuid4().hex[:12]}",
        "model": "jarvis-moderation",
        "results": [{"flagged": r["flagged"], "categories": r["categories"]} for r in results]
    }
