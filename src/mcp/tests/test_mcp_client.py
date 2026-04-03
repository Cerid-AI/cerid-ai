# Copyright (c) 2026 Cerid AI. Apache-2.0 license.
"""Tests for MCPClientManager — external MCP server connection management."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.mcp_client import ExternalTool, MCPClientManager, MCPServerConfig


# ---------------------------------------------------------------------------
# MCPServerConfig dataclass
# ---------------------------------------------------------------------------


class TestMCPServerConfig:
    def test_create_stdio_config(self):
        cfg = MCPServerConfig(
            name="test-server",
            transport="stdio",
            command="npx",
            args=["-y", "@some/mcp-server"],
        )
        assert cfg.name == "test-server"
        assert cfg.transport == "stdio"
        assert cfg.enabled is True
        assert cfg.command == "npx"
        assert cfg.args == ["-y", "@some/mcp-server"]
        assert cfg.url == ""

    def test_create_sse_config(self):
        cfg = MCPServerConfig(
            name="remote",
            transport="sse",
            url="https://mcp.example.com/sse",
            headers={"Authorization": "Bearer tok"},
        )
        assert cfg.transport == "sse"
        assert cfg.url == "https://mcp.example.com/sse"
        assert cfg.headers["Authorization"] == "Bearer tok"

    def test_to_dict(self):
        cfg = MCPServerConfig(name="s1", transport="stdio", command="node", args=["server.js"])
        d = cfg.to_dict()
        assert d["name"] == "s1"
        assert d["transport"] == "stdio"
        assert d["enabled"] is True
        assert "command" in d

    def test_defaults(self):
        cfg = MCPServerConfig(name="minimal", transport="stdio")
        assert cfg.enabled is True
        assert cfg.env == {}
        assert cfg.headers == {}


# ---------------------------------------------------------------------------
# MCPClientManager — config operations (sync, no MCP SDK needed)
# ---------------------------------------------------------------------------


class TestMCPClientManagerConfig:
    def setup_method(self):
        self.mgr = MCPClientManager()

    def test_add_server(self):
        cfg = MCPServerConfig(name="a", transport="stdio", command="echo")
        self.mgr.add_server(cfg)
        servers = self.mgr.list_servers()
        assert len(servers) == 1
        assert servers[0]["name"] == "a"
        assert servers[0]["status"] == "disconnected"

    def test_remove_server(self):
        cfg = MCPServerConfig(name="rm-me", transport="stdio")
        self.mgr.add_server(cfg)
        assert self.mgr.remove_server("rm-me") is True
        assert self.mgr.list_servers() == []

    def test_remove_nonexistent_server(self):
        assert self.mgr.remove_server("ghost") is False

    @patch.dict("os.environ", {"MCP_SERVERS_CONFIG": json.dumps([
        {"name": "s1", "transport": "stdio", "command": "node", "args": ["a.js"]},
        {"name": "s2", "transport": "sse", "url": "https://x.com/sse"},
    ])})
    def test_load_config_json_string(self):
        count = self.mgr.load_config()
        assert count == 2
        names = [s["name"] for s in self.mgr.list_servers()]
        assert "s1" in names
        assert "s2" in names

    @patch.dict("os.environ", {"MCP_SERVERS_CONFIG": ""})
    def test_load_config_empty_returns_zero(self):
        assert self.mgr.load_config() == 0

    @patch.dict("os.environ", {"MCP_SERVERS_CONFIG": "not-valid-json"})
    def test_load_config_invalid_json_returns_zero(self):
        assert self.mgr.load_config() == 0


# ---------------------------------------------------------------------------
# MCPClientManager — tool introspection
# ---------------------------------------------------------------------------


class TestMCPClientManagerTools:
    def setup_method(self):
        self.mgr = MCPClientManager()

    def _add_fake_tool(self, server: str, tool: str):
        ns = f"ext_{server}_{tool}"
        self.mgr._tools[ns] = ExternalTool(
            server_name=server,
            tool_name=tool,
            namespaced_name=ns,
            description=f"[{server}] {tool}",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        )

    def test_has_external_tools_empty(self):
        assert self.mgr.has_external_tools() is False

    def test_has_external_tools_with_tools(self):
        self._add_fake_tool("fs", "read_file")
        assert self.mgr.has_external_tools() is True

    def test_list_external_tools_format(self):
        self._add_fake_tool("gh", "search")
        tools = self.mgr.list_external_tools()
        assert len(tools) == 1
        t = tools[0]
        assert t["name"] == "ext_gh_search"
        assert "description" in t
        assert "inputSchema" in t

    def test_list_servers_status_variants(self):
        """Verify connected / error / disconnected statuses."""
        self.mgr.add_server(MCPServerConfig(name="ok", transport="stdio"))
        self.mgr.add_server(MCPServerConfig(name="bad", transport="stdio"))
        self.mgr.add_server(MCPServerConfig(name="idle", transport="stdio"))

        self.mgr._connected.add("ok")
        self.mgr._errors["bad"] = "timeout"

        statuses = {s["name"]: s["status"] for s in self.mgr.list_servers()}
        assert statuses["ok"] == "connected"
        assert statuses["bad"] == "error"
        assert statuses["idle"] == "disconnected"


# ---------------------------------------------------------------------------
# MCPClientManager — call_tool error path (no real MCP SDK)
# ---------------------------------------------------------------------------


class TestMCPClientManagerCallTool:
    @pytest.mark.asyncio
    async def test_call_tool_unknown_raises(self):
        mgr = MCPClientManager()
        with pytest.raises(ValueError, match="Unknown external tool"):
            await mgr.call_tool("ext_nope_missing", {"q": "hi"})

    @pytest.mark.asyncio
    async def test_call_tool_disconnected_raises(self):
        mgr = MCPClientManager()
        mgr._tools["ext_srv_t"] = ExternalTool(
            server_name="srv", tool_name="t",
            namespaced_name="ext_srv_t",
            description="t", input_schema={},
        )
        with pytest.raises(RuntimeError, match="not connected"):
            await mgr.call_tool("ext_srv_t", {})

    @pytest.mark.asyncio
    async def test_call_tool_dispatches_to_session(self):
        mgr = MCPClientManager()
        mgr._tools["ext_s_do"] = ExternalTool(
            server_name="s", tool_name="do",
            namespaced_name="ext_s_do",
            description="do it", input_schema={},
        )

        # Fake session with call_tool returning MCP-like result
        content_item = MagicMock()
        content_item.text = "result-text"
        result = MagicMock()
        result.content = [content_item]

        session = AsyncMock()
        session.call_tool.return_value = result
        mgr._sessions["s"] = session

        out = await mgr.call_tool("ext_s_do", {"x": 1})
        session.call_tool.assert_awaited_once_with("do", {"x": 1})
        assert out == "result-text"
