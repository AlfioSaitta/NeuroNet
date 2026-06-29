"""
Classificatore — Intent Classification Module.

Centralizza la classificazione degli intenti delle richieste utente.
Fornisce un'interfaccia unificata per:
- Classificazione progetto/codice (Gatekeeper)
- Rilevamento conferme (confirm:TOKEN)
- Rilevamento comandi interni
- Routing al provider LLM appropriato
"""

from __future__ import annotations

import re
from typing import Optional

from config import logger

# ──────────────────────────────────────────────
# Costanti di classificazione
# ──────────────────────────────────────────────

CONFIRM_PATTERN = re.compile(r'^confirm[:\s]+([a-f0-9]{12})$', re.IGNORECASE)
REJECT_PATTERN = re.compile(r'^(reject|deny|refuse|no)[:\s]+([a-f0-9]{12})$', re.IGNORECASE)

PROJECT_KEYWORDS = {
    'codice', 'progetto', 'file', 'script', 'funzione', 'classe', 'metodo',
    'bug', 'errore', 'riga', 'cartella', 'struttura', 'repo', 'repository',
    'implementa', 'refactor', 'test', 'compila', 'variabile', 'log', 'modifica',
    'aggiungi', 'rimuovi', 'codebase',
    'configurazione', 'gestione', 'sicurezza', 'autenticazione', 'connessione',
    'websocket', 'database', 'api', 'endpoint', 'middleware', 'protocollo',
    'server', 'client', 'richiesta', 'risposta', 'proxy', 'rete', 'network',
    'pool', 'worker', 'buffer', 'cache', 'thread', 'processo', 'memoria',
    'algoritmo', 'compressione', 'crittografia', 'token', 'sessione',
    'debug', 'deploy', 'build', 'config', 'runtime', 'dependency', 'package',
    'versione', 'release', 'commit', 'branch', 'migrazione', 'backup'
}

GREETING_WORDS = {
    'ciao', 'hello', 'hi', 'hey', 'buongiorno', 'buonasera', 'salve',
    'grazie', 'thanks', 'ok', 'okay', 'si', 'no', 'come', 'stai',
    'chi', 'che', 'cosa', 'quale', 'quanto', 'dove', 'quando'
}

# ──────────────────────────────────────────────
# Enumerazione intenti
# ──────────────────────────────────────────────

class Intent:
    """Costanti per gli intenti supportati."""
    # Conferma token
    CONFIRM = "confirm"
    REJECT = "reject"
    # Routing
    PROJECT_QUERY = "project_query"    # Richiede RAG sul codice
    GENERAL_CHAT = "general_chat"      # Conversazione generica, no RAG
    WEB_QUERY = "web_query"            # Richiesta web esplicita (/web)
    # Interno
    INTERNAL = "internal"              # Query interna del sistema (Mem0, Worker)
    UNKNOWN = "unknown"                # Non classificabile


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


def is_project_query(text: str) -> bool:
    """
    Classifica rapida: la richiesta riguarda il progetto/codice?

    Usa keyword match veloce (nessuna chiamata LLM).
    """
    if len(text.strip()) < 5 or text.startswith("/web "):
        return False

    msg_lower = text.lower()
    words = set(re.findall(r'\b\w+\b', msg_lower))

    # Keyword match
    if words.intersection(PROJECT_KEYWORDS):
        return True

    # Pattern path (src/, app/, lib/, estensioni)
    if re.search(r'(\.[a-z]{1,4}\b|\b(src|app|lib|bin)/)', msg_lower):
        return True

    return False


def is_greeting(text: str) -> bool:
    """Verifica se il messaggio è un saluto / conversazione generica."""
    msg_lower = text.lower()
    words = set(re.findall(r'\b\w+\b', msg_lower))
    return bool(words.intersection(GREETING_WORDS))


def is_web_query(text: str) -> bool:
    """Verifica se la richiesta inizia con /web (ricerca web esplicita)."""
    return text.strip().startswith("/web ")


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


def classify(text: str) -> str:
    """
    Classifica l'intento completo di un messaggio utente.

    Returns:
        Uno dei valori Intent.*
    """
    if not text or not text.strip():
        return Intent.UNKNOWN

    # 1. Priorità massima: conferme
    result = classify_confirmation(text)
    if result:
        token, approved = result
        return Intent.CONFIRM if approved else Intent.REJECT

    # 2. Query interna del sistema
    if is_internal_query(text):
        return Intent.INTERNAL

    # 3. Web query esplicita
    if is_web_query(text):
        return Intent.WEB_QUERY

    # 4. Progetto/codice
    if is_project_query(text):
        return Intent.PROJECT_QUERY

    # 5. Default: conversazione generale
    return Intent.GENERAL_CHAT


def needs_rag(intent: str) -> bool:
    """Restituisce True se l'intento richiede l'arricchimento RAG."""
    return intent in (Intent.PROJECT_QUERY, Intent.WEB_QUERY)


def needs_confirmation(text: str) -> bool:
    """
    Verifica rapida se il testo contiene una richiesta di conferma.
    Utility per endpoint API.
    """
    return classify_confirmation(text) is not None
