# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for middleware/auth.py — API key authentication middleware."""

from __future__ import annotations

import hashlib

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.middleware.auth import EXEMPT_PATHS, EXEMPT_PREFIXES, APIKeyMiddleware, _redact_ip

# ---------------------------------------------------------------------------
# Helper: minimal ASGI app for middleware testing
# ---------------------------------------------------------------------------

async def _ok_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _make_app(api_key: str | None = None) -> Starlette:
    """Create a Starlette app with auth middleware for testing."""
    app = Starlette(
        routes=[
            Route("/", _ok_endpoint),
            Route("/health", _ok_endpoint),
            Route("/api/v1/health", _ok_endpoint),
            Route("/docs", _ok_endpoint),
            Route("/openapi.json", _ok_endpoint),
            Route("/redoc", _ok_endpoint),
            Route("/mcp/sse", _ok_endpoint),
            Route("/mcp/messages", _ok_endpoint, methods=["POST"]),
            Route("/query", _ok_endpoint, methods=["POST"]),
            Route("/artifacts", _ok_endpoint),
            Route("/agent/query", _ok_endpoint, methods=["POST"]),
            Route("/ingest_file", _ok_endpoint, methods=["POST"]),
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
        assert len(result) == 12

    def test_deterministic(self):
        assert _redact_ip("10.0.0.1") == _redact_ip("10.0.0.1")

    def test_different_ips_differ(self):
        assert _redact_ip("10.0.0.1") != _redact_ip("10.0.0.2")


# ---------------------------------------------------------------------------
# Tests: No API key configured (bypass mode)
# ---------------------------------------------------------------------------

class TestNoKeyConfigured:
    """When no CERID_API_KEY is set, all requests should pass through."""

    def test_unprotected_endpoint_passes(self):
        client = TestClient(_make_app(api_key=None))
        resp = client.get("/artifacts")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_empty_string_key_passes(self):
        client = TestClient(_make_app(api_key=""))
        resp = client.post("/query")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: API key configured — authentication required
# ---------------------------------------------------------------------------

class TestKeyConfigured:
    """When CERID_API_KEY is set, non-exempt paths require a valid key."""

    @pytest.fixture
    def client(self):
        return TestClient(_make_app(api_key="test-secret-key-123"))

    def test_valid_key_passes(self, client):
        resp = client.get("/artifacts", headers={"X-API-Key": "test-secret-key-123"})
        assert resp.status_code == 200

    def test_invalid_key_returns_401(self, client):
        resp = client.get("/artifacts", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 401
        assert "Invalid or missing API key" in resp.json()["detail"]

    def test_missing_key_returns_401(self, client):
        resp = client.get("/artifacts")
        assert resp.status_code == 401

    def test_empty_key_header_returns_401(self, client):
        resp = client.get("/artifacts", headers={"X-API-Key": ""})
        assert resp.status_code == 401

    def test_post_endpoint_requires_key(self, client):
        resp = client.post("/agent/query")
        assert resp.status_code == 401

    def test_post_with_valid_key_passes(self, client):
        resp = client.post("/ingest_file", headers={"X-API-Key": "test-secret-key-123"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: Exempt paths
# ---------------------------------------------------------------------------

class TestExemptPaths:
    """Exempt paths should pass through without API key."""

    @pytest.fixture
    def client(self):
        return TestClient(_make_app(api_key="secret"))

    @pytest.mark.parametrize("path", ["/", "/health", "/api/v1/health", "/docs", "/openapi.json", "/redoc"])
    def test_exempt_exact_paths(self, client, path):
        resp = client.get(path)
        assert resp.status_code == 200, f"Expected 200 for exempt path {path}, got {resp.status_code}"

    def test_exempt_mcp_prefix(self, client):
        resp = client.get("/mcp/sse")
        assert resp.status_code == 200

    def test_exempt_mcp_messages(self, client):
        resp = client.post("/mcp/messages")
        assert resp.status_code == 200

    def test_non_exempt_path_blocked(self, client):
        resp = client.get("/artifacts")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Tests: Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_exempt_paths_contains_expected(self):
        assert "/health" in EXEMPT_PATHS
        assert "/" in EXEMPT_PATHS
        assert "/docs" in EXEMPT_PATHS

    def test_exempt_prefixes_contains_mcp(self):
        assert "/mcp/" in EXEMPT_PREFIXES
