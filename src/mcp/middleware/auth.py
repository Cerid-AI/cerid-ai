"""
API key authentication middleware.

Checks X-API-Key header against CERID_API_KEY env var.
When no key is configured, all requests pass through (backward compatible).
Exempt paths: /health, /, /docs, /openapi.json, /mcp/*
"""
from __future__ import annotations

import logging
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("ai-companion.auth")

EXEMPT_PATHS = {"/health", "/", "/docs", "/openapi.json", "/redoc"}
EXEMPT_PREFIXES = ("/mcp/",)


class APIKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_key: str | None = None):
        super().__init__(app)
        self.api_key = api_key or os.getenv("CERID_API_KEY", "")

    async def dispatch(self, request: Request, call_next):
        # Skip auth if no key configured
        if not self.api_key:
            return await call_next(request)

        path = request.url.path

        # Exempt paths
        if path in EXEMPT_PATHS or path.endswith("/health"):
            return await call_next(request)
        for prefix in EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # Check header
        provided = request.headers.get("X-API-Key", "")
        if provided != self.api_key:
            client = request.client.host if request.client else "unknown"
            logger.warning(f"Unauthorized request to {path} from {client}")
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )

        return await call_next(request)
