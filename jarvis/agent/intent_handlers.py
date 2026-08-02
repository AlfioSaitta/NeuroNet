"""
Intent handlers (Fase 4): effetti collaterali degli intent funzionali.

Ogni handler è una funzione async che riceve l'IntentResult + un dict di
contesto (user_id, chat_id, project) e restituisce un messaggio di conferma
da appendere alla risposta LLM (o None se nessuna azione eseguita).

Firma UNIFICATA (Fase 4.12): `handler(result, context)` — context è un dict
con chiavi opzionali note (user_id, chat_id, project). Il dispatcher
`intent_router.dispatch()` instrada qui rispettando le soglie §4.3.

Lazy import ovunque per evitare circular dependencies con scheduler,
memory, tasks, ecc. La registrazione nella DISPATCH_TABLE avviene via
register_handlers(), chiamato dai caller (main.py, chat.py, dashboard).
"""

import logging
import os
import re
import subprocess
import time
from typing import Optional

from agent.intent_router import IntentResult

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Helper comuni (Fase 4.13-4.19)
# ──────────────────────────────────────────────

# Comandi SSH whitelist (read senza conferma, write con conferma)
_SSH_READ_COMMANDS = ("uptime", "df -h", "ps aux", "free -h")
_SSH_WRITE_COMMANDS = ("deploy", "restart", "rm")

# Variabili config da NON esporre nel ramo "get" (segreti)
_SECRET_RE = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD|PASSWD|JWT)", re.IGNORECASE)


def _git_repo_dir(ctx: dict) -> Optional[str]:
    """Repo git da usare per le operazioni: context repo_dir > project attivo > DOC_DIR.

    Il caller (main.py) può passare un `repo_dir` esplicito nel context;
    in alternativa il project attivo viene risolto via rag.engine.get_project_path;
    come ultimo fallback DOC_DIR.
    """
    direct = ctx.get("repo_dir")
    if direct and os.path.isdir(os.path.join(direct, ".git")):
        return direct
    from core.config import DOC_DIR
    proj = ctx.get("project")
    if proj:
        try:
            from rag.engine import get_project_path
            p = get_project_path(proj)
            if p and os.path.isdir(os.path.join(p, ".git")):
                return p
        except Exception:
            pass
    if DOC_DIR and os.path.isdir(os.path.join(DOC_DIR, ".git")):
        return DOC_DIR
    return None


def _run_git(repo_dir: str, *args: str) -> tuple[int, str]:
    """Esegue un comando git nel repo; ritorna (returncode, output)."""
    result = subprocess.run(
        ["git", *args], cwd=repo_dir, capture_output=True, text=True, timeout=20
    )
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    return result.returncode, output[:2000]


async def _confirm_or_pending(context: dict, action_desc: str, timeout: int = 300):
    """Richiede conferma per un'operazione write.

    - Se nel context c'è un ConfirmationManager (Telegram/API), lo usa.
    - Se assente, il default è AutoProvider → approva (coerente coi tag
      handler esistenti che eseguono direttamente).
    - Con provider token-based solleva PendingConfirmation → ritorna la
      stringa CONFIRM_REQ da mostrare all'utente.

    Returns: True (approvato) | False (rifiutato) | str (CONFIRM_REQ pendente).
    """
    mgr = context.get("confirmation_mgr")
    if mgr is None:
        from agent.confirmation import ConfirmationManager
        mgr = ConfirmationManager()
    try:
        return await mgr.ask(action_desc, timeout=timeout)
    except Exception as pc:  # PendingConfirmation (token-based)
        from agent.confirmation import PendingConfirmation
        if isinstance(pc, PendingConfirmation):
            return f"CONFIRM_REQ:{pc.token}:{pc.action_desc}"
        logger.error(f"conferma error: {pc}")
        return False


async def handle_schedule(result: IntentResult, context: Optional[dict] = None) -> Optional[str]:
    """'ricordami tra 30 minuti di X' → add_relative_job(minutes, prompt, chat_id).

    Slot attesi (SLOT_EXTRACTORS["schedule"]): duration_min (minuti),
    message (prompt del promemoria). Context: chat_id (int, default 0).

    Ritorna il messaggio di conferma da appendere alla risposta, o None
    se gli slot non sono validi.
    """
    if result.intent != "schedule":
        return None
    ctx = context or {}
    chat_id = ctx.get("chat_id", 0)
    slots = result.slots or {}
    duration_min = slots.get("duration_min")
    message = (slots.get("message") or "").strip()
    if not duration_min or not message:
        logger.debug(
            "schedule: slot mancanti (duration_min=%r, message=%r)",
            duration_min, message,
        )
        return None
    try:
        from scheduler.cron import add_relative_job
        success, jid, computed_date = add_relative_job(
            int(duration_min), message, chat_id
        )
        if success:
            logger.info(
                "⏱️ schedule intent → job %s: '%s' tra %s min (chat_id=%s)",
                jid, message, duration_min, chat_id,
            )
            return (
                f"🔔 **Promemoria impostato**: tra {duration_min} minuti — "
                f"\"{message}\" (alle `{computed_date}`)"
            )
        logger.warning("schedule: add_relative_job fallito: %s", jid)
    except Exception as e:
        logger.error("schedule: add_relative_job error: %s", e)
    return None


async def handle_memory(result: IntentResult, context: Optional[dict] = None) -> Optional[str]:
    """'ricorda che X' → save_to_memory(content) / 'che ricordi su X?' → retrieve.

    Slot attesi (SLOT_EXTRACTORS["memory"]): action ("save"|"retrieve"),
    content (fatto da memorizzare). Context: user_id (str, default "alfio_dev"),
    project (str|None). Il salvataggio segue il pattern del tag <MEMORY>
    (memory/engine.py:save_to_memory con filtro user+project).

    - action == "save" → salva il fatto + conferma testuale.
    - action == "retrieve" → nessuna azione qui: la ricerca filtrata è già
      fatta dal context gathering di build_omniscient_prompt (prompt.py);
      la risposta contestuale arriva dal LLM. Ritorna None.
    """
    if result.intent != "memory":
        return None
    ctx = context or {}
    user_id = ctx.get("user_id", "alfio_dev")
    project = ctx.get("project")
    slots = result.slots or {}
    action = slots.get("action") or "save"
    if action == "retrieve":
        return None
    content = (slots.get("content") or "").strip()
    if not content:
        logger.debug("memory: slot content assente (slots=%s)", slots)
        return None
    try:
        from memory.engine import save_to_memory
        ok = await save_to_memory(content, user_id=user_id, project=project)
        if ok:
            logger.info(
                "🧠 memory intent → salvato (%s chars, user=%s, project=%s)",
                len(content), user_id, project,
            )
            return f"🧠 **Ricordato**: {content}"
        logger.warning("memory: save_to_memory fallito (user=%s)", user_id)
    except Exception as e:
        logger.error("memory: save_to_memory error: %s", e)
    return None


async def handle_git(result: IntentResult, context: Optional[dict] = None) -> Optional[str]:
    """'che branch siamo?' / 'committa con messaggio fix' → operazioni git.

    Slot attesi (SLOT_EXTRACTORS["git"]): operation (status|log|commit|branch|merge),
    message (messaggio commit). Context: project (per _git_repo_dir).

    - status/log → READ diretto (nessun LLM oltre al router), output formattato.
    - commit/branch/merge/push/pull → WRITE con _confirm_or_pending (timeout 300s).
    - Nessun repo git → messaggio di avviso.
    """
    if result.intent != "git":
        return None
    ctx = context or {}
    slots = result.slots or {}
    operation = slots.get("operation") or "status"
    repo_dir = _git_repo_dir(ctx)
    if not repo_dir:
        return "⚠️ **Nessun repository git** trovato (progetto attivo o DOC_DIR)."

    # ── READ: status / log ──
    if operation in ("status", "log"):
        if operation == "status":
            rc, out = _run_git(repo_dir, "status", "--short", "--branch")
            label = "stato del repo"
        else:
            rc, out = _run_git(repo_dir, "log", "--oneline", "-20")
            label = "ultimi commit"
        if rc == 0:
            logger.info("🪪 git intent → %s su %s", operation, repo_dir)
            return f"```\n{out or '(nessuna modifica)'}\n```\n🪪 **{label}** (`{repo_dir}`)"
        return f"⚠️ **Errore git {operation}**: {out[:300]}"

    # ── WRITE: commit / branch / merge ──
    message = (slots.get("message") or "").strip()
    if operation == "commit" and not message:
        message = "auto-commit (intent router)"
    confirmed = await _confirm_or_pending(ctx, f"git {operation}: {message or ''}")
    if isinstance(confirmed, str):
        return confirmed  # CONFIRM_REQ pendente
    if not confirmed:
        return "⛔ **Operazione git annullata** (conferma negata)."

    try:
        if operation == "commit":
            rc, out = _run_git(repo_dir, "add", "-A")
            if rc != 0:
                return f"⚠️ **Errore git add**: {out[:300]}"
            rc, out = _run_git(repo_dir, "commit", "-m", message)
            if rc == 0:
                logger.info("✅ git intent → commit '%s' su %s", message, repo_dir)
                return f"✅ **Commit creato**: `{message}`"
            return f"⚠️ **Errore commit**: {out[:300]}"
        if operation == "branch":
            branch = message or "main"
            rc, out = _run_git(repo_dir, "checkout", branch)
            if rc == 0:
                return f"🔀 **Branch cambiato**: `{branch}`"
            return f"⚠️ **Errore branch**: {out[:300]}"
        if operation == "merge":
            cmd = "pull" if "pull" in message.lower() else "push" if "push" in message.lower() else "merge"
            rc, out = _run_git(repo_dir, cmd)
            if rc == 0:
                return f"🔄 **git {cmd} completato**."
            return f"⚠️ **Errore git {cmd}**: {out[:300]}"
    except Exception as e:
        logger.error("git: handler error: %s", e)
    return None


async def handle_ssh(result: IntentResult, context: Optional[dict] = None) -> Optional[str]:
    """'uptime su produzione' / 'deploy su debian' → comando SSH whitelist.

    Slot attesi (SLOT_EXTRACTORS["ssh"]): host (nome server), command.
    Riusa l'infrastruttura ESISTENTE external/infrastructure.py:
    load_infra() per la mappa host + run_on_server(server_name, command)
    (asyncssh, known_hosts=None). NIENTE SSH_HOSTS in .env.

    - READ (uptime, df -h, ps aux, free -h): esecuzione diretta.
    - WRITE (deploy, restart, rm): whitelist + _confirm_or_pending (300s).
    - Comando fuori whitelist: MAI eseguito → None (nessuna azione).
    """
    if result.intent != "ssh":
        return None
    ctx = context or {}
    slots = result.slots or {}
    host = (slots.get("host") or "").strip()
    command = (slots.get("command") or "").strip()
    if not host or not command:
        logger.debug("ssh: slot mancanti (host=%r, command=%r)", host, command)
        return None

    # ── 1. Whitelist PRIMA di tutto (difensivo: mai eseguire fuori whitelist) ──
    cmd_head = command.split()[0].lower()
    is_write = cmd_head in _SSH_WRITE_COMMANDS
    is_read = (
        command.lower() in _SSH_READ_COMMANDS
        or any(command.lower().startswith(c) for c in _SSH_READ_COMMANDS)
    )
    if not is_write and not is_read:
        logger.warning("ssh: comando fuori whitelist, non eseguito: %r", command)
        return None

    # ── 2. Host deve esistere nell'infra ──
    try:
        from external.infrastructure import load_infra
        infra = load_infra()
        if host not in infra:
            known = ", ".join(sorted(infra.keys())) if infra else "nessuno"
            return f"⚠️ **Server `{host}` non configurato** (infra: {known})."
    except Exception as e:
        logger.error("ssh: load_infra error: %s", e)
        return None

    # ── 3. Write → conferma obbligatoria ──
    if is_write:
        confirmed = await _confirm_or_pending(ctx, f"SSH {host}: {command}")
        if isinstance(confirmed, str):
            return confirmed  # CONFIRM_REQ pendente
        if not confirmed:
            return "⛔ **Comando SSH annullato** (conferma negata)."

    try:
        from external.infrastructure import run_on_server
        out = await run_on_server(host, command)
        logger.info("⚙️ ssh intent → '%s' su %s", command, host)
        if out.startswith("[SSH Output") or out.startswith("[SSH Error"):
            return f"```bash\n{out}\n```"
        return f"⚠️ **SSH {host}**: {out[:300]}"
    except Exception as e:
        logger.error("ssh: run_on_server error: %s", e)
    return None


async def handle_transcribe(result: IntentResult, context: Optional[dict] = None) -> Optional[str]:
    """'trascrivi questo audio' → faster-whisper (stesso pattern di /v1/audio).

    Slot attesi (SLOT_EXTRACTORS["transcribe"]): source (audio|voice), lang.
    L'audio arriva dal CONTESTO, non dal testo: context["audio_path"] (str)
    o context["audio_bytes"] (bytes) + context["filename"]. Nessuna fonte
    audio → None (il LLM risponde naturalmente).
    """
    if result.intent != "transcribe":
        return None
    ctx = context or {}
    slots = result.slots or {}
    audio_path = ctx.get("audio_path")
    audio_bytes = ctx.get("audio_bytes")
    if audio_path:
        try:
            with open(audio_path, "rb") as f:
                audio_bytes = f.read()
            filename = audio_path.rsplit("/", 1)[-1]
        except Exception as e:
            logger.error("transcribe: lettura audio_path error: %s", e)
            return None
    if not audio_bytes:
        logger.debug("transcribe: nessuna fonte audio nel contesto (source=%r)", slots.get("source"))
        return None
    filename = ctx.get("filename") or "audio.webm"
    lang = slots.get("lang") or ctx.get("lang")
    try:
        from openai_api.audio import _transcribe_audio
        text = await _transcribe_audio(
            audio_bytes, filename, language=lang, response_format="text"
        )
        if text and str(text).strip():
            logger.info("🎙️ transcribe intent → %d chars (%s)", len(str(text)), filename)
            return f"🎙️ **Trascrizione**: {text}"
        logger.warning("transcribe: output vuoto da faster-whisper")
    except Exception as e:
        logger.error("transcribe: _transcribe_audio error: %s", e)
    return None


async def handle_fetch(result: IntentResult, context: Optional[dict] = None) -> Optional[str]:
    """'che c'è su questa pagina?' + URL → Crawl4AI, output markdown pulito.

    Slot attesi (SLOT_EXTRACTORS["fetch"]): url, format (markdown|html|testo).
    Riusa il pattern Crawl4AI di rag/web_search.py (CRAWL4AI_HOST + bearer).
    Se l'URL non è valido o il crawl fallisce → None (fallback a web search
    gestito dal routing/LLM, nessuna azione server-side).
    """
    if result.intent != "fetch":
        return None
    ctx = context or {}
    slots = result.slots or {}
    url = (slots.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        logger.debug("fetch: URL non valido: %r", url)
        return None
    try:
        import core.state as state
        from core.config import CRAWL4AI_HOST, CRAWL4AI_API_TOKEN
        headers = {}
        if CRAWL4AI_API_TOKEN:
            headers["Authorization"] = f"Bearer {CRAWL4AI_API_TOKEN}"
        res = await state.http_client.post(
            f"{CRAWL4AI_HOST}/crawl",
            json={"urls": [url]},
            headers=headers,
            timeout=20.0,
        )
        if res.status_code != 200:
            logger.warning("fetch: Crawl4AI status %s per %s", res.status_code, url)
            return None
        data = res.json()
        md = ""
        if "results" in data and data["results"]:
            md_raw = data["results"][0].get("markdown", "")
            if isinstance(md_raw, dict):
                md = md_raw.get("fit_markdown") or md_raw.get("raw_markdown", "")
            else:
                md = str(md_raw or "")
        md = str(md).strip()[:3000]
        if md:
            logger.info("🌐 fetch intent → %d chars da %s", len(md), url)
            return f"📄 **Contenuto di {url}**:\n\n{md}"
        logger.warning("fetch: nessun markdown da %s", url)
    except Exception as e:
        logger.warning("fetch: Crawl4AI error per %s: %s", url, e)
    return None


async def handle_translate(result: IntentResult, context: Optional[dict] = None) -> Optional[str]:
    """'traduci in inglese: buongiorno mondo' → traduzione via LLM (nessun side effect).

    Slot attesi (SLOT_EXTRACTORS["translate"]): target_lang, text.
    La traduzione è prodotta dal LLM nella risposta (branch translate = risposta
    diretta, zero context gathering). L'handler non esegue azioni server-side:
    logga l'intento e ritorna None. Se target_lang manca, il LLM lo chiede.
    """
    if result.intent != "translate":
        return None
    ctx = context or {}
    slots = result.slots or {}
    target_lang = slots.get("target_lang")
    text = (slots.get("text") or "").strip()
    logger.info(
        "🔤 translate intent → %s (%d chars)%s",
        target_lang or "?", len(text), f" su {ctx.get('project')}" if ctx.get("project") else "",
    )
    return None


async def handle_config(result: IntentResult, context: Optional[dict] = None) -> Optional[str]:
    """'imposta LLAMA_MODEL_PATH su X' / 'mostra le impostazioni' → settings.

    Slot attesi (SLOT_EXTRACTORS["config"]): action (set|get|reset), key, value.
    - get → read-only da os.environ; MAI esporre segreti (filtro _SECRET_RE).
    - set/reset → _persist_env() (settings_manager.py, scrittura atomica) + conferma.
    """
    if result.intent != "config":
        return None
    ctx = context or {}
    slots = result.slots or {}
    action = slots.get("action") or "get"
    key = (slots.get("key") or "").strip().upper()
    value = (slots.get("value") or "").strip()

    if action == "get":
        if not key:
            # "mostra le impostazioni" senza chiave → solo le variabili non-segrete note.
            public = [
                (k, v) for k, v in os.environ.items()
                if k.isupper() and not _SECRET_RE.search(k)
            ]
            if not public:
                return None
            lines = [f"{k}={v}" for k, v in sorted(public)[:25]]
            return "⚙️ **Impostazioni** (non-segrete):\n```\n" + "\n".join(lines) + "\n```"
        if _SECRET_RE.search(key):
            return f"🔒 **`{key}` è una variabile segreta** — valore non esposto."
        current = os.environ.get(key, "(non impostata)")
        logger.info("⚙️ config intent → get %s", key)
        return f"⚙️ **{key}** = `{current}`"

    # ── set / reset: WRITE con conferma ──
    if not key:
        return None
    if action == "reset":
        value = ""
    confirmed = await _confirm_or_pending(
        ctx, f"config {action}: {key}" + (f" = {value}" if value else "")
    )
    if isinstance(confirmed, str):
        return confirmed  # CONFIRM_REQ pendente
    if not confirmed:
        return "⛔ **Modifica configurazione annullata** (conferma negata)."
    try:
        from admin.settings_manager import _persist_env
        ok = _persist_env(key, value)
        if ok:
            logger.info("⚙️ config intent → %s %s=%r", action, key, value)
            if action == "reset":
                return f"♻️ **`{key}` resettata** (persistita su .env)."
            return f"⚙️ **`{key}` impostata** a `{value or '(vuoto)'}` (persistita su .env)."
        logger.warning("config: _persist_env fallito per %s", key)
    except Exception as e:
        logger.error("config: _persist_env error: %s", e)
    return None


async def handle_maintenance(result: IntentResult, context: Optional[dict] = None) -> Optional[str]:
    """'pulisci la cache' / 'reindicizza il progetto X' → manutenzione RAG.

    Slot attesi (SLOT_EXTRACTORS["maintenance"]): operation
    (cache_clear|reindex|cleanup|status). Context: project (per reindex).
    - status → read-only (uptime, richieste, trace attive).
    - cache_clear → semantic_cache_clear() (non distruttivo, diretto).
    - reindex/cleanup → operazioni distruttive: _confirm_or_pending (300s).
    """
    if result.intent != "maintenance":
        return None
    ctx = context or {}
    slots = result.slots or {}
    operation = slots.get("operation") or "status"

    if operation == "status":
        try:
            import core.state as state
            from core.config import LLAMA_MODEL_PATH
            start = getattr(state, "_start_time", None) or time.time()
            uptime_s = int(time.time() - start)
            return (
                f"🖥️ **Stato Jarvis**: uptime {uptime_s}s · "
                f"richieste totali {state.total_requests} · "
                f"trace attivi {len(state.pipeline_traces)} · "
                f"modello `{LLAMA_MODEL_PATH.rsplit('/', 1)[-1]}`"
            )
        except Exception as e:
            logger.error("maintenance: status error: %s", e)
            return None

    if operation == "cache_clear":
        try:
            from rag.cache import semantic_cache_clear
            await semantic_cache_clear()
            logger.info("🗑️ maintenance intent → cache semantica resettata")
            return "🗑️ **Cache semantica resettata**."
        except Exception as e:
            logger.warning("maintenance: semantic_cache_clear error: %s", e)
            return "⚠️ **Errore reset cache**."

    # ── reindex / cleanup: distruttivi, conferma obbligatoria ──
    confirmed = await _confirm_or_pending(ctx, f"maintenance {operation}")
    if isinstance(confirmed, str):
        return confirmed  # CONFIRM_REQ pendente
    if not confirmed:
        return "⛔ **Operazione di manutenzione annullata** (conferma negata)."
    try:
        if operation == "reindex":
            from rag.engine import get_project_path, ingest_local_documents
            proj = ctx.get("project")
            path = get_project_path(proj) if proj else None
            if not path:
                return f"⚠️ **Progetto `{proj}` non trovato** — impossibile reindicizzare."
            import asyncio
            import core.state as state
            task = asyncio.create_task(ingest_local_documents(single_project_path=path))
            state.background_tasks.add(task)
            task.add_done_callback(state.background_tasks.discard)
            logger.info("♻️ maintenance intent → reindex avviato per %s", proj)
            return f"♻️ **Re-index avviato** per `{proj}` (in background)."
        if operation == "cleanup":
            from rag.engine import get_project_path
            import core.state as state
            cols_info = await state.qdrant.get_collections()
            deleted = []
            for col in cols_info.collections:
                if not col.name.startswith("collateral_docs_"):
                    continue
                col_proj = col.name.replace("collateral_docs_", "")
                col_proj = re.sub(r"_v\d+$", "", col_proj)
                if col_proj == "default":
                    continue
                if not get_project_path(col_proj):
                    await state.qdrant.delete_collection(collection_name=col.name)
                    deleted.append(col_proj)
                    logger.info("🗑️ maintenance cleanup → collezione orfana %s eliminata", col.name)
            if deleted:
                return f"🧹 **Cleanup completato**: {len(deleted)} collezioni orfane eliminate (`{', '.join(deleted[:5])}`)."
            return "🧹 **Cleanup completato**: nessuna collezione orfana trovata."
    except Exception as e:
        logger.error("maintenance: %s error: %s", operation, e)
    return None


async def handle_task(result: IntentResult, context: Optional[dict] = None) -> Optional[str]:
    """'aggiungi un task: X' → add_todo / 'segna come fatto...' → mark_done.

    Slot attesi (SLOT_EXTRACTORS["task"]): action ("add"|"done"|"list"),
    description, priority, deadline. Context: user_id (str, default "alfio_dev").
    Reusa la logica dei tag <TODO_ADD>/<TODO_DONE> (tag_handlers.py).
    Feedback esito nel testo.

    - action == "add" → add_todo(desc, priority, deadline, "personale", user_id).
    - action == "done" → match case-insensitive sulla descrizione tra le open
      tasks dell'utente; se trovata, mark_done(tid, user_id).
    - action == "list" → nessuna azione: il LLM elenca già le task dal
      contesto (budget allocator). Ritorna None.
    """
    if result.intent != "task":
        return None
    ctx = context or {}
    user_id = ctx.get("user_id", "alfio_dev")
    slots = result.slots or {}
    action = slots.get("action") or "list"
    if action == "list":
        return None
    try:
        from scheduler.tasks import add_todo, mark_done, get_open_tasks
        if action == "add":
            desc = (slots.get("description") or "").strip()
            if not desc:
                logger.debug("task: add senza description (slots=%s)", slots)
                return None
            prio = (slots.get("priority") or "media").strip()
            dead = (slots.get("deadline") or "nessuna").strip()
            tid = add_todo(desc, prio, dead, "personale", user_id)
            logger.info(
                "📝 task intent → add_todo %s: '%s' (prio=%s, deadline=%s, user=%s)",
                tid, desc, prio, dead, user_id,
            )
            return f"📝 **Task Aggiunto**: [{tid}] _{desc}_ (Prio: {prio}, Scad: {dead})"
        if action == "done":
            desc = (slots.get("description") or "").strip()
            if not desc:
                logger.debug("task: done senza description (slots=%s)", slots)
                return None
            open_tasks = get_open_tasks(user_id)
            for tid, t in open_tasks.items():
                t_desc = (t.get("desc") or "")
                if desc.lower() in t_desc.lower() or t_desc.lower() in desc.lower():
                    if mark_done(tid, user_id):
                        return f"✅ **Task Completato**: [{tid}] _{t_desc}_"
            logger.info(
                "task: done nessun match per '%s' tra %d open tasks",
                desc, len(open_tasks),
            )
            return None
    except Exception as e:
        logger.error("task: handler error: %s", e)
    return None


def register_handlers() -> None:
    """Registra i gestori implementati nella DISPATCH_TABLE (idempotente).

    Chiamato dai caller (main.py, chat.py, dashboard) per iniettare i
    gestori senza creare import circolari (intent_router non importa
    questo modulo).
    """
    from agent.intent_router import register_handler
    register_handler("schedule", handle_schedule)
    register_handler("memory", handle_memory)
    register_handler("task", handle_task)
    register_handler("git", handle_git)
    register_handler("ssh", handle_ssh)
    register_handler("transcribe", handle_transcribe)
    register_handler("fetch", handle_fetch)
    register_handler("translate", handle_translate)
    register_handler("config", handle_config)
    register_handler("maintenance", handle_maintenance)
