# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
In-memory sliding window rate limiter.

Rate limits:
  /agent/       → 20 requests per 60 seconds
  /ingest       → 10 requests per 60 seconds
  /recategorize → 10 requests per 60 seconds
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

RATE_LIMITS: dict[str, tuple[int, int]] = {
    "/agent/": (20, 60),
    "/ingest": (10, 60),
    "/recategorize": (10, 60),
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self._hits: dict[str, list[float]] = defaultdict(list)
        # Per-key locks to avoid blocking unrelated endpoints
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        client_ip = request.client.host if request.client else "unknown"

        for prefix, (max_req, window) in RATE_LIMITS.items():
            if path.startswith(prefix):
                key = f"{client_ip}:{prefix}"
                async with self._locks[key]:
                    now = time.time()
                    self._hits[key] = [t for t in self._hits[key] if now - t < window]
                    if len(self._hits[key]) >= max_req:
                        return JSONResponse(
                            status_code=429,
                            content={"detail": f"Rate limit exceeded. Max {max_req} requests per {window}s."},
                        )
                    self._hits[key].append(now)
                break

        return await call_next(request)