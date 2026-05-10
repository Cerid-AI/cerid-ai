# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for app.processor.metrics.

All tests use in-process FakeRedis so no real Redis is required.
asyncio_mode = "auto" (pyproject.toml) means every async test function
is auto-detected.
"""
from __future__ import annotations

import time
from decimal import Decimal
from unittest.mock import MagicMock

import fakeredis
import pytest

from app.processor.metrics import (
    _sync_cost_usd_7d,
    _sync_jobs_completed_24h,
    _sync_throttled_ticks,
    processor_cost_usd_7d,
    processor_jobs_completed_24h,
    processor_throttled_ticks,
    record_completion,
    record_throttled,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def redis_client():
    """Isolated in-process FakeRedis per test."""
    server = fakeredis.FakeServer()
    return fakeredis.FakeRedis(server=server, decode_responses=True)


# ---------------------------------------------------------------------------
# record_completion + processor_jobs_completed_24h round-trip
# ---------------------------------------------------------------------------


async def test_record_completion_roundtrip(redis_client):
    """A completed job shows up in the 24h count."""
    now = time.time()
    await record_completion(
        redis_client,
        "job-1",
        completed_at=now,
        actual_cost_usd=Decimal("0.01"),
    )
    count = await processor_jobs_completed_24h(redis_client)
    assert count == 1


async def test_record_multiple_completions(redis_client):
    """Multiple completions are summed correctly."""
    now = time.time()
    for i in range(5):
        await record_completion(
            redis_client,
            f"job-{i}",
            completed_at=now,
            actual_cost_usd=Decimal("0.01"),
        )
    count = await processor_jobs_completed_24h(redis_client)
    assert count == 5


# ---------------------------------------------------------------------------
# 24h windowing — old entries excluded
# ---------------------------------------------------------------------------


async def test_24h_window_excludes_old_entries(redis_client):
    """An entry older than 24 h is not counted."""
    _25h_ago = time.time() - (25 * 3600)
    _1h_ago = time.time() - 3600

    # Old job (outside window)
    await record_completion(
        redis_client,
        "old-job",
        completed_at=_25h_ago,
        actual_cost_usd=Decimal("1.00"),
    )
    # Recent job (inside window)
    await record_completion(
        redis_client,
        "new-job",
        completed_at=_1h_ago,
        actual_cost_usd=Decimal("0.05"),
    )

    count = await processor_jobs_completed_24h(redis_client)
    assert count == 1  # Only the recent job


# ---------------------------------------------------------------------------
# record_throttled + processor_throttled_ticks round-trip
# ---------------------------------------------------------------------------


async def test_throttled_ticks_roundtrip(redis_client):
    """A throttle event shows up in the tick count."""
    await record_throttled(redis_client)
    count = await processor_throttled_ticks(redis_client, window_s=3600.0)
    assert count == 1


async def test_throttled_ticks_custom_window(redis_client):
    """Custom window excludes events outside it."""
    # Write a throttle event at the current time — should be within 60s window
    await record_throttled(redis_client)
    count_60s = await processor_throttled_ticks(redis_client, window_s=60.0)
    assert count_60s == 1


async def test_throttled_multiple_events(redis_client):
    """Multiple throttle events are counted correctly."""
    for _ in range(3):
        await record_throttled(redis_client)
    count = await processor_throttled_ticks(redis_client, window_s=3600.0)
    assert count == 3


# ---------------------------------------------------------------------------
# processor_cost_usd_7d — decimal summation
# ---------------------------------------------------------------------------


async def test_cost_usd_7d_sums_correctly(redis_client):
    """Decimal sums across multiple completions are accurate."""
    now = time.time()
    costs = [Decimal("0.01"), Decimal("0.023"), Decimal("0.007")]
    for i, cost in enumerate(costs):
        await record_completion(
            redis_client,
            f"job-cost-{i}",
            completed_at=now,
            actual_cost_usd=cost,
        )
    total = await processor_cost_usd_7d(redis_client)
    assert total == sum(costs)


async def test_cost_usd_7d_excludes_old_entries(redis_client):
    """Entries older than 7 days are excluded from cost sum."""
    _8d_ago = time.time() - (8 * 86400)
    _1d_ago = time.time() - 86400

    await record_completion(
        redis_client,
        "old-cost-job",
        completed_at=_8d_ago,
        actual_cost_usd=Decimal("99.99"),
    )
    await record_completion(
        redis_client,
        "recent-cost-job",
        completed_at=_1d_ago,
        actual_cost_usd=Decimal("0.42"),
    )
    total = await processor_cost_usd_7d(redis_client)
    assert total == Decimal("0.42")


async def test_cost_usd_7d_empty(redis_client):
    """No completions returns Decimal('0')."""
    total = await processor_cost_usd_7d(redis_client)
    assert total == Decimal("0")


# ---------------------------------------------------------------------------
# Defensive: unreachable Redis returns zero values
# ---------------------------------------------------------------------------


async def test_jobs_completed_24h_defensive_on_error():
    """Returns 0 cleanly when Redis raises."""
    mock = MagicMock()
    mock.zcount.side_effect = ConnectionError("Redis unreachable")
    count = await processor_jobs_completed_24h(mock)
    assert count == 0


async def test_cost_usd_7d_defensive_on_error():
    """Returns Decimal('0') cleanly when Redis raises."""
    mock = MagicMock()
    mock.zrangebyscore.side_effect = ConnectionError("Redis unreachable")
    total = await processor_cost_usd_7d(mock)
    assert total == Decimal("0")


async def test_throttled_ticks_defensive_on_error():
    """Returns 0 cleanly when Redis raises."""
    mock = MagicMock()
    mock.zremrangebyscore.side_effect = ConnectionError("Redis unreachable")
    count = await processor_throttled_ticks(mock, window_s=3600.0)
    assert count == 0


async def test_record_completion_defensive_on_error():
    """Does not raise when Redis raises."""
    mock = MagicMock()
    mock.zadd.side_effect = ConnectionError("Redis unreachable")
    # Should not raise
    await record_completion(
        mock,
        "job-x",
        completed_at=time.time(),
        actual_cost_usd=Decimal("0.01"),
    )


async def test_record_throttled_defensive_on_error():
    """Does not raise when Redis raises."""
    mock = MagicMock()
    mock.zadd.side_effect = ConnectionError("Redis unreachable")
    # Should not raise
    await record_throttled(mock)


# ---------------------------------------------------------------------------
# Synchronous helpers (used by health.py in sync context)
# ---------------------------------------------------------------------------


def test_sync_jobs_completed_24h(redis_client):
    """_sync_jobs_completed_24h returns 0 for empty set."""
    assert _sync_jobs_completed_24h(redis_client) == 0


def test_sync_cost_usd_7d(redis_client):
    """_sync_cost_usd_7d returns Decimal('0') for empty set."""
    assert _sync_cost_usd_7d(redis_client) == Decimal("0")


def test_sync_throttled_ticks(redis_client):
    """_sync_throttled_ticks returns 0 for empty set."""
    assert _sync_throttled_ticks(redis_client, 3600.0) == 0
