# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Redis-backed metrics accessors for the background processor.

Key design
----------
Each metric uses a Redis sorted set scored by epoch seconds.  The sorted
set allows efficient range queries (ZRANGEBYSCORE) so each accessor only
reads members inside its time window instead of scanning all entries.

All writers are called by the worker; all readers are called by the router
and health endpoint.  Every accessor is defensive — a missing or unreachable
Redis returns a zero value so the health endpoint never 503s due to a
metrics probe failure.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.metrics")

# Sorted-set keys
_COMPLETED_KEY = "cerid:proc:completed:by_ts"
_COST_KEY = "cerid:proc:cost:by_ts"
_THROTTLED_KEY = "cerid:proc:throttled:by_ts"

# Per-job-type rolling duration lists (Phase 0.4a) — a capped LPUSH/LTRIM
# list per job_type, plus a SET tracking which job_types have data so the
# reader never needs a Redis KEYS/SCAN. Surfaces the head-of-line-blocking
# signal that was previously invisible (e.g. wiki_refresh p95 in the
# 40-110s range) — record_completion only stored {job_id: epoch} before.
_DURATION_KEY_PREFIX = "cerid:proc:duration:by_type:"
_DURATION_TYPES_KEY = "cerid:proc:duration:job_types"
_DURATION_MAX_ENTRIES = 200

_24H_S: float = 86_400.0
_7D_S: float = 604_800.0


def _duration_key(job_type: str) -> str:
    return f"{_DURATION_KEY_PREFIX}{job_type}"


# ---------------------------------------------------------------------------
# Writers (called by the worker)
# ---------------------------------------------------------------------------


def _sync_record_completion(
    redis_client: Any,
    job_id: str,
    completed_at: float,
    actual_cost_usd: Decimal,
    job_type: str | None,
    duration_s: float | None,
) -> None:
    """Synchronous inner — runs in a thread via asyncio.to_thread."""
    # Completed-set: member=job_id, score=epoch
    redis_client.zadd(_COMPLETED_KEY, {job_id: completed_at})
    # Cost set: member=<job_id>:<decimal_str>, score=epoch
    cost_member = f"{job_id}:{actual_cost_usd!s}"
    redis_client.zadd(_COST_KEY, {cost_member: completed_at})

    if job_type and duration_s is not None:
        key = _duration_key(job_type)
        redis_client.lpush(key, repr(duration_s))
        redis_client.ltrim(key, 0, _DURATION_MAX_ENTRIES - 1)
        redis_client.sadd(_DURATION_TYPES_KEY, job_type)


async def record_completion(
    redis_client: Any,
    job_id: str,
    *,
    completed_at: float,
    actual_cost_usd: Decimal,
    job_type: str | None = None,
    duration_s: float | None = None,
) -> None:
    """Record a job completion in both the completed and cost sorted sets.

    Parameters
    ----------
    redis_client
        Synchronous ``redis.Redis`` client.
    job_id
        The job identifier (member for the completed sorted set).
    completed_at
        Epoch-seconds timestamp of completion (float).
    actual_cost_usd
        Actual cost in USD as a ``Decimal``.
    job_type
        Optional job-type label (e.g. ``"wiki_refresh"``). When supplied
        together with ``duration_s``, the duration is appended to a capped
        rolling list per job_type so ``processor_job_type_stats`` can
        surface p50/p95 latency by job type (Phase 0.4a).
    duration_s
        Optional wall-clock execution time in seconds for this job.
    """
    try:
        await asyncio.to_thread(
            _sync_record_completion,
            redis_client,
            job_id,
            completed_at,
            actual_cost_usd,
            job_type,
            duration_s,
        )
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("processor.metrics.record_completion", exc)


def _sync_record_throttled(redis_client: Any) -> None:
    """Synchronous inner — runs in a thread via asyncio.to_thread."""
    ts = time.time()
    # Use "<ts>:<counter_hint>" to allow multiple entries in the same second.
    import os
    member = f"{ts}:{os.urandom(4).hex()}"
    redis_client.zadd(_THROTTLED_KEY, {member: ts})


async def record_throttled(redis_client: Any) -> None:
    """Record a throttle event in the throttled sorted set."""
    try:
        await asyncio.to_thread(_sync_record_throttled, redis_client)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("processor.metrics.record_throttled", exc)


# ---------------------------------------------------------------------------
# Readers (called by the router and health endpoint)
# ---------------------------------------------------------------------------


def _sync_jobs_completed_24h(redis_client: Any) -> int:
    now = time.time()
    cutoff = now - _24H_S
    count = redis_client.zcount(_COMPLETED_KEY, cutoff, "+inf")
    return int(count)


async def processor_jobs_completed_24h(redis_client: Any) -> int:
    """Return the number of completed jobs in the last 24 hours."""
    try:
        return await asyncio.to_thread(_sync_jobs_completed_24h, redis_client)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("processor.metrics.jobs_completed_24h", exc)
        return 0


def _sum_cost_members(members: list[Any]) -> Decimal:
    """Sum decimal costs from cost-log members shaped ``<job_id>:<decimal_str>``.

    The job_id may contain hyphens (UUID) but never colons, so split on the
    last colon; malformed rows are logged and skipped, not fatal.
    """
    total = Decimal("0")
    for member in members:
        member_str = member.decode() if isinstance(member, bytes) else str(member)
        parts = member_str.rsplit(":", 1)
        if len(parts) == 2:
            try:
                total += Decimal(parts[1])
            except Exception as exc:  # silent-catch-allowed: malformed cost-row decimal — skip, sum the rest
                log_swallowed_error('app.processor.metrics', exc)
    return total


def _sync_cost_usd_7d(redis_client: Any) -> Decimal:
    now = time.time()
    cutoff = now - _7D_S
    members: list[Any] = redis_client.zrangebyscore(_COST_KEY, cutoff, "+inf")
    return _sum_cost_members(members)


async def processor_cost_usd_7d(redis_client: Any) -> Decimal:
    """Return total actual cost in USD over the last 7 days."""
    try:
        return await asyncio.to_thread(_sync_cost_usd_7d, redis_client)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("processor.metrics.cost_usd_7d", exc)
        return Decimal("0")


def _sync_cost_usd_month(redis_client: Any) -> Decimal:
    now = datetime.now(timezone.utc)
    cutoff = datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp()
    members: list[Any] = redis_client.zrangebyscore(_COST_KEY, cutoff, "+inf")
    return _sum_cost_members(members)


async def processor_cost_usd_month(redis_client: Any) -> Decimal:
    """Return total actual cost in USD for the current UTC calendar month."""
    try:
        return await asyncio.to_thread(_sync_cost_usd_month, redis_client)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("processor.metrics.cost_usd_month", exc)
        return Decimal("0")


def _sync_throttled_ticks(redis_client: Any, window_s: float) -> int:
    now = time.time()
    cutoff = now - window_s
    # Trim old entries before counting (keeps the set bounded)
    redis_client.zremrangebyscore(_THROTTLED_KEY, 0, cutoff)
    count = redis_client.zcard(_THROTTLED_KEY)
    return int(count)


async def processor_throttled_ticks(
    redis_client: Any,
    *,
    window_s: float = 3600.0,
) -> int:
    """Return the number of throttle events in the last ``window_s`` seconds."""
    try:
        return await asyncio.to_thread(
            _sync_throttled_ticks, redis_client, window_s
        )
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("processor.metrics.throttled_ticks", exc)
        return 0


# ---------------------------------------------------------------------------
# Per-job-type latency (Phase 0.4a)
# ---------------------------------------------------------------------------


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile over an already-sorted ascending list."""
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, int(round(pct * (len(sorted_values) - 1))))
    return sorted_values[idx]


def _sync_job_type_stats(redis_client: Any) -> dict[str, dict[str, float | int]]:
    raw_types = redis_client.smembers(_DURATION_TYPES_KEY)
    stats: dict[str, dict[str, float | int]] = {}
    for raw_jt in raw_types:
        job_type = raw_jt.decode() if isinstance(raw_jt, bytes) else str(raw_jt)
        raw_durations = redis_client.lrange(_duration_key(job_type), 0, -1)
        durations: list[float] = []
        for raw in raw_durations:
            try:
                durations.append(float(raw))
            except (TypeError, ValueError) as exc:  # silent-catch-allowed: malformed duration-row float — skip, keep the rest
                log_swallowed_error('app.processor.metrics', exc)
                continue
        if not durations:
            continue
        durations.sort()
        stats[job_type] = {
            "count": len(durations),
            "p50_s": round(_percentile(durations, 0.50), 3),
            "p95_s": round(_percentile(durations, 0.95), 3),
        }
    return stats


async def processor_job_type_stats(
    redis_client: Any,
) -> dict[str, dict[str, float | int]]:
    """Return per-job-type ``{count, p50_s, p95_s}`` from the rolling duration lists.

    Surfaces the head-of-line-blocking signal Phase 0.4a targets — a
    wiki_refresh job with a p95 in the 40-110s range was previously
    invisible because ``record_completion`` only stored ``{job_id: epoch}``.
    """
    try:
        return await asyncio.to_thread(_sync_job_type_stats, redis_client)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("processor.metrics.job_type_stats", exc)
        return {}
