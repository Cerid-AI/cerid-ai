# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
In-memory sliding window rate limiter with per-client isolation.

Each ``X-Client-ID`` value gets its own independent rate bucket so that
one consumer (e.g. the GUI) cannot starve another (e.g. cli-ingest).

Limits are configured in ``config.settings.CLIENT_RATE_LIMITS``.
Supports X-Forwarded-For behind trusted proxies (TRUSTED_PROXIES env var).
Adds IETF RateLimit-* response headers on rate-limited paths.
"""
from __future__ import annotations

import asyncio
import ipaddress
import math
import os
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# Trusted proxy networks — when direct peer is trusted, use X-Forwarded-For.
# Comma-separated CIDRs or IPs, e.g. "172.17.0.0/16,10.0.0.1"
_trusted_raw = os.getenv("TRUSTED_PROXIES", "")
TRUSTED_PROXIES: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
for _cidr in _trusted_raw.split(","):
    _cidr = _cidr.strip()
    if _cidr:
        try:
            TRUSTED_PROXIES.append(ipaddress.ip_network(_cidr, strict=False))
        except ValueError:
            pass


def get_client_ip(request: Request) -> str:
    """Resolve client IP, respecting X-Forwarded-For behind trusted proxies."""
    direct = request.client.host if request.client else "unknown"
    if not TRUSTED_PROXIES or direct == "unknown":
        return direct
    try:
        addr = ipaddress.ip_address(direct)
        if any(addr in net for net in TRUSTED_PROXIES):
            forwarded = request.headers.get("X-Forwarded-For", "")
            if forwarded:
                # Walk from right to left; return first IP not in trusted proxies
                for ip_str in reversed(forwarded.split(",")):
                    ip_str = ip_str.strip()
                    try:
                        ip = ipaddress.ip_address(ip_str)
                        if not any(ip in net for net in TRUSTED_PROXIES):
                            return ip_str
                    except ValueError:
                        continue
                # All IPs in chain are trusted — use leftmost as best guess
                return forwarded.split(",")[0].strip()
    except ValueError:
        pass
    return direct


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self._hits: dict[str, list[float]] = defaultdict(list)
        # Per-key locks to avoid blocking unrelated endpoints
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def dispatch(self, request: Request, call_next):
        from config.settings import CLIENT_RATE_LIMITS

        path = request.url.path
        method = request.method

        # GET requests are read-only lookups — exempt from rate limiting
        # to avoid exhausting the budget with report fetches, health checks, etc.
        if method == "GET":
            return await call_next(request)

        # MCP SSE transport and health paths are internal — exempt
        if path.startswith(("/mcp/", "/health", "/setup/")):
            return await call_next(request)

        # Per-client isolation via X-Client-ID (set by RequestIDMiddleware)
        client_id = request.headers.get("x-client-id", "gui")
        client_limits = CLIENT_RATE_LIMITS.get(
            client_id, CLIENT_RATE_LIMITS.get("_default", {}),
        )

        for prefix, (max_req, window) in client_limits.items():
            if path.startswith(prefix):
                key = f"client:{client_id}:{prefix}"
                async with self._locks[key]:
                    now = time.time()
                    self._hits[key] = [t for t in self._hits[key] if now - t < window]
                    current = len(self._hits[key])

                    if current >= max_req:
                        reset = int(max(1, math.ceil(window - (now - self._hits[key][0]))))
                        return JSONResponse(
                            status_code=429,
                            content={
                                "detail": f"Rate limit exceeded. Max {max_req} requests per {window}s.",
                                "retry_after": reset,
                            },
                            headers={
                                "RateLimit-Limit": str(max_req),
                                "RateLimit-Remaining": "0",
                                "RateLimit-Reset": str(reset),
                                "Retry-After": str(reset),
                            },
                        )

                    self._hits[key].append(now)
                    remaining = max_req - current - 1
                    reset = math.ceil(window - (now - self._hits[key][0]))

                response = await call_next(request)
                response.headers["RateLimit-Limit"] = str(max_req)
                response.headers["RateLimit-Remaining"] = str(remaining)
                response.headers["RateLimit-Reset"] = str(reset)
                return response

        return await call_next(request)
