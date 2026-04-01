# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for middleware/request_id.py — X-Request-ID tracing middleware."""

import uuid

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from middleware.request_id import RequestIDMiddleware

# ---------------------------------------------------------------------------
# Helper: minimal ASGI app with request ID middleware
# ---------------------------------------------------------------------------

async def _echo_request_id(request: Request) -> JSONResponse:
    """Echo back the request_id from request.state for assertion."""
    rid = getattr(request.state, "request_id", None)
    return JSONResponse({"request_id": rid})


def _make_app() -> Starlette:
    app = Starlette(routes=[Route("/test", _echo_request_id)])
    app.add_middleware(RequestIDMiddleware)
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRequestIDMiddleware:
    def test_generates_uuid_when_no_header(self):
        client = TestClient(_make_app())
        resp = client.get("/test")
        assert resp.status_code == 200
        rid = resp.headers["X-Request-ID"]
        # Should be a valid UUID
        uuid.UUID(rid)  # Raises ValueError if not valid

    def test_propagates_existing_header(self):
        client = TestClient(_make_app())
        custom_id = "custom-trace-id-12345"
        resp = client.get("/test", headers={"X-Request-ID": custom_id})
        assert resp.status_code == 200
        assert resp.headers["X-Request-ID"] == custom_id

    def test_sets_request_state(self):
        client = TestClient(_make_app())
        custom_id = "state-test-id-999"
        resp = client.get("/test", headers={"X-Request-ID": custom_id})
        body = resp.json()
        assert body["request_id"] == custom_id

    def test_generated_id_in_state_and_response(self):
        client = TestClient(_make_app())
        resp = client.get("/test")
        body = resp.json()
        # The generated ID should match between state and response header
        assert body["request_id"] == resp.headers["X-Request-ID"]

    def test_each_request_gets_unique_id(self):
        client = TestClient(_make_app())
        resp1 = client.get("/test")
        resp2 = client.get("/test")
        assert resp1.headers["X-Request-ID"] != resp2.headers["X-Request-ID"]
