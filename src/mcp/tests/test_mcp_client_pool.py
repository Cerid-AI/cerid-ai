# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Tests for MCPClientPool circuit-breaker + singleton semantics.

Pure logic tests — no real MCP server connection. The actual transport
layer (MCPHTTPClient) needs a sibling docker-compose service running and
isn't exercised here; Phase 3 D5 integration tests cover that with a
mock MCP server fixture.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.mcp_clients.client_pool import (
    _COOL_DOWN_SECONDS,
    _FAILURE_THRESHOLD,
    MCPClientPool,
    get_pool,
    reset_pool_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_pool_for_tests()
    yield
    reset_pool_for_tests()


def test_get_pool_returns_singleton():
    a = get_pool()
    b = get_pool()
    assert a is b


def test_get_pool_isolated_after_reset():
    a = get_pool()
    reset_pool_for_tests()
    b = get_pool()
    assert a is not b


def test_register_idempotent():
    pool = MCPClientPool()
    pool.register("gmail", "http://gmail-mcp:8080/mcp")
    pool.register("gmail", "http://gmail-mcp:8080/mcp")  # silent re-register
    assert pool.is_registered("gmail")
    assert len(pool.list_connectors()) == 1


def test_list_connectors_health_snapshot():
    pool = MCPClientPool()
    pool.register("gmail", "http://gmail-mcp:8080/mcp")
    pool.register("calendar", "http://calendar-mcp:8080/mcp")
    snapshot = pool.list_connectors()
    names = {c["name"] for c in snapshot}
    assert names == {"gmail", "calendar"}
    for c in snapshot:
        assert c["failures"] == 0
        assert c["circuit_open"] is False


@pytest.mark.asyncio
async def test_call_tool_unknown_connector_raises():
    pool = MCPClientPool()
    with pytest.raises(KeyError, match="not registered"):
        await pool.call_tool("nope", "x")


@pytest.mark.asyncio
async def test_call_tool_success_resets_failure_counter():
    pool = MCPClientPool()
    pool.register("gmail", "http://gmail-mcp:8080/mcp")

    # Inject a mock client (bypass the real transport)
    state = pool._connectors["gmail"]
    state.client = MagicMock()
    state.client.call_tool = AsyncMock(return_value={"result": "ok"})
    state.failures = 2  # simulate prior partial failures

    result = await pool.call_tool("gmail", "search", {"q": "hello"})
    assert result == {"result": "ok"}
    assert state.failures == 0
    assert state.opened_at is None


@pytest.mark.asyncio
async def test_call_tool_failure_increments_counter():
    pool = MCPClientPool()
    pool.register("gmail", "http://gmail-mcp:8080/mcp")
    state = pool._connectors["gmail"]
    state.client = MagicMock()
    state.client.call_tool = AsyncMock(
        side_effect=ConnectionError("dial tcp: lookup gmail-mcp on 127.0.0.11: no such host"),
    )

    with pytest.raises(ConnectionError):
        await pool.call_tool("gmail", "search", {"q": "x"})
    assert state.failures == 1


@pytest.mark.asyncio
async def test_circuit_opens_after_threshold_failures():
    pool = MCPClientPool()
    pool.register("gmail", "http://gmail-mcp:8080/mcp")
    state = pool._connectors["gmail"]
    state.client = MagicMock()
    state.client.call_tool = AsyncMock(side_effect=ConnectionError("dead"))

    for _ in range(_FAILURE_THRESHOLD):
        with pytest.raises(ConnectionError):
            await pool.call_tool("gmail", "x")

    assert state.failures == _FAILURE_THRESHOLD
    assert state.opened_at is not None
    assert state.is_open()


@pytest.mark.asyncio
async def test_open_circuit_short_circuits_returning_none():
    pool = MCPClientPool()
    pool.register("gmail", "http://gmail-mcp:8080/mcp")
    state = pool._connectors["gmail"]
    state.client = MagicMock()

    # Force-open the circuit
    import time
    state.opened_at = time.monotonic()
    state.failures = _FAILURE_THRESHOLD

    # While the circuit is open, call_tool returns None without calling the client
    result = await pool.call_tool("gmail", "x")
    assert result is None
    state.client.call_tool.assert_not_called()


@pytest.mark.asyncio
async def test_circuit_recovers_after_cool_down():
    pool = MCPClientPool()
    pool.register("gmail", "http://gmail-mcp:8080/mcp")
    state = pool._connectors["gmail"]
    state.client = MagicMock()
    state.client.call_tool = AsyncMock(return_value={"ok": True})

    # Simulate breaker opened in the past, beyond the cool-down window
    import time
    state.opened_at = time.monotonic() - _COOL_DOWN_SECONDS - 1
    state.failures = _FAILURE_THRESHOLD

    # is_open() should now return False (cool-down elapsed)
    assert state.is_open() is False

    # A successful call resets failure counter + opened_at
    result = await pool.call_tool("gmail", "x")
    assert result == {"ok": True}
    assert state.failures == 0
    assert state.opened_at is None
