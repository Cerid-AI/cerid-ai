# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Request ID middleware — generates or propagates X-Request-ID for tracing.

The request ID is stored in a ``contextvars.ContextVar`` so that any async
code executing within the request lifecycle can access it via
``get_request_id()`` without explicit parameter passing.
"""
from __future__ import annotations

import contextvars
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# ContextVars available to all async code within a request
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)
client_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "client_id", default="gui"
)


def get_request_id() -> str:
    """Return the current request ID (empty string outside a request context)."""
    return request_id_var.get()


def get_client_id() -> str:
    """Return the current client ID (``'gui'`` outside a request context)."""
    return client_id_var.get()


def tracing_headers() -> dict[str, str]:
    """Return headers dict with tracing IDs for outbound httpx calls."""
    headers: dict[str, str] = {}
    rid = request_id_var.get()
    if rid:
        headers["X-Request-ID"] = rid
    cid = client_id_var.get()
    if cid:
        headers["X-Client-ID"] = cid
    return headers


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id

        client_id = request.headers.get("X-Client-ID", "gui")
        request.state.client_id = client_id

        # Store in contextvars for agent/service code
        rid_token = request_id_var.set(request_id)
        cid_token = client_id_var.set(client_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request_id_var.reset(rid_token)
            client_id_var.reset(cid_token)
