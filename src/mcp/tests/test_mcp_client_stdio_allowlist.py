# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""POST /mcp-servers must not spawn whatever binary the request body names (F071)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.mcp_client import router
from utils.mcp_client import mcp_client_manager


@pytest.fixture(autouse=True)
def _clean_manager():
    mcp_client_manager._configs.clear()
    mcp_client_manager._errors.clear()
    mcp_client_manager._connected.clear()
    yield
    mcp_client_manager._configs.clear()
    mcp_client_manager._errors.clear()
    mcp_client_manager._connected.clear()


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture()
def spawn_spy():
    """Spy on the SDK's process launcher — being called at all is the breach."""
    calls: list = []

    def _record(params, *a, **kw):
        calls.append(params)
        raise RuntimeError("spawn blocked by test")

    with patch("mcp.client.stdio.stdio_client", side_effect=_record) as _m:
        yield calls


def _body(**over):
    body = {
        "name": "evil",
        "transport": "stdio",
        "command": "/bin/sh",
        "args": ["-c", "touch /tmp/cerid-pwned"],
        "env": {},
    }
    body.update(over)
    return body


class TestArbitraryCommandIsRefused:
    def test_absolute_binary_path_is_refused(self, client: TestClient, spawn_spy):
        resp = client.post("/mcp-servers", json=_body())
        assert resp.status_code == 400, resp.text
        assert spawn_spy == [], "the request body reached the process launcher"
        assert mcp_client_manager.list_servers() == [], "refused server was still registered"

    def test_stdio_is_off_by_default_even_for_npx(self, client: TestClient, spawn_spy):
        resp = client.post("/mcp-servers", json=_body(command="npx", args=["-y", "@x/y"]))
        assert resp.status_code == 400, resp.text
        assert spawn_spy == []

    def test_command_outside_the_allowlist_is_refused(
        self, client: TestClient, spawn_spy, monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("CERID_MCP_STDIO_ALLOWED_COMMANDS", "npx")
        resp = client.post("/mcp-servers", json=_body(command="curl"))
        assert resp.status_code == 400, resp.text
        assert spawn_spy == []

    def test_allowlisted_name_may_not_be_smuggled_as_a_path(
        self, client: TestClient, spawn_spy, monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("CERID_MCP_STDIO_ALLOWED_COMMANDS", "npx")
        resp = client.post("/mcp-servers", json=_body(command="/tmp/evil/npx"))
        assert resp.status_code == 400, resp.text
        assert spawn_spy == []

    def test_loader_hijacking_env_is_refused(
        self, client: TestClient, spawn_spy, monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("CERID_MCP_STDIO_ALLOWED_COMMANDS", "npx")
        resp = client.post(
            "/mcp-servers",
            json=_body(command="npx", env={"LD_PRELOAD": "/tmp/evil.so"}),
        )
        assert resp.status_code == 400, resp.text
        assert spawn_spy == []


class TestAllowlistedServerStillWorks:
    def test_allowlisted_command_is_accepted(
        self, client: TestClient, spawn_spy, monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("CERID_MCP_STDIO_ALLOWED_COMMANDS", "npx,uvx")
        resp = client.post(
            "/mcp-servers", json=_body(name="good", command="npx", args=["-y", "@x/y"]),
        )
        assert resp.status_code == 201, resp.text
        assert [s["name"] for s in mcp_client_manager.list_servers()] == ["good"]
        assert len(spawn_spy) == 1, "an allowlisted server must still be launched"
        assert spawn_spy[0].command == "npx"

    def test_sse_transport_is_untouched(self, client: TestClient, spawn_spy):
        resp = client.post(
            "/mcp-servers",
            json={"name": "remote", "transport": "sse", "url": "http://127.0.0.1:1/sse"},
        )
        assert resp.status_code == 201, resp.text
        assert spawn_spy == []


class TestEnvConfigIsValidatedToo:
    def test_env_supplied_stdio_config_is_validated_at_spawn(self, spawn_spy):
        """MCP_SERVERS_CONFIG takes the same path — validate at the spawn site."""
        import asyncio

        from utils.mcp_client import MCPClientManager, MCPServerConfig

        mgr = MCPClientManager()
        mgr.add_server(MCPServerConfig(name="e", transport="stdio", command="/bin/sh"))
        asyncio.run(mgr.connect_all())
        assert spawn_spy == []
        assert "e" in mgr._errors
