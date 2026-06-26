import os
import json
import logging
from config import DOC_DIR

logger = logging.getLogger(__name__)

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Scrive o sovrascrive un file fisico all'interno del progetto dell'utente. Usa questo tool per editare il codice. Richiede autorizzazione.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Percorso relativo al progetto (es. SlotBuilder/main.py)"},
                    "content": {"type": "string", "description": "Codice sorgente o contenuto completo da scrivere nel file"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Elimina un file fisico dal progetto dell'utente. Richiede autorizzazione.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Percorso relativo al progetto (es. SlotBuilder/vecchio_file.py)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Legge il contenuto completo di un file fisico per analizzarlo. NON richiede autorizzazione.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Percorso relativo al progetto (es. SlotBuilder/main.py)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "replace_in_file",
            "description": "Sostituisce un blocco di testo esatto all'interno di un file fisico. Ideale per fare modifiche mirate a file senza sovrascriverli per intero. Richiede autorizzazione.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Percorso relativo al progetto (es. cartella/main.py)."},
                    "target_text": {"type": "string", "description": "Il blocco di codice originale esatto da trovare (rispettare indentazione e spazi)."},
                    "replacement_text": {"type": "string", "description": "Il nuovo blocco di codice da inserire."}
                },
                "required": ["path", "target_text", "replacement_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "Elenca i file e le cartelle in una directory del progetto. NON richiede autorizzazione. Usa questo tool quando l'utente chiede di vedere/elencare/esplorare i file in una cartella, o quando dice 'esegui' dopo che hai suggerito di listare una directory. I file di codice (.py, .go, .ts, .tsx, .js, .rs, .java, .c, .cpp, .sql, .yaml, .md, .json, .txt, .html, .css) vengono mostrati per primi.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Percorso relativo al progetto (es. '' per la root del progetto, 'SlotBuilder/' per una sotto-directory)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell_command",
            "description": "Esegue un comando bash nel container. Per comandi DISTRUTTIVI (git commit, git push, rm, mv, touch, mkdir, write) richiede autorizzazione. Per comandi READ-ONLY (ls, find, cat, head, tail, grep, diff, pwd, stat, which, file, sort, cut, wc, du, df, uptime, echo, date, whoami, id, uname, ps) NON richiede autorizzazione. Usalo per ispezionare il progetto (ls, find, grep, cat), per informazioni di sistema, o quando l'utente dice 'esegui' dopo aver chiesto un comando.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Il comando bash da eseguire."},
                    "directory": {"type": "string", "description": "La cartella di lavoro in cui eseguire il comando (es. SlotBuilder/). Default: root del progetto."}
                },
                "required": ["command"]
            }
        }
    }
]

def resolve_path(rel_path):
    safe_path = os.path.normpath(os.path.join(DOC_DIR, rel_path))
    if not safe_path.startswith(DOC_DIR):
        raise ValueError("Path escape attempt")
    return safe_path

import asyncio
pending_confirmations = {}

async def ask_confirmation(bot, chat_id, action_desc):
    if not bot or not chat_id:
        return True # Bypass if no bot available
        
    future = asyncio.Future()
    pending_confirmations[chat_id] = future
    
    await bot.send_message(
        chat_id=chat_id, 
        text=f"⚠️ **ATTENZIONE: Richiesta Autorizzazione**\nL'LLM sta per eseguire:\n`{action_desc}`\n\nVuoi autorizzare? Rispondi con **Y** o **N**.",
        parse_mode="Markdown"
    )
    
    try:
        # Attesa massima 5 minuti
        approved = await asyncio.wait_for(future, timeout=300)
        return approved
    except asyncio.TimeoutError:
        pending_confirmations.pop(chat_id, None)
        return False

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
        if name == "write_file":
            path = resolve_path(args["path"])
            approved = await ask_confirmation(bot, chat_id, f"Scrittura file in: {path}")
            if not approved:
                return f"❌ Scrittura rifiutata dall'utente."
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(args["content"])
            return f"✅ File {args['path']} scritto con successo su disco."
            
        elif name == "delete_file":
            path = resolve_path(args["path"])
            approved = await ask_confirmation(bot, chat_id, f"Eliminazione file: {path}")
            if not approved:
                return f"❌ Eliminazione rifiutata dall'utente."
            if os.path.exists(path):
                os.remove(path)
                return f"✅ File {args['path']} eliminato fisicamente."
            return "⚠️ File non trovato."
            
        elif name == "read_file":
            path = resolve_path(args["path"])
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    fc = f.read()
                    if len(fc) > 8000:
                        fc = fc[:4000] + f"\n...[TRUNCATED {len(fc)-8000} chars]...\n" + fc[-4000:]
                    return fc
            return "⚠️ File non trovato."
            
        elif name == "replace_in_file":
            path = resolve_path(args["path"])
            target = args.get("target_text", "")
            replacement = args.get("replacement_text", "")
            approved = await ask_confirmation(bot, chat_id, f"Patch file: {path}\n\n**DA:**\n```\n{target[:300]}...\n```\n\n**A:**\n```\n{replacement[:300]}...\n```")
            if not approved:
                return f"❌ Modifica (patch) rifiutata dall'utente."
            
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                
                if target in content:
                    content = content.replace(target, replacement, 1)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(content)
                    return f"✅ File {args['path']} patchato con successo."
                else:
                    return "⚠️ ERRORE: target_text non trovato nel file. Forse l'indentazione o i ritorni a capo non combaciano esattamente. Usa read_file per assicurarti del contenuto esatto."
            return "⚠️ File non trovato."

        elif name == "list_directory":
            rel_path = args.get("path", "")
            target_dir = resolve_path(rel_path) if rel_path else DOC_DIR
            
            if not os.path.isdir(target_dir):
                return f"⚠️ Directory non trovata: {rel_path or '(root)'}"
            
            items = sorted(os.listdir(target_dir))
            dirs = []
            files_code = []
            files_other = []
            for item in items:
                if item.startswith('.'):
                    continue
                full = os.path.join(target_dir, item)
                if os.path.isdir(full):
                    dirs.append(f"📁 {item}/")
                elif any(item.endswith(ext) for ext in ('.py', '.go', '.ts', '.tsx', '.js', '.jsx', '.rs', '.java', '.c', '.cpp', '.h', '.hpp', '.sql', '.yaml', '.yml', '.md', '.json', '.txt', '.html', '.css', '.sh', '.toml')):
                    files_code.append(f"📄 {item}")
                else:
                    files_other.append(f"📄 {item}")
            
            lines = [f"📂 *{rel_path or 'Root'}* ({len(dirs)} dirs, {len(files_code)} code files, {len(files_other)} altri):"]
            if dirs:
                lines.append("\n📁 **Cartelle:**")
                lines.extend(dirs)
            if files_code:
                lines.append("\n📄 **File di codice:**")
                lines.extend(files_code)
            if files_other:
                lines.append("\n📄 **Altri file:**")
                lines.extend(files_other)
            if not dirs and not files_code and not files_other:
                lines.append("\n_Cartella vuota._")
            
            return "\n".join(lines)
            
        elif name == "run_shell_command":
            cmd = args["command"]
            rel_dir = args.get("directory", "")
            target_dir = resolve_path(rel_dir) if rel_dir else DOC_DIR
            
            READONLY_COMMANDS = ["ls", "find", "cat", "head", "tail", "grep", "pwd", "echo", "date", "whoami", "id", "uname", "df", "du", "ps", "uptime", "which", "file", "stat", "diff", "sort", "cut", "wc", "printenv"]
            base_cmd = cmd.strip().split()[0] if cmd.strip() else ""
            ALLOWED_COMMANDS = READONLY_COMMANDS + ["git", "mkdir", "touch", "rm", "mv", "cp", "chmod", "chown"]
            
            if base_cmd not in ALLOWED_COMMANDS:
                return f"❌ Comando '{cmd}' non consentito. Comandi permessi: {', '.join(ALLOWED_COMMANDS)}"
            
            # Salta conferma per comandi read-only (esecuzione immediata)
            needs_confirmation = base_cmd not in READONLY_COMMANDS
            if needs_confirmation:
                approved = await ask_confirmation(bot, chat_id, f"Esecuzione bash in {target_dir}:\n$ {cmd}")
                if not approved:
                    return f"❌ Comando rifiutato dall'utente."
            
            import subprocess
            try:
                result = subprocess.run(cmd, shell=True, cwd=target_dir, capture_output=True, text=True, timeout=60)
                out = result.stdout.strip()
                err = result.stderr.strip()
                
                # Previene OOM del context window limitando l'output
                if len(out) > 4000: out = out[:2000] + "\n...[TRUNCATED]...\n" + out[-2000:]
                if len(err) > 4000: err = err[:2000] + "\n...[TRUNCATED]...\n" + err[-2000:]
                
                if result.returncode == 0:
                    return f"✅ Comando '{cmd}' completato.\nOut:\n{out}" + (f"\nErr:\n{err}" if err else "")
                else:
                    return f"❌ Comando '{cmd}' fallito (exit {result.returncode}).\nOut:\n{out}\nErr:\n{err}"
            except subprocess.TimeoutExpired:
                return f"⏳ Comando timeout."
            except Exception as e:
                return f"❌ Errore sistema: {e}"

        elif name.startswith("skill_"):
            from skills_manager import execute_dynamic_skill
            approved = await ask_confirmation(bot, chat_id, f"Esecuzione Skill: {name}\nArgomenti: {args}")
            if not approved:
                return f"❌ Esecuzione Skill rifiutata dall'utente."
            return await execute_dynamic_skill(name, args)

        else:
            return f"⚠️ Tool {name} sconosciuto."
    except Exception as e:
        return f"❌ Errore durante l'esecuzione del tool {name}: {str(e)}"

# Inietta dinamicamente le Skill lette dalla cartella skills/
try:
    from skills_manager import get_skills_schemas
    TOOLS_SCHEMA.extend(get_skills_schemas())
except ImportError:
    pass
