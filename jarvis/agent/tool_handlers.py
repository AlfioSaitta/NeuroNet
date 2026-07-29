"""
Tool Handlers — Implementazioni dei tool Jarvis (estratto da agent/tools.py).
Ogni handler e` una funzione async separata:
  - read-only: accept (args, confirmation_mgr=None)
  - write: accept (args, confirmation_mgr) e usano confirmation_mgr.ask()
Il dispatch table _HANDLERS e` in agent/tools.py.
"""

import os
import re
import json
import logging
import subprocess
from typing import Optional
from core.config import DOC_DIR

logger = logging.getLogger(__name__)

# Lazy imports for confirmation system (avoid circular deps)
try:
    from agent.confirmation import PendingConfirmation
except ImportError:
    PendingConfirmation = type("_PendingConfirmation", (Exception,), {})


# ══════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════

def resolve_path(rel_path: str) -> str:
    """Risolve un percorso relativo al progetto in un percorso assoluto sicuro."""
    safe_path = os.path.normpath(os.path.join(DOC_DIR, rel_path))
    if not safe_path.startswith(DOC_DIR):
        raise ValueError("Path escape attempt")
    return safe_path


def _find_git_root(target_dir: str) -> str | None:
    """Trova la root del repository git risalendo dalla directory data."""
    d = target_dir if os.path.isdir(target_dir) else os.path.dirname(target_dir)
    while True:
        if os.path.isdir(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _run_cmd(cmd: str, cwd: str, timeout: int = 60) -> tuple[int, str, str]:
    """Esegue un comando shell e restituisce (returncode, stdout, stderr)."""
    result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


# ══════════════════════════════════════════════
# READ-ONLY TOOLS (no confirmation needed)
# ══════════════════════════════════════════════

async def handle_read_file(args, confirmation_mgr=None):
    path = resolve_path(args["path"])
    if not os.path.exists(path):
        return "\u26a0\ufe0f File non trovato."
    with open(path, "r", encoding="utf-8") as f:
        fc = f.read()
    if len(fc) > 8000:
        fc = fc[:4000] + f"\n\u23e4\u23e4\u23e4 [TRUNCATED: {len(fc)} total chars] \u23e4\u23e4\u23e4\n" + fc[-4000:]
    return fc


async def handle_read_file_range(args, confirmation_mgr=None):
    path = resolve_path(args["path"])
    start_line = max(1, args.get("start_line", 1))
    end_line = min(start_line + 199, args.get("end_line", start_line + 49))
    if not os.path.exists(path):
        return "\u26a0\ufe0f File non trovato."
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    total = len(lines)
    if start_line > total:
        return f"\u26a0\ufe0f Il file ha solo {total} righe (richieste dalla {start_line})."
    end_line = min(end_line, total)
    selected = lines[start_line - 1:end_line]
    output = f"\U0001f4c4 {args['path']}  (righe {start_line}-{end_line} di {total})\n"
    output += "\u2500\u2500\u2500\u2500\n"
    for i, line in enumerate(selected, start=start_line):
        output += f"{i:>6} \u2502 {line}"
    if not output.endswith("\n"):
        output += "\n"
    return output


async def handle_search_code(args, confirmation_mgr=None):
    query = args["query"]
    file_pat = args.get("file_pattern", "")
    rel_path = args.get("path", "")
    max_results = min(args.get("max_results", 20), 50)

    target_dir = resolve_path(rel_path) if rel_path else DOC_DIR
    if not os.path.isdir(target_dir):
        return "\u26a0\ufe0f Directory non trovata."

    grep_cmd = f'grep -rnI --color=never "{query}"'
    if file_pat:
        grep_cmd += f' --include="{file_pat}"'
    grep_cmd += f" {target_dir}"

    rc, out, err = _run_cmd(grep_cmd, DOC_DIR)
    if rc not in (0, 1):
        return f"\u274c Errore ricerca: {err[:500]}"

    if not out:
        return f"\U0001f50d Nessun risultato per '{query}'."

    results = out.split("\n")
    total = len(results)
    shown = results[:max_results]
    truncated = total - max_results if total > max_results else 0

    lines = [f"\U0001f50d **Risultati per**: `{query}` ({total} occorrenze{', mostrate '+str(max_results) if truncated else ''}):\n"]
    for r in shown:
        parts = r.split(":", 2)
        if len(parts) >= 3:
            fpath = os.path.relpath(parts[0], DOC_DIR)
            lineno = parts[1]
            content = parts[2].strip()[:120]
            lines.append(f"  `{fpath}:{lineno}`  {content}")
        else:
            lines.append(f"  {r}")
    if truncated:
        lines.append(f"\n  ... e altri {truncated} risultati. Affina la ricerca per piu' precisione.")
    return "\n".join(lines)


async def handle_find_files(args, confirmation_mgr=None):
    pattern = args["pattern"]
    rel_path = args.get("path", "")
    max_results = min(args.get("max_results", 30), 100)

    target_dir = resolve_path(rel_path) if rel_path else DOC_DIR
    if not os.path.isdir(target_dir):
        return "\u26a0\ufe0f Directory non trovata."

    find_cmd = (
        f'find {target_dir} -type f -iname "{pattern}" '
        f'-not -path "*/node_modules/*" -not -path "*/.git/*" '
        f'-not -path "*/venv/*" -not -path "*/__pycache__/*" '
        f'2>/dev/null'
    )
    rc, out, err = _run_cmd(find_cmd, DOC_DIR)
    if not out:
        return f"\U0001f50d Nessun file trovato per pattern '{pattern}'."

    results = sorted(out.split("\n"))
    total = len(results)
    shown = results[:max_results]
    truncated = total - max_results if total > max_results else 0

    lines = [f"\U0001f50d **File trovati**: {pattern} ({total}{', mostrati '+str(max_results) if truncated else ''}):\n"]
    for r in shown:
        rel = os.path.relpath(r, DOC_DIR)
        try:
            size = os.path.getsize(r)
            size_str = f"{size/1024:.1f}KB" if size > 1024 else f"{size}B"
        except OSError:
            size_str = "?"
        lines.append(f"  \U0001f4c4 `{rel}` ({size_str})")
    if truncated:
        lines.append(f"\n  ... e altri {truncated} risultati. Usa un pattern piu' specifico.")
    return "\n".join(lines)


async def handle_list_directory(args, confirmation_mgr=None):
    rel_path = args.get("path", "")
    show_hidden = args.get("show_hidden", False)
    max_depth = min(args.get("max_depth", 0), 2)

    target_dir = resolve_path(rel_path) if rel_path else DOC_DIR
    if not os.path.isdir(target_dir):
        return f"\u26a0\ufe0f Directory non trovata: {rel_path or '(root)'}"

    EXCLUDE_DIRS = {'.git', 'node_modules', 'venv', '__pycache__', '.venv', 'vendor', '.idea', '.codex', '.omo'}

    def _walk(d, depth=0):
        results = []
        try:
            items = sorted(os.listdir(d))
        except PermissionError:
            return results
        for item in items:
            if not show_hidden and item.startswith('.'):
                continue
            full = os.path.join(d, item)
            if os.path.isdir(full):
                if item in EXCLUDE_DIRS:
                    continue
                results.append(("dir", depth, item))
                if depth < max_depth:
                    results.extend(_walk(full, depth + 1))
            else:
                ext = os.path.splitext(item)[1].lower()
                results.append(("file", depth, item, ext))
        return results

    entries = _walk(target_dir)

    dirs = [(d, item) for (typ, d, item) in entries if typ == "dir"]
    code_files = [(d, item) for (typ, d, item, ext) in entries if typ == "file" and ext in ('.py', '.go', '.ts', '.tsx', '.js', '.jsx', '.rs', '.java', '.c', '.cpp', '.h', '.hpp', '.sql', '.yaml', '.yml', '.md', '.json', '.txt', '.html', '.css', '.sh', '.toml', '.xml', '.mod', '.sum', '.env.example')]
    other_files = [(d, item) for (typ, d, item, ext) in entries if typ == "file" and ext not in ('.py', '.go', '.ts', '.tsx', '.js', '.jsx', '.rs', '.java', '.c', '.cpp', '.h', '.hpp', '.sql', '.yaml', '.yml', '.md', '.json', '.txt', '.html', '.css', '.sh', '.toml', '.xml', '.mod', '.sum', '.env.example')]

    output = f"\U0001f4c2 *{rel_path or 'Root'}* ({len(dirs)} cartelle, {len(code_files)} file di codice, {len(other_files)} altri)\n"

    if dirs:
        output += "\n\U0001f4c1 **Cartelle:**\n"
        for depth, item in dirs:
            prefix = "  " * depth
            output += f"  {prefix}\U0001f4c1 {item}/\n"
    if code_files:
        output += "\n\U0001f4c4 **File di codice:**\n"
        for depth, item in code_files:
            prefix = "  " * depth
            output += f"  {prefix}\U0001f4c4 {item}\n"
    if other_files:
        output += "\n\U0001f4c4 **Altri file:**\n"
        for depth, item in other_files:
            prefix = "  " * depth
            output += f"  {prefix}\U0001f4c4 {item}\n"
    if not dirs and not code_files and not other_files:
        output += "\n_Cartella vuota._\n"

    return output


# ══════════════════════════════════════════════
# GIT READ-ONLY (no confirmation)
# ══════════════════════════════════════════════

async def handle_git_status(args, confirmation_mgr=None):
    rel_path = args.get("path", "")
    target_dir = resolve_path(rel_path) if rel_path else DOC_DIR
    git_root = _find_git_root(target_dir)
    if not git_root:
        return "\u26a0\ufe0f Questa directory non e' un repository git."

    rc_b, out_b, _ = _run_cmd("git branch --show-current", git_root)
    branch = out_b if rc_b == 0 else "unknown"

    rc_s, out_s, err_s = _run_cmd("git status --short", git_root)
    rc_d, out_d, _ = _run_cmd("git diff --stat", git_root)

    staged = []
    modified = []
    untracked = []
    conflicts = []
    if rc_s == 0:
        for line in out_s.split("\n"):
            if not line.strip():
                continue
            status = line[:2]
            file = line[3:]
            if status in ("??",):
                untracked.append(file)
            elif "U" in status or status in ("DD", "AA", "UU"):
                conflicts.append(file)
            elif " " in status and status[0] != " ":
                staged.append(file)
            else:
                modified.append(file)

    output = f"\U0001f4ca **Git Status** \u2014 branch: `{branch}`\n"
    rel_git = os.path.relpath(git_root, DOC_DIR)
    if rel_git != ".":
        output += f"   Repo: `{rel_git}/`\n"

    if staged:
        output += f"\n\u2705 **Staged** ({len(staged)}):\n"
        for f in staged[:20]:
            output += f"  \u2705 `{f}`\n"
        if len(staged) > 20:
            output += f"  ... e altri {len(staged) - 20}\n"
    if modified:
        output += f"\n\U0001f4dd **Modificati** ({len(modified)}):\n"
        for f in modified[:20]:
            output += f"  \U0001f4dd `{f}`\n"
        if len(modified) > 20:
            output += f"  ... e altri {len(modified) - 20}\n"
    if untracked:
        output += f"\n\U0001f195 **Untracked** ({len(untracked)}):\n"
        for f in untracked[:20]:
            output += f"  \U0001f195 `{f}`\n"
        if len(untracked) > 20:
            output += f"  ... e altri {len(untracked) - 20}\n"
    if conflicts:
        output += f"\n\u26a0\ufe0f **Conflitti** ({len(conflicts)}):\n"
        for f in conflicts:
            output += f"  \u26a0\ufe0f `{f}`\n"
    if not staged and not modified and not untracked and not conflicts:
        output += "\n\u2728 Working tree pulito.\n"

    if rc_d == 0 and out_d:
        output += f"\n\U0001f4ca **Diff stat:**\n```\n{out_d[:1000]}\n```\n"

    return output


async def handle_git_diff(args, confirmation_mgr=None):
    rel_path = args.get("path", "")
    file_filter = args.get("file", "")
    staged = args.get("staged", False)
    max_lines = min(args.get("max_lines", 100), 300)

    target_dir = resolve_path(rel_path) if rel_path else DOC_DIR
    git_root = _find_git_root(target_dir)
    if not git_root:
        return "\u26a0\ufe0f Questa directory non e' un repository git."

    diff_cmd = "git diff --color=never"
    if staged:
        diff_cmd += " --cached"
    if file_filter:
        diff_cmd += f" -- '{file_filter}'"

    rc, out, err = _run_cmd(diff_cmd, git_root)
    if rc != 0:
        return f"\u274c Errore git diff: {err[:500]}"
    if not out:
        return "\u2728 Nessuna modifica."

    lines = out.split("\n")
    total = len(lines)
    shown = lines[:max_lines]
    truncated = total - max_lines if total > max_lines else 0

    output = f"\U0001f4ca **Diff** (staged={staged})"
    if file_filter:
        output += f" \u2014 file: `{file_filter}`"
    output += f" \u2014 {total} righe:\n\n```diff\n"
    output += "\n".join(shown)
    if truncated:
        output += f"\n\u23e4\u23e4\u23e4 [{truncated} righe in piu', usa un filtro piu' specifico] \u23e4\u23e4\u23e4"
    output += "\n```\n"
    return output


async def handle_git_log(args, confirmation_mgr=None):
    rel_path = args.get("path", "")
    limit = min(args.get("limit", 10), 50)
    branch = args.get("branch", "")

    target_dir = resolve_path(rel_path) if rel_path else DOC_DIR
    git_root = _find_git_root(target_dir)
    if not git_root:
        return "\u26a0\ufe0f Questa directory non e' un repository git."

    log_cmd = f"git log --oneline --graph --decorate -{limit}"
    if branch:
        log_cmd += f" {branch}"

    detail_cmd = f"git log --format='%h %ad %an: %s' --date=short -{limit}"
    if branch:
        detail_cmd += f" {branch}"

    rc_graph, out_graph, _ = _run_cmd(log_cmd, git_root)
    rc_detail, out_detail, _ = _run_cmd(detail_cmd, git_root)

    rc_b, out_b, _ = _run_cmd("git branch --show-current", git_root)
    current = out_b if rc_b == 0 else "?"

    output = f"\U0001f4dc **Git Log** \u2014 branch: `{current}`"
    if branch:
        output += f" \u2192 `{branch}`"
    rel_git = os.path.relpath(git_root, DOC_DIR)
    if rel_git != ".":
        output += f"  (repo: `{rel_git}/`)"
    output += "\n\n"

    if rc_graph == 0 and out_graph:
        output += f"```\n{out_graph}\n```\n"
    if rc_detail == 0 and out_detail:
        output += f"```\n{out_detail}\n```\n"

    return output


# ══════════════════════════════════════════════
# WRITE TOOLS (require confirmation)
# ══════════════════════════════════════════════

async def handle_write_file(args, confirmation_mgr):
    path = resolve_path(args["path"])
    try:
        approved = await confirmation_mgr.ask(f"Scrittura file: {args['path']}")
    except PendingConfirmation as e:
        return f"\u26a0\ufe0f **Conferma richiesta**: {e.action_desc}\nPer autorizzare, invia: `confirm:{e.token}`"
    if not approved:
        return "\u274c Scrittura rifiutata dall'utente."
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(args["content"])
    size = os.path.getsize(path)
    return f"\u2705 File `{args['path']}` scritto ({size/1024:.1f}KB)."


async def handle_replace_in_file(args, confirmation_mgr):
    path = resolve_path(args["path"])
    target = args.get("target_text", "")
    replacement = args.get("replacement_text", "")
    try:
        approved = await confirmation_mgr.ask(
            f"Patch file: {args['path']}\n\n"
            f"**DA:**\n```\n{target[:300]}{'...' if len(target) > 300 else ''}\n```\n"
            f"**A:**\n```\n{replacement[:300]}{'...' if len(replacement) > 300 else ''}\n```"
        )
    except PendingConfirmation as e:
        return f"\u26a0\ufe0f **Conferma richiesta**: {e.action_desc}\nPer autorizzare, invia: `confirm:{e.token}`"
    if not approved:
        return "\u274c Modifica rifiutata dall'utente."

    if not os.path.exists(path):
        return "\u26a0\ufe0f File non trovato."

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    if target not in content:
        return "\u26a0\ufe0f ERRORE: target_text non trovato. Usa read_file per verificare il contenuto esatto (indentazione, spazi, newline)."

    content = content.replace(target, replacement, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"\u2705 File `{args['path']}` patchato con successo."


async def handle_delete_file(args, confirmation_mgr):
    path = resolve_path(args["path"])
    try:
        approved = await confirmation_mgr.ask(f"Eliminazione file: {args['path']}")
    except PendingConfirmation as e:
        return f"\u26a0\ufe0f **Conferma richiesta**: {e.action_desc}\nPer autorizzare, invia: `confirm:{e.token}`"
    if not approved:
        return "\u274c Eliminazione rifiutata dall'utente."
    if os.path.exists(path):
        os.remove(path)
        return f"\u2705 File `{args['path']}` eliminato."
    return "\u26a0\ufe0f File non trovato."


# ══════════════════════════════════════════════
# GIT WRITE (require confirmation)
# ══════════════════════════════════════════════

async def handle_git_commit(args, confirmation_mgr):
    message = args["message"]
    rel_path = args.get("path", "")
    files_str = args.get("files", "")

    target_dir = resolve_path(rel_path) if rel_path else DOC_DIR
    git_root = _find_git_root(target_dir)
    if not git_root:
        return "\u26a0\ufe0f Questa directory non e' un repository git."

    try:
        approved = await confirmation_mgr.ask(
            f"Git commit in `{os.path.relpath(git_root, DOC_DIR)}/`:\n"
            f"`{message}`"
        )
    except PendingConfirmation as e:
        return f"\u26a0\ufe0f **Conferma richiesta**: {e.action_desc}\nPer autorizzare, invia: `confirm:{e.token}`"
    if not approved:
        return "\u274c Commit rifiutato dall'utente."

    if files_str:
        for f in files_str.split(","):
            f = f.strip()
            if f:
                _run_cmd(f"git add '{f}'", git_root)
    else:
        _run_cmd("git add -A", git_root)

    rc, out, err = _run_cmd(f"git commit -m '{message}'", git_root)
    if rc == 0:
        return f"\u2705 Commit creato:\n```\n{out}\n```"
    return f"\u274c Commit fallito:\n{err[:500]}"


async def handle_git_push(args, confirmation_mgr):
    rel_path = args.get("path", "")
    remote = args.get("remote", "origin")
    branch = args.get("branch", "")

    target_dir = resolve_path(rel_path) if rel_path else DOC_DIR
    git_root = _find_git_root(target_dir)
    if not git_root:
        return "\u26a0\ufe0f Questa directory non e' un repository git."

    if not branch:
        _, out_b, _ = _run_cmd("git branch --show-current", git_root)
        branch = out_b

    try:
        approved = await confirmation_mgr.ask(
            f"Git push: `{remote}/{branch}` in `{os.path.relpath(git_root, DOC_DIR)}/`"
        )
    except PendingConfirmation as e:
        return f"\u26a0\ufe0f **Conferma richiesta**: {e.action_desc}\nPer autorizzare, invia: `confirm:{e.token}`"
    if not approved:
        return "\u274c Push rifiutato dall'utente."

    rc, out, err = _run_cmd(f"git push {remote} {branch}", git_root, timeout=120)
    if rc == 0:
        return f"\u2705 Push completato:\n```\n{out}\n```"
    return f"\u274c Push fallito:\n{err[:500]}"


# ══════════════════════════════════════════════
# SHELL
# ══════════════════════════════════════════════

async def handle_run_shell_command(args, confirmation_mgr):
    cmd = args["command"]
    rel_dir = args.get("directory", "")
    timeout = min(args.get("timeout", 60), 300)
    target_dir = resolve_path(rel_dir) if rel_dir else DOC_DIR

    READONLY_COMMANDS = [
        "ls", "find", "cat", "head", "tail", "grep", "pwd", "echo",
        "date", "whoami", "id", "uname", "df", "du", "ps", "uptime",
        "which", "file", "stat", "diff", "sort", "cut", "wc", "printenv",
        "python3 -c", "python3 -m", "pip list", "pip show",
    ]
    base_cmd = cmd.strip().split()[0] if cmd.strip() else ""
    is_readonly = any(cmd.strip().startswith(ro) for ro in READONLY_COMMANDS)

    ALLOWED_COMMANDS = [
        "ls", "find", "cat", "head", "tail", "grep", "pwd", "echo",
        "date", "whoami", "id", "uname", "df", "du", "ps", "uptime",
        "which", "file", "stat", "diff", "sort", "cut", "wc", "printenv",
        "git", "mkdir", "touch", "rm", "mv", "cp", "chmod", "chown",
        "python3", "pip", "node", "npm", "go", "cargo", "rustc",
        "docker", "docker-compose",
    ]

    if base_cmd not in ALLOWED_COMMANDS and not is_readonly:
        return f"\u274c Comando '{base_cmd}' non consentito."

    if not is_readonly:
        try:
            approved = await confirmation_mgr.ask(
                f"Esecuzione in `{os.path.relpath(target_dir, DOC_DIR)}/`:\n$ {cmd[:300]}"
            )
        except PendingConfirmation as e:
            return f"\u26a0\ufe0f **Conferma richiesta**: {e.action_desc}\nPer autorizzare, invia: `confirm:{e.token}`"
        if not approved:
            return "\u274c Comando rifiutato dall'utente."

    try:
        result = subprocess.run(cmd, shell=True, cwd=target_dir,
                                capture_output=True, text=True, timeout=timeout)
        out = result.stdout.strip()
        err = result.stderr.strip()

        if len(out) > 4000:
            out = out[:2000] + "\n\u23e4\u23e4\u23e4 [TRUNCATED] \u23e4\u23e4\u23e4\n" + out[-2000:]
        if len(err) > 4000:
            err = err[:2000] + "\n\u23e4\u23e4\u23e4 [TRUNCATED] \u23e4\u23e4\u23e4\n" + err[-2000:]

        if result.returncode == 0:
            return f"\u2705 `{cmd}`\n```\n{out}\n```" + (f"\nErr:\n```\n{err}\n```" if err else "")
        else:
            return f"\u274c `{cmd}` (exit {result.returncode})\n```\n{out}\n```\nErr:\n```\n{err}\n```"
    except subprocess.TimeoutExpired:
        return f"\u23f3 Comando terminato per timeout ({timeout}s)."
    except Exception as e:
        return f"\u274c Errore: {e}"


# ══════════════════════════════════════════════
# SKILLS (Jarvis-native)
# ══════════════════════════════════════════════

async def handle_load_skill(args, confirmation_mgr=None):
    skill_name = args.get("name", "")
    if not skill_name:
        return "\u26a0\ufe0f Specificare un nome skill (name param)."
    try:
        from agent.skills import load_skill
        content = await load_skill(skill_name)
        if content:
            return content
        return f"\u26a0\ufe0f Skill '{skill_name}' non trovata. Usa skill_discover per la lista."
    except ImportError:
        return "\u26a0\ufe0f Skills manager non disponibile."


async def handle_skill_discover(args, confirmation_mgr=None):
    try:
        from agent.skills import get_skill_list_xml
        xml = get_skill_list_xml()
        if xml:
            return f"\U0001f4d6 **Skill disponibili**\n\n{xml}"
        return "\U0001f4d6 Nessuna skill configurata."
    except ImportError:
        return "\u26a0\ufe0f Skills manager non disponibile."


async def handle_skill_generic(name: str, args, confirmation_mgr):
    """Handle skill_* prefix tools: load .skill.md context or execute legacy YAML."""
    try:
        from agent.skills import load_skill, execute_skill

        content = await load_skill(name)
        if content:
            return content

        try:
            approved = await confirmation_mgr.ask(f"Esecuzione Skill: {name}\n{args}")
        except PendingConfirmation as e:
            return f"\u26a0\ufe0f **Conferma richiesta**: {e.action_desc}\nPer autorizzare, invia: `confirm:{e.token}`"
        if not approved:
            return "\u274c Skill rifiutata dall'utente."
        return await execute_skill(name, args)
    except ImportError:
        return "\u26a0\ufe0f Skills manager non disponibile."


# ══════════════════════════════════════════════
# MCP TOOLS
# ══════════════════════════════════════════════

async def handle_mcp_execute(name: str, args):
    """Handle mcp_* prefix tools via MCP client."""
    try:
        from api.mcp.client import get_mcp_manager
        manager = get_mcp_manager()
        return await manager.execute_tool(name, args)
    except ImportError:
        return "\u26a0\ufe0f MCP client non disponibile."
