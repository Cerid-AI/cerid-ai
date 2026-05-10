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
from decimal import Decimal
from typing import Any

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.metrics")

# Sorted-set keys
_COMPLETED_KEY = "cerid:proc:completed:by_ts"
_COST_KEY = "cerid:proc:cost:by_ts"
_THROTTLED_KEY = "cerid:proc:throttled:by_ts"

_24H_S: float = 86_400.0
_7D_S: float = 604_800.0


# ---------------------------------------------------------------------------
# Writers (called by the worker)
# ---------------------------------------------------------------------------


def _sync_record_completion(
    redis_client: Any,
    job_id: str,
    completed_at: float,
    actual_cost_usd: Decimal,
) -> None:
    """Synchronous inner — runs in a thread via asyncio.to_thread."""
    # Completed-set: member=job_id, score=epoch
    redis_client.zadd(_COMPLETED_KEY, {job_id: completed_at})
    # Cost set: member=<job_id>:<decimal_str>, score=epoch
    cost_member = f"{job_id}:{actual_cost_usd!s}"
    redis_client.zadd(_COST_KEY, {cost_member: completed_at})


async def record_completion(
    redis_client: Any,
    job_id: str,
    *,
    completed_at: float,
    actual_cost_usd: Decimal,
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
    """
    try:
        await asyncio.to_thread(
            _sync_record_completion,
            redis_client,
            job_id,
            completed_at,
            actual_cost_usd,
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


def _sync_cost_usd_7d(redis_client: Any) -> Decimal:
    now = time.time()
    cutoff = now - _7D_S
    members: list[Any] = redis_client.zrangebyscore(_COST_KEY, cutoff, "+inf")
    total = Decimal("0")
    for member in members:
        member_str = member.decode() if isinstance(member, bytes) else str(member)
        # Format: "<job_id>:<decimal_str>"
        # job_id itself may contain hyphens (UUID), not colons.
        # Split on last colon to isolate the decimal part safely.
        parts = member_str.rsplit(":", 1)
        if len(parts) == 2:
            try:
                total += Decimal(parts[1])
            except Exception:  # noqa: BLE001
                pass
    return total


async def processor_cost_usd_7d(redis_client: Any) -> Decimal:
    """Return total actual cost in USD over the last 7 days."""
    try:
        return await asyncio.to_thread(_sync_cost_usd_7d, redis_client)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("processor.metrics.cost_usd_7d", exc)
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
