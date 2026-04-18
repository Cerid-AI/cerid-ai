# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Task 10: rate-limit breach must return a graceful 429 with Retry-After.

Regression coverage for smoke Test E, where 25 concurrent /agent/query POSTs
under X-Client-ID=gui (limit 20/min) all showed up as connection errors.
Graceful backpressure is a 429 with a Retry-After header, never a dropped
connection.

These tests exercise the middleware in isolation against a minimal Starlette
app so they do not depend on ChromaDB, Neo4j, or OpenRouter.
"""
from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.middleware.rate_limit import RateLimitMiddleware


async def _ok(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _make_app() -> Starlette:
    app = Starlette(
        routes=[
            Route("/agent/query", _ok, methods=["POST"]),
        ],
    )
    app.add_middleware(RateLimitMiddleware)
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_make_app())


def test_429_returned_when_gui_quota_exceeded(client: TestClient) -> None:
    """After GUI's 20/min quota is used, next request returns 429 with Retry-After."""
    headers = {"X-Client-ID": "gui"}
    statuses: list[int] = []
    for _ in range(25):
        r = client.post(
            "/agent/query",
            json={"query": "probe", "domains": ["general"], "n_results": 3},
            headers=headers,
        )
        statuses.append(r.status_code)
        if r.status_code == 429:
            assert "Retry-After" in r.headers, "429 must include Retry-After"
            body = r.json()
            assert "retry_after" in body or "detail" in body
            break
    assert 429 in statuses, f"expected 429 in statuses, got {statuses}"


def test_429_body_structure(client: TestClient) -> None:
    """429 body shape: { detail: str, retry_after: int }."""
    headers = {"X-Client-ID": "gui"}
    r = None
    for _ in range(30):
        r = client.post(
            "/agent/query",
            json={"query": "probe", "domains": ["general"], "n_results": 3},
            headers=headers,
        )
        if r.status_code == 429:
            break
    assert r is not None and r.status_code == 429
    body = r.json()
    # Body must carry retry_after as a numeric type (matches the header).
    assert isinstance(body.get("retry_after"), (int, float))
    assert body["retry_after"] >= 1
    # Retry-After header is an RFC 7231 integer seconds value.
    assert r.headers["Retry-After"].isdigit()
    assert int(r.headers["Retry-After"]) >= 1


def test_rate_limit_does_not_raise(client: TestClient) -> None:
    """Middleware must always produce a response; never drop the connection."""
    headers = {"X-Client-ID": "gui"}
    for _ in range(40):
        r = client.post(
            "/agent/query",
            json={"query": "probe", "domains": ["general"], "n_results": 3},
            headers=headers,
        )
        assert r.status_code in (200, 429), f"unexpected status {r.status_code}"


def test_retry_after_header_matches_body(client: TestClient) -> None:
    """Retry-After header and body retry_after field must agree."""
    headers = {"X-Client-ID": "gui"}
    r = None
    for _ in range(30):
        r = client.post(
            "/agent/query",
            json={"query": "probe", "domains": ["general"], "n_results": 3},
            headers=headers,
        )
        if r.status_code == 429:
            break
    assert r is not None and r.status_code == 429
    assert int(r.headers["Retry-After"]) == int(r.json()["retry_after"])


def test_per_client_isolation_under_breach() -> None:
    """Trading-agent quota is independent of gui breach."""
    client = TestClient(_make_app())
    # Burn the gui quota (20/min on /agent/).
    for _ in range(25):
        client.post(
            "/agent/query",
            json={"query": "probe", "domains": ["general"], "n_results": 3},
            headers={"X-Client-ID": "gui"},
        )
    # trading-agent has its own bucket (80/min on /agent/) — first request must pass.
    r = client.post(
        "/agent/query",
        json={"query": "probe", "domains": ["general"], "n_results": 3},
        headers={"X-Client-ID": "trading-agent"},
    )
    assert r.status_code == 200, (
        f"trading-agent starved by gui breach — cross-client leakage: {r.status_code}"
    )
