"""Chat completions endpoint."""
import asyncio
import json
import uuid
from datetime import datetime, UTC

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse

from core.config import logger, MODEL_ID, MODEL_PROFILE
from core.llm_engine import engine
from core.chat_utils import handle_confirmation_token, build_llm_options, spawn_background
from agent.prompt import build_omniscient_prompt
from memory.engine import process_response_tags
from agent.tags import strip_action_tags, TagSafeStream
from agent.tools import execute_tool_call
from agent.confirmation import ConfirmationManager
from agent.classifier import is_internal_query
from .models import ChatCompletionRequestOpenAI
import core.state as state

# ── T1+T2 Helper: Ricostruisce tool_calls da chunk streaming ──────────
def _reconstruct_tool_calls(stream_chunks):
    """Reconstruct complete tool_calls list from OpenAI streaming delta chunks.
    Raggruppa per index e concatena function.arguments frammentati."""
    by_index = {}
    for chunk in stream_chunks:
        choices = chunk.get("choices", [])
        if not choices:
            continue
        delta = choices[0].get("delta", {})
        tc_list = delta.get("tool_calls", [])
        if not tc_list:
            tc_list = choices[0].get("tool_calls", [])
        for tc in tc_list:
            idx = tc.get("index", 0)
            if idx not in by_index:
                by_index[idx] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
            func = tc.get("function", {})
            if func.get("name"):
                by_index[idx]["function"]["name"] = func["name"]
            if func.get("arguments"):
                by_index[idx]["function"]["arguments"] += func["arguments"]
            if tc.get("id"):
                by_index[idx]["id"] = tc["id"]
    result = []
    for idx in sorted(by_index.keys()):
        tc = by_index[idx]
        if not tc["id"]:
            tc["id"] = f"call_{uuid.uuid4().hex[:12]}"
        result.append({"id": tc["id"], "type": tc["type"], "function": dict(tc["function"])})
    return result


router = APIRouter()


@router.post("/v1/chat/completions")
async def openai_chat_completions(payload: ChatCompletionRequestOpenAI, request: Request):
    state.total_requests += 1

    body = payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()
    is_stream = body.get("stream", False)
    raw_messages = body.get("messages", [])

    options = build_llm_options(body)

    # ── response_format: json_object → pass to create_chat_completion natively ──
    response_format = body.get("response_format")
    grammar = None
    if isinstance(response_format, dict) and response_format.get("type") in ("json_object", "json_schema"):
        options["response_format"] = response_format
        logger.info(f"JSON mode attivato via response_format={response_format.get('type')}")

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
    confirm_resp = await handle_confirmation_token(body)
    if confirm_resp is not None:
        return confirm_resp

    # ── Detect internal Mem0 extraction queries (## Summary prefix) ──
    # These come from Mem0's own LLM calls during memory.add() — they need raw
    # generation (no RAG/memory pipeline) to avoid infinite recursive loops.
    _is_internal = False
    if ollama_messages and isinstance(ollama_messages[-1], dict) and ollama_messages[-1].get("role") == "user":
        last_text = str(ollama_messages[-1].get("content", ""))
        if is_internal_query(last_text):
            _is_internal = True

    if _is_internal:
        # Bypass build_omniscient_prompt (no RAG, no memory context — circular!)
        enriched = ollama_messages
        gatekeeper_result = None
    else:
        enriched, gatekeeper_result = await build_omniscient_prompt(
            ollama_messages, user_id=current_user_id,
            conversation_id=str(conversation_id), concise=concise
        )

    # ── Apply reasoning configuration (thinking suppression) ──
    # Must match what main.py does to prevent models from outputting
    # chain-of-thought reasoning as their entire response.
    if gatekeeper_result:
        from core.reasoning import configura_richiesta_agente
        # Find original (pre-enrichment) last user message
        _orig_msg = ""
        for m in reversed(raw_messages):
            if isinstance(m, dict) and m.get("role") == "user":
                _orig_msg = m.get("content", "")
                break
        if _orig_msg:
            _content_prompt, _chat_kwargs, _settings = configura_richiesta_agente(
                MODEL_PROFILE, gatekeeper_result, _orig_msg,
            )
            # Apply chat_template_kwargs (enable_thinking, etc.)
            options.setdefault("chat_template_kwargs", {}).update(_chat_kwargs)
            # Apply logit_bias to block thinking tokens
            if _settings.get("logit_bias"):
                options.setdefault("logit_bias", {}).update(_settings["logit_bias"])
            # Temperature/top_p overrides only if not explicitly set by client
            for _key in ("temperature", "top_p", "repeat_penalty"):
                if _key not in options and _key in _settings:
                    options[_key] = _settings[_key]
            # Inject /no_think prefix (prepend to enriched content, don't replace)
            if _content_prompt and _content_prompt != _orig_msg:
                # Extract the prefix only (e.g. "/no_think ") from content_prompt
                _prefix = _content_prompt
                if _orig_msg and _content_prompt.endswith(_orig_msg):
                    _prefix = _content_prompt[:-len(_orig_msg)]
                for m in reversed(enriched):
                    if m.get("role") == "user":
                        m["content"] = _prefix + m["content"]
                        break

    tools = body.get("tools")
    # Propaga tool_choice alle options per forzatura Qwen XML tool call
    if tool_choice is not None:
        options["tool_choice"] = tool_choice
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

        # ── Tool calling loop (non-stream) — T1: parallel execution ──
        # Since we don't pass tools to llama-cpp-python (Qwen XML format conflict),
        # the model emits tool calls as XML in content. Parse them here.
        tool_calls = choice.get("tool_calls", [])
        if not tool_calls:
            _raw_c = choice.get("content", "")
            if _raw_c:
                from core.llm_engine import parse_qwen_tool_calls
                import re as _re
                tool_calls = parse_qwen_tool_calls(_raw_c)
                if tool_calls:
                    choice["content"] = _re.sub(
                        r'<tool_call[^>]*>.*?</tool_call\s*>\s*', '',
                        _raw_c, flags=_re.DOTALL
                    ).strip()
        if tool_calls:
            if confirmation_mgr is None:
                confirmation_mgr = ConfirmationManager.from_request(request_id=conversation_id)
            enriched.append(dict(choice))
            # T1: Esecuzione tool IN PARALLELO (asyncio.gather) invece di for-sequenziale
            async def _exec_one_tool(tc):
                _res = await execute_tool_call(tc, confirmation_mgr=confirmation_mgr)
                return {"role": "tool", "content": _res, "name": tc.get("function", {}).get("name", "unknown")}
            _tool_msgs = await asyncio.gather(*[_exec_one_tool(tc) for tc in tool_calls])
            enriched.extend(_tool_msgs)
            # Rimuovi tool_choice forzato per non interferire con risposta finale
            options.pop("tool_choice", None)
            response = await engine.generate_chat_with_router(
                enriched, tools=tools, options=options, stream=False,
                grammar=grammar, preferred_provider=body.get("provider"),
            )
            if "error" in response:
                return JSONResponse(status_code=500, content={"error": response["error"]})
            choice = response["choices"][0]["message"]

        content = choice.get("content", "")

        # Skip process_response_tags for internal Mem0 queries to avoid
        # recursive memory storage → more Mem0 extractions → infinite loop
        if _is_internal:
            cleaned = content
        else:
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
            nonlocal confirmation_mgr
            gen = await engine.generate_chat_with_router(
                enriched, tools=tools, options=options, stream=True,
                grammar=grammar, preferred_provider=body.get("provider"),
            )
            if isinstance(gen, dict) and "error" in gen:
                yield f"data: {json.dumps({'error': gen['error']})}\n\n"
                return

            response_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
            response_created = int(datetime.now(UTC).timestamp())

            # ── T1+T2: Streaming loop con tool-calling support ──
            safe_stream = TagSafeStream(model_family=MODEL_PROFILE.family)
            full_chunks = []
            role_sent = False
            tool_calls_stream_acc = []
            tool_calls_detected = False

            async for chunk in gen:
                if "choices" in chunk and len(chunk["choices"]) > 0:
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    finish_reason = chunk["choices"][0].get("finish_reason")

                    # T1: Raccogli tool_calls dai chunk streaming
                    tc_delta = delta.get("tool_calls")
                    if tc_delta:
                        tool_calls_stream_acc.append(chunk)
                        tool_calls_detected = True

                    # Strip XML action tags (MEMORY, SCHEDULE, etc.) BEFORE streaming
                    cleaned_content = safe_stream.process(content) if content else ""

                    # Se finish_reason=tool_calls, interrompiamo il primo stream
                    if finish_reason == "tool_calls":
                        tool_calls_detected = True
                        if not tc_delta:
                            tool_calls_stream_acc.append(chunk)
                        # Invia eventuale flush del buffer safe stream
                        _flush = safe_stream.flush()
                        if _flush:
                            cleaned_content = (cleaned_content + _flush) if cleaned_content else _flush
                        break

                    # Gestione flush a finish_reason (non-tool_calls)
                    if finish_reason:
                        _flush = safe_stream.flush()
                        if _flush:
                            cleaned_content = (cleaned_content + _flush) if cleaned_content else _flush

                    # T2: Stream SUBITO il contenuto al client
                    # IMPORTANT: NEVER yield finish_reason from the first stream.
                    # The model returns finish_reason="stop" even when there's a
                    # <tool_call> embedded in content. If we yield "stop" here,
                    # the client closes the connection before we process the tool
                    # call and generate the second stream.
                    if not role_sent:
                        role_sent = True
                        yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': response_created, 'model': MODEL_ID, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
                        if cleaned_content:
                            yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': response_created, 'model': MODEL_ID, 'choices': [{'index': 0, 'delta': {'content': cleaned_content}, 'finish_reason': None}]})}\n\n"
                    else:
                        delta_dict = {"content": cleaned_content} if cleaned_content else {}
                        yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': response_created, 'model': MODEL_ID, 'choices': [{'index': 0, 'delta': delta_dict, 'finish_reason': None}]})}\n\n"

                    if content:
                        full_chunks.append(content)
                    if finish_reason:
                        break

            # CRITICO: Chiudere l'async generator per rilasciare PriorityLock,
            # altrimenti la seconda chiamata LLM (gen2) si blocca in deadlock.
            await gen.aclose()

            # ── T1+T2: Tool-calling handling ──
            # Also check for XML tool calls in content (model outputs XML, not delta.tool_calls)
            _otc = None
            if not tool_calls_detected:
                _full_text_tc = "".join(full_chunks)
                if _full_text_tc:
                    from core.llm_engine import parse_qwen_tool_calls
                    _otc = parse_qwen_tool_calls(_full_text_tc)
                    if _otc:
                        tool_calls_detected = True
            if tool_calls_detected:
                if _otc:
                    tool_calls = _otc
                    first_text = "".join(full_chunks)
                    import re as _re
                    first_text = _re.sub(
                        r'<tool_call[^>]*>.*?</tool_call\s*>\s*', '',
                        first_text, flags=_re.DOTALL
                    ).strip()
                else:
                    tool_calls = _reconstruct_tool_calls(tool_calls_stream_acc)
                    first_text = "".join(full_chunks)

                # Costruisce assistant message con contenuto catturato + tool_calls
                assistant_msg = {"role": "assistant", "content": first_text}
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                enriched.append(assistant_msg)

                # T1: Esecuzione tool IN PARALLELO
                if confirmation_mgr is None:
                    confirmation_mgr = ConfirmationManager.from_request(request_id=conversation_id)

                async def _exec_one_tool(tc):
                    _res = await execute_tool_call(tc, confirmation_mgr=confirmation_mgr)
                    return {"role": "tool", "content": _res, "name": tc.get("function", {}).get("name", "unknown")}

                tool_msgs = await asyncio.gather(*[_exec_one_tool(tc) for tc in tool_calls])
                enriched.extend(tool_msgs)

                # Rimuovi tool_choice forzato per non interferire con risposta finale
                options.pop("tool_choice", None)

                # Seconda chiamata LLM in streaming (continuazione dopo tool)
                gen2 = await engine.generate_chat_with_router(
                    enriched, tools=tools, options=options, stream=True,
                    grammar=grammar, preferred_provider=body.get("provider"),
                )

                if not (isinstance(gen2, dict) and "error" in gen2):
                    # Second stream: text answer after tool execution.
                    # No TagSafeStream — Qwen wraps its response in <think>...</think>
                    # after tool calls, and TagSafeStream would consume it all.
                    async for chunk in gen2:
                        if "choices" in chunk and len(chunk["choices"]) > 0:
                            delta2 = chunk["choices"][0].get("delta", {})
                            content2 = delta2.get("content", "")
                            fr2 = chunk["choices"][0].get("finish_reason")

                            cleaned2 = content2 if content2 else ""

                            if cleaned2:
                                if not role_sent:
                                    role_sent = True
                                    yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': response_created, 'model': MODEL_ID, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': cleaned2}, 'finish_reason': None}]})}\n\n"
                                else:
                                    yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': response_created, 'model': MODEL_ID, 'choices': [{'index': 0, 'delta': {'content': cleaned2}, 'finish_reason': fr2}]})}\n\n"

                            if content2:
                                full_chunks.append(content2)
                            if fr2:
                                break

            full_text = "".join(full_chunks)

            # Invia SUBITO [DONE] per non bloccare il client
            yield "data: [DONE]\n\n"

            # Processa i tag in BACKGROUND per effetti collaterali (MEMORY, SCHEDULE, SSH, ecc.)
            # Skip for internal Mem0 queries to avoid recursive memory loops
            if full_text and not _is_internal:
                try:
                    spawn_background(process_response_tags(full_text, user_id=current_user_id))
                except Exception as e:
                    logger.warning(f"⚠️ Background tag processing error: {e}")

        return StreamingResponse(openai_stream_gen(), media_type="text/event-stream")
