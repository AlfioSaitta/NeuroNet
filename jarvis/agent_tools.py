"""
Agent Tools — Tool-calling per Jarvis LLM Agent.

Ogni tool ha:
  - Nome chiaro e description con trigger per instruction following
  - Parametri ben tipizzati
  - Conferma solo per azioni DISTRUTTIVE (scrittura, eliminazione, git push/commit)
  - Read-only tools NON richiedono conferma (esecuzione immediata)
"""

import os
import re
import json
import logging
import subprocess
from config import DOC_DIR

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# TOOL SCHEMAS (registro per LLM)
# ──────────────────────────────────────────────

TOOLS_SCHEMA = [
    # ── LETTURA FILE ──
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Legge il contenuto completo di un file. NON richiede autorizzazione. Per file grandi (>8000 caratteri), il contenuto viene troncato con inizio e fine. Per leggere solo righe specifiche, usa read_file_range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Percorso relativo al progetto (es. 'SlotBuilder/main.py')"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file_range",
            "description": "Legge un intervallo di righe da un file. NON richiede autorizzazione. Ideale per ispezionare sezioni specifiche senza caricare l'intero file. Le righe sono 1-indexed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Percorso relativo al progetto (es. 'SlotBuilder/main.py')"},
                    "start_line": {"type": "integer", "description": "Numero di riga iniziale (1-indexed, default: 1)."},
                    "end_line": {"type": "integer", "description": "Numero di riga finale (1-indexed, default: 50). Massimo 200 righe per chiamata."}
                },
                "required": ["path"]
            }
        }
    },
    # ── RICERCA ──
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Cerca testo/codice nei file del progetto (grep ricorsivo). NON richiede autorizzazione. Usa questo tool quando l'utente chiede di 'cercare', 'trovare', 'dove si trova', 'in quali file compare', o per analisi trasversali del codice.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Il testo o pattern da cercare (case-insensitive). Può essere una regex semplice (es. 'def handle_', 'import os', 'class User')."},
                    "file_pattern": {"type": "string", "description": "Filtro per tipo di file (es. '*.py', '*.ts', '*.go', '*.rs'). Default: tutti i file di codice."},
                    "path": {"type": "string", "description": "Sotto-directory in cui cercare (es. 'SlotBuilder/src'). Default: tutto il progetto."},
                    "max_results": {"type": "integer", "description": "Numero massimo di risultati (default: 20, max: 50)."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "Trova file per nome/pattern nel progetto. NON richiede autorizzazione. Usa supporto wildcard (es. '*test*', '*.controller.ts', 'main*'). Utile per navigare il progetto quando non si conosce il percorso esatto.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Pattern del nome file (es. '*.py', '*test*', 'main*', '*.controller.ts')."},
                    "path": {"type": "string", "description": "Sotto-directory in cui cercare (es. 'SlotBuilder/src'). Default: tutto il progetto."},
                    "max_results": {"type": "integer", "description": "Numero massimo di risultati (default: 30, max: 100)."}
                },
                "required": ["pattern"]
            }
        }
    },
    # ── ESILORAZIONE ──
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "Elenca file e cartelle in una directory del progetto. NON richiede autorizzazione. Usa questo tool quando l'utente chiede di vedere/elencare/esplorare i file in una cartella, o quando dice 'esegui'/'fai' dopo che hai suggerito di listare. Mostra cartelle, file di codice e altri file separatamente.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Percorso relativo al progetto (es. '' per la root, 'SlotBuilder/' per una sotto-directory, 'SlotBuilder/src/components' per una directory annidata)."},
                    "show_hidden": {"type": "boolean", "description": "Se true, mostra anche file nascosti (.gitignore, .env, ecc.). Default: false."},
                    "max_depth": {"type": "integer", "description": "Profondità massima di ricorsione per sotto-cartelle (0 = solo primo livello, 1 = primo e secondo, default: 0). Massimo: 2."}
                },
                "required": ["path"]
            }
        }
    },
    # ── GIT (READ-ONLY) ──
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Mostra lo stato git del progetto (file modificati, staged, untracked). NON richiede autorizzazione. Usa prima di fare commit o push per vedere cosa è cambiato.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Sotto-directory del progetto git (es. 'SlotBuilder'). Default: tutto il progetto."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Mostra le modifiche non ancora committate (diff). NON richiede autorizzazione. Usa per vedere esattamente cosa è cambiato prima di un commit. Opzionalmente filtra per file specifico.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Sotto-directory del progetto git (es. 'SlotBuilder'). Default: tutto il progetto."},
                    "file": {"type": "string", "description": "Filtra diff per un file specifico (es. 'main.py'). Default: tutti i file modificati."},
                    "staged": {"type": "boolean", "description": "Se true, mostra il diff dell'area di staging (git diff --cached). Default: false."},
                    "max_lines": {"type": "integer", "description": "Numero massimo di righe di diff (default: 100, max: 300)."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "Mostra la cronologia dei commit recenti. NON richiede autorizzazione. Usa per vedere la storia del progetto, trovare commit specifici o capire cosa è stato fatto di recente.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Sotto-directory del progetto git (es. 'SlotBuilder'). Default: tutto il progetto."},
                    "limit": {"type": "integer", "description": "Numero di commit da mostrare (default: 10, max: 50)."},
                    "branch": {"type": "string", "description": "Nome del branch (es. 'main', 'feature/xyz'). Default: branch corrente."}
                },
                "required": []
            }
        }
    },
    # ── GIT (SCRITTURA, con conferma) ──
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "Crea un commit git con i cambiamenti locali. Richiede autorizzazione. Usa DOPO aver mostrato git_status/git_diff e aver ricevuto conferma dall'utente. Staged tutti i file modificati e crea il commit con il messaggio specificato.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Messaggio del commit (conventional commits: 'feat: ...', 'fix: ...', 'refactor: ...', 'docs: ...')"},
                    "path": {"type": "string", "description": "Sotto-directory del progetto git (es. 'SlotBuilder'). Default: tutto il progetto."},
                    "files": {"type": "string", "description": "File specifici da includere separati da virgola (es. 'main.py,utils/helper.ts'). Default: tutti i file modificati."}
                },
                "required": ["message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_push",
            "description": "Esegue git push per inviare i commit al repository remoto. Richiede autorizzazione. Usa DOPO un commit, quando l'utente dice 'pusha' o 'carica su GitHub'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Sotto-directory del progetto git (es. 'SlotBuilder'). Default: tutto il progetto."},
                    "remote": {"type": "string", "description": "Nome del remote (es. 'origin'). Default: origin."},
                    "branch": {"type": "string", "description": "Nome del branch da pushare. Default: branch corrente."}
                },
                "required": []
            }
        }
    },
    # ── SCRITTURA FILE (con conferma) ──
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Scrive o sovrascrive un file fisico all'interno del progetto. Richiede autorizzazione. Usa questo tool per editare il codice, creare nuovi file, o modificare file esistenti. Il contenuto sostituisce completamente il file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Percorso relativo al progetto (es. 'SlotBuilder/main.py')"},
                    "content": {"type": "string", "description": "Codice sorgente o contenuto completo da scrivere nel file"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "replace_in_file",
            "description": "Sostituisce un blocco di testo esatto all'interno di un file. Richiede autorizzazione. Ideale per modifiche mirate senza sovrascrivere l'intero file. Prima dell'uso, leggere il file per avere il contenuto esatto da sostituire.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Percorso relativo al progetto (es. 'SlotBuilder/main.py')."},
                    "target_text": {"type": "string", "description": "Il blocco di codice originale ESATTO da trovare (inclusa indentazione). Usa read_file prima per avere il contenuto preciso."},
                    "replacement_text": {"type": "string", "description": "Il nuovo blocco di codice da inserire al posto del target."}
                },
                "required": ["path", "target_text", "replacement_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Elimina un file dal progetto. Richiede autorizzazione. Usa solo quando l'utente chiede esplicitamente di eliminare un file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Percorso relativo al progetto (es. 'SlotBuilder/main.py')"}
                },
                "required": ["path"]
            }
        }
    },
    # ── SHELL (avanzato) ──
    {
        "type": "function",
        "function": {
            "name": "run_shell_command",
            "description": "Esegue un comando bash nel container. Per comandi READ-ONLY (ls, find, cat, head, tail, grep, diff, pwd, stat, which, file, sort, cut, wc, du, df, uptime, echo, date, whoami, id, uname, ps, printenv, python3 -c, python3 -m) NON richiede autorizzazione. Per comandi DISTRUTTIVI (git commit, git push, rm, mv, cp, mkdir, touch, chmod, python3, pip) richiede autorizzazione. Per operazioni git comuni usa i tool dedicati git_status/git_diff/git_log/git_commit/git_push.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Il comando bash da eseguire."},
                    "directory": {"type": "string", "description": "La cartella di lavoro (es. 'SlotBuilder/'). Default: root del progetto."},
                    "timeout": {"type": "integer", "description": "Timeout in secondi (default: 60, max: 300). Utile per comandi lenti (build, test)."}
                },
                "required": ["command"]
            }
        }
    },
]


# ──────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────

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


# ──────────────────────────────────────────────
# CONFIRMATION SYSTEM
# ──────────────────────────────────────────────

import asyncio
pending_confirmations = {}


async def ask_confirmation(bot, chat_id, action_desc):
    if not bot or not chat_id:
        return True  # bypass if no bot available

    future = asyncio.Future()
    pending_confirmations[chat_id] = future

    await bot.send_message(
        chat_id=chat_id,
        text=f"⚠️ **ATTENZIONE: Richiesta Autorizzazione**\nL'LLM sta per eseguire:\n`{action_desc}`\n\nVuoi autorizzare? Rispondi con **Y** o **N**.",
        parse_mode="Markdown"
    )

    try:
        approved = await asyncio.wait_for(future, timeout=300)
        return approved
    except asyncio.TimeoutError:
        pending_confirmations.pop(chat_id, None)
        return False


# ──────────────────────────────────────────────
# TOOL EXECUTOR
# ──────────────────────────────────────────────

async def execute_tool_call(tool_call, bot=None, chat_id=None):
    name = tool_call.get("function", {}).get("name")
    try:
        args_raw = tool_call.get("function", {}).get("arguments", "{}")
        if isinstance(args_raw, dict):
            args = args_raw
        else:
            args = json.loads(args_raw)
    except Exception as e:
        return f"❌ Errore parser argomenti: {e}"

    try:
        # ═══════════════════════════════════════
        # READ-ONLY TOOLS (no confirmation)
        # ═══════════════════════════════════════

        if name == "read_file":
            path = resolve_path(args["path"])
            if not os.path.exists(path):
                return "⚠️ File non trovato."
            with open(path, "r", encoding="utf-8") as f:
                fc = f.read()
            if len(fc) > 8000:
                fc = fc[:4000] + f"\n⏤⏤⏤ [TRUNCATED: {len(fc)} total chars] ⏤⏤⏤\n" + fc[-4000:]
            return fc

        elif name == "read_file_range":
            path = resolve_path(args["path"])
            start_line = max(1, args.get("start_line", 1))
            end_line = min(start_line + 199, args.get("end_line", start_line + 49))
            if not os.path.exists(path):
                return "⚠️ File non trovato."
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            total = len(lines)
            if start_line > total:
                return f"⚠️ Il file ha solo {total} righe (richieste dalla {start_line})."
            end_line = min(end_line, total)
            selected = lines[start_line - 1:end_line]
            output = f"📄 {args['path']}  (righe {start_line}-{end_line} di {total})\n"
            output += "────\n"
            for i, line in enumerate(selected, start=start_line):
                output += f"{i:>6} │ {line}"
            if not output.endswith("\n"):
                output += "\n"
            return output

        elif name == "search_code":
            query = args["query"]
            file_pat = args.get("file_pattern", "")
            rel_path = args.get("path", "")
            max_results = min(args.get("max_results", 20), 50)

            target_dir = resolve_path(rel_path) if rel_path else DOC_DIR
            if not os.path.isdir(target_dir):
                return "⚠️ Directory non trovata."

            # Costruisci comando grep
            grep_cmd = f'grep -rnI --color=never "{query}"'
            if file_pat:
                # Converte *.py in --include='*.py'
                grep_cmd += f' --include="{file_pat}"'
            grep_cmd += f" {target_dir}"

            rc, out, err = _run_cmd(grep_cmd, DOC_DIR)
            if rc not in (0, 1):  # 1 = no matches
                return f"❌ Errore ricerca: {err[:500]}"

            if not out:
                return f"🔍 Nessun risultato per '{query}'."

            results = out.split("\n")
            total = len(results)
            shown = results[:max_results]
            truncated = total - max_results if total > max_results else 0

            # Raggruppa per directory per leggibilità
            lines = [f"🔍 **Risultati per**: `{query}` ({total} occorrenze{', mostrate '+str(max_results) if truncated else ''}):\n"]
            for r in shown:
                # Formato: /path/file:line:content
                parts = r.split(":", 2)
                if len(parts) >= 3:
                    fpath = os.path.relpath(parts[0], DOC_DIR)
                    lineno = parts[1]
                    content = parts[2].strip()[:120]
                    lines.append(f"  `{fpath}:{lineno}`  {content}")
                else:
                    lines.append(f"  {r}")
            if truncated:
                lines.append(f"\n  ... e altri {truncated} risultati. Affina la ricerca per più precisione.")
            return "\n".join(lines)

        elif name == "find_files":
            pattern = args["pattern"]
            rel_path = args.get("path", "")
            max_results = min(args.get("max_results", 30), 100)

            target_dir = resolve_path(rel_path) if rel_path else DOC_DIR
            if not os.path.isdir(target_dir):
                return "⚠️ Directory non trovata."

            # find per nome file (case-insensitive), escludi dirs comuni
            find_cmd = (
                f'find {target_dir} -type f -iname "{pattern}" '
                f'-not -path "*/node_modules/*" -not -path "*/.git/*" '
                f'-not -path "*/venv/*" -not -path "*/__pycache__/*" '
                f'2>/dev/null'
            )
            rc, out, err = _run_cmd(find_cmd, DOC_DIR)
            if not out:
                return f"🔍 Nessun file trovato per pattern '{pattern}'."

            results = sorted(out.split("\n"))
            total = len(results)
            shown = results[:max_results]
            truncated = total - max_results if total > max_results else 0

            lines = [f"🔍 **File trovati**: {pattern} ({total}{', mostrati '+str(max_results) if truncated else ''}):\n"]
            for r in shown:
                rel = os.path.relpath(r, DOC_DIR)
                try:
                    size = os.path.getsize(r)
                    size_str = f"{size/1024:.1f}KB" if size > 1024 else f"{size}B"
                except OSError:
                    size_str = "?"
                lines.append(f"  📄 `{rel}` ({size_str})")
            if truncated:
                lines.append(f"\n  ... e altri {truncated} risultati. Usa un pattern più specifico.")
            return "\n".join(lines)

        elif name == "list_directory":
            rel_path = args.get("path", "")
            show_hidden = args.get("show_hidden", False)
            max_depth = min(args.get("max_depth", 0), 2)

            target_dir = resolve_path(rel_path) if rel_path else DOC_DIR
            if not os.path.isdir(target_dir):
                return f"⚠️ Directory non trovata: {rel_path or '(root)'}"

            EXCLUDE_DIRS = {'.git', 'node_modules', 'venv', '__pycache__', '.venv', 'vendor', '.idea', '.codex', '.omo'}

            def _walk(d, depth=0):
                """Walk ricorsivo con profondità limitata."""
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

            output = f"📂 *{rel_path or 'Root'}* ({len(dirs)} cartelle, {len(code_files)} file di codice, {len(other_files)} altri)\n"

            if dirs:
                output += "\n📁 **Cartelle:**\n"
                for depth, item in dirs:
                    prefix = "  " * depth
                    output += f"  {prefix}📁 {item}/\n"
            if code_files:
                output += "\n📄 **File di codice:**\n"
                for depth, item in code_files:
                    prefix = "  " * depth
                    output += f"  {prefix}📄 {item}\n"
            if other_files:
                output += "\n📄 **Altri file:**\n"
                for depth, item in other_files:
                    prefix = "  " * depth
                    output += f"  {prefix}📄 {item}\n"
            if not dirs and not code_files and not other_files:
                output += "\n_Cartella vuota._\n"

            return output

        # ── GIT READ-ONLY (no confirmation) ──

        elif name == "git_status":
            rel_path = args.get("path", "")
            target_dir = resolve_path(rel_path) if rel_path else DOC_DIR
            git_root = _find_git_root(target_dir)
            if not git_root:
                return "⚠️ Questa directory non è un repository git."

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

            output = f"📊 **Git Status** — branch: `{branch}`\n"
            rel_git = os.path.relpath(git_root, DOC_DIR)
            if rel_git != ".":
                output += f"   Repo: `{rel_git}/`\n"

            if staged:
                output += f"\n✅ **Staged** ({len(staged)}):\n"
                for f in staged[:20]:
                    output += f"  ✅ `{f}`\n"
                if len(staged) > 20:
                    output += f"  ... e altri {len(staged) - 20}\n"
            if modified:
                output += f"\n📝 **Modificati** ({len(modified)}):\n"
                for f in modified[:20]:
                    output += f"  📝 `{f}`\n"
                if len(modified) > 20:
                    output += f"  ... e altri {len(modified) - 20}\n"
            if untracked:
                output += f"\n🆕 **Untracked** ({len(untracked)}):\n"
                for f in untracked[:20]:
                    output += f"  🆕 `{f}`\n"
                if len(untracked) > 20:
                    output += f"  ... e altri {len(untracked) - 20}\n"
            if conflicts:
                output += f"\n⚠️ **Conflitti** ({len(conflicts)}):\n"
                for f in conflicts:
                    output += f"  ⚠️ `{f}`\n"
            if not staged and not modified and not untracked and not conflicts:
                output += "\n✨ Working tree pulito.\n"

            if rc_d == 0 and out_d:
                output += f"\n📊 **Diff stat:**\n```\n{out_d[:1000]}\n```\n"

            return output

        elif name == "git_diff":
            rel_path = args.get("path", "")
            file_filter = args.get("file", "")
            staged = args.get("staged", False)
            max_lines = min(args.get("max_lines", 100), 300)

            target_dir = resolve_path(rel_path) if rel_path else DOC_DIR
            git_root = _find_git_root(target_dir)
            if not git_root:
                return "⚠️ Questa directory non è un repository git."

            diff_cmd = "git diff --color=never"
            if staged:
                diff_cmd += " --cached"
            if file_filter:
                diff_cmd += f" -- '{file_filter}'"

            rc, out, err = _run_cmd(diff_cmd, git_root)
            if rc != 0:
                return f"❌ Errore git diff: {err[:500]}"
            if not out:
                return "✨ Nessuna modifica."

            lines = out.split("\n")
            total = len(lines)
            shown = lines[:max_lines]
            truncated = total - max_lines if total > max_lines else 0

            output = f"📊 **Diff** (staged={staged})"
            if file_filter:
                output += f" — file: `{file_filter}`"
            output += f" — {total} righe:\n\n```diff\n"
            output += "\n".join(shown)
            if truncated:
                output += f"\n⏤⏤⏤ [{truncated} righe in più, usa un filtro più specifico] ⏤⏤⏤"
            output += "\n```\n"
            return output

        elif name == "git_log":
            rel_path = args.get("path", "")
            limit = min(args.get("limit", 10), 50)
            branch = args.get("branch", "")

            target_dir = resolve_path(rel_path) if rel_path else DOC_DIR
            git_root = _find_git_root(target_dir)
            if not git_root:
                return "⚠️ Questa directory non è un repository git."

            log_cmd = f"git log --oneline --graph --decorate -{limit}"
            if branch:
                log_cmd += f" {branch}"

            # Ottieni dettagli come autore, data
            detail_cmd = f"git log --format='%h %ad %an: %s' --date=short -{limit}"
            if branch:
                detail_cmd += f" {branch}"

            rc_graph, out_graph, _ = _run_cmd(log_cmd, git_root)
            rc_detail, out_detail, _ = _run_cmd(detail_cmd, git_root)

            # Branch corrente
            rc_b, out_b, _ = _run_cmd("git branch --show-current", git_root)
            current = out_b if rc_b == 0 else "?"

            output = f"📜 **Git Log** — branch: `{current}`"
            if branch:
                output += f" → `{branch}`"
            rel_git = os.path.relpath(git_root, DOC_DIR)
            if rel_git != ".":
                output += f"  (repo: `{rel_git}/`)"
            output += "\n\n"

            if rc_graph == 0 and out_graph:
                output += f"```\n{out_graph}\n```\n"
            if rc_detail == 0 and out_detail:
                output += f"```\n{out_detail}\n```\n"

            return output

        # ═══════════════════════════════════════
        # WRITE TOOLS (require confirmation)
        # ═══════════════════════════════════════

        elif name == "write_file":
            path = resolve_path(args["path"])
            approved = await ask_confirmation(bot, chat_id, f"Scrittura file: {args['path']}")
            if not approved:
                return "❌ Scrittura rifiutata dall'utente."
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(args["content"])
            size = os.path.getsize(path)
            return f"✅ File `{args['path']}` scritto ({size/1024:.1f}KB)."

        elif name == "replace_in_file":
            path = resolve_path(args["path"])
            target = args.get("target_text", "")
            replacement = args.get("replacement_text", "")
            approved = await ask_confirmation(
                bot, chat_id,
                f"Patch file: {args['path']}\n\n"
                f"**DA:**\n```\n{target[:300]}{'...' if len(target) > 300 else ''}\n```\n"
                f"**A:**\n```\n{replacement[:300]}{'...' if len(replacement) > 300 else ''}\n```"
            )
            if not approved:
                return "❌ Modifica rifiutata dall'utente."

            if not os.path.exists(path):
                return "⚠️ File non trovato."

            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            if target not in content:
                return "⚠️ ERRORE: target_text non trovato. Usa read_file per verificare il contenuto esatto (indentazione, spazi, newline)."

            content = content.replace(target, replacement, 1)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"✅ File `{args['path']}` patchato con successo."

        elif name == "delete_file":
            path = resolve_path(args["path"])
            approved = await ask_confirmation(bot, chat_id, f"Eliminazione file: {args['path']}")
            if not approved:
                return "❌ Eliminazione rifiutata dall'utente."
            if os.path.exists(path):
                os.remove(path)
                return f"✅ File `{args['path']}` eliminato."
            return "⚠️ File non trovato."

        # ── GIT WRITE (with confirmation) ──

        elif name == "git_commit":
            message = args["message"]
            rel_path = args.get("path", "")
            files_str = args.get("files", "")

            target_dir = resolve_path(rel_path) if rel_path else DOC_DIR
            git_root = _find_git_root(target_dir)
            if not git_root:
                return "⚠️ Questa directory non è un repository git."

            approved = await ask_confirmation(
                bot, chat_id,
                f"Git commit in `{os.path.relpath(git_root, DOC_DIR)}/`:\n"
                f"`{message}`"
            )
            if not approved:
                return "❌ Commit rifiutato dall'utente."

            # Stage files
            if files_str:
                for f in files_str.split(","):
                    f = f.strip()
                    if f:
                        _run_cmd(f"git add '{f}'", git_root)
            else:
                _run_cmd("git add -A", git_root)

            rc, out, err = _run_cmd(f"git commit -m '{message}'", git_root)
            if rc == 0:
                return f"✅ Commit creato:\n```\n{out}\n```"
            return f"❌ Commit fallito:\n{err[:500]}"

        elif name == "git_push":
            rel_path = args.get("path", "")
            remote = args.get("remote", "origin")
            branch = args.get("branch", "")

            target_dir = resolve_path(rel_path) if rel_path else DOC_DIR
            git_root = _find_git_root(target_dir)
            if not git_root:
                return "⚠️ Questa directory non è un repository git."

            if not branch:
                _, out_b, _ = _run_cmd("git branch --show-current", git_root)
                branch = out_b

            approved = await ask_confirmation(
                bot, chat_id,
                f"Git push: `{remote}/{branch}` in `{os.path.relpath(git_root, DOC_DIR)}/`"
            )
            if not approved:
                return "❌ Push rifiutato dall'utente."

            rc, out, err = _run_cmd(f"git push {remote} {branch}", git_root, timeout=120)
            if rc == 0:
                return f"✅ Push completato:\n```\n{out}\n```"
            return f"❌ Push fallito:\n{err[:500]}"

        # ── SHELL ──

        elif name == "run_shell_command":
            cmd = args["command"]
            rel_dir = args.get("directory", "")
            timeout = min(args.get("timeout", 60), 300)
            target_dir = resolve_path(rel_dir) if rel_dir else DOC_DIR

            # Comandi considerati sicuri (read-only)
            READONLY_COMMANDS = [
                "ls", "find", "cat", "head", "tail", "grep", "pwd", "echo",
                "date", "whoami", "id", "uname", "df", "du", "ps", "uptime",
                "which", "file", "stat", "diff", "sort", "cut", "wc", "printenv",
                "python3 -c", "python3 -m", "pip list", "pip show",
            ]
            base_cmd = cmd.strip().split()[0] if cmd.strip() else ""
            # Controlla se il comando inizia con un prefisso read-only
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
                return f"❌ Comando '{base_cmd}' non consentito."

            # Salta conferma per read-only
            if not is_readonly:
                approved = await ask_confirmation(
                    bot, chat_id,
                    f"Esecuzione in `{os.path.relpath(target_dir, DOC_DIR)}/`:\n$ {cmd[:300]}"
                )
                if not approved:
                    return "❌ Comando rifiutato dall'utente."

            try:
                result = subprocess.run(cmd, shell=True, cwd=target_dir,
                                        capture_output=True, text=True, timeout=timeout)
                out = result.stdout.strip()
                err = result.stderr.strip()

                # Previeni context window overflow
                if len(out) > 4000:
                    out = out[:2000] + "\n⏤⏤⏤ [TRUNCATED] ⏤⏤⏤\n" + out[-2000:]
                if len(err) > 4000:
                    err = err[:2000] + "\n⏤⏤⏤ [TRUNCATED] ⏤⏤⏤\n" + err[-2000:]

                if result.returncode == 0:
                    return f"✅ `{cmd}`\n```\n{out}\n```" + (f"\nErr:\n```\n{err}\n```" if err else "")
                else:
                    return f"❌ `{cmd}` (exit {result.returncode})\n```\n{out}\n```\nErr:\n```\n{err}\n```"
            except subprocess.TimeoutExpired:
                return f"⏳ Comando terminato per timeout ({timeout}s)."
            except Exception as e:
                return f"❌ Errore: {e}"

        # ── DYNAMIC SKILLS ──

        elif name.startswith("skill_"):
            from skills_manager import execute_dynamic_skill
            approved = await ask_confirmation(bot, chat_id, f"Esecuzione Skill: {name}\n{args}")
            if not approved:
                return "❌ Skill rifiutata dall'utente."
            return await execute_dynamic_skill(name, args)

        else:
            return f"⚠️ Tool `{name}` sconosciuto."

    except Exception as e:
        logger.error(f"Errore tool {name}: {e}", exc_info=True)
        return f"❌ Errore durante l'esecuzione di `{name}`: {str(e)}"


# Inietta dinamicamente le Skill YAML dalla cartella skills/
try:
    from skills_manager import get_skills_schemas
    TOOLS_SCHEMA.extend(get_skills_schemas())
except ImportError:
    pass
