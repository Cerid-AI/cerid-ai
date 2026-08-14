# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""FastAPI router for the background processor subsystem.

Endpoints
---------
GET  /processor/status    — queue sizes, pause flag, 24h/7d metrics, mode/cap,
                             per-job-type latency, chat route-decision counts
                             and per-model chat latency (Phase 0.4a telemetry)
GET  /processor/recent    — recently completed/failed jobs
POST /processor/pause     — halt new dequeues
POST /processor/resume    — lift the pause

All endpoints access the live queue via ``request.app.state.processor_queue``
and the metrics accessors via ``app.processor.metrics``.  No module-level
singletons — state is injected through ``app.state`` so the router can be
mounted against a minimal test app with mocked state.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request

import config
from app.deps import get_redis
from app.processor.metrics import (
    processor_cost_usd_7d,
    processor_cost_usd_month,
    processor_job_type_stats,
    processor_jobs_completed_24h,
    processor_throttled_ticks,
)
from app.routers.chat import get_chat_model_latency_stats, get_chat_route_counts_today
from core.processor.mode import resolve_processor_mode
from core.utils.swallowed import log_swallowed_error

router = APIRouter(prefix="/processor", tags=["processor"])
logger = logging.getLogger("ai-companion.processor.router")


def _get_queue(request: Request) -> Any:
    """Retrieve the queue from app.state — safe-guard for tests."""
    return getattr(request.app.state, "processor_queue", None)


# ---------------------------------------------------------------------------
# GET /processor/status
# ---------------------------------------------------------------------------


@router.get("/status")
async def processor_status(request: Request) -> dict[str, Any]:
    """Return current queue depth, pause state, and rolling metrics."""
    queue = _get_queue(request)

    # Queue sizes by priority name
    queue_sizes: dict[str, int] = {}
    paused: bool = False

    if queue is not None:
        try:
            sizes_by_priority = await queue.size_by_priority()
            queue_sizes = {p.value: count for p, count in sizes_by_priority.items()}
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("processor.router.size_by_priority", exc)

        try:
            # RedisJobQueue._is_paused() already wraps the Redis GET in its
            # own asyncio.to_thread helper — reuse it instead of reaching
            # into ``queue._r`` directly (was a raw sync call on the queue's
            # internal client, bypassing its async wrapper). Still a
            # leading-underscore method — making it public requires editing
            # app/db/redis/processor_queue.py, which is outside this file's
            # ownership for this change; noted for the integrator.
            paused = await queue._is_paused()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("processor.router.paused_probe", exc)

    # Metrics
    redis_client = None
    try:
        redis_client = get_redis()
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("processor.router.get_redis", exc)

    jobs_24h = 0
    cost_7d_float = 0.0
    throttled_1h = 0

    if redis_client is not None:
        try:
            jobs_24h = await processor_jobs_completed_24h(redis_client)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("processor.router.jobs_24h", exc)

        try:
            cost_7d = await processor_cost_usd_7d(redis_client)
            cost_7d_float = float(cost_7d)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("processor.router.cost_7d", exc)

        try:
            throttled_1h = await processor_throttled_ticks(
                redis_client, window_s=3600.0
            )
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("processor.router.throttled_ticks", exc)

    monthly_spend_usd = 0.0
    if redis_client is not None:
        try:
            monthly_spend_usd = float(await processor_cost_usd_month(redis_client))
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("processor.router.monthly_spend", exc)

    # Phase 0.4a telemetry: per-job-type processor latency + chat
    # route-decision counters + per-model chat latency, so the
    # 40-110s wiki_refresh head-of-line blocker (and TIER_P95_MS drift)
    # are visible from a single endpoint instead of only {job_id: epoch}.
    job_type_latency: dict[str, dict[str, float | int]] = {}
    chat_route_counts_today: dict[str, int] = {}
    chat_model_latency: dict[str, dict[str, float | int]] = {}

    if redis_client is not None:
        try:
            job_type_latency = await processor_job_type_stats(redis_client)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("processor.router.job_type_stats", exc)

        try:
            chat_route_counts_today = get_chat_route_counts_today(redis_client)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("processor.router.chat_route_counts", exc)

        try:
            chat_model_latency = get_chat_model_latency_stats(redis_client)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("processor.router.chat_model_latency", exc)

    return {
        "queue_sizes": queue_sizes,
        "paused": paused,
        "jobs_completed_24h": jobs_24h,
        "cost_usd_7d": cost_7d_float,
        "throttled_ticks_1h": throttled_1h,
        "mode": resolve_processor_mode(config.settings.PROCESSOR_MODE),
        "monthly_spend_usd": monthly_spend_usd,
        "cap_usd": float(config.settings.PROCESSOR_MONTHLY_CAP_USD),
        "job_type_latency": job_type_latency,
        "chat_route_counts_today": chat_route_counts_today,
        "chat_model_latency": chat_model_latency,
    }


# ---------------------------------------------------------------------------
# GET /processor/recent
# ---------------------------------------------------------------------------


@router.get("/recent")
async def processor_recent(
    request: Request,
    limit: int = 20,
    job_type: str | None = None,
    per_type_cap: int = Query(5, ge=0),
) -> list[dict[str, Any]]:
    """Return the most recent terminal jobs, newest first.

    The default listing caps each job type at ``per_type_cap`` records so a
    high-frequency type cannot displace everything else (live, wiki_refresh
    held 88 of the 100 most recent records). ``per_type_cap=0`` restores the
    raw newest-first slice; ``job_type=<type>`` drills into one type,
    uncapped.
    """
    queue = _get_queue(request)
    if queue is None:
        return []

    try:
        records = await queue.list_recent(
            limit, job_type=job_type, per_type_cap=per_type_cap
        )
        return [r.to_dict() for r in records]
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("processor.router.list_recent", exc)
        return []


# ---------------------------------------------------------------------------
# POST /processor/pause
# ---------------------------------------------------------------------------


@router.post("/pause")
async def processor_pause(request: Request) -> dict[str, bool]:
    """Halt new dequeues.  In-flight jobs are allowed to finish."""
    queue = _get_queue(request)
    if queue is not None:
        try:
            await queue.pause()
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("processor.router.pause", exc)
    return {"paused": True}


# ---------------------------------------------------------------------------
# POST /processor/resume
# ---------------------------------------------------------------------------


@router.post("/resume")
async def processor_resume(request: Request) -> dict[str, bool]:
    """Lift the pause — new dequeues resume immediately."""
    queue = _get_queue(request)
    if queue is not None:
        try:
            await queue.resume()
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("processor.router.resume", exc)
    return {"paused": False}
