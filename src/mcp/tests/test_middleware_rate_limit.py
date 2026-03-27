# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for middleware/rate_limit.py — sliding window rate limiter."""

from unittest.mock import MagicMock, patch

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.middleware.rate_limit import RateLimitMiddleware, get_client_ip

# ---------------------------------------------------------------------------
# Helper: minimal ASGI app with rate limiter
# ---------------------------------------------------------------------------

async def _ok_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _make_app() -> Starlette:
    app = Starlette(
        routes=[
            Route("/agent/query", _ok_endpoint, methods=["GET", "POST"]),
            Route("/agent/audit", _ok_endpoint, methods=["POST"]),
            Route("/ingest", _ok_endpoint, methods=["POST"]),
            Route("/ingest_file", _ok_endpoint, methods=["POST"]),
            Route("/recategorize", _ok_endpoint, methods=["POST"]),
            Route("/health", _ok_endpoint),
            Route("/artifacts", _ok_endpoint),
        ],
    )
    app.add_middleware(RateLimitMiddleware)
    return app


# ---------------------------------------------------------------------------
# Tests: Rate limit headers on limited paths
# ---------------------------------------------------------------------------

class TestRateLimitHeaders:
    """Rate-limited paths should include RateLimit-* headers."""

    @pytest.fixture
    def client(self):
        return TestClient(_make_app())

    def test_agent_path_has_headers(self, client):
        resp = client.post("/agent/query")
        assert "RateLimit-Limit" in resp.headers
        assert "RateLimit-Remaining" in resp.headers
        assert "RateLimit-Reset" in resp.headers

    def test_ingest_path_has_headers(self, client):
        resp = client.post("/ingest")
        assert resp.headers["RateLimit-Limit"] == "10"

    def test_recategorize_path_has_headers(self, client):
        resp = client.post("/recategorize")
        assert resp.headers["RateLimit-Limit"] == "10"

    def test_non_limited_path_no_headers(self, client):
        resp = client.get("/health")
        assert "RateLimit-Limit" not in resp.headers

    def test_remaining_decrements(self, client):
        resp1 = client.post("/agent/query")
        remaining1 = int(resp1.headers["RateLimit-Remaining"])

        resp2 = client.post("/agent/query")
        remaining2 = int(resp2.headers["RateLimit-Remaining"])

        assert remaining2 == remaining1 - 1


# ---------------------------------------------------------------------------
# Tests: Rate limit enforcement
# ---------------------------------------------------------------------------

class TestRateLimitEnforcement:
    """Exceeding the limit returns 429."""

    def test_exceeding_limit_returns_429(self):
        client = TestClient(_make_app())
        # /ingest limit is 10 per 60s
        for i in range(10):
            resp = client.post("/ingest")
            assert resp.status_code == 200, f"Request {i+1} should pass"

        # 11th request should be rate limited
        resp = client.post("/ingest")
        assert resp.status_code == 429
        assert "Rate limit exceeded" in resp.json()["detail"]

    def test_429_includes_retry_after(self):
        client = TestClient(_make_app())
        for _ in range(10):
            client.post("/ingest")

        resp = client.post("/ingest")
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        assert int(resp.headers["Retry-After"]) > 0

    def test_429_remaining_is_zero(self):
        client = TestClient(_make_app())
        for _ in range(10):
            client.post("/ingest")

        resp = client.post("/ingest")
        assert resp.headers["RateLimit-Remaining"] == "0"

    def test_agent_limit_is_20(self):
        client = TestClient(_make_app())
        for i in range(20):
            resp = client.post("/agent/query")
            assert resp.status_code == 200, f"Request {i+1} should pass"

        resp = client.post("/agent/query")
        assert resp.status_code == 429


# ---------------------------------------------------------------------------
# Tests: Window expiration
# ---------------------------------------------------------------------------

class TestWindowExpiration:
    """Requests outside the time window should not count."""

    def test_old_hits_expire(self):
        app = _make_app()
        client = TestClient(app)

        # Fill up the limit
        for _ in range(10):
            client.post("/ingest")

        # Verify we're at the limit
        resp = client.post("/ingest")
        assert resp.status_code == 429

        # Find the middleware instance and manipulate timestamps
        for mw in app.middleware_stack.__dict__.get("app", app).__dict__.values():
            if isinstance(mw, RateLimitMiddleware):
                break

        # Access middleware's internal state via the app
        # Manually expire hits by manipulating timestamps
        middleware = None
        current = app.middleware_stack
        while current:
            if isinstance(current, RateLimitMiddleware):
                middleware = current
                break
            current = getattr(current, "app", None)

        if middleware:
            # Push all hit timestamps back beyond the window
            for key in middleware._hits:
                middleware._hits[key] = [t - 120 for t in middleware._hits[key]]

            # Now request should pass
            resp = client.post("/ingest")
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: Path isolation
# ---------------------------------------------------------------------------

class TestPathIsolation:
    """Different rate-limited paths use separate counters."""

    def test_agent_and_ingest_separate(self):
        client = TestClient(_make_app())

        # Use up ingest limit
        for _ in range(10):
            client.post("/ingest")

        # Ingest should be blocked
        resp = client.post("/ingest")
        assert resp.status_code == 429

        # Agent should still work (different counter)
        resp = client.post("/agent/query")
        assert resp.status_code == 200

    def test_non_limited_path_unaffected(self):
        client = TestClient(_make_app())

        # Use up ingest limit
        for _ in range(10):
            client.post("/ingest")

        # /health is not rate limited at all
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_ingest_prefix_covers_ingest_file(self):
        client = TestClient(_make_app())
        # Both /ingest and /ingest_file should hit the same rate limit
        for _ in range(5):
            client.post("/ingest")
        for _ in range(5):
            client.post("/ingest_file")

        # Should now be at the limit for /ingest prefix
        resp = client.post("/ingest")
        assert resp.status_code == 429


# ---------------------------------------------------------------------------
# Tests: get_client_ip
# ---------------------------------------------------------------------------

class TestGetClientIP:
    """Test client IP resolution with and without trusted proxies."""

    def test_direct_ip_no_proxies(self):
        request = MagicMock()
        request.client.host = "192.168.1.100"
        request.headers = {}

        with patch("app.middleware.rate_limit.TRUSTED_PROXIES", []):
            assert get_client_ip(request) == "192.168.1.100"

    def test_unknown_client(self):
        request = MagicMock()
        request.client = None

        with patch("app.middleware.rate_limit.TRUSTED_PROXIES", []):
            assert get_client_ip(request) == "unknown"

    def test_trusted_proxy_uses_forwarded(self):
        import ipaddress

        request = MagicMock()
        request.client.host = "172.17.0.1"
        request.headers = {"X-Forwarded-For": "203.0.113.50, 172.17.0.1"}

        trusted = [ipaddress.ip_network("172.17.0.0/16")]
        with patch("app.middleware.rate_limit.TRUSTED_PROXIES", trusted):
            assert get_client_ip(request) == "203.0.113.50"

    def test_untrusted_proxy_ignores_forwarded(self):
        import ipaddress

        request = MagicMock()
        request.client.host = "10.0.0.5"
        request.headers = {"X-Forwarded-For": "203.0.113.50"}

        trusted = [ipaddress.ip_network("172.17.0.0/16")]
        with patch("app.middleware.rate_limit.TRUSTED_PROXIES", trusted):
            # 10.0.0.5 is not in trusted range — ignore XFF
            assert get_client_ip(request) == "10.0.0.5"

    def test_multi_hop_forwarded(self):
        import ipaddress

        request = MagicMock()
        request.client.host = "172.17.0.2"
        request.headers = {"X-Forwarded-For": "203.0.113.10, 172.17.0.3, 172.17.0.2"}

        trusted = [ipaddress.ip_network("172.17.0.0/16")]
        with patch("app.middleware.rate_limit.TRUSTED_PROXIES", trusted):
            # Walk right-to-left: 172.17.0.2 trusted, 172.17.0.3 trusted, 203.0.113.10 not → return it
            assert get_client_ip(request) == "203.0.113.10"

    def test_all_trusted_returns_leftmost(self):
        import ipaddress

        request = MagicMock()
        request.client.host = "172.17.0.2"
        request.headers = {"X-Forwarded-For": "172.17.0.10, 172.17.0.3"}

        trusted = [ipaddress.ip_network("172.17.0.0/16")]
        with patch("app.middleware.rate_limit.TRUSTED_PROXIES", trusted):
            # All IPs trusted — return leftmost as best guess
            assert get_client_ip(request) == "172.17.0.10"


