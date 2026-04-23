# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""POST /mcp-servers must surface connect failures as 201 + status="error",
NOT bubble them as 500.

Backstop for the 2026-04-23 incident: anyio's exit-stack cleanup raised a
``BaseExceptionGroup`` after a failed stdio handshake (e.g. user typo'd a
command), which escaped the handler and produced "Internal Server Error"
in the UI instead of a useful error message in the rendered server card.
"""
from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app():
    """Mount only the mcp_client router for a focused test."""
    from app.routers.mcp_client import router

    app = FastAPI()
    app.include_router(router)
    return app


def _payload():
    return {
        "name": "test-fs",
        "transport": "stdio",
        "command": "echo",
        "args": ["hello"],
    }


@patch("utils.mcp_client.mcp_client_manager")
def test_add_server_returns_201_on_connect_exception_group(mgr):
    """``connect_all`` raising BaseExceptionGroup → 201 + status=error.

    The 2026-04-23 bug: anyio cleanup raises BaseExceptionGroup *after*
    connect_all's per-server try/except already swallowed the inner error,
    and the handler had no outer guard — so 500 escaped.
    """
    async def _raising_connect_all():
        # ExceptionGroup is the actual class anyio uses on Python 3.11+.
        raise BaseExceptionGroup("anyio cleanup", [RuntimeError("subprocess died")])

    mgr.connect_all.side_effect = _raising_connect_all
    mgr.list_servers.return_value = [
        {"name": "test-fs", "transport": "stdio", "status": "error",
         "error": "subprocess died", "tool_count": 0, "tools": []},
    ]

    client = TestClient(_make_app())
    res = client.post("/mcp-servers", json=_payload())

    assert res.status_code == 201, f"expected 201, got {res.status_code}: {res.text}"
    body = res.json()
    assert body["name"] == "test-fs"
    assert body["status"] == "error"
    assert body["error"]  # truthy — the user needs *something* actionable


@patch("utils.mcp_client.mcp_client_manager")
def test_add_server_returns_201_on_plain_exception(mgr):
    """Even a plain Exception out of connect_all should still produce 201."""
    async def _raising_connect_all():
        raise RuntimeError("connect refused")

    mgr.connect_all.side_effect = _raising_connect_all
    mgr.list_servers.return_value = []  # nothing got registered

    client = TestClient(_make_app())
    res = client.post("/mcp-servers", json=_payload())

    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "error"
    assert "connect refused" in (body.get("error") or "")


@patch("utils.mcp_client.mcp_client_manager")
def test_add_server_returns_201_on_clean_connect(mgr):
    """Sanity: when connect_all succeeds, status reflects what list_servers reports."""
    async def _ok_connect_all():
        return ["test-fs"]

    mgr.connect_all.side_effect = _ok_connect_all
    mgr.list_servers.return_value = [
        {"name": "test-fs", "transport": "stdio", "status": "connected",
         "error": None, "tool_count": 4, "tools": ["foo", "bar", "baz", "quux"]},
    ]

    client = TestClient(_make_app())
    res = client.post("/mcp-servers", json=_payload())

    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "connected"
    assert body["tool_count"] == 4
    assert body["error"] is None
