# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The names the two MCP clients import exist in the installed ``mcp`` SDK.

Both clients import the SDK lazily, inside the method that connects, so a
renamed symbol (2.x renamed ``streamablehttp_client`` to
``streamable_http_client`` and moved headers onto an ``httpx2`` client) would
surface only on the first connector call in production. These resolve the same
names at test time and drive ``MCPHTTPClient`` through a fake transport.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from core.mcp_clients.http_client import MCPHTTPClient


def test_sdk_exports_every_name_the_clients_import():
    import httpx2
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client

    assert callable(streamable_http_client)
    assert callable(sse_client) and callable(stdio_client)
    assert ClientSession and StdioServerParameters and httpx2.AsyncClient


@pytest.mark.asyncio
async def test_http_client_passes_headers_and_timeout_on_the_httpx2_client():
    seen: dict = {}

    @asynccontextmanager
    async def fake_transport(url, *, http_client):
        seen["url"] = url
        seen["headers"] = dict(http_client.headers)
        seen["timeout"] = http_client.timeout
        yield ("read", "write")  # 2.x yields the two streams; the session-id callback is gone

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    client = MCPHTTPClient(
        url="http://gmail-mcp:8080/mcp",
        headers={"Authorization": "Bearer t"},
        timeout=12.0,
    )

    with patch("mcp.client.streamable_http.streamable_http_client", fake_transport), \
            patch("mcp.ClientSession", return_value=session):
        await client.connect()

    assert seen["url"] == "http://gmail-mcp:8080/mcp"
    assert seen["headers"]["authorization"] == "Bearer t"
    assert seen["timeout"].connect == 12.0
    assert seen["timeout"].read == 300.0
    session.initialize.assert_awaited_once()
