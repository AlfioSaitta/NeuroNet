"""
Tag Processor — Centralized tag registry, extraction, handler dispatch, and cleaning.

Unifica tutta la logica di parsing dei tag d'azione e dei pattern thinking
che prima era duplicata in memory.py, telegram_bot.py e jarvis_agent.py.

Flusso:
  1. LLM genera risposta con tag XML embedded (es. <MEMORY>...</MEMORY>)
  2. process_all_tags() estrae ogni tag, esegue l'handler corrispondente,
     e restituisce testo pulito + messaggi di feedback.
  3. Ogni modulo (API, Telegram, Desktop) chiama la stessa funzione.
"""

import re
import asyncio
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable


# ──────────────────────────────────────────────
# Thinking patterns — pulizia input da modelli
# ──────────────────────────────────────────────
# Ogni entry: (regex_pattern, replacement)
# Applicati in ordine prima dei tag action.
THINKING_PATTERNS: list[tuple[str, str]] = [
    # Gemma 4: <|channel>thought\n...\n<channel|>
    (r'<\|channel>thought\s*.*?<channel\|>\s*', ''),
    # Generic channel leftovers
    (r'<\|?channel\|?>', ''),
    # DeepSeek / Gemma: <|think|>...</end> or <|think|>...<|end|>
    (r'(?s)<\|think\|>.*?(?:</?end>?|<\|end\|>)\s*', ''),
    # Solar Pro: [ANALYSIS]...[/ANALYSIS]
    (r'(?s)\[ANALYSIS\].*?\[/ANALYSIS\]\s*', ''),
    # ChatML: <|im_start|>...<|im_end|> blocks
    (r'<\|im_start\|>.*?<\|im_end\|>', ''),
    # ChatML: leftover single tags
    (r'<\|im_start\|>|<\|im_end\|>', ''),
    # Harmony: <|start|>...<|end|> blocks (OpenAI GPT-5 style)
    (r'(?s)<\|start\|>.*?<\|end\|>\s*', ''),
    # Text prefixes: "thought:", "thinking:", "reasoning:"
    (r'(?i)^\s*(?:thought|thinking|reasoning):.*?\n\s*', ''),
    # Residual newlines from stripping
    (r'\n{3,}', '\n\n'),
]

# Compiled cache
_THINKING_RE: list[tuple[re.Pattern, str]] = [
    (re.compile(p, re.DOTALL), r) for p, r in THINKING_PATTERNS
]


def strip_thinking_blocks(text: str) -> str:
    """Rimuove dal testo tutti i blocchi di reasoning/thinking dei vari modelli."""
    for pattern, replacement in _THINKING_RE:
        text = pattern.sub(replacement, text)
    return text.strip()


# ──────────────────────────────────────────────
# Tag context
# ──────────────────────────────────────────────

@dataclass
class TagContext:
    """Contesto passato a ogni handler di tag."""
    user_id: str = "alfio_dev"
    project: Optional[str] = None
    chat_id: Optional[int] = None  # Telegram chat ID per notifiche
    # Campi opzionali compilati a runtime
    full_response: str = ""        # Risposta LLM completa (pre-processing)
    raw_tags: dict[str, list[str]] = field(default_factory=dict)  # tag_name -> [content, ...]


# ──────────────────────────────────────────────
# Tag definition
# ──────────────────────────────────────────────

TagHandler = Callable[["TagDef", str, TagContext], Awaitable[Optional[str]]]


@dataclass
class TagDef:
    """
    Definizione di un tag d'azione.

    name:        Nome del tag (es. "MEMORY")
    pattern:     Regex compiled per estrarre il contenuto (con DOTALL)
    handler:     Async function( TagDef, content_str, TagContext ) -> Optional[str]
                 Il return è un messaggio di feedback per l'utente (None = silenzioso)
    visibility:  "hidden"  → rimosso dal testo, nessun feedback
                 "action"  → rimosso dal testo, feedback appeso al messaggio
                 "kept"    → lasciato nel testo così com'è
    description: Spiegazione del tag (per documentazione e prompt generation)
    is_self_closing: True se il tag è auto-chiudente (<TAG/>)
    """
    name: str
    pattern: re.Pattern
    handler: Optional[TagHandler] = None
    visibility: str = "hidden"
    description: str = ""
    is_self_closing: bool = False

    def __post_init__(self):
        if self.visibility not in ("hidden", "action", "kept"):
            raise ValueError(f"Tag {self.name}: visibility must be hidden/action/kept")


# ──────────────────────────────────────────────
# TAG REGISTRY
# ──────────────────────────────────────────────
# L'ordine conta: i tag valutati per primi vengono processati per primi
# e i loro span rimossi prima che i tag successivi vedano il testo.
# Metti i tag più specifici prima di quelli generici.

_TAG_REGISTRY: dict[str, TagDef] = {}


# ── Helper per costruire pattern ──

def _tag_pattern(tag_name: str) -> re.Pattern:
    """Pattern per tag con contenuto: <TAG>...</TAG>"""
    return re.compile(rf"<{tag_name}>(.*?)</{tag_name}>", re.DOTALL | re.IGNORECASE)

def _self_closing_pattern(tag_name: str) -> re.Pattern:
    """Pattern per tag auto-chiudente: <TAG/> o <TAG >"""
    return re.compile(rf"<{tag_name}\s*/?\s*>", re.IGNORECASE)


# ── Handler implementations (lazy imports per evitare circular deps) ──

async def _handle_memory(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    content = content.strip()
    if not content:
        return None
    from memory import save_to_memory
    ok = await save_to_memory(content, user_id=ctx.user_id, project=ctx.project)
    if ok:
        from config import logger
        logger.info(f"🧠 MEMORY tag impresso{' ['+ctx.project+']' if ctx.project else ''}: {content[:100]}")
    return None  # Silenzioso — nessun feedback visibile


async def _handle_schedule(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    try:
        cron_expr, prompt_text = content.split("|", 1)
        from cron_agent import add_cron_job
        success, jid = add_cron_job(cron_expr.strip(), prompt_text.strip(), ctx.chat_id or 0)
        if success:
            return f"⏱️ **Notifica Schedulata**: `{cron_expr.strip()}`"
        return f"❌ **Errore Schedulazione**: {jid}"
    except Exception as e:
        from config import logger
        logger.error(f"Errore parsing SCHEDULE: {e}")
        return None


async def _handle_notify_once(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    try:
        date_str, prompt_text = content.split("|", 1)
        from cron_agent import add_date_job
        success, jid = add_date_job(date_str.strip(), prompt_text.strip(), ctx.chat_id or 0)
        if success:
            return f"🔔 **Promemoria Impostato per**: `{date_str.strip()}`"
        return f"⚠️ **Errore Data**: {jid}"
    except Exception as e:
        from config import logger
        logger.error(f"Errore parsing NOTIFY_ONCE: {e}")
        return None


async def _handle_notify_in(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    try:
        minutes_str, prompt_text = content.split("|", 1)
        from cron_agent import add_relative_job
        success, jid, computed_date = add_relative_job(
            int(minutes_str.strip()), prompt_text.strip(), ctx.chat_id or 0
        )
        if success:
            return f"🔔 **Promemoria tra {minutes_str.strip()} minuti** (alle `{computed_date}`)"
        return f"⚠️ **Errore Timer**: {jid}"
    except Exception as e:
        from config import logger
        logger.error(f"Errore parsing NOTIFY_IN: {e}")
        return None


async def _handle_ssh(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    try:
        server_name, command = content.split("|", 1)
        from infrastructure import run_on_server
        from config import logger
        logger.info(f"⚙️ SSH: esecuzione su {server_name.strip()}: {command.strip()}")

        # Esecuzione asincrona — risultato mandato separatamente
        async def _bg_ssh():
            out = await run_on_server(server_name.strip(), command.strip())
            if ctx.chat_id and state.telegram_app and state.telegram_app.bot:
                await state.telegram_app.bot.send_message(
                    chat_id=ctx.chat_id,
                    text=f"```bash\n{out}\n```",
                    parse_mode="Markdown"
                )
        import state
        task = asyncio.create_task(_bg_ssh())
        state.background_tasks.add(task)
        task.add_done_callback(state.background_tasks.discard)

        return f"⚙️ **Esecuzione SSH su `{server_name.strip()}` avviata...**"
    except Exception as e:
        from config import logger
        logger.error(f"Errore parsing SSH: {e}")
        return None


async def _handle_todo_add(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    try:
        parts = content.split("|")
        desc = parts[0].strip()
        prio = parts[1].strip() if len(parts) > 1 else "media"
        dead = parts[2].strip() if len(parts) > 2 else "nessuna"
        task_type = parts[3].strip().lower() if len(parts) > 3 else "personale"
        from task_manager import add_todo
        tid = add_todo(desc, prio, dead, task_type, ctx.user_id)
        type_label = "Progetto" if task_type == "progetto" else "Personale"
        return f"📝 **Task Aggiunto ({type_label})**: [{tid}] _{desc}_ (Prio: {prio}, Scad: {dead})"
    except Exception as e:
        from config import logger
        logger.error(f"Errore parsing TODO_ADD: {e}")
        return None


async def _handle_todo_done(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    try:
        tid = content.strip()
        from task_manager import mark_done
        success = mark_done(tid, ctx.user_id)
        if success:
            return f"✅ **Task Completato**: [{tid}]"
        return f"⚠️ **Errore**: Task [{tid}] non trovato o non autorizzato."
    except Exception as e:
        from config import logger
        logger.error(f"Errore parsing TODO_DONE: {e}")
        return None


async def _handle_web(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<WEB>query di ricerca</WEB> — Esegue una ricerca web."""
    query = content.strip()
    if not query:
        return None
    from web_search import perform_web_search_and_crawl
    from config import logger
    logger.info(f"🌐 WEB tag: ricerca '{query[:80]}...'")
    results, _ = await perform_web_search_and_crawl(query, force=True)
    if results and results != "Nessun risultato online.":
        return f"🌐 **Risultati Web per**: _{query}_\n\n{results[:1500]}"
    return f"🌐 **Nessun risultato web** per: _{query}_"


async def _handle_file(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<FILE>path/to/file</FILE> — Legge e include contenuto file."""
    path = content.strip()
    if not path:
        return None
    from config import DOC_DIR
    import os
    # Cerca prima assoluto, poi relativo a DOC_DIR
    if os.path.isabs(path) and os.path.isfile(path):
        filepath = path
    else:
        filepath = os.path.join(DOC_DIR, path) if DOC_DIR else path
    if not os.path.isfile(filepath):
        return f"📄 **File non trovato**: `{path}`"
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            fc = f.read()
        max_chars = 4000
        content_out = fc[:max_chars]
        if len(fc) > max_chars:
            content_out += f"\n... [troncato, {len(fc)} chars totali]"
        rel = os.path.relpath(filepath, DOC_DIR) if DOC_DIR else filepath
        return f"📄 **File**: `{rel}`\n```\n{content_out}\n```"
    except Exception as e:
        return f"⚠️ **Errore lettura file**: {e}"


async def _handle_emotion(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<EMOTION>stato</EMOTION> — Imposta stato emotivo (per UI)."""
    emotion = content.strip().lower()
    if not emotion:
        return None
    import state as gstate
    gstate.last_emotion = emotion
    from config import logger
    logger.info(f"🎭 EMOTION tag: {emotion}")
    return None  # Silenzioso — letto dalla UI


async def _handle_think_deep(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<THINK_DEEP/> — Attiva modalità ragionamento approfondito."""
    import state as gstate
    gstate.deepthink_mode = True
    from config import logger
    logger.info(f"🧠 THINK_DEEP attivato")
    return None  # Silenzioso — il prompt builder lo leggerà


async def _handle_cache_clear(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<CACHE_CLEAR/> — Resetta la cache semantica."""
    from rag import semantic_cache_clear
    try:
        await semantic_cache_clear()
        return "🗑️ **Cache semantica resettata**."
    except Exception as e:
        from config import logger
        logger.warning(f"Errore CACHE_CLEAR: {e}")
        return "⚠️ **Errore reset cache**."


async def _handle_confidence(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<CONFIDENCE>0.95</CONFIDENCE> — Salva metadato di confidenza (invisibile)."""
    try:
        score = float(content.strip())
        import state as gstate
        gstate.last_confidence = score
    except ValueError:
        pass
    return None  # Silenzioso


async def _handle_ask(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<ASK>domanda</ASK> — Il LLM fa una domanda all'utente."""
    question = content.strip()
    if not question:
        return None
    import state as gstate
    if not hasattr(gstate, 'pending_questions'):
        gstate.pending_questions = []
    gstate.pending_questions.append(question)
    from config import logger
    logger.info(f"❓ ASK tag: {question[:100]}")
    # La domanda viene mostrata all'utente come parte della risposta
    return f"❓ **{question}**"


async def _handle_rag(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<RAG>project_name</RAG> — Forza RAG su progetto specifico."""
    project = content.strip()
    if not project:
        return None
    import state as gstate
    gstate.forced_rag_project = project
    from config import logger
    logger.info(f"📁 RAG tag: forzato progetto '{project}'")
    return f"📁 **RAG focalizzato su**: `{project}`"


async def _handle_summary(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<SUMMARY target="user_id">testo</SUMMARY> — Salva riepilogo cross-user."""
    import re as _re
    m = _re.match(r'^\s*target\s*=\s*"([^"]+)"\s*>\s*(.*)$', content, _re.DOTALL)
    if not m:
        return None
    target_user = m.group(1).strip()
    summary_text = m.group(2).strip()
    if not summary_text:
        return None
    from memory import save_to_memory
    ok = await save_to_memory(summary_text, user_id=target_user, project=ctx.project)
    from config import logger
    if ok:
        logger.info(f"📋 SUMMARY tag: riepilogo salvato per user '{target_user}'")
        return f"📋 **Riepilogo salvato** per `{target_user}`."
    return None


async def _handle_branch(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<BRANCH>project|branch</BRANCH> — Cambia branch git."""
    try:
        parts = content.split("|", 1)
        project_name = parts[0].strip()
        branch_name = parts[1].strip() if len(parts) > 1 else parts[0].strip()
        from config import DOC_DIR
        import os, subprocess
        repo_dir = os.path.join(DOC_DIR, project_name) if DOC_DIR else project_name
        if not os.path.isdir(os.path.join(repo_dir, ".git")):
            return f"⚠️ **Non è un repository git**: `{repo_dir}`"
        result = subprocess.run(
            ["git", "checkout", branch_name],
            cwd=repo_dir, capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            return f"🔀 **Branch cambiato**: `{branch_name}` in `{project_name}`"
        return f"⚠️ **Errore git**: {result.stderr.strip()[:200]}"
    except Exception as e:
        return f"⚠️ **Errore BRANCH**: {e}"


async def _handle_commit(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<COMMIT>message</COMMIT> — Committa modifiche locali."""
    msg = content.strip()
    if not msg:
        return None
    from config import DOC_DIR
    import os, subprocess
    try:
        if not DOC_DIR or not os.path.isdir(os.path.join(DOC_DIR, ".git")):
            return f"⚠️ **Nessun repository git trovato** in `{DOC_DIR}`"
        result = subprocess.run(
            ["git", "add", "-A"],
            cwd=DOC_DIR, capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return f"⚠️ **Errore git add**: {result.stderr.strip()[:200]}"
        result = subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=DOC_DIR, capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            return f"✅ **Commit creato**: `{msg}`"
        return f"⚠️ **Errore commit**: {result.stderr.strip()[:200]}"
    except Exception as e:
        return f"⚠️ **Errore COMMIT**: {e}"


async def _handle_exec(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<EXEC>timeout_sec|comando</EXEC> — Esegue comando shell (safe-mode)."""
    try:
        parts = content.split("|", 1)
        timeout = int(parts[0].strip())
        command = parts[1].strip() if len(parts) > 1 else parts[0].strip()
    except (ValueError, IndexError):
        timeout = 30
        command = content.strip()
    import subprocess
    # Safe command whitelist
    allowed_prefixes = ("ls", "cat", "head", "tail", "echo", "date", "whoami",
                        "pwd", "df", "du", "ps", "uptime", "free", "uname",
                        "git status", "git log", "git diff")
    if not any(command.startswith(p) for p in allowed_prefixes):
        return f"⚠️ **Comando non consentito**: solo comandi readonly. Usa SSH per esecuzione remota."
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=min(timeout, 60)
        )
        output = (result.stdout or "") + (result.stderr or "")
        if len(output) > 2000:
            output = output[:2000] + "\n... [troncato]"
        return f"```bash\n{output}\n```"
    except subprocess.TimeoutExpired:
        return f"⚠️ **Timeout** ({timeout}s)"
    except Exception as e:
        return f"⚠️ **Errore EXEC**: {e}"


# ── Build registry ──

def _register(tag: TagDef):
    """Registra un tag nel registry globale."""
    _TAG_REGISTRY[tag.name.upper()] = tag


# Tag esistenti (da memory.py + telegram_bot.py)
_register(TagDef("MEMORY",       _tag_pattern("MEMORY"),        _handle_memory,       "hidden",  "Salva un fatto in memoria episodica (Mem0)"))
_register(TagDef("SCHEDULE",     _tag_pattern("SCHEDULE"),      _handle_schedule,     "action",  "Crea un promemoria schedulato (cron)"))
_register(TagDef("NOTIFY_ONCE",  _tag_pattern("NOTIFY_ONCE"),   _handle_notify_once,  "action",  "Crea un promemoria singolo a data fissa"))
_register(TagDef("NOTIFYONCE",   _tag_pattern("NOTIFYONCE"),    _handle_notify_once,  "action",  "Alias per NOTIFY_ONCE (senza underscore)"))
_register(TagDef("NOTIFY_IN",    _tag_pattern("NOTIFY_IN"),     _handle_notify_in,    "action",  "Crea un promemoria tra N minuti"))
_register(TagDef("NOTIFYIN",     _tag_pattern("NOTIFYIN"),      _handle_notify_in,    "action",  "Alias per NOTIFY_IN (senza underscore)"))
_register(TagDef("SSH",          _tag_pattern("SSH"),           _handle_ssh,          "action",  "Esegue un comando SSH su server remoto"))
_register(TagDef("TODO_ADD",     _tag_pattern("TODO_ADD"),      _handle_todo_add,     "action",  "Aggiunge un task alla todo list"))
_register(TagDef("TODO_DONE",    _tag_pattern("TODO_DONE"),     _handle_todo_done,    "action",  "Segna un task come completato"))

# Nuovi tag (proposti)
_register(TagDef("WEB",          _tag_pattern("WEB"),           _handle_web,          "action",  "Esegue una ricerca web e include i risultati"))
_register(TagDef("FILE",         _tag_pattern("FILE"),          _handle_file,         "action",  "Legge e include il contenuto di un file"))
_register(TagDef("EMOTION",      _tag_pattern("EMOTION"),       _handle_emotion,      "hidden",  "Imposta lo stato emotivo per l'interfaccia UI"))
_register(TagDef("THINK_DEEP",   _self_closing_pattern("THINK_DEEP"), _handle_think_deep, "hidden", "Attiva modalità ragionamento approfondito", True))
_register(TagDef("CACHE_CLEAR",  _self_closing_pattern("CACHE_CLEAR"), _handle_cache_clear, "action", "Resetta la cache semantica", True))
_register(TagDef("CONFIDENCE",   _tag_pattern("CONFIDENCE"),    _handle_confidence,   "hidden",  "Autovalutazione della confidenza della risposta"))
_register(TagDef("ASK",          _tag_pattern("ASK"),           _handle_ask,          "action",  "Il LLM fa una domanda all'utente (reverse interaction)"))
_register(TagDef("RAG",          _tag_pattern("RAG"),           _handle_rag,          "action",  "Forza RAG su un progetto specifico"))
_register(TagDef("SUMMARY",      re.compile(r"<SUMMARY\s+[^>]*>.*?</SUMMARY>", re.DOTALL), _handle_summary, "action", "Salva un riepilogo nella memoria di un altro utente"))
_register(TagDef("BRANCH",       _tag_pattern("BRANCH"),        _handle_branch,       "action",  "Cambia branch git in un progetto"))
_register(TagDef("COMMIT",       _tag_pattern("COMMIT"),        _handle_commit,       "action",  "Crea un commit git con i cambiamenti locali"))
_register(TagDef("EXEC",         _tag_pattern("EXEC"),          _handle_exec,         "action",  "Esegue un comando shell readonly (whitelist)"))


# ──────────────────────────────────────────────
# Compiled strip regex (auto-generata dal registry)
# ──────────────────────────────────────────────

def _build_strip_regex() -> re.Pattern:
    """Costruisce una regex che matcha tutti i tag con visibility=hidden/action."""
    tags_to_strip = [
        t for t in _TAG_REGISTRY.values()
        if t.visibility in ("hidden", "action")
    ]
    if not tags_to_strip:
        return re.compile(r'(?!)')  # never matches
    # Genera pattern alternativi: <TAG>...</TAG> e <TAG/>
    patterns = []
    for t in tags_to_strip:
        if t.is_self_closing:
            patterns.append(rf"<{t.name}\s*/?\s*>")
        else:
            patterns.append(rf"<{t.name}>.*?</{t.name}>")
    combined = "|".join(patterns)
    return re.compile(combined, re.DOTALL | re.IGNORECASE)

_STRIP_ALL_RE: re.Pattern = _build_strip_regex()


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

def get_registry() -> dict[str, TagDef]:
    """Restituisce il registro tag in sola lettura."""
    return dict(_TAG_REGISTRY)


def get_tag(name: str) -> Optional[TagDef]:
    """Restituisce un TagDef per nome (case-insensitive)."""
    return _TAG_REGISTRY.get(name.upper())


def strip_all_tags(text: str) -> str:
    """Rimuove dal testo tutti i tag con visibility=hidden/action (incluso il contenuto)."""
    return _STRIP_ALL_RE.sub("", text).strip()


async def process_all_tags(
    text: str,
    context: Optional[TagContext] = None,
) -> tuple[str, list[str]]:
    """
    Funzione principale: processa TUTTI i tag nel testo.

    1. Applica THINKING_PATTERNS (pulisce blocchi reasoning)
    2. Per ogni tag nel registro:
       - Estrae contenuto via regex
       - Se handler presente: lo esegue asincrono
       - Accumula messaggi di feedback
    3. Rimuove i tag dal testo per la versione pulita
    4. Restituisce: (testo_pulito, lista_messaggi_feedback)

    Args:
        text: Testo grezzo della risposta LLM
        context: Contesto opzionale (user_id, project, chat_id)

    Returns:
        (cleaned_text, feedback_messages)
    """
    if not text:
        return "", []

    if context is None:
        context = TagContext()

    # Step 1: Pulisci blocchi di thinking/reasoning
    text = strip_thinking_blocks(text)

    # Step 2: Estrai e processa tutti i tag
    feedback: list[str] = []
    # Lavoriamo su una copia del testo per l'estrazione, ma le sostituzioni
    # le facciamo progressivamente per evitare conflitti di span
    processed = text
    replacements: list[tuple[int, int, str]] = []  # (start, end, replacement)

    for tag in _TAG_REGISTRY.values():
        for match in tag.pattern.finditer(processed):
            content = match.group(1) if not tag.is_self_closing else ""
            start, end = match.start(), match.end()

            if tag.handler:
                try:
                    msg = await tag.handler(tag, content, context)
                    if msg and tag.visibility == "action":
                        feedback.append(msg)
                except Exception as e:
                    from config import logger
                    logger.warning(f"⚠️ Tag handler {tag.name} error: {e}")

            # Segna per la rimozione
            if tag.visibility in ("hidden", "action"):
                replacements.append((start, end, ""))

    # Step 3: Applica le sostituzioni (dalla fine all'inizio per non invalidare gli offset)
    replacements.sort(key=lambda x: -x[0])
    for start, end, replacement in replacements:
        processed = processed[:start] + replacement + processed[end:]

    # Step 4: Pulisci spazi multipli residui
    cleaned = re.sub(r'\s+', ' ', processed).strip()

    return cleaned, feedback


async def process_and_clean(
    text: str,
    user_id: str = "alfio_dev",
    project: Optional[str] = None,
    chat_id: Optional[int] = None,
) -> str:
    """
    Versione semplificata: processa tag, restituisce solo il testo pulito.
    I feedback vengono loggati ma non restituiti.
    Utile per chiamate API che non vogliono gestire feedback espliciti.
    """
    ctx = TagContext(user_id=user_id, project=project, chat_id=chat_id)
    cleaned, feedback = await process_all_tags(text, ctx)
    if feedback:
        from config import logger
        for msg in feedback:
            logger.info(f"📢 Tag feedback: {msg}")
    return cleaned


def register_tag(tag: TagDef) -> None:
    """Registra un tag custom a runtime (per estendibilità)."""
    _TAG_REGISTRY[tag.name.upper()] = tag
    # Ricostruisce la strip regex
    global _STRIP_ALL_RE
    _STRIP_ALL_RE = _build_strip_regex()


def build_tag_instructions() -> str:
    """
    Genera le istruzioni per il LLM su come usare i tag action.
    Usato in prompt_builder.py per comunicare i tag disponibili al modello.
    """
    lines = []
    for tag in _TAG_REGISTRY.values():
        if tag.visibility == "hidden" and tag.name not in ("MEMORY", "CONFIDENCE", "EMOTION", "THINK_DEEP"):
            continue  # Solo tag che l'LLM deve conoscere
        if tag.is_self_closing:
            lines.append(f"- `<{tag.name}/>` — {tag.description}")
        else:
            lines.append(f"- `<{tag.name}>...</{tag.name}>` — {tag.description}")
    return "\n".join(lines)
