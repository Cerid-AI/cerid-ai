# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the decorator-based MCP tool registry."""
from __future__ import annotations

import pytest

from app.tool_registry import (
    TOOL_REGISTRY,
    InvalidParamsError,
    InvalidToolError,
    PermissionDeniedError,
    _swap_registry,
    execute_registered_tool,
    get_registered_schemas,
    register_tool,
    resolve_enabled,
)


@pytest.fixture
def clean_registry():
    """Swap in a fresh empty registry for the duration of a test."""
    _, restore = _swap_registry({})
    yield
    restore()


def test_register_simple_tool(clean_registry):
    @register_tool(
        name="x_test_tool",
        description="A test tool. **Use when** testing. **Returns** ``{ok: true}``.",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
    )
    async def _h() -> dict:
        return {"ok": True}

    assert "x_test_tool" in TOOL_REGISTRY
    t = TOOL_REGISTRY["x_test_tool"]
    assert t.cost_class == "low"  # default
    assert t.enabled is True
    schemas = get_registered_schemas()
    assert any(s["name"] == "x_test_tool" for s in schemas)


def test_register_rejects_non_object_output_schema(clean_registry):
    """The MCP spec mandates outputSchema.type == 'object'."""
    with pytest.raises(ValueError, match="outputSchema.type must be 'object'"):
        @register_tool(
            name="x_bad_output",
            description="Bad",
            input_schema={"type": "object"},
            output_schema={"type": "array"},  # ← MCP spec violation
        )
        async def _h() -> list:
            return []


def test_register_rejects_double_registration(clean_registry):
    @register_tool(
        name="x_dup",
        description="First",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    async def _h1() -> dict:
        return {}

    with pytest.raises(ValueError, match="already registered"):
        @register_tool(
            name="x_dup",  # same name
            description="Second",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
        async def _h2() -> dict:
            return {}


@pytest.mark.asyncio
async def test_execute_routes_to_handler(clean_registry):
    @register_tool(
        name="x_echo",
        description="Echo",
        input_schema={
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
        output_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
    )
    async def _echo(msg: str) -> dict:
        return {"msg": msg}

    out = await execute_registered_tool("x_echo", {"msg": "hi"})
    assert out == {"msg": "hi"}


@pytest.mark.asyncio
async def test_execute_unknown_tool_raises_invalid_tool(clean_registry):
    with pytest.raises(InvalidToolError):
        await execute_registered_tool("x_missing", {})


@pytest.mark.asyncio
async def test_execute_disabled_tool_raises_permission_denied(
    clean_registry, monkeypatch
):
    @register_tool(
        name="x_gated",
        description="Gated",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        feature_flag="ENABLE_X_GATED",
    )
    async def _h() -> dict:
        return {}

    # Flag not set → disabled after resolve
    monkeypatch.delenv("ENABLE_X_GATED", raising=False)
    resolve_enabled()
    with pytest.raises(PermissionDeniedError):
        await execute_registered_tool("x_gated", {})

    # Flag truthy → enabled
    monkeypatch.setenv("ENABLE_X_GATED", "1")
    resolve_enabled()
    out = await execute_registered_tool("x_gated", {})
    assert out == {}


@pytest.mark.asyncio
async def test_execute_disabled_via_env_list(clean_registry, monkeypatch):
    @register_tool(
        name="x_kill",
        description="Always-on by default",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    async def _h() -> dict:
        return {}

    monkeypatch.setenv("MCP_DISABLED_TOOLS", "x_kill,x_other")
    resolve_enabled()
    with pytest.raises(PermissionDeniedError):
        await execute_registered_tool("x_kill", {})


@pytest.mark.asyncio
async def test_execute_bad_kwargs_raises_invalid_params(clean_registry):
    @register_tool(
        name="x_strict",
        description="Strict",
        input_schema={
            "type": "object",
            "properties": {"a": {"type": "integer"}},
            "required": ["a"],
        },
        output_schema={"type": "object"},
    )
    async def _h(a: int) -> dict:
        return {"a": a}

    # Missing required kwarg
    with pytest.raises(InvalidParamsError):
        await execute_registered_tool("x_strict", {})

    # Unexpected kwarg
    with pytest.raises(InvalidParamsError):
        await execute_registered_tool("x_strict", {"a": 1, "b": 2})


def test_schema_includes_deprecation_metadata(clean_registry):
    @register_tool(
        name="x_old",
        description="Old",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        deprecated_since="0.95.0",
        deprecated_replaced_by="x_new",
    )
    async def _h() -> dict:
        return {}

    schema = TOOL_REGISTRY["x_old"].to_mcp_schema()
    assert schema["_deprecated_since"] == "0.95.0"
    assert schema["_deprecated_replaced_by"] == "x_new"


def test_get_registered_schemas_sorts_by_name(clean_registry):
    for name in ["x_zebra", "x_alpha", "x_mango"]:
        @register_tool(
            name=name,
            description=f"Tool {name}",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
        async def _h() -> dict:
            return {}
        # The async-def `_h` is per-iteration; registry stores its own ref.

    schemas = get_registered_schemas()
    names = [s["name"] for s in schemas]
    assert names == sorted(names)
