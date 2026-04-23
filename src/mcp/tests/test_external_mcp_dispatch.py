# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sprint 1A.1 — external MCP tool dispatch wiring.

Verifies that:

* :func:`dispatch_external_mcp_tool` returns ``None`` for non-``ext_``
  names (lets the next dispatcher in the chain claim them) and routes
  ``ext_*`` names to ``mcp_client_manager.call_tool``.
* :func:`get_external_tool_schemas` reflects the manager's discovered
  tool list and returns empty when no servers are connected.
* :func:`app.tools.get_all_tools` concatenates static ``MCP_TOOLS``
  with the dynamic external set in that order.
* The dispatcher is registered into ``app.tools._tool_dispatchers`` at
  import time so ``execute_tool('ext_*', ...)`` reaches the manager
  via the existing extension hook (mirroring the trading-tools pattern).
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services.external_mcp_dispatch import (
    EXTERNAL_PREFIX,
    dispatch_external_mcp_tool,
    get_external_tool_schemas,
)
from app.tools import MCP_TOOLS, _tool_dispatchers, execute_tool, get_all_tools
from utils.mcp_client import ExternalTool, mcp_client_manager

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_external_tool(monkeypatch: pytest.MonkeyPatch):
    """Inject a single discovered external tool into the singleton manager.

    Cleans up on teardown so other tests see an empty external palette.
    """
    tool = ExternalTool(
        server_name="testserver",
        tool_name="ping",
        namespaced_name="ext_testserver_ping",
        description="[testserver] echo back",
        input_schema={"type": "object", "properties": {}},
    )
    original = dict(mcp_client_manager._tools)
    mcp_client_manager._tools[tool.namespaced_name] = tool
    yield tool
    mcp_client_manager._tools.clear()
    mcp_client_manager._tools.update(original)


# ---------------------------------------------------------------------------
# dispatch_external_mcp_tool — namespace gating + routing
# ---------------------------------------------------------------------------


async def test_dispatcher_returns_none_for_non_ext_names() -> None:
    """Names without the ``ext_`` prefix must pass through (return None) so
    the next dispatcher in ``_tool_dispatchers`` can claim them."""
    assert await dispatch_external_mcp_tool("pkb_query", {}) is None
    assert await dispatch_external_mcp_tool("trade_buy", {}) is None
    assert await dispatch_external_mcp_tool("", {}) is None


async def test_dispatcher_routes_ext_names_to_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ext_*`` names invoke ``mcp_client_manager.call_tool`` with the
    full namespaced name and the raw arguments dict."""
    stub = AsyncMock(return_value="external-result")
    monkeypatch.setattr(mcp_client_manager, "call_tool", stub)

    result = await dispatch_external_mcp_tool(
        "ext_testserver_ping", {"x": 1, "y": "hi"},
    )

    assert result == "external-result"
    stub.assert_awaited_once_with("ext_testserver_ping", {"x": 1, "y": "hi"})


async def test_dispatcher_propagates_manager_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disconnected/unknown-tool errors from the manager must propagate so
    the SSE layer surfaces them as JSON-RPC tool errors instead of a
    silent ``None`` (which would falsely look like 'not my tool')."""
    stub = AsyncMock(side_effect=RuntimeError("server 'x' not connected"))
    monkeypatch.setattr(mcp_client_manager, "call_tool", stub)

    with pytest.raises(RuntimeError, match="not connected"):
        await dispatch_external_mcp_tool("ext_x_anything", {})


async def test_external_prefix_is_canonical() -> None:
    """Single source of truth — must match MCPClientManager._discover_tools'
    namespacing (``f'ext_{server_name}_{tool_name}'``)."""
    assert EXTERNAL_PREFIX == "ext_"


# ---------------------------------------------------------------------------
# get_external_tool_schemas — schema augmentation
# ---------------------------------------------------------------------------


async def test_get_external_tool_schemas_empty_when_no_tools() -> None:
    """Empty list when no external servers are connected — never None,
    so the SSE response can safely concatenate."""
    mcp_client_manager._tools.clear()
    assert get_external_tool_schemas() == []


async def test_get_external_tool_schemas_returns_discovered_tools(
    fake_external_tool: ExternalTool,
) -> None:
    schemas = get_external_tool_schemas()
    names = [s["name"] for s in schemas]
    assert fake_external_tool.namespaced_name in names
    matching = next(s for s in schemas if s["name"] == fake_external_tool.namespaced_name)
    assert matching["description"] == fake_external_tool.description
    assert matching["inputSchema"] == fake_external_tool.input_schema


# ---------------------------------------------------------------------------
# get_all_tools — concatenation order + completeness
# ---------------------------------------------------------------------------


async def test_get_all_tools_includes_built_ins(fake_external_tool: ExternalTool) -> None:
    """Static ``MCP_TOOLS`` come first; relative order of built-ins is stable."""
    all_tools = get_all_tools()
    builtin_names = [t["name"] for t in MCP_TOOLS]
    all_names = [t["name"] for t in all_tools]
    # Built-ins prefix the result; external follows
    assert all_names[: len(builtin_names)] == builtin_names
    assert fake_external_tool.namespaced_name in all_names[len(builtin_names) :]


async def test_get_all_tools_returns_only_built_ins_when_no_external() -> None:
    mcp_client_manager._tools.clear()
    all_tools = get_all_tools()
    assert [t["name"] for t in all_tools] == [t["name"] for t in MCP_TOOLS]


# ---------------------------------------------------------------------------
# Bootstrap registration — dispatcher reachable via execute_tool()
# ---------------------------------------------------------------------------


async def test_dispatcher_registered_in_extension_hook() -> None:
    """``app.tools`` registers ``dispatch_external_mcp_tool`` into
    ``_tool_dispatchers`` at import time so the chain at execute_tool's
    tail picks it up. Regression guard against accidental removal."""
    assert dispatch_external_mcp_tool in _tool_dispatchers


async def test_execute_tool_routes_ext_name_through_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
    fake_external_tool: ExternalTool,
) -> None:
    """End-to-end: ``execute_tool('ext_*', ...)`` reaches the manager via
    the registered dispatcher in ``_tool_dispatchers``. This is the
    Sprint 1A.1 acceptance test — proves an LLM tool-call against an
    external MCP tool actually fires through the extension chain."""
    captured: dict[str, Any] = {}

    async def _capture_call(name: str, args: dict[str, Any]) -> str:
        captured["name"] = name
        captured["args"] = args
        return "called-via-extension-chain"

    monkeypatch.setattr(mcp_client_manager, "call_tool", _capture_call)

    result = await execute_tool(
        "ext_testserver_ping", {"echo": "hello"},
    )

    assert result == "called-via-extension-chain"
    assert captured == {
        "name": "ext_testserver_ping",
        "args": {"echo": "hello"},
    }


async def test_execute_tool_unknown_name_still_raises() -> None:
    """Names neither built-in nor matching any dispatcher must still raise
    ``ValueError`` — the new dispatcher must NOT swallow unknown names."""
    mcp_client_manager._tools.clear()
    with pytest.raises(ValueError, match="Unknown tool"):
        await execute_tool("definitely_not_a_real_tool", {})
