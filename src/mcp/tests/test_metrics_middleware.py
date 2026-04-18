# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the metrics middleware — ensures X-Cache headers are stamped
based on response body, so downstream observers (dashboards, smoke tests)
can tell cold from warm without timing it."""

from __future__ import annotations

import json
from unittest.mock import patch

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.middleware.metrics import MetricsMiddleware

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _cached_hit_endpoint(request: Request) -> JSONResponse:
    """Simulates a handler returning a cached payload (as /agent/query does)."""
    return JSONResponse({"results": [], "answer": "hi", "cached": True, "cache_age_ms": 12})


async def _fresh_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"results": [], "answer": "fresh"})


async def _non_query_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


def _make_app(handler=_cached_hit_endpoint) -> Starlette:
    app = Starlette(
        routes=[
            # All mounted at /agent/query so they exercise the query-path branch.
            Route("/agent/query", handler, methods=["POST"]),
            Route("/health", _non_query_endpoint, methods=["GET"]),
        ],
    )
    app.add_middleware(MetricsMiddleware)
    return app


# Patch the underlying metrics recorder to avoid touching Redis in tests.
_METRICS_PATCH = "app.middleware.metrics._record_metric_async"


# ---------------------------------------------------------------------------
# X-Cache header
# ---------------------------------------------------------------------------


class TestXCacheHeader:
    def test_cached_hit_sets_x_cache_hit(self):
        """When response JSON body contains cached=true, X-Cache: HIT is stamped."""
        with patch(_METRICS_PATCH, return_value=None):
            client = TestClient(_make_app())
            resp = client.post("/agent/query")
            assert resp.status_code == 200
            assert json.loads(resp.content).get("cached") is True
            assert resp.headers.get("x-cache") == "HIT"

    def test_fresh_response_sets_x_cache_miss(self):
        """Fresh responses (no cached flag) get X-Cache: MISS."""
        with patch(_METRICS_PATCH, return_value=None):
            client = TestClient(_make_app(handler=_fresh_endpoint))
            resp = client.post("/agent/query")
            assert resp.status_code == 200
            assert resp.headers.get("x-cache") == "MISS"

    def test_non_query_endpoint_gets_no_header(self):
        """Only query-class endpoints are annotated with X-Cache."""
        with patch(_METRICS_PATCH, return_value=None):
            client = TestClient(_make_app())
            resp = client.get("/health")
            assert resp.status_code == 200
            assert "x-cache" not in {k.lower() for k in resp.headers.keys()}
