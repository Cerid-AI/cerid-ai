# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Redis-backed metrics accessors for the memory consolidation pipeline.

Key design
----------
Each metric uses a Redis sorted set scored by epoch seconds, matching
the pattern established in ``app/processor/metrics.py``.

``record_consolidation_failure`` is called from an except-block callback
wired by ``app/main.py`` lifespan — it must never raise, and must be
safe to call when Redis is unavailable.

All accessors are defensive: a missing or unreachable Redis returns ``0``
so the /health endpoint never 503s due to a metrics probe failure.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.memory_metrics")

# Sorted-set key — members are "<ts>:<random_hex>:<reason_slug>", score = epoch
_FAILURES_KEY = "cerid:memory:consolidation_failures:by_ts"

_24H_S: float = 86_400.0


# ---------------------------------------------------------------------------
# Writers (called via callback from core/ except-blocks)
# ---------------------------------------------------------------------------


def _sync_record_consolidation_failure(
    redis_client: Any,
    reason: str,
) -> None:
    """Synchronous inner — runs in a thread via asyncio.to_thread."""
    ts = time.time()
    # Make member unique within the same second — mirrors processor metrics.
    rand = os.urandom(4).hex()
    # Truncate reason to keep member size bounded.
    reason_slug = reason[:64].replace(" ", "_")
    member = f"{ts}:{rand}:{reason_slug}"
    redis_client.zadd(_FAILURES_KEY, {member: ts})


def record_consolidation_failure(redis_client: Any, reason: str) -> None:
    """Synchronous writer — safe to call from a except-block callback.

    This is intentionally synchronous: the callback is invoked from
    ``core/agents/memory.py``'s except-blocks which may run in a sync
    or async context.  The write is best-effort; any exception is
    swallowed via ``log_swallowed_error``.

    Parameters
    ----------
    redis_client
        Synchronous ``redis.Redis`` client.
    reason
        Short description of the failure (logged + stored as slug).
    """
    try:
        _sync_record_consolidation_failure(redis_client, reason)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("memory_metrics.record_consolidation_failure", exc)


async def async_record_consolidation_failure(redis_client: Any, reason: str) -> None:
    """Async variant — wraps the sync writer via asyncio.to_thread."""
    try:
        await asyncio.to_thread(
            _sync_record_consolidation_failure, redis_client, reason
        )
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("memory_metrics.async_record_consolidation_failure", exc)


# ---------------------------------------------------------------------------
# Readers (called by the health endpoint)
# ---------------------------------------------------------------------------


def _sync_memory_consolidation_failures_24h(redis_client: Any) -> int:
    """Count failure entries in the last 24 hours."""
    now = time.time()
    cutoff = now - _24H_S
    count = redis_client.zcount(_FAILURES_KEY, cutoff, "+inf")
    return int(count)


def memory_consolidation_failures_24h(redis_client: Any) -> int:
    """Return the number of consolidation failures recorded in the last 24 h.

    Synchronous — safe to call from the sync ``/health`` handler.
    Returns ``0`` defensively when Redis is unavailable.
    """
    try:
        return _sync_memory_consolidation_failures_24h(redis_client)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("memory_metrics.memory_consolidation_failures_24h", exc)
        return 0


async def async_memory_consolidation_failures_24h(redis_client: Any) -> int:
    """Async variant — wraps the sync reader via asyncio.to_thread."""
    try:
        return await asyncio.to_thread(
            _sync_memory_consolidation_failures_24h, redis_client
        )
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error(
            "memory_metrics.async_memory_consolidation_failures_24h", exc
        )
        return 0
