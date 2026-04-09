# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Request ID middleware — generates or propagates X-Request-ID for tracing.

The request ID is stored in a ``contextvars.ContextVar`` so that any async
code executing within the request lifecycle can access it via
``get_request_id()`` without explicit parameter passing.

ContextVar declarations and accessor functions live in ``core.utils.tracing``
so they can be imported by core agents and utilities without pulling in
Starlette/FastAPI dependencies.
"""
from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from core.utils.tracing import (  # noqa: F401
    client_id_var,
    get_client_id,
    get_request_id,
    request_id_var,
    tracing_headers,
)


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
