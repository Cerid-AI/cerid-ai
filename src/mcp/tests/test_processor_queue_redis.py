# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for RedisJobQueue using fakeredis.

All tests use an in-process FakeRedis server so no real Redis is needed.
``asyncio_mode = "auto"`` (set in pyproject.toml) means every async test
function is detected and run automatically without explicit markers.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import fakeredis
import pytest

from app.db.redis.processor_queue import RedisJobQueue
from core.processor.job import JobRecord, JobResult, JobState
from core.processor.priority import Priority, priority_order

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_record(
    priority: Priority = Priority.LOW,
    job_type: str = "test_job",
    job_id: str | None = None,
) -> JobRecord:
    return JobRecord(
        id=job_id or str(uuid.uuid4()),
        job_type=job_type,
        state=JobState.PENDING,
        priority=priority,
        payload={"key": "value", "num": 42},
        enqueued_at=datetime.now(tz=timezone.utc),
    )


@pytest.fixture
def redis_client():
    """In-process FakeRedis server — isolated per test."""
    server = fakeredis.FakeServer()
    return fakeredis.FakeRedis(server=server, decode_responses=True)


@pytest.fixture
def queue(redis_client):
    return RedisJobQueue(redis_client)


# ---------------------------------------------------------------------------
# Enqueue / dequeue — order and round-trip
# ---------------------------------------------------------------------------


async def test_enqueue_returns_job_id(queue):
    record = _make_record()
    returned_id = await queue.enqueue(record)
    assert returned_id == record.id


async def test_dequeue_returns_enqueued_record(queue):
    record = _make_record(Priority.LOW)
    await queue.enqueue(record)

    dequeued = await queue.dequeue(priority_order())

    assert dequeued is not None
    assert dequeued.id == record.id
    assert dequeued.job_type == record.job_type
    assert dequeued.priority == record.priority
    assert dequeued.payload == record.payload


async def test_dequeue_fifo_within_priority(queue):
    """Items of the same priority come out in insertion order (FIFO)."""
    ids = [str(uuid.uuid4()) for _ in range(3)]
    for jid in ids:
        await queue.enqueue(_make_record(Priority.MEDIUM, job_id=jid))

    out = []
    for _ in range(3):
        r = await queue.dequeue(priority_order())
        assert r is not None
        out.append(r.id)

    assert out == ids


async def test_dequeue_returns_none_when_empty(queue):
    result = await queue.dequeue(priority_order())
    assert result is None


async def test_payload_roundtrip_preserves_types(queue):
    """Ensure integer values in payload survive Redis serialisation."""
    record = _make_record()
    record.payload["nested"] = {"a": 1, "b": [1, 2, 3]}
    await queue.enqueue(record)

    dequeued = await queue.dequeue(priority_order())
    assert dequeued is not None
    assert dequeued.payload["num"] == 42
    assert dequeued.payload["nested"]["b"] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------


async def test_high_priority_dequeues_before_low(queue):
    low = _make_record(Priority.LOW)
    high = _make_record(Priority.HIGH)

    # Enqueue low first, then high
    await queue.enqueue(low)
    await queue.enqueue(high)

    first = await queue.dequeue(priority_order())
    assert first is not None
    assert first.id == high.id


async def test_medium_priority_dequeues_before_low(queue):
    low = _make_record(Priority.LOW)
    medium = _make_record(Priority.MEDIUM)

    await queue.enqueue(low)
    await queue.enqueue(medium)

    first = await queue.dequeue(priority_order())
    assert first is not None
    assert first.id == medium.id


async def test_mixed_priorities_ordered_correctly(queue):
    low = _make_record(Priority.LOW)
    mid = _make_record(Priority.MEDIUM)
    high = _make_record(Priority.HIGH)

    # Enqueue in reverse priority order
    for r in [low, mid, high]:
        await queue.enqueue(r)

    order = []
    for _ in range(3):
        r = await queue.dequeue(priority_order())
        assert r is not None
        order.append(r.priority)

    assert order == [Priority.HIGH, Priority.MEDIUM, Priority.LOW]


# ---------------------------------------------------------------------------
# mark_running → mark_completed
# ---------------------------------------------------------------------------


async def test_mark_running_sets_state_and_started_at(queue):
    record = _make_record()
    await queue.enqueue(record)
    dequeued = await queue.dequeue(priority_order())
    assert dequeued is not None

    await queue.mark_running(dequeued.id)

    updated = await queue._load_record(dequeued.id)
    assert updated is not None
    assert updated.state == JobState.RUNNING
    assert updated.started_at is not None


async def test_mark_completed_updates_state_and_actuals(queue):
    record = _make_record()
    await queue.enqueue(record)
    dequeued = await queue.dequeue(priority_order())
    assert dequeued is not None

    await queue.mark_running(dequeued.id)
    result = JobResult(job_id=dequeued.id, actual_tokens_in=100, actual_tokens_out=50)
    await queue.mark_completed(dequeued.id, result)

    updated = await queue._load_record(dequeued.id)
    assert updated is not None
    assert updated.state == JobState.COMPLETED
    assert updated.actual_tokens_in == 100
    assert updated.actual_tokens_out == 50
    assert updated.completed_at is not None


async def test_mark_completed_removes_from_running_set(queue, redis_client):
    record = _make_record()
    await queue.enqueue(record)
    dequeued = await queue.dequeue(priority_order())
    assert dequeued is not None

    await queue.mark_running(dequeued.id)
    # Verify it's in the running set
    assert redis_client.sismember("cerid:proc:running", dequeued.id)

    result = JobResult(job_id=dequeued.id, actual_tokens_in=0, actual_tokens_out=0)
    await queue.mark_completed(dequeued.id, result)

    assert not redis_client.sismember("cerid:proc:running", dequeued.id)


# ---------------------------------------------------------------------------
# mark_failed
# ---------------------------------------------------------------------------


async def test_mark_failed_sets_state_and_error_message(queue):
    record = _make_record()
    await queue.enqueue(record)
    dequeued = await queue.dequeue(priority_order())
    assert dequeued is not None

    await queue.mark_running(dequeued.id)
    await queue.mark_failed(dequeued.id, "something went wrong")

    updated = await queue._load_record(dequeued.id)
    assert updated is not None
    assert updated.state == JobState.FAILED
    assert updated.error_message == "something went wrong"
    assert updated.completed_at is not None


async def test_mark_failed_removes_from_running_set(queue, redis_client):
    record = _make_record()
    await queue.enqueue(record)
    dequeued = await queue.dequeue(priority_order())
    assert dequeued is not None

    await queue.mark_running(dequeued.id)
    await queue.mark_failed(dequeued.id, "boom")

    assert not redis_client.sismember("cerid:proc:running", dequeued.id)


# ---------------------------------------------------------------------------
# pause / resume
# ---------------------------------------------------------------------------


async def test_pause_causes_dequeue_to_return_none(queue):
    record = _make_record()
    await queue.enqueue(record)

    await queue.pause()
    result = await queue.dequeue(priority_order())
    assert result is None


async def test_resume_restores_dequeue(queue):
    record = _make_record()
    await queue.enqueue(record)

    await queue.pause()
    assert await queue.dequeue(priority_order()) is None

    await queue.resume()
    result = await queue.dequeue(priority_order())
    assert result is not None
    assert result.id == record.id


async def test_pause_does_not_discard_queued_jobs(queue):
    """Jobs enqueued before pause must survive the pause/resume cycle."""
    r1 = _make_record()
    r2 = _make_record()
    await queue.enqueue(r1)
    await queue.enqueue(r2)

    await queue.pause()
    await queue.resume()

    seen = set()
    for _ in range(2):
        r = await queue.dequeue(priority_order())
        assert r is not None
        seen.add(r.id)

    assert seen == {r1.id, r2.id}


# ---------------------------------------------------------------------------
# size_by_priority
# ---------------------------------------------------------------------------


async def test_size_by_priority_returns_accurate_counts(queue):
    await queue.enqueue(_make_record(Priority.HIGH))
    await queue.enqueue(_make_record(Priority.HIGH))
    await queue.enqueue(_make_record(Priority.MEDIUM))

    sizes = await queue.size_by_priority()
    assert sizes[Priority.HIGH] == 2
    assert sizes[Priority.MEDIUM] == 1
    assert sizes[Priority.LOW] == 0


async def test_size_by_priority_decrements_on_dequeue(queue):
    await queue.enqueue(_make_record(Priority.LOW))
    before = await queue.size_by_priority()
    assert before[Priority.LOW] == 1

    await queue.dequeue(priority_order())
    after = await queue.size_by_priority()
    assert after[Priority.LOW] == 0


# ---------------------------------------------------------------------------
# list_recent
# ---------------------------------------------------------------------------


async def test_list_recent_returns_completed_jobs_newest_first(queue):
    records = [_make_record() for _ in range(3)]
    for r in records:
        await queue.enqueue(r)
        dequeued = await queue.dequeue(priority_order())
        assert dequeued is not None
        await queue.mark_running(dequeued.id)
        result = JobResult(job_id=dequeued.id, actual_tokens_in=0, actual_tokens_out=0)
        await queue.mark_completed(dequeued.id, result)

    recent = await queue.list_recent(10)
    assert len(recent) == 3
    # All should be COMPLETED
    for r in recent:
        assert r.state == JobState.COMPLETED
    # Newest first: completed_at descending
    timestamps = [r.completed_at for r in recent]
    assert timestamps == sorted(timestamps, reverse=True)


async def test_list_recent_includes_failed_jobs(queue):
    record = _make_record()
    await queue.enqueue(record)
    dequeued = await queue.dequeue(priority_order())
    assert dequeued is not None
    await queue.mark_running(dequeued.id)
    await queue.mark_failed(dequeued.id, "intentional failure")

    recent = await queue.list_recent(10)
    assert len(recent) == 1
    assert recent[0].state == JobState.FAILED


async def test_list_recent_respects_limit(queue):
    for _ in range(5):
        r = _make_record()
        await queue.enqueue(r)
        d = await queue.dequeue(priority_order())
        assert d is not None
        await queue.mark_running(d.id)
        result = JobResult(job_id=d.id, actual_tokens_in=0, actual_tokens_out=0)
        await queue.mark_completed(d.id, result)

    recent = await queue.list_recent(3)
    assert len(recent) == 3


async def test_list_recent_returns_empty_when_no_jobs(queue):
    assert await queue.list_recent(10) == []
