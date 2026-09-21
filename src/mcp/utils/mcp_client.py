# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""MCP Client — connects to external MCP servers and discovers their tools.

Uses the official ``mcp`` Python SDK with:
- StdioServerParameters for local process-based servers (npx, python, etc.)
- SSE transport for remote HTTP-based servers

External tools are merged into the tool palette with a namespaced prefix:
``ext_{server_name}_{tool_name}`` to avoid collisions with ``pkb_*`` tools.

Configuration via ``MCP_SERVERS_CONFIG`` env var (JSON array) or
per-user CRUD at ``/mcp-servers``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("ai-companion.mcp_client")

# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------


@dataclass
class MCPServerConfig:
    """Configuration for a single external MCP server."""

    name: str
    transport: str  # "stdio" or "sse"
    enabled: bool = True
    # stdio fields
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    # sse fields
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Summary for API responses — deliberately omits env and headers."""
        return {
            "name": self.name,
            "transport": self.transport,
            "enabled": self.enabled,
            "command": self.command,
            "args": self.args,
            "url": self.url,
        }

    def to_persisted(self) -> dict[str, Any]:
        """Everything needed to reconnect after a restart.

        Unlike :meth:`to_dict` this carries ``env`` and ``headers``: without
        them a rehydrated stdio server has no environment and an SSE server
        loses its Authorization header, so the reconnect fails.
        """
        return {**self.to_dict(), "env": self.env, "headers": self.headers}


# ---------------------------------------------------------------------------
# stdio spawn policy
# ---------------------------------------------------------------------------

# A stdio server config can arrive in a POST /mcp-servers body, so the command
# it names is untrusted input. There is no safe way to sanitise "run this
# binary with these arguments" — the only control is an operator naming, out
# of band, which launchers this host may spawn. Empty (the default) means no
# host process is started for anyone.
STDIO_ALLOWLIST_ENV = "CERID_MCP_STDIO_ALLOWED_COMMANDS"

# Variables that turn any allowlisted launcher back into an arbitrary-code
# loader, so they are never taken from a caller-supplied env block.
_FORBIDDEN_ENV_KEYS = frozenset({
    "BASH_ENV",
    "DYLD_FRAMEWORK_PATH",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
    "ENV",
    "LD_AUDIT",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "NODE_OPTIONS",
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONSTARTUP",
})


def stdio_allowlist() -> set[str]:
    """Command names this host is permitted to spawn for stdio MCP servers."""
    raw = os.getenv(STDIO_ALLOWLIST_ENV, "")
    return {c.strip() for c in raw.split(",") if c.strip()}


def validate_stdio_config(cfg: MCPServerConfig) -> None:
    """Raise ``ValueError`` unless *cfg* names a launcher the operator allowed."""
    allowed = stdio_allowlist()
    if not allowed:
        raise ValueError(
            "stdio MCP servers are disabled on this host — set "
            f"{STDIO_ALLOWLIST_ENV} to the command names it may spawn"
        )
    command = cfg.command.strip()
    if not command or command != os.path.basename(command) or os.sep in cfg.command:
        raise ValueError(
            f"stdio command must be a bare command name, not a path: {cfg.command!r}"
        )
    if command not in allowed:
        raise ValueError(
            f"stdio command {command!r} is not listed in {STDIO_ALLOWLIST_ENV}"
        )
    forbidden = sorted(set(cfg.env or {}) & _FORBIDDEN_ENV_KEYS)
    if forbidden:
        raise ValueError(
            f"stdio env may not set loader variables: {', '.join(forbidden)}"
        )


#: Redis hash holding every server registered through ``POST /mcp-servers``.
#: Without it a registration lived only in the process that received it, so a
#: restart silently dropped the server and all of its ``ext_*`` tools.
MCP_SERVERS_KEY = "cerid:mcp_servers"


def _store() -> Any:
    """Redis handle for persisted server configs, or ``None`` when unavailable.

    Imported lazily: ``utils`` must not depend on ``app`` at module scope.
    """
    try:
        from app.deps import get_redis

        return get_redis()
    except Exception as exc:  # noqa: BLE001 — persistence is best-effort
        logger.warning("MCP server store unavailable: %s", exc)
        return None


def _config_from_entry(entry: dict[str, Any]) -> MCPServerConfig:
    """Build a config from a persisted or env-supplied JSON object."""
    return MCPServerConfig(
        name=entry["name"],
        transport=entry.get("transport", "stdio"),
        enabled=entry.get("enabled", True),
        command=entry.get("command", ""),
        args=entry.get("args", []),
        env=entry.get("env", {}),
        url=entry.get("url", ""),
        headers=entry.get("headers", {}),
    )


@dataclass
class ExternalTool:
    """A tool discovered from an external MCP server."""

    server_name: str
    tool_name: str
    namespaced_name: str  # ext_{server}_{tool}
    description: str
    input_schema: dict[str, Any]


# ---------------------------------------------------------------------------
# Manager singleton
# ---------------------------------------------------------------------------


class MCPClientManager:
    """Manages connections to external MCP servers and their tools.

    Lifecycle:
      1. ``load_config()`` — parse env var or DB configs
      2. ``connect_all()`` — connect to all enabled servers (non-blocking per-server)
      3. ``list_external_tools()`` — merge into MCP_TOOLS for ``tools/list``
      4. ``call_tool()`` — dispatch ``ext_*`` tool calls to the correct session
      5. ``shutdown()`` — close all connections
    """

    def __init__(self) -> None:
        self._configs: dict[str, MCPServerConfig] = {}
        self._tools: dict[str, ExternalTool] = {}  # namespaced_name -> ExternalTool
        self._sessions: dict[str, Any] = {}  # name -> ClientSession
        self._exit_stack: AsyncExitStack | None = None
        self._connected: set[str] = set()
        self._errors: dict[str, str] = {}

    # -- Configuration -------------------------------------------------------

    def add_server(self, config: MCPServerConfig, *, persist: bool = True) -> None:
        """Register a server config (does not connect yet).

        Writes through to Redis so the registration outlives this process.
        ``persist=False`` is for configs that already have a durable home —
        the ``MCP_SERVERS_CONFIG`` env var and rehydration itself.
        """
        self._configs[config.name] = config
        if not persist:
            return
        store = _store()
        if store is None:
            logger.warning(
                "MCP server '%s' registered in memory only — no store available; "
                "it will not survive a restart", config.name,
            )
            return
        try:
            store.hset(
                MCP_SERVERS_KEY, config.name, json.dumps(config.to_persisted()),
            )
        except Exception as exc:  # noqa: BLE001 — persistence is best-effort
            logger.warning("Failed to persist MCP server '%s': %s", config.name, exc)

    def hydrate_from_store(self) -> int:
        """Load persisted server configs into this process. Returns the count.

        Called from the app lifespan: before this existed nothing read the
        registrations back, so every one was lost on restart.
        """
        store = _store()
        if store is None:
            return 0
        try:
            raw = store.hgetall(MCP_SERVERS_KEY) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to read persisted MCP servers: %s", exc)
            return 0

        loaded = 0
        for name, blob in raw.items():
            key = name.decode() if isinstance(name, bytes) else str(name)
            try:
                entry = json.loads(blob.decode() if isinstance(blob, bytes) else blob)
                entry.setdefault("name", key)
                self.add_server(_config_from_entry(entry), persist=False)
                loaded += 1
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                logger.warning("Skipping unreadable MCP server '%s': %s", key, exc)
        if loaded:
            logger.info("Rehydrated %d persisted MCP server config(s)", loaded)
        return loaded

    def remove_server(self, name: str) -> bool:
        """Remove a server config and disconnect if connected."""
        if name in self._configs:
            del self._configs[name]
            store = _store()
            if store is not None:
                try:
                    store.hdel(MCP_SERVERS_KEY, name)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to drop MCP server '%s': %s", name, exc)
            self._connected.discard(name)
            self._errors.pop(name, None)
            # Remove discovered tools for this server
            self._tools = {
                k: v for k, v in self._tools.items() if v.server_name != name
            }
            return True
        return False

    def load_config(self) -> int:
        """Load server configs from MCP_SERVERS_CONFIG env var.

        Accepts either a JSON array string or a path to a JSON file.
        Returns number of configs loaded.
        """
        raw = os.getenv("MCP_SERVERS_CONFIG", "").strip()
        if not raw:
            return 0

        try:
            if raw.startswith("["):
                configs = json.loads(raw)
            elif os.path.isfile(raw):
                configs = json.loads(open(raw, encoding="utf-8").read())  # noqa: SIM115
            else:
                logger.warning("MCP_SERVERS_CONFIG is not valid JSON array or file path")
                return 0

            for entry in configs:
                # persist=False: the env var IS the durable record for these.
                self.add_server(_config_from_entry(entry), persist=False)

            logger.info("Loaded %d MCP server configs", len(configs))
            return len(configs)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Failed to parse MCP_SERVERS_CONFIG: %s", e)
            return 0

    # -- Connection ----------------------------------------------------------

    async def connect_all(self) -> list[str]:
        """Connect to all enabled servers. Returns list of connected names.

        Non-blocking per server — a failing server does not block others.
        """
        if self._exit_stack is None:
            self._exit_stack = AsyncExitStack()

        connected: list[str] = []
        for name, cfg in self._configs.items():
            if not cfg.enabled:
                continue
            if name in self._connected:
                # POST /mcp-servers calls connect_all() on every add. Without
                # this, _connect_one ran again for servers already up, replaced
                # self._sessions[name], and leaked the previous stdio
                # subprocess into the exit stack.
                connected.append(name)
                continue
            try:
                session = await asyncio.wait_for(
                    self._connect_one(cfg), timeout=15.0,
                )
                self._sessions[name] = session
                self._connected.add(name)
                self._errors.pop(name, None)

                tool_count = await self._discover_tools(name, session)
                connected.append(name)
                logger.info(
                    "MCP server '%s' connected (%s), %d tools discovered",
                    name, cfg.transport, tool_count,
                )
            except Exception as e:  # noqa: BLE001
                self._errors[name] = str(e)
                logger.warning("MCP server '%s' failed to connect: %s", name, e)

        return connected

    async def _connect_one(self, cfg: MCPServerConfig) -> Any:
        """Connect to a single MCP server and initialize the session."""
        assert self._exit_stack is not None, "connect_all() must initialize _exit_stack first"

        try:
            from mcp import ClientSession, StdioServerParameters
        except ImportError:
            raise ImportError(
                "MCP Python SDK not installed. Run: pip install 'mcp>=1.0'"
            ) from None

        if cfg.transport == "stdio":
            from mcp.client.stdio import stdio_client

            validate_stdio_config(cfg)
            params = StdioServerParameters(
                command=cfg.command,
                args=cfg.args,
                env=cfg.env or None,
            )
            transport = await self._exit_stack.enter_async_context(
                stdio_client(params)
            )
            read_stream, write_stream = transport
            session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
        elif cfg.transport == "sse":
            from mcp.client.sse import sse_client

            transport = await self._exit_stack.enter_async_context(
                sse_client(url=cfg.url, headers=cfg.headers or None)
            )
            read_stream, write_stream = transport
            session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
        else:
            raise ValueError(f"Unknown transport: {cfg.transport}")

        await session.initialize()
        return session

    async def _discover_tools(self, server_name: str, session: Any) -> int:
        """Discover tools from a connected session and register them."""
        response = await session.list_tools()
        count = 0
        for tool in response.tools:
            namespaced = f"ext_{server_name}_{tool.name}"
            self._tools[namespaced] = ExternalTool(
                server_name=server_name,
                tool_name=tool.name,
                namespaced_name=namespaced,
                description=f"[{server_name}] {tool.description or tool.name}",
                input_schema=tool.inputSchema if hasattr(tool, "inputSchema") else {},
            )
            count += 1
        return count

    # -- Tool dispatch -------------------------------------------------------

    async def call_tool(self, namespaced_name: str, arguments: dict[str, Any]) -> Any:
        """Call an external MCP tool by its namespaced name."""
        tool = self._tools.get(namespaced_name)
        if not tool:
            raise ValueError(f"Unknown external tool: {namespaced_name}")

        session = self._sessions.get(tool.server_name)
        if not session:
            raise RuntimeError(
                f"MCP server '{tool.server_name}' is not connected"
            )

        result = await session.call_tool(tool.tool_name, arguments)

        # Extract text content from MCP result
        texts = []
        for content in result.content:
            if hasattr(content, "text"):
                texts.append(content.text)

        return "\n".join(texts) if texts else str(result.content)

    # -- Introspection -------------------------------------------------------

    def list_external_tools(self) -> list[dict[str, Any]]:
        """Return external tools in MCP_TOOLS-compatible format for tools/list."""
        return [
            {
                "name": tool.namespaced_name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
            }
            for tool in self._tools.values()
        ]

    def list_servers(self) -> list[dict[str, Any]]:
        """Return all configured servers with their status."""
        result = []
        for name, cfg in self._configs.items():
            server_tools = [
                t.namespaced_name for t in self._tools.values()
                if t.server_name == name
            ]
            result.append({
                "name": name,
                "transport": cfg.transport,
                "enabled": cfg.enabled,
                "status": (
                    "connected" if name in self._connected
                    else "error" if name in self._errors
                    else "disconnected"
                ),
                "error": self._errors.get(name),
                "tool_count": len(server_tools),
                "tools": server_tools,
            })
        return result

    def has_external_tools(self) -> bool:
        """Check if any external tools are available."""
        return bool(self._tools)

    def get_tool_metadata(self, namespaced_name: str) -> ExternalTool | None:
        """Look up a discovered external tool by its namespaced name.

        Returns ``None`` when the name is unknown — callers (e.g. the
        Sprint 1A.2 governance dispatcher) use this to resolve the
        owning ``server_name`` for policy enforcement and audit logging
        without parsing the namespaced string (server / tool names can
        both contain underscores, so the split is ambiguous).
        """
        return self._tools.get(namespaced_name)

    # -- Lifecycle -----------------------------------------------------------

    async def reconnect(self, name: str) -> bool:
        """Reconnect to a specific server."""
        cfg = self._configs.get(name)
        if not cfg:
            return False

        # Clean up old tools for this server
        self._tools = {
            k: v for k, v in self._tools.items() if v.server_name != name
        }
        self._connected.discard(name)
        self._errors.pop(name, None)

        try:
            session = await asyncio.wait_for(
                self._connect_one(cfg), timeout=15.0,
            )
            self._sessions[name] = session
            self._connected.add(name)
            await self._discover_tools(name, session)
            return True
        except Exception as e:  # noqa: BLE001
            self._errors[name] = str(e)
            logger.warning("Reconnect to '%s' failed: %s", name, e)
            return False

    async def shutdown(self) -> None:
        """Gracefully close all MCP server connections."""
        if self._exit_stack:
            try:
                await self._exit_stack.aclose()
            except Exception as e:  # noqa: BLE001
                logger.warning("MCP client shutdown error: %s", e)
            self._exit_stack = None
        self._sessions.clear()
        self._connected.clear()
        self._tools.clear()
        logger.info("MCP client manager shut down")


# Module-level singleton
mcp_client_manager = MCPClientManager()


async def restore_and_connect() -> list[str]:
    """Startup entry point: load env config, rehydrate the store, connect.

    Nothing called this before, so ``MCP_SERVERS_CONFIG`` was never read and
    user-registered servers were never restored.
    """
    mcp_client_manager.load_config()
    mcp_client_manager.hydrate_from_store()
    return await mcp_client_manager.connect_all()
