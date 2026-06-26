"""
Skills Manager — Jarvis Skill System nativo.

Jarvis skills sono blocchi di istruzioni o comandi che estendono le capacita del LLM.
Possono essere caricati come contesto (instruction) o eseguiti (command).

Formati supportati (Jarvis-native):
  1. YAML (.yaml/.yml) in jarvis/skills/ — comandi sequenziali con template
  2. Markdown (.skill.md) in jarvis/skills/ — istruzioni per il LLM + opzionali MCP servers

Struttura directory:
  - jarvis/skills/           Skill locali al progetto
  - ~/.config/jarvis/skills/ Skill globali dell'utente
"""

import os
import re
import yaml
import json
import logging
import asyncio
from typing import Dict, List, Any, Optional

logger = logging.getLogger("jarvis.skills")

# ──────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────

LOCAL_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")
GLOBAL_SKILLS_DIR = os.path.join(os.path.expanduser("~"), ".config", "jarvis", "skills")

# ──────────────────────────────────────────────
# Cache
# ──────────────────────────────────────────────

_skill_cache: Dict[str, dict] = {}
_skill_cache_key: str = ""


def _cache_key() -> str:
    return f"local:{os.path.isdir(LOCAL_SKILLS_DIR)}|global:{os.path.isdir(GLOBAL_SKILLS_DIR)}"


def _invalidated_cache() -> bool:
    global _skill_cache_key
    key = _cache_key()
    if _skill_cache and _skill_cache_key == key:
        return True
    _skill_cache_key = key
    _skill_cache.clear()
    return False


def _skill_name_valid(name: str) -> bool:
    return bool(re.match(r'^[a-z][a-z0-9_-]*$', name)) and len(name) <= 64


# ═══════════════════════════════════════════════
# PARSER: Jarvis .skill.md format
# ═══════════════════════════════════════════════

def _parse_skill_md(filepath: str) -> Optional[dict]:
    """
    Parse a Jarvis .skill.md file.

    Formato Jarvis-native:
    ---
    name: my-skill
    description: Cosa fa questa skill
    type: instruction      # instruction | command | hybrid
    mcp_servers:           # opzionale: MCP servers embedded
      my-db:
        command: [npx, -y, some-mcp]
    ---
    ## Istruzioni per il LLM
    Corpo markdown con le istruzioni...
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        logger.warning(f"Skill read error {filepath}: {e}")
        return None

    fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n?(.*)', content, re.DOTALL)
    if not fm_match:
        return None

    yaml_str = fm_match.group(1)
    body = fm_match.group(2).strip()

    try:
        fm = yaml.safe_load(yaml_str)
    except yaml.YAMLError as e:
        logger.warning(f"Skill YAML error {filepath}: {e}")
        return None

    if not isinstance(fm, dict):
        return None

    name = str(fm.get("name", "")).strip()
    description = str(fm.get("description", "")).strip()

    if not name or not description:
        logger.warning(f"Skill .skill.md missing name/description: {filepath}")
        return None

    if not _skill_name_valid(name):
        logger.warning(f"Skill invalid name '{name}': {filepath}")
        return None

    skill_type = fm.get("type", "instruction")
    if skill_type not in ("instruction", "command", "hybrid"):
        skill_type = "instruction"

    # MCP servers embedded
    mcp_servers = {}
    raw_mcp = fm.get("mcp_servers", {})
    if isinstance(raw_mcp, dict):
        for srv_name, srv_cfg in raw_mcp.items():
            if isinstance(srv_cfg, dict):
                cmd = srv_cfg.get("command", [])
                if cmd:
                    mcp_servers[srv_name] = {
                        "command": cmd if isinstance(cmd, list) else [cmd],
                        "cwd": srv_cfg.get("cwd"),
                        "env": srv_cfg.get("env", {}),
                    }

    return {
        "name": name,
        "description": description,
        "type": skill_type,
        "body": body,
        "mcp_servers": mcp_servers,
        "filepath": filepath,
        "source": "skill_md",
    }


# ═══════════════════════════════════════════════
# PARSER: Legacy YAML (.yaml/.yml)
# ═══════════════════════════════════════════════

def _parse_legacy_yaml(filepath: str) -> Optional[dict]:
    """Parse legacy Jarvis YAML skill (comandi sequenziali con template)."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception:
        return None

    if not isinstance(data, dict) or "name" not in data:
        return None

    name = str(data["name"])
    safe_name = re.sub(r'[^a-z0-9_-]', '-', name.lower().replace(" ", "-"))
    safe_name = re.sub(r'-+', '-', safe_name).strip('-')

    if not safe_name or not _skill_name_valid(safe_name):
        safe_name = f"skill_{hash(name) % 10000}"

    properties = {}
    required_params = []
    for pname, pinfo in data.get("parameters", {}).items():
        properties[pname] = {
            "type": pinfo.get("type", "string"),
            "description": pinfo.get("description", ""),
        }
        if pinfo.get("required", True):
            required_params.append(pname)

    return {
        "name": safe_name,
        "original_name": name,
        "description": data.get("description", ""),
        "type": "command",
        "body": f"## {name}\n\n{data.get('description', '')}\n\nComandi:\n" +
                "\n".join(f"- `{c}`" for c in data.get("commands", [])),
        "commands": data.get("commands", []),
        "parameters": data.get("parameters", {}),
        "properties": properties,
        "required_params": required_params,
        "mcp_servers": {},
        "filepath": filepath,
        "source": "legacy_yaml",
    }


# ═══════════════════════════════════════════════
# Discovery
# ═══════════════════════════════════════════════

def _scan_directory(skills_dir: str) -> List[dict]:
    """Scan a directory for skill files (.skill.md, .yaml, .yml)."""
    results = []
    if not os.path.isdir(skills_dir):
        return results

    try:
        entries = sorted(os.listdir(skills_dir))
    except PermissionError:
        return results

    for entry in entries:
        path = os.path.join(skills_dir, entry)

        if os.path.isfile(path):
            if entry.endswith(".skill.md"):
                parsed = _parse_skill_md(path)
                if parsed:
                    results.append(parsed)
            elif entry.endswith((".yaml", ".yml")) and not entry.startswith("."):
                parsed = _parse_legacy_yaml(path)
                if parsed:
                    results.append(parsed)
        elif os.path.isdir(path) and not entry.startswith("."):
            nested_md = os.path.join(path, f"{entry}.skill.md")
            if os.path.isfile(nested_md):
                parsed = _parse_skill_md(nested_md)
                if parsed:
                    results.append(parsed)

    return results


def discover_skills() -> Dict[str, dict]:
    """Discover all available skills from all Jarvis-native paths.

    Returns dict of skill_name -> skill_definition.
    Cache invalidated when filesystem state changes.
    """
    global _skill_cache, _skill_cache_key

    if _invalidated_cache() and _skill_cache:
        return _skill_cache

    scanned = set()

    # 1. Local skills: jarvis/skills/
    if LOCAL_SKILLS_DIR not in scanned:
        scanned.add(LOCAL_SKILLS_DIR)
        for skill in _scan_directory(LOCAL_SKILLS_DIR):
            _skill_cache[skill["name"]] = skill

    # 2. Global skills: ~/.config/jarvis/skills/
    if GLOBAL_SKILLS_DIR not in scanned:
        scanned.add(GLOBAL_SKILLS_DIR)
        for skill in _scan_directory(GLOBAL_SKILLS_DIR):
            _skill_cache[skill["name"]] = skill

    if _skill_cache:
        by_type = {}
        for s in _skill_cache.values():
            t = s.get("source", "unknown")
            by_type[t] = by_type.get(t, 0) + 1
        types_desc = ", ".join(f"{k}={v}" for k, v in by_type.items())
        logger.info(f"Skills: {len(_skill_cache)} trovate ({types_desc})")

    return _skill_cache


# ═══════════════════════════════════════════════
# Tool Schema Generation
# ═══════════════════════════════════════════════

def get_skill_tools() -> List[dict]:
    """Generate OpenAI function-calling schemas per le skill Jarvis."""
    skills = discover_skills()
    tools = []

    for name, skill_def in skills.items():
        stype = skill_def.get("type", "instruction")
        source = skill_def.get("source", "")

        if source == "skill_md" and stype == "instruction":
            tools.append({
                "type": "function",
                "function": {
                    "name": f"skill_{name}",
                    "description": f"[SKILL] {skill_def.get('description', '')}",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            })
        elif source == "skill_md" and stype in ("command", "hybrid"):
            tools.append({
                "type": "function",
                "function": {
                    "name": f"skill_{name}",
                    "description": f"[SKILL] {skill_def.get('description', '')}",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "args": {
                                "type": "string",
                                "description": "Argomenti per la skill in formato JSON (opzionale)"
                            }
                        },
                        "required": []
                    }
                }
            })
        elif source == "legacy_yaml":
            properties = skill_def.get("properties", {})
            required_params = skill_def.get("required_params", [])
            tools.append({
                "type": "function",
                "function": {
                    "name": f"skill_{name}",
                    "description": f"[SKILL] {skill_def.get('description', '')}",
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required_params
                    }
                }
            })

    return tools


def get_skill_list_xml() -> str:
    """Get available skills in XML format per il system prompt."""
    skills = discover_skills()
    if not skills:
        return ""

    lines = ["<available_skills>"]
    for name, skill_def in sorted(skills.items()):
        desc = skill_def.get("description", "")
        stype = skill_def.get("type", "instruction")
        lines.append(
            f"  <skill>\n"
            f"    <name>{name}</name>\n"
            f"    <type>{stype}</type>\n"
            f"    <description>{desc}</description>\n"
            f"  </skill>"
        )
    lines.append("</available_skills>")
    return "\n".join(lines)


# ═══════════════════════════════════════════════
# Skill Loading & Execution
# ═══════════════════════════════════════════════

async def load_skill(name: str) -> Optional[str]:
    """Carica una skill by name e restituisce il contenuto testuale."""
    skills = discover_skills()
    clean_name = name[6:] if name.startswith("skill_") else name
    skill_def = skills.get(clean_name)

    if not skill_def:
        return None

    source = skill_def.get("source", "")

    if source == "skill_md":
        body = skill_def.get("body", "")
        mcp = skill_def.get("mcp_servers", {})
        result = body
        if mcp:
            result += "\n\n### 🔌 MCP Servers inclusi\n"
            for srv_name, srv_cfg in mcp.items():
                result += f"- `{srv_name}`: {' '.join(srv_cfg.get('command', []))}\n"
            try:
                from mcp_client import get_mcp_manager
                manager = get_mcp_manager()
                for srv_name, srv_cfg in mcp.items():
                    if not manager.get_server(srv_name):
                        manager.register_stdio(
                            srv_name,
                            srv_cfg.get("command", []),
                            srv_cfg.get("cwd"),
                            srv_cfg.get("env"),
                        )
                        logger.info(f"MCP '{srv_name}' registrato da skill '{clean_name}'")
                if mcp:
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(manager.initialize_all())
                    except RuntimeError:
                        pass
            except ImportError:
                pass
        return result

    elif source == "legacy_yaml":
        commands = skill_def.get("commands", [])
        result = f"## {skill_def.get('original_name', clean_name)}\n\n{skill_def.get('description', '')}\n"
        if commands:
            result += "\n### Comandi:\n"
            for c in commands:
                result += f"- `{c}`\n"
        params = skill_def.get("parameters", {})
        if params:
            result += "\n### Parametri:\n"
            for pname, pinfo in params.items():
                result += f"- `{pname}` ({pinfo.get('type', 'string')}): {pinfo.get('description', '')}\n"
        return result

    return None


async def execute_skill(name: str, kwargs: Dict[str, Any]) -> str:
    """Esegue una skill by name.

    .skill.md instruction → carica contesto
    legacy YAML → esegue comandi
    """
    skills = discover_skills()
    clean_name = name[6:] if name.startswith("skill_") else name
    skill_def = skills.get(clean_name)

    if not skill_def:
        return f"❌ Skill '{clean_name}' non trovata."

    source = skill_def.get("source", "")

    if source == "skill_md":
        body = skill_def.get("body", "")
        mcp = skill_def.get("mcp_servers", {})
        result = f"📖 **Skill: {clean_name}**\n\n{body}"
        if mcp:
            result += "\n\n**🔌 MCP Servers inclusi:**\n"
            for srv in mcp:
                result += f"- `{srv}`\n"
        return result

    elif source == "legacy_yaml":
        commands = skill_def.get("commands", [])
        if not commands:
            return f"⚠️ Skill '{clean_name}' senza comandi."

        results = []
        for cmd_template in commands:
            try:
                cmd = cmd_template.format(**kwargs)
                logger.info(f"Skill exec: {cmd}")
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                out = stdout.decode().strip()
                err = stderr.decode().strip()

                if len(out) > 2000:
                    out = out[:1000] + "\n...[TRUNCATED]...\n" + out[-1000:]
                if len(err) > 2000:
                    err = err[:1000] + "\n...[TRUNCATED]...\n" + err[-1000:]

                if proc.returncode == 0:
                    results.append(f"✅ `{cmd}`:\n{out}")
                else:
                    results.append(f"❌ `{cmd}` FALLITO:\n{err}")
                    break
            except KeyError as e:
                results.append(f"❌ Parametro mancante {e}")
                break
            except Exception as e:
                results.append(f"❌ Errore: {e}")
                break

        return "\n\n".join(results)

    return f"⚠️ Skill '{clean_name}' tipo sconosciuto."


# ═══════════════════════════════════════════════
# MCP Embedded Servers
# ═══════════════════════════════════════════════

def register_skill_mcp_servers() -> int:
    """Registra MCP servers embedded nelle skill Jarvis-native .skill.md."""
    skills = discover_skills()
    registered = 0

    try:
        from mcp_client import get_mcp_manager
        manager = get_mcp_manager()
    except ImportError:
        return 0

    for name, skill_def in skills.items():
        for srv_name, srv_cfg in skill_def.get("mcp_servers", {}).items():
            if not manager.get_server(srv_name):
                manager.register_stdio(
                    srv_name,
                    srv_cfg.get("command", []),
                    srv_cfg.get("cwd"),
                    srv_cfg.get("env"),
                )
                registered += 1
                logger.info(f"MCP embedded '{srv_name}' da skill '{name}'")

    if registered:
        logger.info(f"Skills: {registered} MCP embedded registrati")

    return registered


# ═══════════════════════════════════════════════
# Backward Compatibility
# ═══════════════════════════════════════════════

def get_skills_schemas() -> List[dict]:
    """Backward compat: per TOOLS_SCHEMA extension."""
    return get_skill_tools()


def ensure_skills_dir():
    """Assicura che la directory skills esista."""
    os.makedirs(LOCAL_SKILLS_DIR, exist_ok=True)
    os.makedirs(GLOBAL_SKILLS_DIR, exist_ok=True)


async def execute_dynamic_skill(skill_name: str, kwargs: Dict[str, Any]) -> str:
    """Backward compat: execute skill by name."""
    return await execute_skill(skill_name, kwargs)
