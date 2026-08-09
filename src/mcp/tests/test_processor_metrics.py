# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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
    processor_job_type_stats,
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
# Per-job-type latency (Phase 0.4a)
# ---------------------------------------------------------------------------


async def test_job_type_stats_empty_when_no_data(redis_client):
    """No durations recorded yet — empty dict, not an error."""
    stats = await processor_job_type_stats(redis_client)
    assert stats == {}


async def test_job_type_stats_single_job_type(redis_client):
    """A single duration sample produces count=1 and p50==p95==that value."""
    await record_completion(
        redis_client,
        "job-1",
        completed_at=time.time(),
        actual_cost_usd=Decimal("0.01"),
        job_type="wiki_refresh",
        duration_s=45.5,
    )
    stats = await processor_job_type_stats(redis_client)
    assert stats["wiki_refresh"]["count"] == 1
    assert stats["wiki_refresh"]["p50_s"] == 45.5
    assert stats["wiki_refresh"]["p95_s"] == 45.5


async def test_job_type_stats_computes_percentiles_across_samples(redis_client):
    """p95 sits near the top of the distribution, p50 near the middle."""
    durations = [float(i) for i in range(1, 101)]  # 1..100
    for i, d in enumerate(durations):
        await record_completion(
            redis_client,
            f"job-{i}",
            completed_at=time.time(),
            actual_cost_usd=Decimal("0.00"),
            job_type="entity_extraction",
            duration_s=d,
        )
    stats = await processor_job_type_stats(redis_client)
    entry = stats["entity_extraction"]
    assert entry["count"] == 100
    assert 45 <= entry["p50_s"] <= 55
    assert 90 <= entry["p95_s"] <= 100


async def test_job_type_stats_separates_job_types(redis_client):
    """Two job types keep independent rolling lists."""
    await record_completion(
        redis_client, "a", completed_at=time.time(), actual_cost_usd=Decimal("0"),
        job_type="wiki_refresh", duration_s=90.0,
    )
    await record_completion(
        redis_client, "b", completed_at=time.time(), actual_cost_usd=Decimal("0"),
        job_type="entity_extraction", duration_s=2.0,
    )
    stats = await processor_job_type_stats(redis_client)
    assert set(stats.keys()) == {"wiki_refresh", "entity_extraction"}
    assert stats["wiki_refresh"]["p50_s"] == 90.0
    assert stats["entity_extraction"]["p50_s"] == 2.0


async def test_job_type_stats_list_is_capped(redis_client):
    """The rolling duration list is capped at 200 entries per job_type."""
    from app.processor.metrics import _DURATION_MAX_ENTRIES, _duration_key

    for i in range(_DURATION_MAX_ENTRIES + 25):
        await record_completion(
            redis_client, f"job-{i}", completed_at=time.time(), actual_cost_usd=Decimal("0"),
            job_type="wiki_refresh", duration_s=float(i),
        )
    length = redis_client.llen(_duration_key("wiki_refresh"))
    assert length == _DURATION_MAX_ENTRIES


async def test_record_completion_without_job_type_skips_duration_list(redis_client):
    """Backward compatibility: omitting job_type/duration_s records no duration."""
    await record_completion(
        redis_client,
        "job-legacy",
        completed_at=time.time(),
        actual_cost_usd=Decimal("0.01"),
    )
    stats = await processor_job_type_stats(redis_client)
    assert stats == {}


async def test_job_type_stats_defensive_on_error():
    """Returns {} cleanly when Redis raises."""
    mock = MagicMock()
    mock.smembers.side_effect = ConnectionError("Redis unreachable")
    stats = await processor_job_type_stats(mock)
    assert stats == {}


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
