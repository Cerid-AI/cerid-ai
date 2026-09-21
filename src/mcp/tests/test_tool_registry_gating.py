# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The tool-registry safety gates must hold without a startup hook.

``ToolDef.feature_flag`` and ``MCP_DISABLED_TOOLS`` were both documented as
operator controls — the docstring on ``feature_flag`` names
``pkb_artifact_delete``, an irreversible hard delete, as the thing it gates.
Both were resolved by ``resolve_enabled()``, described as "called once at app
startup (app/main.py lifespan)". Nothing in src/mcp called it, so every tool
kept its ``True`` default and neither control did anything in production.

The existing suite could not see it: every test in test_tool_registry.py
calls ``resolve_enabled()`` itself first, which is the one thing the running
server never does. These tests deliberately do not call it.
"""
from __future__ import annotations

import pytest

from app.tool_registry import (
    PermissionDeniedError,
    _swap_registry,
    execute_registered_tool,
    get_registered_schemas,
    register_tool,
)


@pytest.fixture
def clean_registry():
    _, restore = _swap_registry({})
    yield
    restore()


def _register(name: str, **kwargs: object):
    @register_tool(
        name=name,
        description=f"{name}. **Use when** testing. **Returns** ``{{ok: true}}``.",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        **kwargs,
    )
    async def _h() -> dict:
        return {"ok": True}

    return _h


@pytest.mark.asyncio
async def test_disabled_tools_env_applies_without_a_startup_call(
    clean_registry, monkeypatch
) -> None:
    """The production path: no lifespan hook ran, the operator set the env var."""
    _register("x_kill")
    monkeypatch.setenv("MCP_DISABLED_TOOLS", "x_kill,x_other")

    with pytest.raises(PermissionDeniedError):
        await execute_registered_tool("x_kill", {})


@pytest.mark.asyncio
async def test_feature_flag_gates_without_a_startup_call(
    clean_registry, monkeypatch
) -> None:
    _register("x_gated", feature_flag="MCP_ENABLE_X_GATED")
    monkeypatch.delenv("MCP_ENABLE_X_GATED", raising=False)

    with pytest.raises(PermissionDeniedError):
        await execute_registered_tool("x_gated", {})

    monkeypatch.setenv("MCP_ENABLE_X_GATED", "1")
    assert await execute_registered_tool("x_gated", {}) == {"ok": True}


def test_disabled_tool_is_absent_from_the_advertised_schemas(
    clean_registry, monkeypatch
) -> None:
    """A gated-off tool the LLM can still see is a gate that only stops the
    honest caller."""
    _register("x_visible")
    _register("x_hidden")
    monkeypatch.setenv("MCP_DISABLED_TOOLS", "x_hidden")

    names = {s["name"] for s in get_registered_schemas()}
    assert names == {"x_visible"}


@pytest.mark.asyncio
async def test_env_change_takes_effect_on_the_next_call(
    clean_registry, monkeypatch
) -> None:
    """Enablement is read per call, not frozen at import or at first use."""
    _register("x_toggle")
    assert await execute_registered_tool("x_toggle", {}) == {"ok": True}

    monkeypatch.setenv("MCP_DISABLED_TOOLS", "x_toggle")
    with pytest.raises(PermissionDeniedError):
        await execute_registered_tool("x_toggle", {})

    monkeypatch.setenv("MCP_DISABLED_TOOLS", "")
    assert await execute_registered_tool("x_toggle", {}) == {"ok": True}


@pytest.mark.asyncio
async def test_ungated_tool_stays_enabled(clean_registry, monkeypatch) -> None:
    """The control: nothing in the env, nothing gated off."""
    _register("x_plain")
    monkeypatch.delenv("MCP_DISABLED_TOOLS", raising=False)
    assert await execute_registered_tool("x_plain", {}) == {"ok": True}
