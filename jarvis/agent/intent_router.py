"""
Intent Router — Classificazione intenti centralizzata (LLM + regex).

COMPONENTE 1 del piano docs/plans/intent_understanding_llm.md.
Il nome "gatekeeper" sparisce dal sistema: la classificazione intenti vive
qui, la compressione contesto vive in agent/context_compressor.py (Fase 5).

Architettura ibrida:
- _fast_path(): casi deterministici tier-0 (greeting puro, /web, confirm token,
  query interne, JSON dump, nome progetto, META, SIMPLE, PROJECT_KEYWORDS, path)
  — 0 LLM, latenza 26ms preservata.
- _llm_classify(): main model + grammatica GBNF (solo intent+project+confidence).
  Completato in Fase 2 del piano (isolato in sviluppo fino al superamento del benchmark).
- _extract_slots(): regex post-hoc dedicate per slot (city, duration_min,
  file_path, topic, content, priority, deadline...). La GBNF NON supporta
  stringhe libere (regola `word` senza spazi): gli slot si estraggono qui.
- classify(): API unica — tier-0 -> cache LRU 60s -> LLM -> fallback general.

Contratto API (release finale, nessuna retro-compatibilità):
  IntentResult è il contratto nativo (ex gatekeeper, eliminato in Fase 3).
  I consumer leggono SEMPRE IntentResult.intent (22 valori estesi).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Costanti consolidate (ex prompt.py / classifier.py)
# ──────────────────────────────────────────────

META_PHRASES = re.compile(
    # ITALIANO: richieste di progetto/elenco
    r'(quali\s+(sono\s+)?(i\s+|i\s+tuoi\s+|i\s+nostri\s+)?progetti'
    r'|dammi\s+(la\s+)?lista(\s+dei)?(\s+\w+)?\s+progetti'
    r'|mostra\s+(la\s+)?lista(\s+dei)?(\s+\w+)?\s+progetti'
    r'|lista\s+(dei\s+)?(\w+\s+)?progetti'
    # Forme imperative/clitiche (FIX 2026-08-02): "listami i progetti",
    # "elenca i progetti", "mostrami i progetti" — senza spazio dopo il verbo.
    r'|listami\s+(i\s+|tutti\s+i\s+|tuoi\s+|nostri\s+)?progetti'
    r'|elencami\s+(i\s+|tutti\s+i\s+|tuoi\s+|nostri\s+)?progetti'
    r'|elenca\s+(i\s+|tutti\s+i\s+|tuoi\s+|nostri\s+)?progetti'
    r'|mostrami\s+(i\s+|tutti\s+i\s+|tuoi\s+|nostri\s+)?progetti'
    r'|fammi\s+vedere\s+(i\s+|tutti\s+i\s+|tuoi\s+|nostri\s+)?progetti'
    r'|dimmi\s+(quali\s+sono\s+)?(i\s+|tutti\s+i\s+|tuoi\s+|nostri\s+)?progetti'
    r'|che\s+progetti'
    r'|progetti\s+in\s+(memoria|rag)'
    r'|elenco\s+(dei\s+)?(\w+\s+)?progetti'
    r'|quanti\s+progetti'
    r'|progetti\s+(hai|conosci|hai\s+in|in\s+corso|ci\s+sono|sono\s+disponibili)'
    r'|a\s+quali\s+progetti'
    r'|quali\s+sono\s+(i\s+)?(tuoi\s+|nostri\s+|miei\s+|vostri\s+|suoi\s+)?progetti'
    # INGLESE: project listing requests
    r'|which\s+(are\s+)?(the\s+)?(your\s+|our\s+|all\s+)?projects'
    r'|list\s+(of\s+)?(the\s+)?(your\s+|our\s+|all\s+)?projects'
    r'|give\s+me\s+(the\s+)?(list\s+of\s+)?(the\s+)?(your\s+|our\s+|all\s+)?projects'
    r'|show\s+me\s+(the\s+)?(list\s+of\s+)?(the\s+)?(your\s+|our\s+|all\s+)?projects'
    r'|projects\s+in\s+(memory|rag)'
    # CAPACITÀ / HELP
    r'|cosa\s+sai\s+fare'
    r'|what\s+can\s+you\s+do'
    r'|come\s+funzioni'
    r'|how\s+(do\s+)?you\s+work'
    r'|quali\s+(sono\s+)?le\s+tue\s+capacit)',
    re.IGNORECASE
)

# Query fattuali semplici che NON richiedono contesto progetto (bypass→general)
SIMPLE_QUERIES = re.compile(
    # ITALIANO: data, ora, tempo, posizione
    r'(che\s+ora\s+)?(è|sono)\??$'
    r'|(che\s+)?ore\s+sono\??$'
    r'|che\s+(giorno|data)\s+(è|siamo)\??$'
    r'|dammi\s+(la\s+)?(data|ora|data\s+e\s+ora)(\s+attuale)?\??$'
    r'|che\s+tempo\s+(fa|farà)\??$'
    r'|com\'è\s+il\s+tempo\??$'
    r'|dove\s+mi\s+trovo\??$'
    r'|dove\s+sono\??$'
    r'|posizione\s+(attuale|corrente)\??$'
    r'|racconta\s+una\s+barzelletta'
    r'|definizione\s+di\s+\w+'
    # INGLESE: date, time, weather, location
    r'|what\s+(time|date|day)\s+is\s+it\??$'
    r'|current\s+(date|time|date\s+and\s+time)\??$'
    r'|tell\s+me\s+(the\s+)?(date|time|date\s+and\s+time)\??$'
    r'|what.s\s+the\s+weather(\s+like)?\??$'
    r'|where\s+am\s+i\??$'
    r'|tell\s+me\s+a\s+joke'
    # CAMBIO LINGUA / LANGUAGE SWITCH
    r'|parla\s+(in\s+)?\w+(\s+con\s+me)?'
    r'|speak\s+\w+(\s+(with|to)\s+me)?'
    r'|(parli|puoi\s+parlare)\s+\w+'
    r'|(can\s+(you\s+)?speak)\s+\w+'
    r'|in\s+italiano\s*(per\s+favore|please)?'
    r'|change\s+(the\s+)?language\s+to\s+\w+',
    re.IGNORECASE
)

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

PURE_GREETINGS = frozenset({'ciao', 'hello', 'hi', 'hey', 'buongiorno', 'buonasera',
                            'buona sera', 'buon pomeriggio', 'salve', 'saluti', 'buondì',
                            'good morning', 'good evening', 'good afternoon'})

CONFIRM_PATTERN = re.compile(r'^confirm[:\s]+([a-f0-9]{12})$', re.IGNORECASE)
REJECT_PATTERN = re.compile(r'^(reject|deny|refuse|no)[:\s]+([a-f0-9]{12})$', re.IGNORECASE)

# Regex per path file (estensione). Pattern base: prompt.py:892 (include hpp — NON rimuovere).
_FILE_PATH_RE = re.compile(r'\b([\w\-./]+\.(?:py|js|ts|jsx|tsx|go|c|cpp|h|hpp|rs|sql|yaml|yml|md|json))\b')

# ──────────────────────────────────────────────
# Pattern intent-specifici (priorità sul fast-path generico PROJECT_KEYWORDS)
# ──────────────────────────────────────────────
# Fase 2: senza questi, PROJECT_KEYWORDS / nome-progetto rubano le query di
# memory/schedule/task/git/ssh/translate/fetch/config/maintenance/code/analyze
# (es. "ricorda che il deploy è giovedì" → "deploy" ∈ PROJECT_KEYWORDS → project
# MA l'intento atteso è memory). Se un pattern specifico matcha → il fast-path
# NON intercetta → la query va al classificatore LLM (Fase 2).
# ORDINE: prima i pattern più specifici/verbi, poi le keyword.

# schedule: verbi di reminder + ricorrenze + "tra X" + "alle HH:MM"
# NOTA: "ricordami" NUDO è memory (memorizza un fatto) — NON schedule.
# "ricordami tra 30 minuti" / "ricordami ogni mattina" matchano comunque
# via "tra \d" / "ogni ..." (branch alternativi sotto).
_INTENT_PATTERN_SCHEDULE = re.compile(
    r'\b(promemoria|timer|sveglia|notifica)\b'
    r'|\b(tra|fra)\s+\d+'
    r'|\b(alle?|at)\s+\d{1,2}([:.]\d{2})?'
    r'|\bogni\s+(giorno|mattina|sera|settimana|martedì|giovedì|lunedì|venerdì|sabato|domenica|mercoledì)\b',
    re.IGNORECASE
)
# memory: salvare/recuperare ricordi ("ricordami di/che", "che ricordi", "memorizza", "remember")
_INTENT_PATTERN_MEMORY = re.compile(
    r'\b(ricordami|ricordi\s+(di|su)|che\s+cosa\s+ricordi|che\s+ricordi|memorizza|'
    r'ricorda\s+che|memoria\s+(su|di)|(do\s+you\s+)?remember)\b',
    re.IGNORECASE
)
# task: task/todo add/done/list
_INTENT_PATTERN_TASK = re.compile(
    r'\b(task|todo)\b'
    r'|\b(aggiungi|crea|segna\s+come\s+fatto|completa|elenca)\b(?=.*\b(task|todo)\b)',
    re.IGNORECASE
)
# git: operazioni git esplicite
_INTENT_PATTERN_GIT = re.compile(
    r'\b(git\s+(status|log|diff|commit|push|pull|merge|branch|checkout)|che\s+branch|branch\s+attuale|'
    r'committa|fai\s+commit|crea\s+branch|storico\s+commit|ultimi\s+commit|storico\s+dei\s+commit)\b',
    re.IGNORECASE
)
# ssh: comandi su server remoti (read + write whitelisted)
_INTENT_PATTERN_SSH = re.compile(
    r'\b(deploy|uptime|df\s+-h|ps\s+aux|free\s+-h|riavvia|restart|rm\s+-rf)\b'
    r'|\b(su|sul)\s+[\w\-.]*(server|produzione|debian|vps)\b',
    re.IGNORECASE
)
# translate: traduzione esplicita
_INTENT_PATTERN_TRANSLATE = re.compile(
    r'\b(traduci|traduzione|translate|traduci\s+in|translate\s+(into|to))\b',
    re.IGNORECASE
)
# transcribe: audio/voce a testo
_INTENT_PATTERN_TRANSCRIBE = re.compile(
    r'\b(trascrivi|trascrizione|dettato|messaggio\s+vocale|transcribe)\b',
    re.IGNORECASE
)
# fetch: URL esplicito
_INTENT_PATTERN_FETCH = re.compile(
    r'(https?://[^\s]+)'
    r'|\b(che\s+c.?è\s+su\s+questa\s+pagina|apri\s+questa\s+pagina|fetch\s+the\s+content)\b',
    re.IGNORECASE
)
# config: leggere/impostare/resettare impostazioni Jarvis
_INTENT_PATTERN_CONFIG = re.compile(
    r'\b(imposta\s+|sett[a]?|set\s+)[\w_]+\s*(a|su|=)?'
    r'|\b(mostra|quali\s+sono|leggi|get)\s+(configurazion[ei]?|impostazion[ei]?|settings)\b'
    r'|\b(reset|ripristina)\s+[\w_]+',
    re.IGNORECASE
)
# maintenance: pulizia cache, reindex, cleanup
_INTENT_PATTERN_MAINTENANCE = re.compile(
    r'\b(pulisci\s+(la\s+)?cache|svuota\s+(la\s+)?cache|clear\s+(the\s+)?cache)\b'
    r'|\b(reindicizza|reindex|re-ingest|reingest)\b'
    r'|\b(pulizia\s+collezioni|collezioni\s+orfane|cleanup)\b',
    re.IGNORECASE
)
# analyze: explain/diagnose/performance (READ ONLY)
_INTENT_PATTERN_ANALYZE = re.compile(
    r'\b(analizza|analisi|performance|spiega\s+come\s+funziona|perch[ée]\s+non\s+funziona|debugga)\b',
    re.IGNORECASE
)
# plan: proposte/step (READ ONLY)
_INTENT_PATTERN_PLAN = re.compile(
    r'\b(piano|pianifica|progetta|strategia|come\s+implementer(?:ei|esti)|quali\s+step|quali\s+passi)\b',
    re.IGNORECASE
)
# code: refactor/implement/fix (MODIFICA)
_INTENT_PATTERN_CODE = re.compile(
    r'\b(rifattorizza|refactor|ripulisci|semplifica|implementa|correggi|risolvi|fix)\b'
    r'|\b(leggi\s+(?:il\s+)?file|scrivi\s+(?:il\s+)?file|modifica\s+(?:il\s+)?file)\b',
    re.IGNORECASE
)
# action: tool operations dirette (leggi/scrivi file, esegui comando)
_INTENT_PATTERN_ACTION = re.compile(
    r'\b(esegui\s+questo\s+comando|run\s+this\s+command|esegui\s+il\s+comando)\b',
    re.IGNORECASE
)

# Lista ORDINATA (priorità decrescente) per il fast-path. Le query che matchano
# NON vengono intercettate dal tier-0 → vanno al classificatore LLM (Fase 2).
_INTENT_LLM_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("schedule", _INTENT_PATTERN_SCHEDULE),
    ("memory", _INTENT_PATTERN_MEMORY),
    ("task", _INTENT_PATTERN_TASK),
    ("git", _INTENT_PATTERN_GIT),
    ("ssh", _INTENT_PATTERN_SSH),
    ("translate", _INTENT_PATTERN_TRANSLATE),
    ("transcribe", _INTENT_PATTERN_TRANSCRIBE),
    ("fetch", _INTENT_PATTERN_FETCH),
    ("config", _INTENT_PATTERN_CONFIG),
    ("maintenance", _INTENT_PATTERN_MAINTENANCE),
    ("analyze", _INTENT_PATTERN_ANALYZE),
    ("plan", _INTENT_PATTERN_PLAN),
    ("code", _INTENT_PATTERN_CODE),
    ("action", _INTENT_PATTERN_ACTION),
]


def _match_llm_intent_pattern(user_message: str) -> Optional[str]:
    """Ritorna l'intent specifico se la query matcha un pattern dedicato, else None.

    Usato in _fast_path() per NON intercettare (→ LLM) le query che PROJECT_KEYWORDS
    o il nome-progetto ruberebbero.
    """
    for intent, pattern in _INTENT_LLM_PATTERNS:
        if pattern.search(user_message):
            return intent
    return None

# ──────────────────────────────────────────────
# Tassonomia intent (22: 18 LLM + 4 tier-0 regex)
# ──────────────────────────────────────────────

LLM_INTENTS = frozenset({
    "project", "general", "web", "schedule", "meta", "action", "memory", "task",
    "analyze", "plan", "code", "git", "ssh", "transcribe", "fetch", "translate",
    "config", "maintenance",
})

TIER0_INTENTS = frozenset({"greeting", "confirm", "reject", "internal"})

ALL_INTENTS = LLM_INTENTS | TIER0_INTENTS


@dataclass
class IntentResult:
    """Risultato della classificazione intento (contratto nativo, 22 valori).

    Ex gatekeeper — ora contratto unico (Fase 3, nessuna proiezione).
    """
    intent: str
    slots: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    source: str = "llm"              # "regex" | "llm" | "fallback"
    project: Optional[str] = None


# ──────────────────────────────────────────────
# Helper greeting (Fase 4.1 — dedup short-circuit)
# ──────────────────────────────────────────────

GREETING_RESPONSE = "Ciao! 👋 Come posso aiutarti?"


def is_greeting_result(gk: Optional[IntentResult]) -> bool:
    """True se l'IntentResult è un saluto puro (short-circuit 26ms).

    Unico punto di verifica per i 4 siti di greeting short-circuit
    (main.py, server_v2.py, dashboard.py, prompt.py).
    """
    return gk is not None and gk.intent == "greeting"


# ──────────────────────────────────────────────
# Grammatica GBNF — SOLO intent + project + confidence (nessuno slot libero)
# ──────────────────────────────────────────────

GBNF_GRAMMAR_INTENT = r'''root ::= "{\"intent\": " intent ", \"project\": " projval ", \"confidence\": " number "}"
intent ::= "\"project\"" | "\"general\"" | "\"web\"" | "\"schedule\"" | "\"meta\"" | "\"action\"" | "\"memory\"" | "\"task\"" | "\"analyze\"" | "\"plan\"" | "\"code\"" | "\"git\"" | "\"ssh\"" | "\"transcribe\"" | "\"fetch\"" | "\"translate\"" | "\"config\"" | "\"maintenance\""
projval ::= string | "null"
string ::= "\"" word "\""
word ::= [a-zA-Z] ([a-zA-Z0-9_.-])*
number ::= [0-1] "." digit+ | "1" "." "0"+
digit ::= [0-9]
'''


# ──────────────────────────────────────────────
# System prompt per intent classification (36 few-shot: 2 esempi × 18 intent)
# ──────────────────────────────────────────────

INTENT_SYSTEM_PROMPT = """You are an intent classifier for Jarvis, an AI assistant.
Classify the user's request into EXACTLY ONE of these intents:

- project: questions about code, files, architecture, bugs of a project in the knowledge base
- general: casual chat, jokes, factual questions that do not need live data
- web: requests needing live/current data (weather, news, prices) or explicit web search
- schedule: reminders, timers, recurring jobs ("remind me in 30 minutes to...", "every morning...")
- meta: asks about Jarvis capabilities, available projects, help
- action: direct tool operations (read/write a file, run a command)
- memory: save or retrieve episodic memories ("remember that...", "what do you remember about...")
- task: add, mark-done or list todos/tasks
- analyze: explain, diagnose or performance-analysis of code — READ ONLY, never modifies
- plan: propose an implementation plan or step-by-step approach — READ ONLY, output is a plan
- code: refactor, implement or fix code — MODIFIES CODE, requires confirmation
- git: git operations (status, log, diff, commit, branch, push, merge)
- ssh: commands on remote servers
- transcribe: audio/voice to text
- fetch: get the content of a specific URL
- translate: translate text into another language
- config: get, set or reset Jarvis settings
- maintenance: cache clear, RAG reindex, orphan collection cleanup

Return ONLY a JSON object with keys:
- "intent": one of the values above
- "project": the project name if explicitly mentioned, otherwise null
- "confidence": a number between 0.0 and 1.0

Examples:
User: che tempo fa a Catania?
{"intent": "web", "project": null, "confidence": 0.97}

User: what is the price of Bitcoin right now?
{"intent": "web", "project": null, "confidence": 0.96}

User: qual è il problema nel caricamento dei modelli?
{"intent": "project", "project": null, "confidence": 0.9}

User: how does the watchdog work in this repo?
{"intent": "project", "project": null, "confidence": 0.88}

User: raccontami una barzelletta
{"intent": "general", "project": null, "confidence": 0.98}

User: explain the difference between TCP and UDP
{"intent": "general", "project": null, "confidence": 0.93}

User: ricordami tra 30 minuti di chiamare Marco
{"intent": "schedule", "project": null, "confidence": 0.95}

User: remind me every morning to drink water
{"intent": "schedule", "project": null, "confidence": 0.94}

User: quali progetti hai in memoria?
{"intent": "meta", "project": null, "confidence": 0.95}

User: what can you do?
{"intent": "meta", "project": null, "confidence": 0.93}

User: leggi il file jarvis/main.py
{"intent": "action", "project": null, "confidence": 0.9}

User: esegui questo comando: ls -la
{"intent": "action", "project": null, "confidence": 0.91}

User: ricorda che il deploy è giovedì
{"intent": "memory", "project": null, "confidence": 0.96}

User: what do you remember about the NeuroNet project?
{"intent": "memory", "project": "NeuroNet", "confidence": 0.9}

User: aggiungi un task: scrivere la doc, priorità alta
{"intent": "task", "project": null, "confidence": 0.95}

User: mark the todo about the gateway as done
{"intent": "task", "project": null, "confidence": 0.92}

User: perché non funziona il caricamento dei modelli?
{"intent": "analyze", "project": null, "confidence": 0.94}

User: analyze the performance of rag/engine.py
{"intent": "analyze", "project": null, "confidence": 0.93}

User: come implementeresti la gestione dei rate limit?
{"intent": "plan", "project": null, "confidence": 0.92}

User: make a plan for the gateway refactor
{"intent": "plan", "project": null, "confidence": 0.91}

User: rifattorizza il modulo auth
{"intent": "code", "project": null, "confidence": 0.95}

User: fix the bug in the model loading
{"intent": "code", "project": null, "confidence": 0.93}

User: che branch siamo?
{"intent": "git", "project": null, "confidence": 0.96}

User: committa le modifiche con messaggio fix
{"intent": "git", "project": null, "confidence": 0.94}

User: fai deploy sul server di produzione
{"intent": "ssh", "project": null, "confidence": 0.9}

User: show the uptime of the server
{"intent": "ssh", "project": null, "confidence": 0.92}

User: trascrivi il messaggio vocale
{"intent": "transcribe", "project": null, "confidence": 0.95}

User: transcribe this audio file
{"intent": "transcribe", "project": null, "confidence": 0.94}

User: che c'è su questa pagina? https://docs.example.com/guide
{"intent": "fetch", "project": null, "confidence": 0.96}

User: fetch the content of https://example.com
{"intent": "fetch", "project": null, "confidence": 0.95}

User: traduci in inglese: buongiorno mondo
{"intent": "translate", "project": null, "confidence": 0.97}

User: translate this text into French
{"intent": "translate", "project": null, "confidence": 0.96}

User: imposta LLAMA_MODEL_PATH su ./models/x.gguf
{"intent": "config", "project": null, "confidence": 0.94}

User: show the current settings
{"intent": "config", "project": null, "confidence": 0.9}

User: pulisci la cache semantica
{"intent": "maintenance", "project": null, "confidence": 0.95}

User: reindex the NeuroNet project
{"intent": "maintenance", "project": "NeuroNet", "confidence": 0.93}"""


# ──────────────────────────────────────────────
# Slot extractors (regex post-hoc — base: pattern esistenti in web_search.py)
# ──────────────────────────────────────────────
# CONVENZIONE GRUPPI E ORDINE (CRITICA — il benchmark §10.3 è l'assert):
#   1. Ogni tuple (regex, slot, cast) DEVE avere il VALORE nel gruppo 1 della regex.
#      - cast `str` / `to_lang` / `to_minutes` leggono `m.group(1)`.
#      - Verbi/preposizioni vanno in gruppi NON catturanti (?:...).
#      - Regex con valore costante (3° elemento = stringa/bool) ignorano i gruppi.
#      - Regex con cast `str` MA senza gruppi catturanti vanno parenthesizzate.
#   2. SEMANTICA "RIGHTMOST MATCH VINCE" per slot: se più regex di uno STESSO slot
#      matchano, vince il match più a DESTRA nel testo (confronto su m.start()).

_LANG_MAP = {
    "inglese": "en", "english": "en", "en": "en",
    "italiano": "it", "italian": "it", "it": "it",
    "francese": "fr", "french": "fr", "fr": "fr",
    "tedesco": "de", "german": "de", "de": "de",
    "spagnolo": "es", "spanish": "es", "es": "es",
    "portoghese": "pt", "portuguese": "pt", "pt": "pt",
    "russo": "ru", "russian": "ru", "ru": "ru",
    "cinese": "zh", "chinese": "zh", "zh": "zh",
    "giapponese": "ja", "japanese": "ja", "ja": "ja",
    "arabo": "ar", "arabic": "ar", "ar": "ar",
}

_DURATION_FACTORS = {
    "minuto": 1, "minuti": 1, "min": 1,
    "ora": 60, "ore": 60, "h": 60,
    "secondo": 1 / 60, "secondi": 1 / 60, "s": 1 / 60,
}


def to_minutes(m: re.Match) -> int:
    """Converte una durata ("2 ore", "30 minuti", "1h") in minuti."""
    try:
        amount = int(m.group(1))
        unit = (m.group(2) or "").lower().rstrip('s')
        if unit in ("ore", "ora", "h"):
            return amount * 60
        if unit in ("secondi", "secondo", "s"):
            return max(1, round(amount / 60))
        return amount
    except (IndexError, ValueError, TypeError):
        return 0


def to_lang(m: re.Match) -> str:
    """Mappa un nome lingua (o codice) al codice ISO 639-1; fallback = token raw."""
    token = m.group(1).lower() if m.group(1) else ""
    return _LANG_MAP.get(token, token)


SLOT_EXTRACTORS: dict[str, list[tuple[re.Pattern, str, Any]]] = {
    "web": [
        (re.compile(r'\b(meteo|tempo|weather)\b', re.I), "topic", "weather"),
        (re.compile(r'\b(notizie|news|novità)\b', re.I), "topic", "news"),
        # "prezzo" (singolare) NON matchava \b(prezzi?|...)\b — \bprezz[oi]\b copre entrambi.
        (re.compile(r'\b(prezz[oi]|costo|quanto costa)\b', re.I), "topic", "prices"),
        (re.compile(r'\b(?:a|ad|in)\s+([A-ZÀ-Ý][a-zà-ÿ]+)\b'), "city", str),   # "a Catania"
    ],
    "schedule": [
        # "timer di 2 ore" non matchava (solo "tra X"). Preposizioni di/per aggiunte.
        (re.compile(r'\b(?:tra|di|per|in)\s+(\d+)\s*(minuti?|min|ore?|secondi?|h)\b', re.I), "duration_min", to_minutes),
        (re.compile(r'\bogni\s+(giorno|mattina|sera|settimana|lunedì|martedì|mercoledì|giovedì|venerdì|sabato|domenica)\b', re.I), "action", "cron"),
        # "alle 9:30" / "at 9:30" / "for 9:30" → group(1) include i minuti ("9:30").
        (re.compile(r'\b(?:alle?|at|for)\s+(\d{1,2}(?:[:.]\d{2})?)\b', re.I), "time", str),
        (re.compile(r'\b(ricordami|ricorda|promemoria|timer|remind(?:er)?|set\s+a\s+reminder)\b', re.I), "action", "remind"),
        # message: tutto ciò che segue la preposizione "di" (standard dei
        # promemoria: "ricordami tra X minuti di MESSAGE"). NIENTE whitelist
        # di verbi — "controllare la posta" / "comprare il pane" / "fare la
        # spesa" matchano tutti (pre-fix: solo chiamare/scrivere/mandare/fare/
        # preparare → slot mancante → handle_schedule ritornava None).
        (re.compile(r'\bdi\s+(.+)', re.I), "message", str),
    ],
    "project": [
        (_FILE_PATH_RE, "file_path", str),
    ],
    "memory": [
        # save: "ricordami", "memorizza", "salva", "remember that...".
        # (?<!you\s) evita che "what DO YOU REMEMBER?" venga preso come save
        # (rightmost-wins: "remember" a pos > "what do you remember" → retrieve perdeva).
        (re.compile(r'\b(ricorda(?:ti|mi)?|memorizza|salva|(?<!you\s)remember(?!\s+that))\b', re.I), "action", "save"),
        (re.compile(r'\b(che ricordi|che cosa ricordi|ricordi di|memoria su|memoria di|what do you remember|do you remember)\b', re.I), "action", "retrieve"),
        # "ricorda CHE il deploy è giovedì" → content="il deploy è giovedì" (senza "che" il benchmark fallisce)
        (re.compile(r'\b(?:di|su|riguardo|che|that)\s+(.+)$', re.I), "content", str),
    ],
    "task": [
        # "aggiungi UNA TODO" — alternanza estesa task|todo + articolo opzionale.
        (re.compile(r'\b(aggiungi|crea|nuovo)\s+(?:un\s+|una\s+)?(?:task|todo)\b', re.I), "action", "add"),
        (re.compile(r'\b(?:task|todo)\s*[:：]\s*(.+)', re.I), "description", str),
        # parenthesizzato: senza il gruppo esterno, l'alternanza spezza il match.
        (re.compile(r'\b((?:segna|marca)\s+(?:come\s+)?fatto|completat[oa])\b', re.I), "action", "done"),
        (re.compile(r'\b(mostra|elenca|quali)\s+(?:sono\s+)?(?:i\s+)?task\b', re.I), "action", "list"),
        (re.compile(r'\bpriorit[àa]\s+(alta|media|bassa)\b', re.I), "priority", str),
        # [^,]+ si ferma alla virgola → deadline="venerdì" (non "venerdì, priorità bassa").
        (re.compile(r'\b(?:entro|scadenza|deadline)\s+([^,]+)', re.I), "deadline", str),
    ],
    "analyze": [
        (re.compile(r'\b(analizza|analisi|performance)\b', re.I), "task", "performance"),
        (re.compile(r'\b(debugga|debug|perch[ée] non funziona|non funziona)\b', re.I), "task", "diagnose"),
        (re.compile(r'\b(spiega|explain|come funziona)\b', re.I), "task", "explain"),
        (_FILE_PATH_RE, "file_path", str),   # "analizza le performance di rag/engine.py" → file_path
    ],
    "plan": [
        (re.compile(r'\b(come\s+implementer(?:ei|esti)|come\s+lo\s+implementeresti|come\s+farei|come\s+faresti)\b', re.I), "task", "propose"),
        (re.compile(r'\b(piano|pianifica|progetta|strategia|quali\s+step|quali\s+passi)\b', re.I), "task", "steps"),
    ],
    "code": [
        (re.compile(r'\b(leggi|leggere|read)\s+(?:il\s+)?file\b', re.I), "operation", "read"),   # client agentici: "leggi il file X"
        (re.compile(r'\b(rifattorizza|refactor|ripulisci|semplifica)\b', re.I), "operation", "refactor"),
        (re.compile(r'\b(correggi|fix|risolvi|sistem[ai])\b', re.I), "operation", "fix"),
        (re.compile(r'\b(implementa|scrivi|crea|aggiungi|modifica)\b', re.I), "operation", "implement"),
        # "rifattorizza il modulo auth" → target="auth".
        (re.compile(r'\b(?:il\s+)?modulo\s+([\w\-./]+)\b', re.I), "target", str),
        (_FILE_PATH_RE, "file_path", str),   # "leggi il file jarvis/main.py" → file_path
    ],
    "git": [
        (re.compile(r'\b(che\s+branch|branch\s+attuale|stato\s+del\s+repo|git\s+status)\b', re.I), "operation", "status"),
        # "mostra gli ultimi commit" / "storico dei commit" → log.
        (re.compile(r'\b(git\s+log|storico\s+commit|storico\s+dei\s+commit|ultimi\s+commit|mostra\s+(?:gli\s+)?(?:ultimi\s+)?commit)\b', re.I), "operation", "log"),
        # "commit" nudo NON matcha (rightmost-wins: "git log degli ultimi commit" → log ✓).
        (re.compile(r'\b(committa|git\s+commit|fai\s+commit)\b', re.I), "operation", "commit"),
        (re.compile(r'\b(crea\s+branch|nuovo\s+branch|switch\s+branch)\b', re.I), "operation", "branch"),
        (re.compile(r'\b(push|pull|merge)\b', re.I), "operation", "merge"),
        (re.compile(r'\b(?:con\s+messaggio|messaggio)\s+(.+)', re.I), "message", str),
    ],
    "ssh": [
        # "produzione" non duplicato nel gruppo alternato.
        (re.compile(r'\b(?:su|sul|nel)\s+([\w\-.]+\s*server|produzione|debian|vps)\b', re.I), "host", str),
        (re.compile(r'\b(deploy|restart|riavvia|rm\s+-rf|rimuovi)\b', re.I), "command", str),
        (re.compile(r'\b(uptime|df\s+-h|ps\s+aux|free\s+-h)\b', re.I), "command", str),
        (re.compile(r'\b(?:esegui|esegui\s+su|lancia)\s+(.+)', re.I), "command", str),
    ],
    "transcribe": [
        # double-quote: "dell'audio" NON compila con single-quote.
        (re.compile(r"\b(trascrivi|trascrizione|testo\s+dell'audio|dettato)\b", re.I), "source", "audio"),
        # "vocale" (typo fix v1: "vocal"). "audio" NON è qui (conflitto rightmost con regex 1).
        (re.compile(r'\b(vocale|voce|messaggio\s+vocale)\b', re.I), "source", "voice"),
        (re.compile(r'\b(?:in|in\s+lingua)\s+([a-zà-ÿ]{2,8})\b', re.I), "lang", str),
    ],
    "fetch": [
        # Parenthesizzata: cast `str` legge m.group(1) → senza gruppo esterno = IndexError.
        (re.compile(r'(https?://[^\s]+)'), "url", str),
        (re.compile(r'\b(?:leggi|apri|estra|scarica|contenuto\s+di)\s+(https?://[^\s]+|\S+\.\S+)', re.I), "url", str),
        (re.compile(r'\bformato\s+(markdown|html|testo)\b', re.I), "format", str),
    ],
    "translate": [
        # [a-z]{2,3} non matchava "inglese" (7 char) → _LANG_MAP.
        (re.compile(r'\b(?:in|verso|to)\s+([a-zà-ÿ]{2,8})\b', re.I), "target_lang", to_lang),
        # "dal francese" (da+il) → alternanza con preposizioni articolate.
        (re.compile(r'\b(?:da\s+|dal\s+|dall[ae]?\s+|from\s+)([a-zà-ÿ]{2,8})\b', re.I), "source_lang", to_lang),
        # prefisso opzionale: "traduci dal francese: bonjour tout le monde" → text="bonjour tout le monde".
        (re.compile(r'\b(?:traduci|traduzione|translate)\s*(?:(?:in|da|dal|dall[ae]?|from)\s+[a-zà-ÿ]{2,8})?\s*[:：]?\s*(.+)', re.I), "text", str),
    ],
    "config": [
        # Alternanza ORDINATA: "imposta il X" PRIMA di "imposta X" (ordered, primo branch vince).
        (re.compile(r'\b(?:imposta\s+il|imposta|sett[a]?|set|cambia\s+il|cambia)\s+([\w_]+)\s*(?:a|su|=)?\s*(.+)', re.I), "key", str),
        # action="set" da "imposta LLAMA_MODEL_PATH" (la tuple key NON lo produce).
        (re.compile(r'\b(imposta|imposta\s+il|sett[a]?|set|cambia\s+il)\s+[\w_]+(?:\s+(?:a|su|=)\s+\S+)?\b', re.I), "action", "set"),
        (re.compile(r'\b(?:a|su|=)\s*(.+?)\s*$', re.I), "value", str),
        # [ei]? copre "configurazione/i" e "impostazione/i"; articolo (?:il|la|le)?.
        (re.compile(r'\b(mostra|quali\s+sono|leggi|get)\s+(?:il\s+|la\s+|le\s+)?(configurazion[ei]?|impostazion[ei]?|settings)\b', re.I), "action", "get"),
        (re.compile(r'\b(reset|ripristina)\s+([\w_]+)\b', re.I), "action", "reset"),
    ],
    "maintenance": [
        (re.compile(r'\b(pulisci|svuota|clear)\s+(?:la\s+)?cache\b', re.I), "operation", "cache_clear"),
        (re.compile(r'\b(reindicizza|reindex|re-ingest|reingest)\b', re.I), "operation", "reindex"),
        (re.compile(r'\b(pulizia|cleanup|collezioni\s+orfane)\b', re.I), "operation", "cleanup"),
        # "stato dei servizi" ("dei" ≠ "del") → articolate aggiunte.
        (re.compile(r'\b(stato|status|health)\s+(?:del\s+|della\s+|dei\s+|degli\s+)?(?:sistema|servizi)\b', re.I), "operation", "status"),
    ],
    "action": [
        (re.compile(r'\b(leggi|leggere|read|apri|mostra)\s+(?:il\s+)?file\b', re.I), "operation", "file_read"),
        (re.compile(r'\b(scrivi|modifica|aggiorna|write|salva)\s+(?:il\s+)?file\b', re.I), "operation", "file_write"),
        (re.compile(r'\bgit\s+(status|log|diff)\b', re.I), "operation", "git_read"),
        (re.compile(r'\bgit\s+(commit|push|merge|branch)\b', re.I), "operation", "git_write"),
        (re.compile(r'\b(esegui|lancia|run|exec)\s+(?:questo\s+)?comando\b', re.I), "operation", "shell"),
        (re.compile(r'\b(pericolos[oa]|distruttiv[oa]|irreversibil)\b', re.I), "destructive", True),
    ],
    "meta": [
        (re.compile(r'\b(quali\s+progetti|che\s+progetti|progetti\s+disponibili|lista\s+progetti)\b', re.I), "query_type", "projects"),
        (re.compile(r'\b(cosa\s+sai\s+fare|quali\s+capacità|che\s+cose?\s+puoi)\b', re.I), "query_type", "capabilities"),
        (re.compile(r'\b(aiutami|aiuto|help|guida)\b', re.I), "query_type", "help"),
    ],
    "general": [
        # Nessuno slot: risposta diretta, zero gathering.
    ],
}


# ──────────────────────────────────────────────
# Cache classificazione (LRU, TTL 60s)
# ──────────────────────────────────────────────

_CLASSIFY_CACHE: dict[str, tuple[float, IntentResult]] = {}
_CLASSIFY_CACHE_TTL = 60.0
_CLASSIFY_CACHE_MAX = 256


def _cache_get(message: str) -> Optional[IntentResult]:
    now = time.monotonic()
    entry = _CLASSIFY_CACHE.get(message)
    if entry and now - entry[0] < _CLASSIFY_CACHE_TTL:
        return entry[1]
    return None


def _cache_put(message: str, result: IntentResult) -> None:
    now = time.monotonic()
    if len(_CLASSIFY_CACHE) >= _CLASSIFY_CACHE_MAX:
        expired = [k for k, (ts, _) in _CLASSIFY_CACHE.items() if now - ts >= _CLASSIFY_CACHE_TTL]
        for k in expired:
            del _CLASSIFY_CACHE[k]
        if len(_CLASSIFY_CACHE) >= _CLASSIFY_CACHE_MAX:
            # TTL non bastava: butta la voce più vecchia
            oldest = min(_CLASSIFY_CACHE.items(), key=lambda kv: kv[1][0], default=None)
            if oldest is not None:
                del _CLASSIFY_CACHE[oldest[0]]
    _CLASSIFY_CACHE[message] = (now, result)


# ──────────────────────────────────────────────
# FAST PATH — casi deterministici tier-0 (0 LLM, 26ms)
# ──────────────────────────────────────────────

def _fast_path(user_message: str, context: Optional[dict] = None) -> Optional[IntentResult]:
    """Casi deterministici centralizzati (ex keyword bypass) + /web + confirm/reject
    + query interne. 0 LLM, source="regex".

    Ritorna IntentResult se matcha, None se deve passare al passo successivo.
    """
    ctx = context or {}
    msg_lower = user_message.lower().strip()

    # ── Conferma/rifiuto token (priorità massima: approva/annulla op. pendente) ──
    m = CONFIRM_PATTERN.match(msg_lower)
    if m:
        logger.info(f"✅ FastPath: CONFIRM (token {m.group(1)})")
        return IntentResult(intent="confirm", slots={"token": m.group(1)}, confidence=1.0, source="regex")
    m = REJECT_PATTERN.match(msg_lower)
    if m:
        logger.info(f"❌ FastPath: REJECT (token {m.group(2)})")
        return IntentResult(intent="reject", slots={"token": m.group(2)}, confidence=1.0, source="regex")

    # ── Query interna del sistema (Mem0 loop guard, Bug 8) ──
    txt = user_message.strip()
    if (txt.startswith("## Summary") or "Extract entities" in txt
            or txt.startswith("ADD_MEMORY") or txt.startswith("UPDATE_MEMORY")
            or "deduce the facts" in txt):
        logger.info("🛡️ FastPath: INTERNAL (query interna)")
        return IntentResult(intent="internal", confidence=1.0, source="regex")

    # ── /web esplicito ──
    if user_message.strip().startswith("/web "):
        logger.info("🌐 FastPath: WEB (prefisso /web)")
        return IntentResult(intent="web", slots={"query": user_message.strip()[5:]}, confidence=1.0, source="regex")

    if len(msg_lower) < 3:
        return IntentResult(intent="general", confidence=1.0, source="regex")

    # ── Pure greeting detection (0 LLM, short-circuit 26ms) ──
    if len(msg_lower) < 30:
        if msg_lower.strip() in PURE_GREETINGS:
            logger.info(f"👋 FastPath: GREETING (saluto puro: '{msg_lower}')")
            return IntentResult(intent="greeting", confidence=1.0, source="regex")
        _greeting_words = set(re.findall(r'\b\w+\b', msg_lower))
        if _greeting_words and len(_greeting_words) <= 2:
            if all(w in PURE_GREETINGS for w in _greeting_words):
                logger.info(f"👋 FastPath: GREETING (saluto puro: '{msg_lower}')")
                return IntentResult(intent="greeting", confidence=1.0, source="regex")

    # ── Cherry Studio / client JSON conversation dump ──
    _stripped = user_message.strip()
    if (_stripped.startswith('[') and '{"role"' in _stripped) or _stripped.startswith('[{"role"'):
        logger.info(f"🧠 FastPath: GENERAL (JSON conversation dump, {len(_stripped)}ch)")
        return IntentResult(intent="general", confidence=1.0, source="regex")

    # ── Pattern intent-specifici → NON intercettare (delega al LLM) ──
    # PRIMA del nome-progetto e di PROJECT_KEYWORDS: "ricorda che il deploy è
    # giovedì" ha "deploy" ∈ PROJECT_KEYWORDS ma è memory; "che cosa ricordi sul
    # progetto NeuroNet?" cita il progetto ma è memory-retrieve.
    intent_hint = _match_llm_intent_pattern(user_message)
    if intent_hint is not None:
        logger.info(f"🎯 FastPath: delega a LLM (pattern intent '{intent_hint}': '{user_message[:50]}')")
        return None

    # ── Nome progetto in query ──
    projects = ctx.get("projects_available", [])
    for proj in projects:
        proj_lower = proj.lower()
        for variant in (proj_lower, proj_lower.replace('_', '-'), proj_lower.replace('_', ' ')):
            if variant in msg_lower:
                logger.info(f"🧠 FastPath: PROJECT (nome progetto in query: {proj})")
                return IntentResult(intent="project", project=proj, confidence=1.0, source="regex")

    # ── META phrases ──
    if META_PHRASES.search(msg_lower):
        logger.info("🧠 FastPath: META (frase match)")
        return IntentResult(intent="meta", confidence=1.0, source="regex")

    # ── Simple factual queries (data, ora, meteo, posizione) → general ──
    if SIMPLE_QUERIES.match(msg_lower):
        logger.info(f"🧠 FastPath: GENERAL (query fattuale semplice: '{msg_lower[:50]}')")
        return IntentResult(intent="general", confidence=1.0, source="regex")

    # ── PROJECT_KEYWORDS / path regex → project ──
    words = set(re.findall(r'\b\w+\b', msg_lower))
    if words.intersection(PROJECT_KEYWORDS):
        logger.info("🧠 FastPath: PROJECT (keyword match)")
        return IntentResult(intent="project", confidence=1.0, source="regex")
    if re.search(r'(\.[a-z]{1,4}\b|\b(src|app|lib|bin)/)', msg_lower):
        logger.info("🧠 FastPath: PROJECT (path regex match)")
        return IntentResult(intent="project", confidence=1.0, source="regex")

    return None  # Nessun fast-path → LLM


# ──────────────────────────────────────────────
# SLOT EXTRACTION (regex post-hoc)
# ──────────────────────────────────────────────

def _extract_slots(intent: str, message: str) -> dict[str, Any]:
    """Estrae slot dal messaggio con regex post-hoc dedicate.

    Semantica: RIGHTMOST MATCH VINCE per slot (confronto su m.start()).
    L'ordine delle regex nella lista è IRRILEVANTE per slot in conflitto.
    """
    slots: dict[str, tuple[int, Any]] = {}
    for regex, slot, cast in SLOT_EXTRACTORS.get(intent, []):
        for m in regex.finditer(message):
            if cast is str:
                # Marcatore: il VALORE è nel gruppo 1 della regex.
                try:
                    value = m.group(1)
                except IndexError:
                    continue
            elif callable(cast):
                try:
                    value = cast(m)
                except (IndexError, ValueError, TypeError):
                    continue
            else:
                value = cast  # costante (str/bool)
            if slot not in slots or m.start() > slots[slot][0]:
                slots[slot] = (m.start(), value)
    return {slot: entry[1] for slot, entry in slots.items()}


# ──────────────────────────────────────────────
# LLM CLASSIFY (Fase 2 — skeleton)
# ──────────────────────────────────────────────

async def _llm_classify(user_message: str, context: Optional[dict] = None) -> Optional[IntentResult]:
    """Classificazione via main model + GBNF (solo intent+project+confidence).

    FASE 2 del piano intent_understanding_llm.md — pattern di riferimento
    del classificatore legacy (eliminato in Fase 3) ma con:
    - model="chat" (main model, 0 VRAM extra)
    - temperature=0.0, num_predict=60, priority=1, stop=["\n"]
    - asyncio.wait_for(..., timeout=15.0)
    - LlamaGrammar.from_string(GBNF_GRAMMAR_INTENT)
    - Parsing JSON: re.search(JSON_RE, content, re.DOTALL) + json.loads
    - Validazione intent ∈ 18 valori; fallback None su errore → caller usa _fallback
    (JSON_RE = pattern 'braces.*braces' raw — v. llm_engine.py:754-763)

    Gli slot NON escono dal JSON (la GBNF vieta stringhe libere): vengono
    estratti post-hoc da _extract_slots() nel caller (classify()).

    Returns:
        IntentResult(source="llm") oppure None → il caller fa fallback general.
    """
    try:
        from core.llm_engine import engine, extract_content
    except ImportError as exc:
        logger.warning(f"⚠️ _llm_classify: llm_engine non importabile → None ({exc})")
        return None

    if not engine.chat_model:
        logger.warning("⚠️ _llm_classify: chat model non caricato → None")
        return None

    ctx = context or {}
    active_project = ctx.get("active_project") or "nessuno"
    projects_str = ", ".join(ctx.get("projects_available", [])) or "nessuno"

    prompt = f"""Contesto:
- Progetto attivo: {active_project}
- Progetti disponibili: {projects_str}

Richiesta: "{user_message[:1000]}"

Classifica l'intento in UN SOLO valore dei 18 elencati sotto.
JSON esatto: {{"intent":"<valore>","project":"null|Nome","confidence":0.95}}

project|general|web|schedule|meta|action|memory|task|analyze|plan|code|git|ssh|transcribe|fetch|translate|config|maintenance"""

    try:
        from llama_cpp import LlamaGrammar
    except ImportError:
        logger.warning("⚠️ _llm_classify: llama_cpp non importabile → None")
        return None

    try:
        grammar_obj = LlamaGrammar.from_string(GBNF_GRAMMAR_INTENT)
        messages = [{"role": "system", "content": INTENT_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}]
        response = await asyncio.wait_for(
            engine.generate_chat(
                messages, stream=False,
                options={"temperature": 0.0, "num_predict": 60, "stop": ["\n"]},
                grammar=grammar_obj,
                model="chat", priority=1,
            ),
            timeout=15.0,
        )
    except asyncio.TimeoutError:
        logger.warning("⚠️ _llm_classify: timeout 15s → None")
        return None
    except Exception as exc:
        logger.warning(f"⚠️ _llm_classify: errore generazione → None ({repr(exc)})")
        return None

    if "error" in response:
        logger.warning(f"⚠️ _llm_classify: errore LLM → None ({response['error']})")
        return None

    try:
        content = extract_content(response)
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if not match:
            logger.warning(f"⚠️ _llm_classify: JSON non trovato in '{content[:60]}...' → None")
            return None

        result = json.loads(match.group(0))
        intent = result.get("intent", "general")
        project = result.get("project")
        try:
            confidence = float(result.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        available = ctx.get("projects_available", [])
        if project and project not in available:
            project = None
        if intent not in LLM_INTENTS:
            logger.warning(f"⚠️ _llm_classify: intent '{intent}' non valido → general")
            intent = "general"

        logger.info(f"🧠 IntentRouter LLM: {intent} | project={project} | conf={confidence:.2f}")
        return IntentResult(
            intent=intent,
            project=project if intent == "project" else None,
            confidence=confidence,
            source="llm",
        )
    except (ValueError, TypeError, AttributeError) as exc:
        logger.warning(f"⚠️ _llm_classify: parsing JSON fallito → None ({repr(exc)})")
        return None


# ──────────────────────────────────────────────
# FALLBACK (safety-net)
# ──────────────────────────────────────────────

def _fallback(user_message: str) -> IntentResult:
    """Safety-net: mai crash, mai intent sbagliato senza via di fuga → general."""
    return IntentResult(intent="general", confidence=0.0, source="fallback")


# ──────────────────────────────────────────────
# API UNICA
# ──────────────────────────────────────────────

async def classify(user_message: str, context: Optional[dict] = None) -> IntentResult:
    """Classifica l'intento di una richiesta utente.

    Chain fissa (nessun kill switch): fast_path → cache → LLM → fallback.

    Args:
        user_message: Testo della richiesta utente.
        context: Dict opzionale con chiavi note:
            projects_available (list[str]), active_project (str|None),
            recent_messages (list[str]).

    Returns:
        IntentResult con intent (22 valori), slots, confidence, source, project.
    """
    if not user_message or not user_message.strip():
        return IntentResult(intent="general", confidence=0.0, source="fallback")

    # 1. Fast path (regex deterministiche, 0 LLM)
    result = _fast_path(user_message, context)
    if result is not None:
        # Gli slot si estraggono SEMPRE (anche dal fast-path): il benchmark §10.3
        # richiede es. project + file_path ("c'è un bug in auth.py" → {file_path}).
        result.slots = _extract_slots(result.intent, user_message)
        return result

    # 2. Cache hit (LRU TTL 60s)
    cached = _cache_get(user_message)
    if cached is not None:
        return cached

    # 3. LLM classification (main model + GBNF)
    try:
        result = await _llm_classify(user_message, context or {})
    except Exception as exc:
        logger.warning(f"⚠️ _llm_classify fallita: {exc}")
        result = None

    if result is None:
        # 4. Fallback → general
        result = _fallback(user_message)
    else:
        # Slot extraction regex post-hoc (mai dall'LLM)
        result.slots = _extract_slots(result.intent, user_message)

    _cache_put(user_message, result)
    return result


# ──────────────────────────────────────────────
# DISPATCH TABLE (Fase 4 — gestori iniettati dai caller)
# ──────────────────────────────────────────────

DISPATCH_TABLE: dict[str, Callable] = {}
"""Matrice di routing intent→handler (§4.3).

I gestori sono INIETTATI dai caller (main.py, chat.py, dashboard) via
register_handlers()/register_handler() per evitare import circolari:
intent_router conosce solo classificazione + slot, la gestione resta nei
moduli proprietari (cron.py, tools.py, memory/engine.py, reasoning.py).

Soglie di confidenza (§4.3): read-only 0.50-0.70, side effects ≥0.70
(schedule 0.75, action/memory/task/code 0.70, write git/ssh/config/maintenance
0.70). Sotto soglia → fallback a general/project/web, MAI eseguire azioni.
"""

# Soglie di confidenza per intent (§4.3):
# - read-only: 0.50-0.70 (project/general/web/meta/analyze/plan/transcribe/fetch/translate)
# - effetti collaterali: ≥0.70 (schedule 0.75, action/memory/task/code 0.70)
# - git/ssh/config/maintenance: read 0.60 / write 0.70 (via _WRITE_SLOTS)
# - greeting/confirm/reject/internal: 1.0 (tier-0 regex deterministici)
INTENT_THRESHOLDS: dict[str, float] = {
    "project": 0.60,
    "general": 0.50,
    "web": 0.60,
    "schedule": 0.75,
    "meta": 0.60,
    "action": 0.70,
    "memory": 0.70,
    "task": 0.70,
    "analyze": 0.60,
    "plan": 0.60,
    "code": 0.70,
    "git": 0.60,          # read; write 0.70 via _WRITE_SLOTS
    "ssh": 0.60,          # read; write 0.70 via _WRITE_SLOTS
    "transcribe": 0.60,
    "fetch": 0.60,
    "translate": 0.60,
    "config": 0.60,       # get; set/reset 0.70 via _WRITE_SLOTS
    "maintenance": 0.60,  # status; cache_clear/reindex/cleanup 0.70 via _WRITE_SLOTS
    "greeting": 1.0,
    "confirm": 1.0,
    "reject": 1.0,
    "internal": 1.0,
}

# Slot che indicano operazioni WRITE (side effects) per intent a doppio tier.
# Soglia 0.70 se lo slot operation/action/command è in questo set, altrimenti 0.60.
_WRITE_SLOTS: dict[str, set[str]] = {
    "git": {"commit", "branch", "push", "pull", "merge"},
    "ssh": {"deploy", "restart", "rm", "rimuovi"},
    "config": {"set", "reset"},
    "maintenance": {"cache_clear", "reindex", "cleanup"},
}

# Slot keys da ispezionare per determinare l'operazione (write vs read).
_OP_SLOT_KEYS = ("operation", "action", "command")


def intent_threshold(intent: str, slots: Optional[dict] = None) -> float:
    """Soglia di confidenza per un intent (§4.3).

    - Soglia base da INTENT_THRESHOLDS.
    - git/ssh/config/maintenance: se lo slot operation/action/command è una
      scrittura → 0.70 (effetti collaterali), altrimenti 0.60 (read-only).
    """
    base = INTENT_THRESHOLDS.get(intent, 0.70)
    write_ops = _WRITE_SLOTS.get(intent)
    if write_ops:
        for key in _OP_SLOT_KEYS:
            op = (slots or {}).get(key)
            if op and str(op).strip().lower() in write_ops:
                return 0.70
    return base


def register_handler(intent: str, handler: Callable) -> None:
    """Registra un gestore nella DISPATCH_TABLE (iniettato dai caller).

    Evita import circolari: intent_router non importa i moduli proprietari
    (cron.py, tools.py, memory/engine.py...) — la registrazione avviene nei
    caller (main.py, chat.py, dashboard) via register_handlers().
    """
    DISPATCH_TABLE[intent] = handler


async def dispatch(result: Optional[IntentResult], context: Optional[dict] = None) -> Optional[str]:
    """Instrada un IntentResult al gestore registrato, se la confidenza è ≥ soglia.

    Principio §4.3: MAI eseguire azioni sotto soglia — sotto soglia si
    fallback a general/project/web (il prompt builder fa il context
    gathering normale) e nessun effetto collaterale parte.

    Args:
        result: IntentResult da classify()/build_omniscient_prompt.
        context: Dict opzionale per il gestore (user_id, chat_id, project...).

    Returns:
        str | None: messaggio di conferma da appendere alla risposta,
        o None se nessuna azione (sotto soglia / handler assente / nessuna conferma).
    """
    if result is None:
        return None
    threshold = intent_threshold(result.intent, result.slots)
    if result.confidence < threshold:
        logger.info(
            "⛔ dispatch: intent=%s conf=%.2f < soglia %.2f → nessuna azione (fallback)",
            result.intent, result.confidence, threshold,
        )
        return None
    handler = DISPATCH_TABLE.get(result.intent)
    if handler is None:
        return None
    try:
        confirm = await handler(result, context or {})
        if confirm:
            logger.info("✅ dispatch: intent=%s → %s", result.intent, str(confirm)[:80])
        return confirm
    except Exception as exc:
        logger.warning(f"⚠️ dispatch handler {result.intent} error: {exc}")
        return None
