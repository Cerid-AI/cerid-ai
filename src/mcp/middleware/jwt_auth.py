# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""JWT authentication middleware for multi-user mode.

When ``CERID_MULTI_USER=true``, validates ``Authorization: Bearer <token>``
on every request (except exempt paths) and populates ``request.state``
with ``user_id``, ``tenant_id``, and ``role``.

Disabled entirely when ``CERID_MULTI_USER=false`` (single-user mode).
"""
from __future__ import annotations

import logging

import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from config.features import (
    CERID_JWT_SECRET,
    CERID_MULTI_USER,
)

logger = logging.getLogger("ai-companion.jwt_auth")

EXEMPT_PATHS = {
    "/health", "/api/v1/health", "/",
    "/docs", "/openapi.json", "/redoc",
}
EXEMPT_PREFIXES = (
    "/auth/", "/api/v1/auth/",
    "/mcp/",
)


def create_access_token(payload: dict, secret: str = CERID_JWT_SECRET) -> str:
    """Create a JWT access token."""
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str, secret: str = CERID_JWT_SECRET) -> dict:
    """Decode and verify a JWT access token. Raises jwt.PyJWTError on failure."""
    return jwt.decode(token, secret, algorithms=["HS256"])


class JWTAuthMiddleware(BaseHTTPMiddleware):
    """Validates JWT Bearer tokens when multi-user mode is enabled."""

    async def dispatch(self, request: Request, call_next):
        # Skip entirely if multi-user is disabled
        if not CERID_MULTI_USER:
            return await call_next(request)

        path = request.url.path

        # Exempt paths
        if path in EXEMPT_PATHS:
            return await call_next(request)
        for prefix in EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # Extract Bearer token
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid Authorization header"},
            )

        token = auth_header[7:]  # Strip "Bearer "

        try:
            payload = decode_access_token(token)
        except jwt.ExpiredSignatureError:
            return JSONResponse(
                status_code=401,
                content={"detail": "Token expired"},
            )
        except jwt.PyJWTError as e:
            logger.warning(f"JWT validation failed: {e}")
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid token"},
            )

        # Populate request.state for downstream middleware and routes
        request.state.user_id = payload.get("sub")
        request.state.tenant_id = payload.get("tenant_id")
        request.state.role = payload.get("role", "member")

        return await call_next(request)
