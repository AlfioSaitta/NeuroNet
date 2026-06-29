"""Content moderation endpoint."""
import json
import re
import uuid
from datetime import datetime, UTC

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config import logger
from llm_engine import engine
from .models import ModerationRequestOpenAI
import state

router = APIRouter()


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
