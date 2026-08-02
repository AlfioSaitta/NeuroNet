"""
Classificatore — Classificazione di conferme e query interne.

Fase 1 del piano intent_understanding_llm.md: il modulo conserva SOLO le
funzioni vive dopo la centralizzazione in agent/intent_router.py.

Rimossi (centralizzati in agent.intent_router):
- PROJECT_KEYWORDS, GREETING_WORDS, Intent enum
- is_project_query, is_greeting, is_web_query, classify, needs_rag, needs_confirmation

Conservati:
- CONFIRM_PATTERN / REJECT_PATTERN + classify_confirmation (chat_utils.py)
- is_internal_query (main.py, openai_api/chat.py — Mem0 loop guard)
"""

from __future__ import annotations

import re
from typing import Optional

from core.config import logger

# ──────────────────────────────────────────────
# Costanti di classificazione
# ──────────────────────────────────────────────

CONFIRM_PATTERN = re.compile(r'^confirm[:\s]+([a-f0-9]{12})$', re.IGNORECASE)
REJECT_PATTERN = re.compile(r'^(reject|deny|refuse|no)[:\s]+([a-f0-9]{12})$', re.IGNORECASE)

# ──────────────────────────────────────────────
# Funzioni di classificazione
# ──────────────────────────────────────────────

def classify_confirmation(text: str) -> Optional[tuple[str, bool]]:
    """
    Verifica se un messaggio utente contiene una richiesta di conferma.

    Args:
        text: Il messaggio utente da analizzare.

    Returns:
        (token, approved) se riconosciuto, None altrimenti.
    """
    clean = text.strip().lower()

    m = CONFIRM_PATTERN.match(clean)
    if m:
        return m.group(1), True

    m = REJECT_PATTERN.match(clean)
    if m:
        return m.group(2), False

    return None


def is_internal_query(text: str) -> bool:
    """
    Verifica se la richiesta è una query interna del sistema.

    Pattern riconosciuti:
    - ## Summary (Mem0 reflection)
    - Extract entities
    - ADD_MEMORY / UPDATE_MEMORY
    - deduce the facts
    """
    txt = text.strip()
    return any([
        txt.startswith("## Summary"),
        "Extract entities" in txt,
        txt.startswith("ADD_MEMORY"),
        txt.startswith("UPDATE_MEMORY"),
        "deduce the facts" in txt,
    ])
