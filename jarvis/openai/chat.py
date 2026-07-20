"""Chat completions endpoint."""
import asyncio
import json
import uuid
from datetime import datetime, UTC

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse

from config import logger, MODEL_ID
from llm_engine import engine
from prompt_builder import build_omniscient_prompt
from memory import process_response_tags
from tag_processor import strip_action_tags, TagSafeStream
from agent_tools import execute_tool_call
from confirmation_manager import ApiTokenProvider, ConfirmationManager
from classificatore import classify_confirmation
from .models import ChatCompletionRequestOpenAI
import state

router = APIRouter()


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
    if body.get("seed") is not None:
        options["seed"] = body["seed"]
    if body.get("stop") is not None:
        stop_seq = body["stop"]
        if isinstance(stop_seq, list):
            options["stop"] = stop_seq
        elif isinstance(stop_seq, str):
            options["stop"] = [stop_seq]

    # ── response_format: json_object → JSON grammar ──
    response_format = body.get("response_format")
    grammar = None
    if isinstance(response_format, dict) and response_format.get("type") == "json_object":
        grammar = "root ::= object\nobject ::= \"{\" pair (\",\" pair)* \"}\"\npair ::= string \":\" value\nstring ::= \"\\\"\" [^\"]* \"\\\"\"\nvalue ::= string | number | object | array | \"true\" | \"false\" | \"null\"\nnumber ::= [0-9]+ (\".\" [0-9]+)?\narray ::= \"[\" value (\",\" value)* \"]\"\n%whitespace ::= /[ \\t\\n]+/"
        logger.info("JSON mode attivato via response_format=json_object")

    # ── logprobs ──
    logprobs_enabled = body.get("logprobs", False)
    top_logprobs = body.get("top_logprobs", 0) if logprobs_enabled else 0

    # ── tool_choice ──
    tool_choice = body.get("tool_choice", "auto")
    tools = body.get("tools")
    if tools and tool_choice == "none":
        tools = None  # disable tool calling
    elif isinstance(tool_choice, dict):
        # specific function name
        func_name = tool_choice.get("function", {}).get("name", "")
        if func_name and tools:
            tools = [t for t in tools if t.get("function", {}).get("name") == func_name]

    # ── n (number of completions) ──
    n_completions = body.get("n", 1) or 1

    ollama_messages = [{"role": m["role"], "content": m["content"]} for m in raw_messages]

    # User from API key middleware (request.state.user) takes precedence
    user_from_middleware = getattr(request.state, 'user', None)
    current_user_id = user_from_middleware["id"] if user_from_middleware else body.get("user_id") or "alfio_dev"
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
                "model": MODEL_ID,
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
                        "model": MODEL_ID,
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": status_text}, "finish_reason": "stop"}],
                        "usage": {}
                    }
                else:
                    return {
                        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                        "object": "chat.completion",
                        "created": int(datetime.now(UTC).timestamp()),
                        "model": MODEL_ID,
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": "⚠️ Token di conferma non valido o scaduto."}, "finish_reason": "stop"}],
                        "usage": {}
                    }

    enriched = await build_omniscient_prompt(
        ollama_messages, user_id=current_user_id,
        conversation_id=str(conversation_id), concise=concise
    )
    tools = body.get("tools")
    if not is_stream:
        response = await engine.generate_chat_with_router(
            enriched, tools=tools, options=options, stream=False,
            grammar=grammar, preferred_provider=body.get("provider"),
        )
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
            response = await engine.generate_chat_with_router(
                enriched, tools=tools, options=options, stream=False,
                grammar=grammar, preferred_provider=body.get("provider"),
            )
            if "error" in response:
                return JSONResponse(status_code=500, content={"error": response["error"]})
            choice = response["choices"][0]["message"]

        content = choice.get("content", "")
        try:
            cleaned = await asyncio.wait_for(
                process_response_tags(content, user_id=current_user_id),
                timeout=15.0
            )
        except asyncio.TimeoutError:
            logger.warning("⏱️ process_response_tags timed out (15s) — returning raw text")
            cleaned = content
        except Exception as e:
            logger.warning(f"⚠️ process_response_tags error: {e}")
            cleaned = content
        if not cleaned and content:
            cleaned = content

        # Build response choices (n >= 1)
        choices = []
        for idx in range(n_completions):
            msg_data = {
                "role": choice.get("role", "assistant"),
                "content": cleaned if idx == 0 else "",
            }
            c = {
                "index": idx,
                "message": msg_data,
                "finish_reason": "stop",
            }
            if logprobs_enabled and idx == 0:
                c["logprobs"] = {
                    "content": response.get("choices", [{}])[0].get("logprobs"),
                } if response.get("choices") else None
            choices.append(c)

        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(datetime.now(UTC).timestamp()),
            "model": MODEL_ID,
            "choices": choices,
            "usage": response.get("usage", {}),
        }
    else:
        async def openai_stream_gen():
            gen = await engine.generate_chat_with_router(
                enriched, tools=tools, options=options, stream=True,
                grammar=grammar, preferred_provider=body.get("provider"),
            )
            if isinstance(gen, dict) and "error" in gen:
                yield f"data: {json.dumps({'error': gen['error']})}\n\n"
                return

            response_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
            response_created = int(datetime.now(UTC).timestamp())

            safe_stream = TagSafeStream()
            full_chunks = []
            role_sent = False
            async for chunk in gen:
                if "choices" in chunk and len(chunk["choices"]) > 0:
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    finish_reason = chunk["choices"][0].get("finish_reason")

                    # Strip XML action tags (MEMORY, SCHEDULE, etc.) BEFORE streaming
                    # Usa TagSafeStream per gestire tag spalmati su piu' chunk
                    cleaned_content = safe_stream.process(content) if content else ""

                    # Quando arriva finish_reason, rilascia eventuale buffer safe
                    # che TagSafeStream ha trattenuto per sicurezza anti-frammentazione
                    if finish_reason:
                        final_flush = safe_stream.flush()
                        if final_flush:
                            if cleaned_content:
                                cleaned_content += final_flush
                            else:
                                cleaned_content = final_flush

                    if not role_sent:
                        role_sent = True
                        yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': response_created, 'model': MODEL_ID, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
                        if cleaned_content:
                            yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': response_created, 'model': MODEL_ID, 'choices': [{'index': 0, 'delta': {'content': cleaned_content}, 'finish_reason': None}]})}\n\n"
                    else:
                        delta_dict = {"content": cleaned_content} if cleaned_content else {}
                        yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': response_created, 'model': MODEL_ID, 'choices': [{'index': 0, 'delta': delta_dict, 'finish_reason': finish_reason}]})}\n\n"

                    if content:
                        full_chunks.append(content)
                    if finish_reason:
                        break

            full_text = "".join(full_chunks)

            # Invia SUBITO [DONE] per non bloccare il client
            yield "data: [DONE]\n\n"

            # Processa i tag in BACKGROUND per effetti collaterali (MEMORY, SCHEDULE, SSH, ecc.)
            if full_text:
                try:
                    bg_task = asyncio.create_task(
                        process_response_tags(full_text, user_id=current_user_id)
                    )
                    state.background_tasks.add(bg_task)
                    bg_task.add_done_callback(state.background_tasks.discard)
                except Exception as e:
                    logger.warning(f"⚠️ Background tag processing error: {e}")

        return StreamingResponse(openai_stream_gen(), media_type="text/event-stream")
