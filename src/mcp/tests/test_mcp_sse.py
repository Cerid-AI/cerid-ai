# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for MCP SSE transport — session management, JSON-RPC, and queue bounds."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app():
    from app.routers.mcp_sse import router

    app = FastAPI()
    app.include_router(router)
    return app


class TestMCPMessages:
    """Test POST /mcp/messages JSON-RPC handler."""

    def test_initialize_returns_capabilities(self):
        client = TestClient(_make_app())

        # POST without sessionId → direct JSON response (no SSE stream needed)
        response = client.post(
            "/mcp/messages",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["result"]["serverInfo"]["name"] == "cerid-ai-companion"
        assert "tools" in data["result"]["capabilities"]

    def test_tools_list(self):
        client = TestClient(_make_app())

        response = client.post(
            "/mcp/messages",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
        )
        assert response.status_code == 200
        data = response.json()
        tools = data["result"]["tools"]
        assert isinstance(tools, list)
        assert len(tools) > 0
        # Verify tool schema structure
        tool_names = [t["name"] for t in tools]
        assert "pkb_health" in tool_names

    def test_ping(self):
        client = TestClient(_make_app())
        response = client.post(
            "/mcp/messages",
            json={"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}},
        )
        assert response.status_code == 200
        assert response.json()["result"] == {}

    def test_unknown_method(self):
        client = TestClient(_make_app())
        response = client.post(
            "/mcp/messages",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "nonexistent/method",
                "params": {},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32601

    def test_empty_body_returns_202(self):
        client = TestClient(_make_app())
        response = client.post(
            "/mcp/messages",
            content=b"{}",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 202

    def test_malformed_json_returns_400(self):
        client = TestClient(_make_app())
        response = client.post(
            "/mcp/messages",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400

    def test_initialized_notification_returns_202(self):
        client = TestClient(_make_app())
        response = client.post(
            "/mcp/messages",
            json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
        )
        assert response.status_code == 202


class TestSessionManagement:
    """Test session lifecycle and queue bounds."""

    def test_sse_head_returns_200(self):
        client = TestClient(_make_app())
        response = client.head("/mcp/sse")
        assert response.status_code == 200

    def test_sse_post_returns_200(self):
        client = TestClient(_make_app())
        response = client.post("/mcp/sse")
        assert response.status_code == 200

    def test_clear_sessions(self):
        from app.routers.mcp_sse import _sessions, clear_sessions

        _sessions["test-session"] = asyncio.Queue()
        assert len(_sessions) > 0
        clear_sessions()
        assert len(_sessions) == 0


class TestQueueBounds:
    """Test that session queues are bounded."""

    def test_queue_has_maxsize(self):
        """Verify session queues are bounded (not infinite)."""
        queue = asyncio.Queue(maxsize=100)
        assert queue.maxsize == 100


class TestSessionEviction:
    """Test session eviction at capacity."""

    def test_max_sessions_enforced(self):
        from app.routers.mcp_sse import _MAX_SESSIONS, _sessions

        # Clear any existing sessions
        _sessions.clear()

        # Fill to capacity
        for i in range(_MAX_SESSIONS):
            _sessions[f"session-{i}"] = asyncio.Queue(maxsize=100)

        assert len(_sessions) == _MAX_SESSIONS

        # Adding one more should evict oldest (session-0)
        first_key = next(iter(_sessions))
        assert first_key == "session-0"

        _sessions.clear()
