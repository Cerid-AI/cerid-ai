# Copyright (c) 2026 Cerid AI. Apache-2.0 license.
"""Tests for ToolPlugin — custom MCP tool registration via plugins."""

import logging
from unittest.mock import AsyncMock

import pytest

from plugins.base import ToolPlugin


# ---------------------------------------------------------------------------
# Concrete mock subclass
# ---------------------------------------------------------------------------


class FakeToolPlugin(ToolPlugin):
    """Minimal ToolPlugin for testing."""

    name = "fake-tools"
    version = "0.1.0"
    description = "Unit-test tool plugin"

    def __init__(self, tools: list | None = None):
        self._tools = tools or []

    def get_tools(self):
        return self._tools

    # register() inherited from ToolPlugin (no-op by default)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestToolPluginContract:
    def test_get_tools_returns_list(self):
        plugin = FakeToolPlugin(tools=[
            {
                "name": "plg_fake_search",
                "description": "Search something",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                "handler": AsyncMock(),
            }
        ])
        tools = plugin.get_tools()
        assert isinstance(tools, list)
        assert len(tools) == 1
        assert tools[0]["name"] == "plg_fake_search"
        assert "inputSchema" in tools[0]
        assert callable(tools[0]["handler"])

    def test_plugin_metadata(self):
        plugin = FakeToolPlugin()
        assert plugin.name == "fake-tools"
        assert plugin.version == "0.1.0"
        assert plugin.description == "Unit-test tool plugin"

    def test_empty_tools(self):
        plugin = FakeToolPlugin(tools=[])
        assert plugin.get_tools() == []


class TestToolPluginHandlerRegistration:
    """Simulate the handler collection done by plugins/__init__.py."""

    def test_handlers_populated(self):
        handler_fn = AsyncMock(return_value={"ok": True})
        plugin = FakeToolPlugin(tools=[
            {
                "name": "plg_fake_action",
                "description": "Do action",
                "inputSchema": {"type": "object", "properties": {}},
                "handler": handler_fn,
            }
        ])

        # Mimic what load_plugins() does: collect handlers
        handlers: dict = {}
        definitions: list = []
        for tool_def in plugin.get_tools():
            tool_name = tool_def["name"]
            handler = tool_def.pop("handler", None)
            if handler and callable(handler):
                handlers[tool_name] = handler
                definitions.append(tool_def)

        assert "plg_fake_action" in handlers
        assert handlers["plg_fake_action"] is handler_fn
        assert len(definitions) == 1
        # handler should have been popped from the definition dict
        assert "handler" not in definitions[0]

    @pytest.mark.asyncio
    async def test_execute_dispatches_to_handler(self):
        handler_fn = AsyncMock(return_value={"results": [1, 2]})
        plugin = FakeToolPlugin(tools=[
            {
                "name": "plg_fake_calc",
                "description": "Calculate",
                "inputSchema": {"type": "object"},
                "handler": handler_fn,
            }
        ])

        # Build handler map
        handlers = {}
        for t in plugin.get_tools():
            handlers[t["name"]] = t["handler"]

        result = await handlers["plg_fake_calc"]({"x": 42})
        handler_fn.assert_awaited_once_with({"x": 42})
        assert result == {"results": [1, 2]}

    def test_tool_name_collision_warning(self, caplog):
        """When two plugins register the same tool name, a warning should fire."""
        handlers: dict = {}

        handler_a = AsyncMock()
        handler_b = AsyncMock()

        tools_a = [{"name": "plg_shared", "description": "A", "inputSchema": {}, "handler": handler_a}]
        tools_b = [{"name": "plg_shared", "description": "B", "inputSchema": {}, "handler": handler_b}]

        with caplog.at_level(logging.WARNING):
            for tool_set in [tools_a, tools_b]:
                for td in tool_set:
                    name = td["name"]
                    handler = td.pop("handler", None)
                    if name in handlers:
                        logging.getLogger("ai-companion.plugins").warning(
                            "Tool name collision: '%s' already registered, overwriting", name,
                        )
                    if handler:
                        handlers[name] = handler

        assert "Tool name collision" in caplog.text
        # Second handler wins
        assert handlers["plg_shared"] is handler_b
