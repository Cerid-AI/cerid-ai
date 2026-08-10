# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Streamable-HTTP MCP client wrapper.

Wraps the official ``mcp`` PyPI package (Anthropic-maintained, MIT) for
consuming MCP servers from FastAPI. Streamable HTTP transport (not stdio)
because Cerid runs in Docker — stdio pipes don't cross container boundaries.

External MCP servers run as sibling docker-compose services so this client
talks to them over ``http://<service-name>:8080/mcp``.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
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

    @asynccontextmanager
    async def _session_scope(self) -> AsyncIterator[Any]:
        """One initialized session, opened and closed inside the CALLER's task.

        Sessions are deliberately NOT reused across calls. ``streamablehttp_client``
        hands back anyio memory-object streams owned by the task group that
        entered it; the moment that task finishes — a FastAPI request handler,
        or the startup lifespan — anyio closes them. A later call from a
        different task then hits ``ClosedResourceError`` even though the server
        is perfectly healthy. That is exactly what happened to every Pro cloud
        connector: the pool connected once during startup and every subsequent
        tool call failed (observed 2026-08-09 on /connectors/gmail/auth/start).

        A fresh connection per call costs one round-trip. These connectors are
        on-demand lookups, not hot paths, and a correct answer beats a saved
        handshake.
        """
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

        async with AsyncExitStack() as stack:
            # streamablehttp_client returns (read_stream, write_stream, terminator)
            read_stream, write_stream, _terminator = await stack.enter_async_context(
                streamablehttp_client(self.url, headers=self._headers or None)
            )
            session = await stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
            yield session

    async def connect(self) -> None:
        """Verify the server is reachable and speaks MCP.

        Kept for callers that want a startup-time reachability probe. It no
        longer parks a session for later use — see ``_session_scope``.
        """
        async with self._session_scope():
            logger.info(
                "MCP client connected: connector=%s url=%s",
                self.connector_name, self.url,
            )

    async def disconnect(self) -> None:
        """Drop cached state. Sessions are per-call, so there is no live
        transport to tear down — kept so existing callers keep working."""
        self._exit_stack = None
        self._session = None
        self._tools_cache = None

    async def list_tools(self) -> list[Any]:
        """Cache-fronted ``tools/list``. The inventory is stable for a given
        server, so it is cached across calls even though sessions are not."""
        if self._tools_cache is not None:
            return self._tools_cache
        async with self._session_scope() as session:
            result = await session.list_tools()
        # mcp 1.27 returns an object with .tools; older variants returned a list directly
        tools = getattr(result, "tools", result)
        self._tools_cache = list(tools)
        return self._tools_cache

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        """Invoke a tool, in a session scoped to this call."""
        async with self._session_scope() as session:
            return await session.call_tool(name, arguments or {})

    async def __aenter__(self) -> MCPHTTPClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.disconnect()
