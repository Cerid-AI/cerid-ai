# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Metrics collection middleware — non-blocking latency and throughput recording.

Wraps every request with timing instrumentation. Records ``query_latency_ms``
for query endpoints and ``queries_per_minute`` counter for throughput tracking.
All Redis writes are dispatched via ``asyncio.create_task()`` to avoid adding
latency to the request path.

Also stamps an ``X-Cache: HIT|MISS`` header on query responses so dashboards
and smoke harnesses can distinguish warm from cold without timing the call.
The signal is sourced from the response body's ``"cached": true`` flag, which
``utils.query_cache.get_cached`` sets on read-through.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("ai-companion.metrics")

# Endpoints that count as "query" requests for latency tracking
_QUERY_PATHS = frozenset({
    "/query",
    "/agent/query",
    "/sdk/v1/query",
    "/api/v1/query",
    "/api/v1/agent/query",
})


async def _record_metric_async(name: str, value: float, tags: dict[str, str] | None = None) -> None:
    """Record a metric in a non-blocking fire-and-forget manner."""
    try:
        from utils.metrics import get_metrics_collector
        collector = get_metrics_collector()
        collector.record_metric(name, value, tags)
    except Exception as exc:
        logger.debug("Metrics recording failed (non-critical): %s", exc)


async def _body_indicates_cache_hit(response: Response) -> tuple[bool, bytes | None]:
    """Inspect the response body for ``"cached": true``.

    Returns ``(is_hit, body_bytes_or_None)``. When the body was consumed to
    inspect it (streaming path), ``body_bytes`` is the buffered payload and the
    caller must rebuild the response with it — otherwise returns ``(_, None)``.
    """
    # Non-JSON responses can't be a cache hit in any meaningful sense.
    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type:
        return False, None

    # Fast path: ``JSONResponse`` exposes ``.body`` directly, no need to drain.
    body: bytes | None = getattr(response, "body", None)
    consumed = False
    if body is None:
        # Streaming response — drain iterator so we can look at the payload.
        chunks: list[bytes] = []
        async for chunk in response.body_iterator:  # type: ignore[attr-defined]
            chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
        body = b"".join(chunks)
        consumed = True

    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return False, (body if consumed else None)

    is_hit = isinstance(parsed, dict) and parsed.get("cached") is True
    return is_hit, (body if consumed else None)


class MetricsMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that records request latency and throughput metrics.

    - Records ``query_latency_ms`` for query-related endpoints.
    - Records ``queries_per_minute`` counter (value=1) for throughput aggregation.
    - Stamps ``X-Cache: HIT|MISS`` on query endpoints based on response body.
    - All writes are non-blocking via ``asyncio.create_task()``.
    """

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000

        path = request.url.path.rstrip("/")
        is_query = path in _QUERY_PATHS and request.method == "POST"

        if is_query:
            is_hit, buffered_body = await _body_indicates_cache_hit(response)
            # If we had to drain a streaming body to inspect it, rebuild the
            # response so the client still gets the payload.
            if buffered_body is not None:
                response = Response(
                    content=buffered_body,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type,
                )
            response.headers["X-Cache"] = "HIT" if is_hit else "MISS"

            # Record query latency for query endpoints
            asyncio.create_task(_record_metric_async(
                "query_latency_ms",
                elapsed_ms,
                {"path": path, "status": str(response.status_code),
                 "cache": "hit" if is_hit else "miss"},
            ))
            # Throughput counter (each query = 1 unit)
            asyncio.create_task(_record_metric_async(
                "queries_per_minute",
                1.0,
                {"path": path},
            ))

        return response
