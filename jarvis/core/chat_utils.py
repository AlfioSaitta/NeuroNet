"""
Utility condivise per endpoint chat — conferma token, background task, opzioni LLM.
Elimina la duplicazione tra main.py, openai_api/chat.py e admin/dashboard.py.
"""

import asyncio
import logging
import time
from typing import Any, Optional, Callable, Coroutine

from fastapi.responses import JSONResponse

from core.config import MODEL_ID
import core.state as state

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# BACKGROUND TASK HELPER
# ════════════════════════════════════════════════════════════════

def spawn_background(coro: Coroutine) -> asyncio.Task:
    """Avvia un task in background con registrazione in state.background_tasks.
    
    Pattern standard usato in 10+ punti del codebase.
    Fire-and-forget: il task viene rimosso dal set al completamento.
    """
    task = asyncio.create_task(coro)
    state.background_tasks.add(task)
    task.add_done_callback(state.background_tasks.discard)
    return task


# ════════════════════════════════════════════════════════════════
# CONFERMA TOKEN
# ════════════════════════════════════════════════════════════════

async def handle_confirmation_token(
    body: dict,
    conversation_id: str = "",
    user_id: str = "",
) -> Optional[JSONResponse]:
    """Gestisce il confirmation token per gli endpoint chat.
    
    Cerca il token in body['confirmation_token'] oppure lo classifica
    dall'ultimo messaggio utente tramite classify_confirmation().
    
    Returns:
        JSONResponse se il token è presente e processato (approvato/rifiutato/scaduto).
        None se nessun token trovato → il chiamante procede con la chat normale.
    """
    from agent.confirmation import ApiTokenProvider
    from agent.classifier import classify_confirmation

    confirmation_token = body.get("confirmation_token")
    if confirmation_token:
        resolved = ApiTokenProvider.resolve(confirmation_token, approved=True)
        if resolved:
            return JSONResponse(status_code=200, content=_confirm_response(
                "✅ Conferma ricevuta. Operazione autorizzata.", conversation_id
            ))
        # Token non valido → lascia cadere, non blocca
        return None

    raw_messages = body.get("messages", [])
    if raw_messages:
        last_msg = raw_messages[-1] if isinstance(raw_messages[-1], dict) else {}
        if last_msg.get("role") == "user":
            msg_text = str(last_msg.get("content", ""))
            result = classify_confirmation(msg_text)
            if result:
                token, approved = result
                api_resolved = ApiTokenProvider.resolve(token, approved=approved)
                if api_resolved:
                    status_text = "✅ Conferma ricevuta. Operazione autorizzata." if approved else "❌ Operazione rifiutata."
                    return JSONResponse(status_code=200, content=_confirm_response(status_text, conversation_id))
                else:
                    return JSONResponse(status_code=200, content=_confirm_response(
                        "⚠️ Token di conferma non valido o scaduto.", conversation_id
                    ))

    return None


def _confirm_response(message: str, conversation_id: str = "") -> dict:
    """Costruisce la response standard per conferma/rifiuto token."""
    return {
        "id": "chatcmpl-confirm",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_ID,
        "conversation_id": str(conversation_id) if conversation_id else "",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": message},
            "finish_reason": "stop",
        }],
    }


# ════════════════════════════════════════════════════════════════
# OPZIONI LLM DA BODY RICHIESTA
# ════════════════════════════════════════════════════════════════

def build_llm_options(body: dict) -> dict:
    """Estrae le opzioni di generazione LLM dal body della richiesta.
    
    Supporta sia il formato annidato 'options' (Jarvis nativo) che
    i campi diretti (OpenAI API).
    """
    options = body.get("options") or {}
    
    # Campi diretti (formato OpenAI)
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

    # Fase 6.7: reasoning_effort (OpenAI agentic clients) — applicato dal
    # chiamante dopo apply_reasoning_config (high|medium → thinking ON,
    # low → OFF). Qui si estrae il valore grezzo dal body.
    if body.get("reasoning_effort") is not None:
        options["reasoning_effort"] = str(body["reasoning_effort"]).strip().lower()

    return options


# ════════════════════════════════════════════════════════════════
# UTENTE CORRENTE
# ════════════════════════════════════════════════════════════════

def resolve_user_id(body: dict, jwt_user: Optional[dict] = None, default: str = "alfio_dev") -> str:
    """Risolve l'user_id: JWT > body.user_id > options.user_id > default."""
    jwt_id = jwt_user["id"] if jwt_user else None
    options = body.get("options") or {}
    return (
        jwt_id
        or body.get("user_id")
        or (options.get("user_id") if isinstance(options, dict) else None)
        or default
    )
