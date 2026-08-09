# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""
API key authentication middleware.

Checks X-API-Key header against CERID_API_KEY env var.
When no key is configured, all requests pass through (backward compatible).
Exempt paths: /health, /health/* (probes), /, /docs, /openapi.json, /redoc, /mcp/*
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from config.features import DEFAULT_TENANT_ID

logger = logging.getLogger("ai-companion.auth")

EXEMPT_PATHS = {"/health", "/", "/docs", "/openapi.json", "/redoc", "/.well-known/agent.json"}
# /health/* covers liveness/readiness/ping probes (Docker healthcheck hits
# /health/ping) — probes must reach the app without the API key.
EXEMPT_PREFIXES = ("/health/", "/mcp/", "/auth/", "/a2a/")

# /mcp/ and /a2a/ are exempt only while the server is bound to loopback, where
# "reachable" already means "local process." In LAN mode
# (CERID_BIND_ADDR=0.0.0.0) that assumption is false and the exemption would
# expose every MCP tool — including deletes and purges — unauthenticated to the
# whole network, contradicting docs/LAN_REMOTE_ACCESS.md. Keeping loopback
# exempt preserves headerless local MCP clients (see this repo's .mcp.json).
_LOOPBACK_ONLY_EXEMPT_PREFIXES = ("/mcp/", "/a2a/")


def _is_loopback_bind() -> bool:
    """True when the server is bound to a loopback address only."""
    bind = os.getenv("CERID_BIND_ADDR", "127.0.0.1").strip()
    return bind in ("127.0.0.1", "::1", "localhost", "")


def _redact_ip(ip: str) -> str:
    """Hash-redact an IP address for safe logging."""
    return hashlib.sha256(ip.encode()).hexdigest()[:12]


class APIKeyMiddleware(BaseHTTPMiddleware):
    _warned_no_key: bool = False

    def __init__(self, app, api_key: str | None = None):
        super().__init__(app)
        self.api_key = api_key or os.getenv("CERID_API_KEY", "")
        # Bind address is fixed for the process lifetime — resolve once.
        self._loopback_bind = _is_loopback_bind()
        if not self.api_key and not APIKeyMiddleware._warned_no_key:
            logger.warning("API key auth is disabled — all requests will pass through unauthenticated")
            APIKeyMiddleware._warned_no_key = True
        if not self._loopback_bind:
            logger.info("Non-loopback bind — MCP/A2A surfaces require X-API-Key")

    async def dispatch(self, request: Request, call_next):
        # Skip auth if no key configured
        if not self.api_key:
            return await call_next(request)

        path = request.url.path

        # Exempt paths
        if path in EXEMPT_PATHS:
            return await call_next(request)
        for prefix in EXEMPT_PREFIXES:
            if not path.startswith(prefix):
                continue
            if prefix in _LOOPBACK_ONLY_EXEMPT_PREFIXES and not self._loopback_bind:
                break  # LAN mode — fall through to the key check
            return await call_next(request)

        # Check header
        provided = request.headers.get("X-API-Key", "")
        if not provided or not hmac.compare_digest(provided, self.api_key):
            client = request.client.host if request.client else "unknown"
            logger.warning(f"Unauthorized request to {path} from {_redact_ip(client)}")
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )

        return await call_next(request)


def get_current_user(request: Request) -> tuple[str | None, str]:
    """Extract (user_id, tenant_id) from request state.

    Returns (None, DEFAULT_TENANT_ID) when no user is authenticated
    (single-user mode or unauthenticated request).
    """
    user_id = getattr(request.state, "user_id", None)
    tenant_id = getattr(request.state, "tenant_id", DEFAULT_TENANT_ID)
    return user_id, tenant_id
