# Copyright 2026 Cerid AI. Apache-2.0 license.
"""Tests for middleware: API key auth, rate limiter, request ID injection."""

import hashlib

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from middleware.auth import EXEMPT_PATHS, EXEMPT_PREFIXES, APIKeyMiddleware, _redact_ip
from middleware.request_id import RequestIDMiddleware, get_request_id, request_id_var


# ---------------------------------------------------------------------------
# Helper: minimal ASGI app for middleware testing
# ---------------------------------------------------------------------------

async def _ok_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _make_auth_app(api_key: str | None = None) -> Starlette:
    app = Starlette(
        routes=[
            Route("/", _ok_endpoint),
            Route("/health", _ok_endpoint),
            Route("/docs", _ok_endpoint),
            Route("/query", _ok_endpoint, methods=["POST"]),
            Route("/artifacts", _ok_endpoint),
            Route("/mcp/sse", _ok_endpoint),
        ],
    )
    app.add_middleware(APIKeyMiddleware, api_key=api_key)
    return app


# ---------------------------------------------------------------------------
# Tests: _redact_ip helper
# ---------------------------------------------------------------------------

class TestRedactIP:
    def test_returns_sha256_prefix(self):
        result = _redact_ip("192.168.1.1")
        expected = hashlib.sha256(b"192.168.1.1").hexdigest()[:12]
        assert result == expected

    def test_deterministic(self):
        assert _redact_ip("10.0.0.1") == _redact_ip("10.0.0.1")

    def test_different_ips_differ(self):
        assert _redact_ip("10.0.0.1") != _redact_ip("10.0.0.2")


# ---------------------------------------------------------------------------
# Tests: API key bypass mode
# ---------------------------------------------------------------------------

class TestNoKeyConfigured:
    """When no CERID_API_KEY is set, all requests pass through."""

    def test_unprotected_endpoint_passes(self):
        client = TestClient(_make_auth_app(api_key=None))
        resp = client.get("/artifacts")
        assert resp.status_code == 200

    def test_empty_string_key_passes(self):
        client = TestClient(_make_auth_app(api_key=""))
        resp = client.post("/query")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: API key enforcement
# ---------------------------------------------------------------------------

class TestKeyEnforced:
    """When CERID_API_KEY is set, non-exempt paths need the key."""

    def test_missing_key_returns_401(self):
        client = TestClient(_make_auth_app(api_key="secret-key"))
        resp = client.get("/artifacts")
        assert resp.status_code == 401

    def test_correct_key_passes(self):
        client = TestClient(_make_auth_app(api_key="secret-key"))
        resp = client.get("/artifacts", headers={"X-API-Key": "secret-key"})
        assert resp.status_code == 200

    def test_wrong_key_returns_401(self):
        client = TestClient(_make_auth_app(api_key="secret-key"))
        resp = client.get("/artifacts", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 401

    def test_exempt_path_health_passes(self):
        client = TestClient(_make_auth_app(api_key="secret-key"))
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_exempt_prefix_mcp_passes(self):
        client = TestClient(_make_auth_app(api_key="secret-key"))
        resp = client.get("/mcp/sse")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: Request ID middleware
# ---------------------------------------------------------------------------

class TestRequestIDMiddleware:
    def test_generates_request_id(self):
        async def _id_endpoint(request: Request) -> JSONResponse:
            return JSONResponse({"request_id": request.state.request_id})

        app = Starlette(routes=[Route("/test", _id_endpoint)])
        app.add_middleware(RequestIDMiddleware)
        client = TestClient(app)
        resp = client.get("/test")
        assert resp.status_code == 200
        assert len(resp.json()["request_id"]) > 0

    def test_propagates_existing_request_id(self):
        async def _id_endpoint(request: Request) -> JSONResponse:
            return JSONResponse({"request_id": request.state.request_id})

        app = Starlette(routes=[Route("/test", _id_endpoint)])
        app.add_middleware(RequestIDMiddleware)
        client = TestClient(app)
        resp = client.get("/test", headers={"X-Request-ID": "custom-id-123"})
        assert resp.json()["request_id"] == "custom-id-123"

    def test_response_header_set(self):
        app = Starlette(routes=[Route("/test", _ok_endpoint)])
        app.add_middleware(RequestIDMiddleware)
        client = TestClient(app)
        resp = client.get("/test")
        assert "x-request-id" in resp.headers
