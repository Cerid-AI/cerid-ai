# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The MCP transport must not hand itself to any web page (F056).

/mcp/sse used to answer with ``Access-Control-Allow-Origin: *``. Because
Starlette's CORSMiddleware only *adds* that header and never strips one a
route already set, the wildcard survived for origins the app's own CORS
policy rejects — so any site the user visited could read the endpoint event,
lift the sessionId, and drive tool calls through /mcp/messages.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

EVIL = "https://evil.example"
ALLOWED = "http://localhost:5173"

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2024-11-05"},
}


@pytest.fixture()
def client() -> TestClient:
    from app.routers.mcp_sse import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _sse_response(origin: str | None = None):
    """Call the SSE endpoint directly and inspect the response it hands back.

    Going through TestClient would open the never-ending event stream; only
    the status line and headers are under test here.
    """
    import asyncio

    from starlette.requests import Request

    from app.routers.mcp_sse import clear_sessions, mcp_sse_endpoint

    headers = [(b"host", b"testserver")]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/mcp/sse",
        "raw_path": b"/mcp/sse",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 51234),
        "server": ("testserver", 80),
    }
    try:
        return asyncio.run(mcp_sse_endpoint(Request(scope)))
    finally:
        clear_sessions()


class TestSSEHeaders:
    def test_sse_does_not_wildcard_allow_origin(self):
        resp = _sse_response()
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") != "*"

    def test_sse_rejects_disallowed_origin(self):
        resp = _sse_response(EVIL)
        assert resp.status_code == 403
        assert resp.headers.get("access-control-allow-origin") != "*"

    def test_sse_allows_a_configured_origin(self):
        assert _sse_response(ALLOWED).status_code == 200

    def test_sse_allows_a_non_browser_client(self):
        """MCP clients (Claude Code, the SDK) send no Origin at all."""
        assert _sse_response(None).status_code == 200


class TestMessagesOrigin:
    def test_messages_rejects_disallowed_origin(self, client: TestClient):
        resp = client.post("/mcp/messages", json=INITIALIZE, headers={"Origin": EVIL})
        assert resp.status_code == 403, resp.text
        assert "serverInfo" not in resp.text

    def test_call_sync_rejects_disallowed_origin(self, client: TestClient):
        resp = client.post("/mcp/call-sync", json=INITIALIZE, headers={"Origin": EVIL})
        assert resp.status_code == 403, resp.text

    def test_sse_post_probe_rejects_disallowed_origin(self, client: TestClient):
        resp = client.post("/mcp/sse", headers={"Origin": EVIL})
        assert resp.status_code == 403, resp.text

    def test_messages_allows_configured_origin(self, client: TestClient):
        resp = client.post("/mcp/messages", json=INITIALIZE, headers={"Origin": ALLOWED})
        assert resp.status_code == 200, resp.text
        assert resp.json()["result"]["serverInfo"]["name"] == "cerid-ai-companion"

    def test_messages_allows_no_origin(self, client: TestClient):
        resp = client.post("/mcp/messages", json=INITIALIZE)
        assert resp.status_code == 200, resp.text


class TestMessagesContentType:
    """text/plain is a CORS-simple type — it is how the write leg dodges preflight."""

    def test_messages_rejects_text_plain(self, client: TestClient):
        resp = client.post(
            "/mcp/messages",
            content=json.dumps(INITIALIZE),
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 415, resp.text

    def test_call_sync_rejects_form_encoding(self, client: TestClient):
        resp = client.post(
            "/mcp/call-sync",
            content=json.dumps(INITIALIZE),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 415, resp.text

    def test_messages_accepts_json_with_charset(self, client: TestClient):
        resp = client.post(
            "/mcp/messages",
            content=json.dumps(INITIALIZE),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        assert resp.status_code == 200, resp.text
