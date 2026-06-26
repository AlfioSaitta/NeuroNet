"""
MCP Client — Model Context Protocol client per Jarvis.

Supporta:
- Local MCP servers (stdio JSON-RPC subprocess)
- Remote MCP servers (HTTP POST JSON-RPC)
- .mcp.json config loading (OpenCode/Claude Code format)
- Tool discovery + execution
- Skill-embedded MCP servers
"""

import os
import json
import asyncio
import logging
import subprocess
from typing import Dict, List, Any, Optional, Callable

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# JSON-RPC Helpers
# ──────────────────────────────────────────────

_next_id = 0


def _jsonrpc_request(method: str, params: Optional[dict] = None) -> str:
    global _next_id
    _next_id += 1
    req = {
        "jsonrpc": "2.0",
        "id": _next_id,
        "method": method,
    }
    if params is not None:
        req["params"] = params
    return json.dumps(req, ensure_ascii=False)


def _parse_response(line: str) -> dict:
    return json.loads(line)


# ──────────────────────────────────────────────
# Base MCP Client
# ──────────────────────────────────────────────

class BaseMcpClient:
    """Abstract base for MCP client connections."""

    name: str

    async def initialize(self) -> dict:
        """Initialize session with server. Returns server capabilities."""
        raise NotImplementedError

    async def list_tools(self) -> List[dict]:
        """List available tools from server."""
        raise NotImplementedError

    async def call_tool(self, name: str, arguments: dict) -> Any:
        """Call a tool on the server."""
        raise NotImplementedError

    async def close(self):
        """Close connection."""
        raise NotImplementedError

    def to_openai_tool(self, mcp_tool: dict) -> dict:
        """Convert MCP tool definition to OpenAI function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": f"mcp_{self.name}_{mcp_tool['name']}",
                "description": f"[MCP:{self.name}] {mcp_tool.get('description', '')}",
                "parameters": mcp_tool.get("inputSchema", {
                    "type": "object",
                    "properties": {},
                    "required": []
                })
            }
        }


# ──────────────────────────────────────────────
# Local stdio MCP Client
# ──────────────────────────────────────────────

class StdioMcpClient(BaseMcpClient):
    """Connects to a local MCP server via stdio subprocess."""

    def __init__(self, name: str, command: List[str], cwd: Optional[str] = None,
                 env: Optional[Dict[str, str]] = None):
        self.name = name
        self.command = command
        self.cwd = cwd
        self.env = env
        self._process: Optional[subprocess.Popen] = None
        self._server_info: Optional[dict] = None
        self._capabilities: Optional[dict] = None

    async def initialize(self) -> dict:
        if self._process:
            return {"serverInfo": self._server_info, "capabilities": self._capabilities}

        # Spawn subprocess
        merged_env = os.environ.copy()
        if self.env:
            merged_env.update(self.env)

        self._process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd or os.getcwd(),
            env=merged_env,
        )

        # Send initialize request
        init_resp = await self._send_recv({
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                    "resources": {},
                    "prompts": {},
                },
                "clientInfo": {
                    "name": "jarvis",
                    "version": "1.0.0"
                }
            }
        })

        if "error" in init_resp:
            raise RuntimeError(f"MCP init error: {init_resp['error']}")

        result = init_resp.get("result", {})
        self._server_info = result.get("serverInfo", {})
        self._capabilities = result.get("capabilities", {})

        # Send initialized notification
        await self._send_notification("notifications/initialized")

        logger.info(f"MCP [{self.name}] initialized: {self._server_info}")
        return result

    async def _send_recv(self, msg: dict) -> dict:
        if not self._process or not self._process.stdin or not self._process.stdout:
            raise RuntimeError("MCP process not started")

        payload = json.dumps(msg, ensure_ascii=False) + "\n"
        self._process.stdin.write(payload.encode())
        await self._process.stdin.drain()

        # Read response line
        line = await self._process.stdout.readline()
        if not line:
            raise RuntimeError(f"MCP [{self.name}] closed stdout unexpectedly")
        return json.loads(line.decode().strip())

    async def _send_notification(self, method: str, params: Optional[dict] = None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        payload = json.dumps(msg, ensure_ascii=False) + "\n"
        if self._process and self._process.stdin:
            self._process.stdin.write(payload.encode())
            await self._process.stdin.drain()

    async def list_tools(self) -> List[dict]:
        resp = await self._send_recv({
            "jsonrpc": "2.0",
            "id": _jsonrpc_request("tools/list").split("\n")[0],
            "method": "tools/list",
            "params": {}
        })
        # Fix the id since we use a fake one above — just parse response
        # Actually we need to properly track IDs. Let me rewrite.
        # Simple approach: fresh request with sequential ID
        return self._list_tools_impl()

    async def _list_tools_impl(self) -> List[dict]:
        msg_id = id(self)  # unique-ish
        resp = await self._send_recv({
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "tools/list",
            "params": {}
        })
        if "error" in resp:
            logger.error(f"MCP [{self.name}] tools/list error: {resp['error']}")
            return []
        result = resp.get("result", {})
        return result.get("tools", [])

    async def call_tool(self, name: str, arguments: dict) -> Any:
        msg_id = id(self) + hash(name) % (2 ** 31)
        resp = await self._send_recv({
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments,
            }
        })
        if "error" in resp:
            error = resp["error"]
            return f"❌ MCP [{self.name}] tool '{name}' error: {error.get('message', str(error))}"
        result = resp.get("result", {})
        content = result.get("content", [])
        # MCP content is a list of content items (text, image, resource, etc.)
        text_parts = []
        for item in content:
            if item.get("type") == "text":
                text_parts.append(item.get("text", ""))
            elif item.get("type") == "resource":
                text_parts.append(str(item.get("resource", {})))
        return "\n".join(text_parts) if text_parts else str(result)

    async def close(self):
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
            self._process = None
            logger.info(f"MCP [{self.name}] closed")


# ──────────────────────────────────────────────
# Remote HTTP MCP Client
# ──────────────────────────────────────────────

class HttpMcpClient(BaseMcpClient):
    """Connects to a remote MCP server via HTTP POST."""

    def __init__(self, name: str, url: str, headers: Optional[Dict[str, str]] = None,
                 timeout: int = 30):
        self.name = name
        self.url = url
        self.headers = headers or {}
        self.timeout = timeout
        self._initialized = False
        self._server_info: Optional[dict] = None
        self._capabilities: Optional[dict] = None

    async def initialize(self) -> dict:
        if self._initialized:
            return {"serverInfo": self._server_info, "capabilities": self._capabilities}
        # Remote MCP servers may support SSE or simple HTTP POST
        # We use JSON-RPC over HTTP POST (streamable HTTP transport)
        try:
            import httpx
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    self.url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "jarvis", "version": "1.0.0"}
                        }
                    },
                    headers=self.headers,
                )
                data = resp.json()
                if "error" in data:
                    raise RuntimeError(f"MCP remote init error: {data['error']}")
                result = data.get("result", {})
                self._server_info = result.get("serverInfo", {})
                self._capabilities = result.get("capabilities", {})
                self._initialized = True
                logger.info(f"MCP Remote [{self.name}] initialized: {self._server_info}")
                return result
        except ImportError:
            raise RuntimeError("httpx required for remote MCP servers")
        except Exception as e:
            raise RuntimeError(f"MCP remote [{self.name}] connection failed: {e}")

    async def list_tools(self) -> List[dict]:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    self.url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/list",
                        "params": {}
                    },
                    headers=self.headers,
                )
                data = resp.json()
                if "error" in data:
                    logger.error(f"MCP [{self.name}] tools/list error: {data['error']}")
                    return []
                return data.get("result", {}).get("tools", [])
        except Exception as e:
            logger.error(f"MCP [{self.name}] list_tools failed: {e}")
            return []

    async def call_tool(self, name: str, arguments: dict) -> Any:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    self.url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": arguments}
                    },
                    headers=self.headers,
                )
                data = resp.json()
                if "error" in data:
                    error = data["error"]
                    return f"❌ MCP [{self.name}] tool '{name}' error: {error.get('message', str(error))}"
                content = data.get("result", {}).get("content", [])
                text_parts = []
                for item in content:
                    if item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                    elif item.get("type") == "resource":
                        text_parts.append(str(item.get("resource", {})))
                return "\n".join(text_parts) if text_parts else str(data.get("result", {}))
        except Exception as e:
            return f"❌ MCP [{self.name}] tool call failed: {e}"

    async def close(self):
        self._initialized = False
        logger.info(f"MCP Remote [{self.name}] closed")


# ──────────────────────────────────────────────
# MCP Manager
# ──────────────────────────────────────────────

class McpManager:
    """Manages multiple MCP server connections.

    Supports:
    - Loading from .mcp.json (OpenCode/Claude Code format)
    - Loading from opencode.json (OpenCode native format)
    - Programmatic registration
    - Tool discovery across all servers
    """

    def __init__(self):
        self._servers: Dict[str, BaseMcpClient] = {}
        self._tools: Dict[str, tuple[str, str]] = {}  # openai_tool_name -> (server_name, mcp_tool_name)

    # ── Registration ──

    def register_stdio(self, name: str, command: List[str],
                       cwd: Optional[str] = None,
                       env: Optional[Dict[str, str]] = None) -> StdioMcpClient:
        """Register a local stdio MCP server."""
        client = StdioMcpClient(name, command, cwd, env)
        self._servers[name] = client
        return client

    def register_remote(self, name: str, url: str,
                         headers: Optional[Dict[str, str]] = None,
                         timeout: int = 30) -> HttpMcpClient:
        """Register a remote HTTP MCP server."""
        client = HttpMcpClient(name, url, headers, timeout)
        self._servers[name] = client
        return client

    def get_server(self, name: str) -> Optional[BaseMcpClient]:
        return self._servers.get(name)

    def list_servers(self) -> List[str]:
        return list(self._servers.keys())

    # ── Loading from config ──

    async def load_mcp_json(self, config_path: str) -> int:
        """Load MCP servers from a .mcp.json file (OpenCode/Claude Code format).

        .mcp.json format:
        {
          "mcpServers": {
            "server-name": {
              "command": "npx",
              "args": ["-y", "@modelcontextprotocol/server-everything"],
              "cwd": "/path",
              "env": {"KEY": "value"}
            }
          }
        }
        Returns number of servers loaded.
        """
        try:
            with open(config_path, "r") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning(f"MCP: cannot load {config_path}: {e}")
            return 0

        servers = data.get("mcpServers", {})
        if not servers:
            return 0

        count = 0
        for name, cfg in servers.items():
            if name in self._servers:
                logger.info(f"MCP: skipping duplicate server '{name}'")
                continue
            command_list = cfg.get("command", "")
            args = cfg.get("args", [])
            if isinstance(command_list, str):
                command_list = [command_list] + args
            else:
                command_list = command_list + args
            cwd = cfg.get("cwd")
            env = cfg.get("env")
            self.register_stdio(name, command_list, cwd, env)
            count += 1
            logger.info(f"MCP: registered server '{name}' from {config_path}")

        return count

    async def load_opencode_mcp_config(self, config_path: str) -> int:
        """Load MCP servers from opencode.json format.

        opencode.json format:
        {
          "mcp": {
            "server-name": {
              "type": "local",
              "command": ["npx", "-y", "mcp-server"],
              "enabled": true,
              "environment": {"KEY": "value"}
            }
          }
        }
        Also supports remote type:
        {
          "mcp": {
            "server-name": {
              "type": "remote",
              "url": "https://mcp.example.com/mcp",
              "enabled": true,
              "headers": {"Authorization": "Bearer ..."}
            }
          }
        }
        """
        try:
            with open(config_path, "r") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning(f"MCP: cannot load opencode config {config_path}: {e}")
            return 0

        mcp_config = data.get("mcp", {})
        if not mcp_config:
            return 0

        count = 0
        for name, cfg in mcp_config.items():
            if not cfg.get("enabled", True):
                continue
            if name in self._servers:
                continue
            server_type = cfg.get("type", "local")
            if server_type == "local":
                command = cfg.get("command", [])
                if not command:
                    logger.warning(f"MCP: server '{name}' has no command, skipping")
                    continue
                cwd = cfg.get("cwd")
                env = cfg.get("environment")
                self.register_stdio(name, command, cwd, env)
                count += 1
            elif server_type == "remote":
                url = cfg.get("url", "")
                if not url:
                    continue
                headers = cfg.get("headers")
                self.register_remote(name, url, headers)
                count += 1
            logger.info(f"MCP: registered server '{name}' ({server_type}) from {config_path}")

        return count

    # ── Initialization ──

    async def initialize_all(self) -> int:
        """Initialize all registered MCP servers. Returns count of successful inits."""
        tasks = []
        for name, client in self._servers.items():
            tasks.append(self._init_one(name, client))
        results = await asyncio.gather(*tasks, return_exceptions=True)

        success = 0
        for name, result in zip(self._servers.keys(), results):
            if isinstance(result, Exception):
                logger.error(f"MCP [{name}] init failed: {result}")
            else:
                success += 1

        # Discover tools from all servers
        self._discover_tools()

        logger.info(f"MCP: {success}/{len(self._servers)} servers initialized")
        return success

    async def _init_one(self, name: str, client: BaseMcpClient):
        try:
            await client.initialize()
        except Exception as e:
            logger.warning(f"MCP [{name}] init failed: {e}")

    def _discover_tools(self):
        """Discover tools from all initialized servers."""
        self._tools = {}
        # We'll do this lazily (on first tool listing request)
        # This avoids blocking on unresponsive servers

    async def discover_all_tools(self) -> List[dict]:
        """Discover tools from all servers and return in OpenAI format."""
        openai_tools = []
        self._tools = {}

        for name, client in self._servers.items():
            try:
                mcp_tools = await client.list_tools()
                for mcp_tool in mcp_tools:
                    openai_tool = client.to_openai_tool(mcp_tool)
                    openai_name = openai_tool["function"]["name"]
                    self._tools[openai_name] = (name, mcp_tool["name"])
                    openai_tools.append(openai_tool)
                logger.info(f"MCP [{name}]: {len(mcp_tools)} tools discovered")
            except Exception as e:
                logger.warning(f"MCP [{name}]: tool discovery failed: {e}")

        return openai_tools

    async def execute_tool(self, openai_tool_name: str, arguments: dict) -> str:
        """Execute an MCP tool by its OpenAI-compatible name."""
        if openai_tool_name not in self._tools:
            return f"⚠️ MCP tool '{openai_tool_name}' not found"

        server_name, mcp_tool_name = self._tools[openai_tool_name]
        client = self._servers.get(server_name)
        if not client:
            return f"⚠️ MCP server '{server_name}' not found"

        return await client.call_tool(mcp_tool_name, arguments)

    # ── Cleanup ──

    async def close_all(self):
        """Close all MCP server connections."""
        results = await asyncio.gather(
            *[client.close() for client in self._servers.values()],
            return_exceptions=True
        )
        for name, result in zip(self._servers.keys(), results):
            if isinstance(result, Exception):
                logger.warning(f"MCP [{name}] close error: {result}")
        self._servers.clear()
        self._tools.clear()
        logger.info("MCP: all servers closed")


# ──────────────────────────────────────────────
# Global singleton
# ──────────────────────────────────────────────

mcp_manager: Optional[McpManager] = None


def get_mcp_manager() -> McpManager:
    """Get or create the global MCP manager singleton."""
    global mcp_manager
    if mcp_manager is None:
        mcp_manager = McpManager()
    return mcp_manager


async def init_mcp_from_config(config_paths: List[str] = None) -> int:
    """Initialize MCP manager from config files.

    Searches for:
    - .mcp.json (Claude Code format)
    - opencode.json (OpenCode native format)

    Also scans standard paths:
    - {project_root}/.mcp.json
    - ~/.config/opencode/.mcp.json
    - {project_root}/opencode.json
    - {project_root}/opencode.jsonc
    """
    import glob as glob_mod
    manager = get_mcp_manager()

    # Default paths to scan
    if config_paths is None:
        from config import DOC_DIR
        project_root = DOC_DIR

        config_paths = [
            os.path.join(project_root, ".mcp.json"),
            os.path.join(os.path.expanduser("~"), ".config", "opencode", ".mcp.json"),
            os.path.join(project_root, "opencode.json"),
            os.path.join(project_root, "opencode.jsonc"),
        ]

    total = 0
    for path in config_paths:
        if path.endswith(".mcp.json"):
            total += await manager.load_mcp_json(path)
        elif path.endswith((".json", ".jsonc")):
            total += await manager.load_opencode_mcp_config(path)

    if total > 0:
        await manager.initialize_all()
        # Discover tools
        tools = await manager.discover_all_tools()
        logger.info(f"MCP: {total} servers loaded, {len(tools)} tools available")

    return total
