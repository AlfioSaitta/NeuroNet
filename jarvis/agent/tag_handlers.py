"""
Tag handler functions — implementazioni dei 21 tag XML d'azione.
Estratte da agent/tags.py per modularizzazione. Tutti gli handler
usano lazy import per evitare circular dependencies.
"""

import asyncio
import logging
import os
import re
import subprocess
from typing import Optional

from agent.tags import TagDef, TagContext

logger = logging.getLogger(__name__)


async def handle_memory(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    content = content.strip()
    if not content:
        return None
    from memory.engine import save_to_memory
    ok = await save_to_memory(content, user_id=ctx.user_id, project=ctx.project)
    if ok:
        logger.info(f"🧠 MEMORY tag impresso{' ['+ctx.project+']' if ctx.project else ''}: {content[:100]}")
    return None  # Silenzioso


async def handle_schedule(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    try:
        cron_expr, prompt_text = content.split("|", 1)
        from scheduler.cron import add_cron_job
        success, jid = add_cron_job(cron_expr.strip(), prompt_text.strip(), ctx.chat_id or 0)
        if success:
            return f"⏱️ **Notifica Schedulata**: `{cron_expr.strip()}`"
        return f"❌ **Errore Schedulazione**: {jid}"
    except Exception as e:
        logger.error(f"Errore parsing SCHEDULE: {e}")
        return None


async def handle_notify_once(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    try:
        date_str, prompt_text = content.split("|", 1)
        from scheduler.cron import add_date_job
        success, jid = add_date_job(date_str.strip(), prompt_text.strip(), ctx.chat_id or 0)
        if success:
            return f"🔔 **Promemoria Impostato per**: `{date_str.strip()}`"
        return f"⚠️ **Errore Data**: {jid}"
    except Exception as e:
        logger.error(f"Errore parsing NOTIFY_ONCE: {e}")
        return None


async def handle_notify_in(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    try:
        minutes_str, prompt_text = content.split("|", 1)
        from scheduler.cron import add_relative_job
        success, jid, computed_date = add_relative_job(
            int(minutes_str.strip()), prompt_text.strip(), ctx.chat_id or 0
        )
        if success:
            return f"🔔 **Promemoria tra {minutes_str.strip()} minuti** (alle `{computed_date}`)"
        return f"⚠️ **Errore Timer**: {jid}"
    except Exception as e:
        logger.error(f"Errore parsing NOTIFY_IN: {e}")
        return None


async def handle_ssh(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    try:
        server_name, command = content.split("|", 1)
        from external.infrastructure import run_on_server
        logger.info(f"⚙️ SSH: esecuzione su {server_name.strip()}: {command.strip()}")

        async def _bg_ssh():
            out = await run_on_server(server_name.strip(), command.strip())
            import core.state as _state
            if ctx.chat_id and _state.telegram_app and _state.telegram_app.bot:
                await _state.telegram_app.bot.send_message(
                    chat_id=ctx.chat_id,
                    text=f"```bash\n{out}\n```",
                    parse_mode="Markdown"
                )
        import core.state as state
        task = asyncio.create_task(_bg_ssh())
        state.background_tasks.add(task)
        task.add_done_callback(state.background_tasks.discard)

        return f"⚙️ **Esecuzione SSH su `{server_name.strip()}` avviata...**"
    except Exception as e:
        logger.error(f"Errore parsing SSH: {e}")
        return None


async def handle_todo_add(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    try:
        parts = content.split("|")
        desc = parts[0].strip()
        prio = parts[1].strip() if len(parts) > 1 else "media"
        dead = parts[2].strip() if len(parts) > 2 else "nessuna"
        task_type = parts[3].strip().lower() if len(parts) > 3 else "personale"
        from scheduler.tasks import add_todo
        tid = add_todo(desc, prio, dead, task_type, ctx.user_id)
        type_label = "Progetto" if task_type == "progetto" else "Personale"
        return f"📝 **Task Aggiunto ({type_label})**: [{tid}] _{desc}_ (Prio: {prio}, Scad: {dead})"
    except Exception as e:
        logger.error(f"Errore parsing TODO_ADD: {e}")
        return None


async def handle_todo_done(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    try:
        tid = content.strip()
        from scheduler.tasks import mark_done
        success = mark_done(tid, ctx.user_id)
        if success:
            return f"✅ **Task Completato**: [{tid}]"
        return f"⚠️ **Errore**: Task [{tid}] non trovato o non autorizzato."
    except Exception as e:
        logger.error(f"Errore parsing TODO_DONE: {e}")
        return None


async def handle_web(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<WEB>query di ricerca</WEB> — Esegue una ricerca web."""
    query = content.strip()
    if not query:
        return None
    from rag.web_search import perform_web_search_and_crawl
    logger.info(f"🌐 WEB tag: ricerca '{query[:80]}...'")
    results, _ = await perform_web_search_and_crawl(query, force=True)
    if results and results != "Nessun risultato online.":
        return f"🌐 **Risultati Web per**: _{query}_\n\n{results[:1500]}"
    return f"🌐 **Nessun risultato web** per: _{query}_"


async def handle_file(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<FILE>path/to/file</FILE> — Legge e include contenuto file."""
    path = content.strip()
    if not path:
        return None
    from core.config import DOC_DIR
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


async def handle_emotion(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<EMOTION>stato</EMOTION> — Imposta stato emotivo (per UI)."""
    emotion = content.strip().lower()
    if not emotion:
        return None
    import core.state as gstate
    gstate.last_emotion = emotion
    logger.info(f"🎭 EMOTION tag: {emotion}")
    return None


async def handle_think_deep(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<THINK_DEEP/> — Attiva modalità ragionamento approfondito."""
    import core.state as gstate
    gstate.deepthink_mode = True
    logger.info(f"🧠 THINK_DEEP attivato")
    return None


async def handle_cache_clear(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<CACHE_CLEAR/> — Resetta la cache semantica."""
    from rag.cache import semantic_cache_clear
    try:
        await semantic_cache_clear()
        return "🗑️ **Cache semantica resettata**."
    except Exception as e:
        logger.warning(f"Errore CACHE_CLEAR: {e}")
        return "⚠️ **Errore reset cache**."


async def handle_confidence(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<CONFIDENCE>0.95</CONFIDENCE> — Salva metadato di confidenza (invisibile)."""
    try:
        score = float(content.strip())
        import core.state as gstate
        gstate.last_confidence = score
    except ValueError:
        pass
    return None


async def handle_ask(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<ASK>domanda</ASK> — Il LLM fa una domanda all'utente."""
    question = content.strip()
    if not question:
        return None
    import core.state as gstate
    if not hasattr(gstate, 'pending_questions'):
        gstate.pending_questions = []
    gstate.pending_questions.append(question)
    logger.info(f"❓ ASK tag: {question[:100]}")
    return f"❓ **{question}**"


async def handle_rag(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<RAG>project_name</RAG> — Forza RAG su progetto specifico."""
    project = content.strip()
    if not project:
        return None
    import core.state as gstate
    gstate.forced_rag_project = project
    logger.info(f"📁 RAG tag: forzato progetto '{project}'")
    return f"📁 **RAG focalizzato su**: `{project}`"


async def handle_summary(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<SUMMARY target="user_id">testo</SUMMARY> — Salva riepilogo cross-user."""
    m = re.match(r'^\s*target\s*=\s*"([^"]+)"\s*>\s*(.*)$', content, re.DOTALL)
    if not m:
        return None
    target_user = m.group(1).strip()
    summary_text = m.group(2).strip()
    if not summary_text:
        return None
    from memory.engine import save_to_memory
    ok = await save_to_memory(summary_text, user_id=target_user, project=ctx.project)
    if ok:
        logger.info(f"📋 SUMMARY tag: riepilogo salvato per user '{target_user}'")
        return f"📋 **Riepilogo salvato** per `{target_user}`."
    return None


async def handle_branch(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<BRANCH>project|branch</BRANCH> — Cambia branch git."""
    try:
        parts = content.split("|", 1)
        project_name = parts[0].strip()
        branch_name = parts[1].strip() if len(parts) > 1 else parts[0].strip()
        from core.config import DOC_DIR
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


async def handle_commit(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<COMMIT>message</COMMIT> — Committa modifiche locali."""
    msg = content.strip()
    if not msg:
        return None
    from core.config import DOC_DIR
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


async def handle_exec(tag: TagDef, content: str, ctx: TagContext) -> Optional[str]:
    """<EXEC>timeout_sec|comando</EXEC> — Esegue comando shell (safe-mode)."""
    try:
        parts = content.split("|", 1)
        timeout = int(parts[0].strip())
        command = parts[1].strip() if len(parts) > 1 else parts[0].strip()
    except (ValueError, IndexError):
        timeout = 30
        command = content.strip()
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
