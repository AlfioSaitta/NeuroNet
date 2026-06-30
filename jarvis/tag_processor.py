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
#
# Ora con FAMILY_MAP per filtrare pattern per famiglia modello.
# Se model_family è noto (da MODEL_PROFILE), si applicano solo
# i pattern rilevanti, riducendo falsi positivi.
THINKING_PATTERNS: list[tuple[str, str, str]] = [
    # (pattern, replacement, model_family_tag)
    # model_family_tag: "all" = sempre, altrimenti famiglia specifica (qwen, gemma, deepseek, llama, mistral, phi, command-r)
    # NOTA: usare SEMPRE raw strings (r"...") per evitare che | venga interpretato
    # come alternanza regex invece di pipe letterale.
    
    # ── Qwen / QwQ ──
    # Qwen: <|im_start|>...<|im_end|> blocks (chatml leftover)
    (r'<\|im_start\|>.*?<\|im_end\|>', "", "qwen"),
    # Qwen: <|im_start|> or <|im_end|> isolated
    (r'<\|im_start\|>|<\|im_end\|>', "", "qwen"),
    # Qwen thinking: <|think|>...</think> or <|think|>...<|/think|>
    (r'(?s)<\|think\|>.*?(?:</?think>|<\|think\|>)\s*', "", "qwen"),
    # Qwen tool_call tags
    (r'(?s)<\|tool_call\|>.*?<\|tool_call\|>\s*', "", "qwen"),
    
    # ── DeepSeek ──
    # DeepSeek: <|think|>...</end> or <|think|>...<|end|>
    (r'(?s)<\|think\|>.*?(?:</?end>?|<\|end\|>)\s*', "", "deepseek"),
    # DeepSeek R1: <|reflect|>...</|reflect|> and <|plan|>...</|plan|>
    (r'(?s)<\|reflect\|>.*?</\|reflect\|>\s*', "", "deepseek"),
    (r'(?s)<\|plan\|>.*?</\|plan\|>\s*', "", "deepseek"),
    # DeepSeek: ```thought ... ```
    (r'(?s)```thought\s*.*?```\s*', "", "deepseek"),
    
    # ── Gemma ──
    # Gemma 4: <|channel>thought\n...\n<channel|>
    (r'<\|channel>thought\s*.*?<channel\|>\s*', "", "gemma"),
    (r'<\|?channel\|?>', "", "gemma"),
    # Gemma: <|think|>...</end> or <|think|>...<|end|>
    (r'(?s)<\|think\|>.*?(?:</?end>?|<\|end\|>)\s*', "", "gemma"),
    
    # ── Mistral / Codestral ──
    (r'(?s)\[THINK\].*?\[/THINK\]\s*', "", "mistral"),
    (r'(?s)```reasoning\s*.*?```\s*', "", "mistral"),
    
    # ── Command R+ (Cohere) ──
    (r'(?s)<results>.*?</results>\s*', "", "command-r"),
    
    # ── Phi ──
    (r'(?s)```think\s*.*?```\s*', "", "phi"),
    
    # ── Applicati SEMPRE (cross-model) ──
    # Residual ChatML tags (safety net per chat_format=None)
    (r'<\|im_start\|>.*?<\|im_end\|>', "", "all"),
    (r'<\|im_start\|>|<\|im_end\|>', "", "all"),
    # Solar Pro: [ANALYSIS]...[/ANALYSIS]
    (r'(?s)\[ANALYSIS\].*?\[/ANALYSIS\]\s*', "", "all"),
    # Harmony: <|start|>...<|end|> blocks
    (r'(?s)<\|start\|>.*?<\|end\|>\s*', "", "all"),
    # Text prefixes: "thought:", "thinking:", "reasoning:", "reflection:", "analysis:"
    (r'(?i)^\s*(?:thought|thinking|reasoning|reflection|analysis):.*?\n\s*', "", "all"),
    # Step-by-step numbered reasoning prefixes
    (r'(?i)^\s*step\s+\d+[:.].*?\n\s*', "", "all"),
    # Residual newlines
    (r'\n{3,}', "\n\n", "all"),
]

# Compiled cache: pre-compila tutti i pattern
_THINKING_RE: list[tuple[re.Pattern, str, str]] = [
    (re.compile(p, re.DOTALL), r, f) for p, r, f in THINKING_PATTERNS
]


def strip_thinking_blocks(text: str, model_family: str = "all") -> str:
    """
    Rimuove dal testo i blocchi di reasoning/thinking.
    
    Se model_family è specificato (es. "qwen", "gemma"), applica solo
    i pattern rilevanti per quella famiglia + quelli "all".
    Con "all" (default) applica TUTTI i pattern (comportamento legacy).
    """
    for pattern, replacement, tag in _THINKING_RE:
        if model_family != "all" and tag != "all" and tag != model_family:
            continue
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
    from rag_cache import semantic_cache_clear
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
    m = re.match(r'^\s*target\s*=\s*"([^"]+)"\s*>\s*(.*)$', content, re.DOTALL)
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


def strip_action_tags(text: str) -> str:
    """
    Come strip_all_tags ma SENZA .strip() — preserva spazi e whitespace.
    Utile nello streaming dove un chunk può essere solo " " (spazio tra parole).
    """
    return _STRIP_ALL_RE.sub("", text)


class TagSafeStream:
    """
    Stream-safe action tag stripper — previene la fuga di tag XML incompleti
    quando il LLM genera token uno alla volta.

    Il problema: strip_action_tags() usa una regex che matcha solo tag COMPLETI
    (<TAG>...</TAG>). Nello streaming, il tag è quasi sempre spalmato su più chunk:
    ``<NOTIFY_ONCE>`` in un chunk e ``</NOTIFY_ONCE>`` in un altro. La regex non
    matcha mai, e i frammenti di tag grezzi arrivano al client.

    Soluzione: mantiene uno stato ``_in_tag`` attraverso i chunk. Quando rileva
    l'apertura di un tag action noto (es. <NOTIFY_ONCE), trattiene TUTTO in un
    buffer fino a quando non arriva il tag di chiusura. Solo il contenuto che NON
    fa parte di tag viene yieldato.

    Uso:
        safe = TagSafeStream()
        for chunk in stream_dal_llm:
            safe_chunk = safe.process(chunk)
            if safe_chunk:
                yield safe_chunk
        finale = safe.flush()
        if finale:
            yield finale
    """

    def __init__(self):
        self._buffer = ""
        # Stato per tag con coppia apertura/chiusura: in attesa di </TAG>
        self._in_tag: bool = False
        self._tag_name: Optional[str] = None
        # Stato per tag auto-chiudenti: in attesa di /> o >
        self._sc_pending: bool = False
        self._sc_name: Optional[str] = None
        self._sc_pattern: Optional[re.Pattern] = None

        # Pre-costruisce strutture di lookup (una volta sola)
        self._openings: list[tuple[str, str, bool]] = []  # (prefix_upper, name, is_self_closing)
        self._closings: dict[str, str] = {}               # name -> </TAG>_upper
        self._sc_patterns: dict[str, re.Pattern] = {}     # name -> regex

        for name, td in _TAG_REGISTRY.items():
            if td.visibility in ("hidden", "action"):
                self._openings.append((f"<{name}".upper(), name, td.is_self_closing))
                if td.is_self_closing:
                    self._sc_patterns[name] = re.compile(rf"<{name}\s*/?\s*>", re.IGNORECASE)
                else:
                    self._closings[name] = f"</{name}>".upper()

    def process(self, chunk: str) -> str:
        """Processa un chunk dello stream, restituisce solo testo safe (senza tag)."""
        self._buffer += chunk
        return self._drain()

    def flush(self) -> str:
        """Svuota il buffer residuo (solo contenuto NON dentro un tag)."""
        if not self._in_tag and not self._sc_pending and self._buffer:
            result = self._buffer
            self._buffer = ""
            return result
        return ""

    # ── helper interno ──

    def _drain(self) -> str:
        """Cuore dello state machine: processa il buffer finché possibile."""
        output: list[str] = []

        while self._buffer:
            # ── Caso 1: stiamo aspettando la chiusura di un tag normale ──
            if self._in_tag:
                closing = self._closings.get(self._tag_name or "")
                if closing:
                    idx = self._buffer.upper().find(closing)
                    if idx >= 0:
                        end = idx + len(closing)
                        self._buffer = self._buffer[end:]
                        self._in_tag = False
                        self._tag_name = None
                        continue  # Rivela il buffer per altri tag
                break  # Non ancora arrivato </TAG>

            # ── Caso 2: stiamo aspettando la chiusura di un tag auto-chiudente ──
            if self._sc_pending:
                pat = self._sc_pattern
                if pat:
                    m = pat.match(self._buffer)
                    if m:
                        self._buffer = self._buffer[m.end():]
                        self._sc_pending = False
                        self._sc_name = None
                        self._sc_pattern = None
                        continue
                break  # Non ancora completo

            # ── Caso 3: non dentro un tag, cerca la prossima apertura ──
            buf_upper = self._buffer.upper()
            best_pos: Optional[int] = None
            best_name: Optional[str] = None
            best_sc = False

            for prefix, name, is_sc in self._openings:
                pos = buf_upper.find(prefix)
                if pos >= 0 and (best_pos is None or pos < best_pos):
                    best_pos = pos
                    best_name = name
                    best_sc = is_sc

            if best_pos is None:
                # Nessun tag trovato — yielda tutto
                output.append(self._buffer)
                self._buffer = ""
            else:
                # Testo safe prima del tag
                if best_pos > 0:
                    output.append(self._buffer[:best_pos])

                # Taglia via il safe prefix, tieni il tag in buffer
                self._buffer = self._buffer[best_pos:]

                if best_sc:
                    pat = self._sc_patterns.get(best_name or "")
                    if pat:
                        m = pat.match(self._buffer)
                        if m:
                            # Tag auto-chiudente completo — stripalo
                            self._buffer = self._buffer[m.end():]
                            continue
                    # Deve aspettare altro contenuto
                    self._sc_pending = True
                    self._sc_name = best_name
                    self._sc_pattern = pat
                    break
                else:
                    closing = self._closings.get(best_name or "")
                    if closing:
                        idx = self._buffer.upper().find(closing)
                        if idx >= 0:
                            # Tag completo in un colpo solo — stripalo
                            end = idx + len(closing)
                            self._buffer = self._buffer[end:]
                            continue
                    # Deve aspettare </TAG>
                    self._in_tag = True
                    self._tag_name = best_name
                    break

        return "".join(output)


# ======================================================================
# Core tag processing
# ======================================================================


async def process_all_tags(
    text: str,
    context: Optional[TagContext] = None,
    model_family: str = "all",
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
        model_family: Famiglia modello per filtrare thinking patterns
                      ("all" = tutti, "qwen", "gemma", "deepseek", ...)

    Returns:
        (cleaned_text, feedback_messages)
    """
    if not text:
        return "", []

    if context is None:
        context = TagContext()

    # Step 0: Chiudi tag orfani (es. <MEMORY> senza </MEMORY> per troncamento)
    text = close_orphaned_tags(text)

    # Step 1: Pulisci blocchi di thinking/reasoning (ora model-aware)
    text = strip_thinking_blocks(text, model_family=model_family)

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
    model_family: str = "all",
) -> str:
    """
    Versione semplificata: processa tag, restituisce solo il testo pulito.
    I feedback vengono loggati ma non restituiti.
    Utile per chiamate API che non vogliono gestire feedback espliciti.
    """
    ctx = TagContext(user_id=user_id, project=project, chat_id=chat_id)
    cleaned, feedback = await process_all_tags(text, ctx, model_family=model_family)
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


def close_orphaned_tags(text: str) -> str:
    """
    Pre-processing: rileva tag aperti ma non chiusi alla FINE del testo
    e li chiude automaticamente, permettendo a process_all_tags() di
    processarli correttamente.

    Esempio:
      Input: "...ecco le info<MEMORY>fatto importante"
      Output: "...ecco le info<MEMORY>fatto importante</MEMORY>"

    Gestisce anche tag con contenuto che include < (es. codice dentro tag):
      Input: "...<MEMORY>il codice if a < b: ..."
      Output: "...<MEMORY>il codice if a < b: ...</MEMORY>"

    Funziona su:
      - Tag in coda al testo (troncamento da max_tokens)
      - Tag nel mezzo del testo che sono rimasti aperti
    """
    tag_names = [
        t.name for t in _TAG_REGISTRY.values()
        if not t.is_self_closing
    ]
    if not tag_names:
        return text

    text = text.rstrip()
    
    for name in sorted(tag_names, key=len, reverse=True):
        # Pattern più robusto: cerca <TAG>... che non ha </TAG> dopo
        # Usa un approccio stack-based per gestire tag annidati
        pattern_open = re.compile(rf"<{name}\s*>", re.IGNORECASE)
        pattern_close = re.compile(rf"</{name}\s*>", re.IGNORECASE)
        
        opens = list(pattern_open.finditer(text))
        closes = list(pattern_close.finditer(text))
        
        if len(opens) > len(closes):
            # Ci sono tag aperti non chiusi
            if not text.rstrip().endswith(f"</{name}>"):
                text += f"</{name}>"
                
            # Per sicurezza, chiudi anche tag annidati non bilanciati
            # (es. <MEMORY><SCHEDULE>...</MEMORY> senza </SCHEDULE>)
            for inner_name in tag_names:
                if inner_name == name:
                    continue
                inner_open = len(list(re.compile(rf"<{inner_name}\s*>", re.IGNORECASE).finditer(text)))
                inner_close = len(list(re.compile(rf"</{inner_name}\s*>", re.IGNORECASE).finditer(text)))
                if inner_open > inner_close:
                    text += f"</{inner_name}>"

    return text


def strip_orphaned_tags(text: str) -> str:
    """
    Rimuove tag d'azione rimasti aperti/orfani (es. <MEMORY> senza </MEMORY>).
    RECUPERA il contenuto del tag invece di buttarlo via.

    Strategia migliorata con stack:
    1. Tag completi (<TAG>...</TAG>) → estrae il contenuto, rimuove i tag
    2. Tag opening orfani (<TAG>... senza chiusura) → rimuove il tag, tiene il contenuto
    3. Tag closing orfani (</TAG> senza apertura) → rimuove
    4. Gestisce annidamenti: <TAG1><TAG2>...</TAG2>...</TAG1>
    """
    tag_names = sorted(
        [t.name for t in _TAG_REGISTRY.values()
         if not t.is_self_closing and t.visibility in ("hidden", "action")],
        key=len, reverse=True  # Più lunghi prima per match corretto
    )
    if not tag_names:
        return text

    # 1. Approccio stack-based per rimuovere tag bilanciati
    #    Processa ricorsivamente finché non ci sono più tag completi
    prev_text = ""
    while prev_text != text:
        prev_text = text
        for name in tag_names:
            complete = re.compile(
                rf"<{name}\s*>(.*?)</{name}\s*>", re.DOTALL | re.IGNORECASE
            )
            text = complete.sub(r"\1", text)

    # 2. Rimuovi tag opening orfani rimasti
    for name in tag_names:
        orphan_open = re.compile(rf"<{name}\s*>", re.IGNORECASE)
        text = orphan_open.sub("", text)

    # 3. Rimuovi tag closing orfani rimasti
    for name in tag_names:
        orphan_close = re.compile(rf"</{name}\s*>", re.IGNORECASE)
        text = orphan_close.sub("", text)

    return text.strip()


def telegram_prepare_markdown(text: str) -> str:
    """
    Preparazione markdown comune: conversioni che NON dipendono dalla versione Telegram.
    Da chiamare UNA VOLTA sul testo completo prima dello splitting in chunk.

    Gestisce:
      - Pulisce tag orfani
      - ### heading  → *heading* (grassetto)
      - **bold**     → *bold*
      - * a inizio riga → •  (bullet, se non è grassetto/italic valido)
      - ~~strikethrough~~ rimosso (non supportato in legacy)
      - ||spoiler|| rimosso (non supportato in legacy)
    """
    # 0. Pulisci tag orfani
    text = close_orphaned_tags(text)
    text = strip_orphaned_tags(text)

    # 1. Proteggi blocchi di codice (non vanno trasformati)
    code_blocks: dict[str, str] = {}
    def _protect_code(m: re.Match) -> str:
        placeholder = f"__CODEBLOCK_{len(code_blocks)}__"
        code_blocks[placeholder] = m.group(0)
        return placeholder
    text = re.sub(r'```[\s\S]*?```', _protect_code, text)
    text = re.sub(r'(?<!`)`(?!`)([^`\n]+?)`(?!`)', _protect_code, text)

    # 2. ### heading / ## heading / # heading → *heading*
    text = re.sub(
        r'^#{1,3}\s+(.+?)\s*$',
        lambda m: f'*{m.group(1).strip()}*',
        text,
        flags=re.MULTILINE
    )

    # 3. **bold** → *bold* (standard markdown → Telegram compatibile)
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)

    # 4. ~~strikethrough~~ → rimuovi (non supportato in Telegram)
    text = re.sub(r'~~(.+?)~~', r'\1', text)

    # 5. ||spoiler|| → rimuovi (non supportato in Telegram base)
    text = re.sub(r'\|\|(.+?)\|\|', r'\1', text)

    # 6. * a inizio riga seguito da spazio → • (bullet)
    lines = text.split('\n')
    fixed = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith('* '):
            ast_count = stripped.count('*')
            rest = stripped[2:]
            if ast_count == 2 and stripped.rstrip().endswith('*') and not stripped[2:].rstrip().endswith('*'):
                fixed.append(line)
            else:
                prefix = line[:len(line) - len(stripped)]
                fixed.append(prefix + '• ' + rest)
        else:
            fixed.append(line)
    text = '\n'.join(fixed)

    # 7. Ripristina blocchi di codice
    for placeholder, original in code_blocks.items():
        text = text.replace(placeholder, original)

    return text.strip()



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
