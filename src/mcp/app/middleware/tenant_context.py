# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tenant & user context middleware — populates contextvars from request state.

The ``ContextVar`` instances and their accessors live in
``core.context.identity`` (so retrieval code in ``core/`` can read the active
tenant without crossing the ``core must not import app`` boundary). This
module owns only the Starlette middleware that *sets* them on every request,
plus a re-export of the canonical accessors so legacy callers keep working.

When multi-user is disabled (``CERID_MULTI_USER=false``), every request
receives the ``DEFAULT_TENANT_ID`` and ``None`` for user — zero behavioral
change for single-user deployments.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from config.features import DEFAULT_TENANT_ID
from core.context.identity import (
    get_tenant_id,
    get_user_id,
    tenant_id_var,
    user_id_var,
)

__all__ = [
    "TenantContextMiddleware",
    "get_tenant_id",
    "get_user_id",
    "tenant_id_var",
    "user_id_var",
]


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
