"""
Skills Manager — Gestione skill compatibili con OpenCode SKILL.md.

Supporta:
  - OpenCode SKILL.md format (YAML frontmatter + markdown body)
  - Legacy YAML skill format (per backward compatibility)
  - Skill-embedded MCP servers (SKILL.md frontmatter → MCP tools)
  - Skill discovery da .opencode/skills/, .claude/skills/, .agents/skills/
  - Skill loading on-demand via skill() function (stile OpenCode)
"""

import os
import re
import yaml
import json
import logging
import asyncio
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger("jarvis.skills")

# ──────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────

LEGACY_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")

# OpenCode-standard skill paths (relative to project root)
OPENCODE_SKILL_PATHS = [
    ".opencode/skills",
    ".claude/skills",
    ".agents/skills",
]

# Global skill paths
GLOBAL_SKILL_PATHS = [
    os.path.expanduser("~/.config/opencode/skills"),
    os.path.expanduser("~/.claude/skills"),
    os.path.expanduser("~/.agents/skills"),
]

# ──────────────────────────────────────────────
# Skill Cache
# ──────────────────────────────────────────────

_skill_cache: Dict[str, dict] = {}  # name -> skill definition
_skill_cache_root: str = ""  # project_root used for last cache


def _skill_name_valid(name: str) -> bool:
    """Validate skill name per OpenCode spec:
    lowercase alphanumeric with single hyphen separators."""
    return bool(re.match(r'^[a-z0-9]+(-[a-z0-9]+)*$', name)) and len(name) <= 64


# ──────────────────────────────────────────────
# SKILL.md Parser
# ──────────────────────────────────────────────

def _parse_skill_md(filepath: str) -> Optional[dict]:
    """Parse an OpenCode SKILL.md file.

    Format:
    ---
    name: my-skill
    description: Does X and Y
    license: MIT
    compatibility: opencode
    metadata:
      audience: developers
    mcp_servers:
      my-local-server:
        command: [npx, -y, some-mcp-server]
    ---
    ## Skill Body
    Markdown content with instructions...
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        logger.warning(f"SKILL.md read error {filepath}: {e}")
        return None

    # Parse YAML frontmatter (between --- delimiters)
    fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n?(.*)', content, re.DOTALL)
    if not fm_match:
        return None

    yaml_str = fm_match.group(1)
    body = fm_match.group(2).strip()

    try:
        fm = yaml.safe_load(yaml_str)
    except yaml.YAMLError as e:
        logger.warning(f"SKILL.md YAML error {filepath}: {e}")
        return None

    if not isinstance(fm, dict):
        return None

    name = fm.get("name", "")
    description = fm.get("description", "")

    if not name or not description:
        logger.warning(f"SKILL.md missing name/description: {filepath}")
        return None

    if not _skill_name_valid(name):
        logger.warning(f"SKILL.md invalid name '{name}': {filepath}")
        return None

    # Extract MCP server definitions from frontmatter
    mcp_servers = {}
    raw_mcp = fm.get("mcp_servers", {})
    if raw_mcp and isinstance(raw_mcp, dict):
        for srv_name, srv_cfg in raw_mcp.items():
            if isinstance(srv_cfg, dict):
                cmd = srv_cfg.get("command", [])
                if cmd:
                    mcp_servers[srv_name] = {
                        "command": cmd if isinstance(cmd, list) else [cmd],
                        "cwd": srv_cfg.get("cwd"),
                        "env": srv_cfg.get("env"),
                    }

    return {
        "name": name,
        "description": description,
        "license": fm.get("license"),
        "compatibility": fm.get("compatibility"),
        "metadata": fm.get("metadata", {}),
        "mcp_servers": mcp_servers,
        "body": body,
        "filepath": filepath,
        "source": "opencode_skill",
    }


# ──────────────────────────────────────────────
# Legacy YAML Parser
# ──────────────────────────────────────────────

def _parse_legacy_yaml(filepath: str) -> Optional[dict]:
    """Parse legacy Jarvis YAML skill format."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        return None

    if not isinstance(data, dict) or "name" not in data:
        return None

    name = str(data["name"])
    # Convert legacy name to valid skill name
    safe_name = re.sub(r'[^a-z0-9-]', '-', name.lower().replace("_", "-"))
    safe_name = re.sub(r'-+', '-', safe_name).strip('-')

    # Build properties from parameters
    properties = {}
    required_params = []
    for param_name, param_info in data.get("parameters", {}).items():
        properties[param_name] = {
            "type": param_info.get("type", "string"),
            "description": param_info.get("description", ""),
        }
        if param_info.get("required", True):
            required_params.append(param_name)

    return {
        "name": safe_name,
        "original_name": name,
        "description": data.get("description", ""),
        "body": f"## {name}\n\n{data.get('description', '')}\n\nCommands:\n" +
                "\n".join(f"- `{c}`" for c in data.get("commands", [])),
        "commands": data.get("commands", []),
        "parameters": data.get("parameters", {}),
        "properties": properties,
        "required_params": required_params,
        "filepath": filepath,
        "source": "legacy_yaml",
    }


# ──────────────────────────────────────────────
# Scanning
# ──────────────────────────────────────────────

def _scan_skill_directory(skills_dir: str, project_root: str = "") -> List[dict]:
    """Scan a directory for skill definitions (SKILL.md or .yaml/.yml)."""
    results = []
    if not os.path.isdir(skills_dir):
        return results

    try:
        entries = sorted(os.listdir(skills_dir))
    except PermissionError:
        return results

    for entry in entries:
        skill_path = os.path.join(skills_dir, entry)

        if os.path.isdir(skill_path):
            # Look for SKILL.md inside subdirectory
            skill_md = os.path.join(skill_path, "SKILL.md")
            if os.path.isfile(skill_md):
                parsed = _parse_skill_md(skill_md)
                if parsed:
                    results.append(parsed)
                    logger.debug(f"Skill loaded: {parsed['name']} from {skill_md}")
        elif entry.endswith((".yaml", ".yml")) and not entry.startswith("."):
            # Legacy YAML skill file
            parsed = _parse_legacy_yaml(skill_path)
            if parsed:
                results.append(parsed)
                logger.debug(f"Legacy skill loaded: {parsed['name']} from {skill_path}")

    return results


def discover_skills(project_root: str = "") -> Dict[str, dict]:
    """Discover all available skills from all standard paths.
    
    Returns dict of skill_name -> skill_definition.
    Cache is invalidated when project_root changes.
    """
    global _skill_cache, _skill_cache_root

    # Re-scan if project_root changed (cache invalidation)
    if _skill_cache_root != project_root:
        _skill_cache = {}
        _skill_cache_root = project_root

    if _skill_cache:
        return _skill_cache

    scanned_dirs = set()

    # Scan project-relative OpenCode paths
    if project_root:
        for rel_path in OPENCODE_SKILL_PATHS:
            abs_path = os.path.join(project_root, rel_path)
            if abs_path not in scanned_dirs:
                scanned_dirs.add(abs_path)
                for skill in _scan_skill_directory(abs_path, project_root):
                    _skill_cache[skill["name"]] = skill

    # Scan global OpenCode paths
    for global_path in GLOBAL_SKILL_PATHS:
        if global_path not in scanned_dirs:
            scanned_dirs.add(global_path)
            for skill in _scan_skill_directory(global_path):
                _skill_cache[skill["name"]] = skill

    # Scan legacy skills dir (jarvis/skills/)
    if LEGACY_SKILLS_DIR not in scanned_dirs:
        scanned_dirs.add(LEGACY_SKILLS_DIR)
        for skill in _scan_skill_directory(LEGACY_SKILLS_DIR):
            # Legacy skills might have invalid names (underscores, mixed case)
            # but we still expose them
            _skill_cache[skill["name"]] = skill

    logger.info(f"Skills: {len(_skill_cache)} discovered "
                f"({sum(1 for s in _skill_cache.values() if s['source'] == 'opencode_skill')} OpenCode, "
                f"{sum(1 for s in _skill_cache.values() if s['source'] == 'legacy_yaml')} legacy)")
    return _skill_cache


# ──────────────────────────────────────────────
# Tool Schema Generation (OpenCode-compatible)
# ──────────────────────────────────────────────

def get_skill_openai_tools(project_root: str = "") -> List[dict]:
    """Generate OpenAI function-calling tool schemas for all discovered skills.

    OpenCode skill() tool equivalent.
    """
    skills = discover_skills(project_root)
    tools = []

    for name, skill_def in skills.items():
        if skill_def["source"] == "opencode_skill":
            # OpenCode SKILL.md → simple load tool (no params)
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
        elif skill_def["source"] == "legacy_yaml":
            # Legacy YAML → parameterized execution tool
            properties = skill_def.get("properties", {})
            required_params = skill_def.get("required_params", [])
            tools.append({
                "type": "function",
                "function": {
                    "name": f"skill_{name}",
                    "description": f"[SKILL DINAMICA] {skill_def.get('description', '')}",
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required_params
                    }
                }
            })

    return tools


def get_skill_descriptions(project_root: str = "") -> str:
    """Get available skills list as XML (OpenCode-compatible format)."""
    skills = discover_skills(project_root)
    if not skills:
        return ""

    lines = ["<available_skills>"]
    for name, skill_def in sorted(skills.items()):
        desc = skill_def.get("description", "")
        lines.append(f"  <skill>\n    <name>{name}</name>\n    <description>{desc}</description>\n  </skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)


# ──────────────────────────────────────────────
# Skill Execution
# ──────────────────────────────────────────────

async def load_skill(name: str, project_root: str = "") -> Optional[str]:
    """Load a skill by name, returning its full body/markdown content.
    
    Equivalent to OpenCode's skill() tool call.
    Returns None if not found.
    """
    skills = discover_skills(project_root)
    # Strip skill_ prefix if present
    clean_name = name[6:] if name.startswith("skill_") else name
    skill_def = skills.get(clean_name)

    if not skill_def:
        return None

    source = skill_def.get("source", "")

    if source == "opencode_skill":
        body = skill_def.get("body", "")
        mcp_servers = skill_def.get("mcp_servers", {})

        result = body
        if mcp_servers:
            result += "\n\n### Embedded MCP Servers\n"
            for srv_name, srv_cfg in mcp_servers.items():
                result += f"- `{srv_name}`: {' '.join(srv_cfg.get('command', []))}\n"

            # Auto-register MCP servers if MCP manager is available
            try:
                from mcp_client import get_mcp_manager
                manager = get_mcp_manager()
                for srv_name, srv_cfg in mcp_servers.items():
                    if not manager.get_server(srv_name):
                        manager.register_stdio(
                            srv_name,
                            srv_cfg.get("command", []),
                            srv_cfg.get("cwd"),
                            srv_cfg.get("env"),
                        )
                        logger.info(f"Skill-embedded MCP '{srv_name}' registered from skill '{clean_name}'")
            except ImportError:
                pass

        return result

    elif source == "legacy_yaml":
        commands = skill_def.get("commands", [])
        params = skill_def.get("parameters", {})
        result = skill_def.get("body", "")
        if commands:
            result += "\n\nTo execute this skill, provide parameters:\n"
            for pname, pinfo in params.items():
                result += f"- `{pname}` ({pinfo.get('type', 'string')}): {pinfo.get('description', '')}\n"
        return result

    return None


async def execute_skill(name: str, kwargs: Dict[str, Any],
                        project_root: str = "") -> str:
    """Execute a legacy YAML skill (sequential commands).
    
    For OpenCode SKILL.md, use load_skill() instead.
    Returns execution result string.
    """
    skills = discover_skills(project_root)
    clean_name = name[6:] if name.startswith("skill_") else name
    skill_def = skills.get(clean_name)

    if not skill_def:
        return f"❌ Skill '{clean_name}' non trovata."

    if skill_def.get("source") == "opencode_skill":
        # SKILL.md skills don't auto-execute commands; return body for LLM to read
        body = skill_def.get("body", "")
        mcp = skill_def.get("mcp_servers", {})
        result = f"📖 **Skill: {clean_name}**\n\n{body}"
        if mcp:
            result += "\n\n**🔌 MCP Servers disponibili:**\n"
            for srv in mcp:
                result += f"- `{srv}`\n"
        return result

    # Legacy YAML: execute commands sequentially
    commands = skill_def.get("commands", [])
    if not commands:
        return f"⚠️ Skill '{clean_name}' non contiene comandi."

    results = []
    for cmd_template in commands:
        try:
            cmd = cmd_template.format(**kwargs)
            logger.info(f"Skill cmd: {cmd}")
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
                results.append(f"✅ [{cmd}]:\n{out}")
            else:
                results.append(f"❌ [{cmd}] FALLITO:\nOut: {out}\nErr: {err}")
                results.append("⛔ Skill interrotta per errore.")
                break
        except KeyError as e:
            results.append(f"❌ Parametro mancante {e} in '{cmd_template}'")
            break
        except Exception as e:
            results.append(f"❌ Errore: {e}")
            break

    return "\n\n".join(results)


def get_skill(name: str) -> Optional[dict]:
    """Get raw skill definition by name."""
    skills = discover_skills()
    clean_name = name[6:] if name.startswith("skill_") else name
    return skills.get(clean_name)


def update_skill_mcp_servers(project_root: str = ""):
    """Register skill-embedded MCP servers from all discovered OpenCode skills.
    
    Called once at startup to ensure skill MCPs are available.
    """
    skills = discover_skills(project_root)
    registered = 0

    try:
        from mcp_client import get_mcp_manager
        manager = get_mcp_manager()
    except ImportError:
        return 0

    for name, skill_def in skills.items():
        mcp_servers = skill_def.get("mcp_servers", {})
        for srv_name, srv_cfg in mcp_servers.items():
            if not manager.get_server(srv_name):
                manager.register_stdio(
                    srv_name,
                    srv_cfg.get("command", []),
                    srv_cfg.get("cwd"),
                    srv_cfg.get("env"),
                )
                registered += 1
                logger.info(f"Skill-embedded MCP '{srv_name}' from skill '{name}'")

    if registered > 0:
        logger.info(f"Skills: {registered} embedded MCP servers registered")

    return registered


# ──────────────────────────────────────────────
# Backward Compatibility
# ──────────────────────────────────────────────

def get_skills_schemas() -> List[dict]:
    """Backward-compatible: returns legacy tool schemas for TOOLS_SCHEMA extension."""
    return get_skill_openai_tools()


def ensure_skills_dir():
    """Ensure legacy skills directory exists."""
    os.makedirs(LEGACY_SKILLS_DIR, exist_ok=True)


async def execute_dynamic_skill(skill_name: str, kwargs: Dict[str, Any]) -> str:
    """Backward-compatible: execute skill by name."""
    return await execute_skill(skill_name, kwargs)
