# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

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
        return {
            "name": self.name,
            "transport": self.transport,
            "enabled": self.enabled,
            "command": self.command,
            "args": self.args,
            "url": self.url,
        }


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

    def add_server(self, config: MCPServerConfig) -> None:
        """Register a server config (does not connect yet)."""
        self._configs[config.name] = config

    def remove_server(self, name: str) -> bool:
        """Remove a server config and disconnect if connected."""
        if name in self._configs:
            del self._configs[name]
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
                cfg = MCPServerConfig(
                    name=entry["name"],
                    transport=entry.get("transport", "stdio"),
                    enabled=entry.get("enabled", True),
                    command=entry.get("command", ""),
                    args=entry.get("args", []),
                    env=entry.get("env", {}),
                    url=entry.get("url", ""),
                    headers=entry.get("headers", {}),
                )
                self.add_server(cfg)

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
