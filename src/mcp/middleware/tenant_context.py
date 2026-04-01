# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tenant & user context middleware — propagates identity via contextvars.

The tenant ID and user ID are stored in ``contextvars.ContextVar`` instances
so that any async code within the request lifecycle can access them via
``get_tenant_id()`` / ``get_user_id()`` without explicit parameter passing.

When multi-user is disabled (``CERID_MULTI_USER=false``), every request
receives the ``DEFAULT_TENANT_ID`` and ``None`` for user — zero behavioral
change for single-user deployments.
"""
from __future__ import annotations

import contextvars

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from config.features import DEFAULT_TENANT_ID

# ContextVars available to all async code within a request
tenant_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "tenant_id", default=DEFAULT_TENANT_ID
)
user_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "user_id", default=None
)


def get_tenant_id() -> str:
    """Return the current tenant ID (DEFAULT_TENANT_ID outside a request context)."""
    return tenant_id_var.get()


def get_user_id() -> str | None:
    """Return the current user ID (None outside a request or in single-user mode)."""
    return user_id_var.get()


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Sets tenant/user contextvars from request.state (populated by auth middleware)."""

    async def dispatch(self, request: Request, call_next):
        tenant_id = getattr(request.state, "tenant_id", DEFAULT_TENANT_ID)
        user_id = getattr(request.state, "user_id", None)

        t_token = tenant_id_var.set(tenant_id)
        u_token = user_id_var.set(user_id)
        try:
            return await call_next(request)
        finally:
            tenant_id_var.reset(t_token)
            user_id_var.reset(u_token)
