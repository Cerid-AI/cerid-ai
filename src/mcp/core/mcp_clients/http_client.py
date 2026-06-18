# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Streamable-HTTP MCP client wrapper.

Wraps the official ``mcp`` PyPI package (Anthropic-maintained, MIT) for
consuming MCP servers from FastAPI. Streamable HTTP transport (not stdio)
because Cerid runs in Docker — stdio pipes don't cross container boundaries.

External MCP servers run as sibling docker-compose services so this client
talks to them over ``http://<service-name>:8080/mcp``.
"""
from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Any

logger = logging.getLogger("ai-companion.mcp_client_http")

_DEFAULT_TIMEOUT_S = 30.0


class MCPHTTPClient:
    """Per-connector streamable-HTTP MCP client.

    Usage:
        client = MCPHTTPClient(url='http://gmail-mcp:8080/mcp')
        await client.connect()
        result = await client.call_tool('search_messages', {'q': 'sender:alice'})
        await client.disconnect()

    Or as an async context manager:
        async with MCPHTTPClient(url='http://gmail-mcp:8080/mcp') as client:
            result = await client.call_tool('search_messages', {...})

    Connection state is managed by an ``AsyncExitStack`` so partially-failed
    setups (e.g., server reachable but tool initialization errors) clean up
    correctly.
    """

    def __init__(
        self,
        url: str,
        *,
        timeout: float = _DEFAULT_TIMEOUT_S,
        connector_name: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.connector_name = connector_name or url
        # Optional per-connector auth headers (e.g. bearer token for the
        # sibling Pro-tier MCP servers in stacks/connectors/).
        self._headers = headers or {}
        self._exit_stack: AsyncExitStack | None = None
        self._session: Any = None
        self._tools_cache: list[Any] | None = None

    async def connect(self) -> None:
        """Open the streamable-HTTP connection and initialize the session.

        Idempotent: calling connect() on an already-connected client is a
        no-op (returns cached state).
        """
        if self._session is not None:
            return

        # Lazy import — the ``mcp`` package is heavy and imports openssl
        # primitives that some CI environments don't have without extras.
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError as exc:
            raise RuntimeError(
                f"mcp package not installed or incompatible: {exc}. "
                "Install with `pip install 'mcp>=1.27'`."
            ) from exc

        self._exit_stack = AsyncExitStack()
        # streamablehttp_client returns (read_stream, write_stream, terminator)
        transport = await self._exit_stack.enter_async_context(
            streamablehttp_client(self.url, headers=self._headers or None)
        )
        read_stream, write_stream, _terminator = transport

        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await self._session.initialize()
        logger.info(
            "MCP client connected: connector=%s url=%s",
            self.connector_name, self.url,
        )

    async def disconnect(self) -> None:
        """Close the session and underlying transport."""
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._exit_stack = None
        self._session = None
        self._tools_cache = None

    async def list_tools(self) -> list[Any]:
        """Cache-fronted ``tools/list`` call. Tool inventories rarely change
        per session; we cache to avoid the round-trip on every call_tool."""
        if self._tools_cache is not None:
            return self._tools_cache
        if self._session is None:
            await self.connect()
        assert self._session is not None
        result = await self._session.list_tools()
        # mcp 1.27 returns an object with .tools; older variants returned a list directly
        tools = getattr(result, "tools", result)
        self._tools_cache = list(tools)
        return self._tools_cache

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        """Invoke a tool on the connected MCP server.

        Raises ``RuntimeError`` if the session isn't connected (use connect()
        or the async-context-manager form first).
        """
        if self._session is None:
            await self.connect()
        assert self._session is not None
        return await self._session.call_tool(name, arguments or {})

    async def __aenter__(self) -> MCPHTTPClient:
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.disconnect()
