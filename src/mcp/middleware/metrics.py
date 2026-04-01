# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Metrics collection middleware — non-blocking latency and throughput recording.

Wraps every request with timing instrumentation. Records ``query_latency_ms``
for query endpoints and ``queries_per_minute`` counter for throughput tracking.
All Redis writes are dispatched via ``asyncio.create_task()`` to avoid adding
latency to the request path.
"""

from __future__ import annotations

import asyncio
import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from errors import CeridError

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
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        logger.debug("Metrics recording failed (non-critical): %s", exc)


class MetricsMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that records request latency and throughput metrics.

    - Records ``query_latency_ms`` for query-related endpoints.
    - Records ``queries_per_minute`` counter (value=1) for throughput aggregation.
    - All writes are non-blocking via ``asyncio.create_task()``.
    """

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000

        path = request.url.path.rstrip("/")

        # Record query latency for query endpoints
        if path in _QUERY_PATHS and request.method == "POST":
            asyncio.create_task(_record_metric_async(
                "query_latency_ms",
                elapsed_ms,
                {"path": path, "status": str(response.status_code)},
            ))
            # Throughput counter (each query = 1 unit)
            asyncio.create_task(_record_metric_async(
                "queries_per_minute",
                1.0,
                {"path": path},
            ))

        return response
