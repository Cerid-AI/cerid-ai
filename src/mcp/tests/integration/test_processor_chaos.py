# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Chaos integration tests for ProcessorWorker + RedisJobQueue.

Exercises four PR-blocking scenarios using fakeredis (no Docker required):
  1. High queue depth with priority ordering
  2. CPU load > ceiling triggers worker throttle
  3. Disable (pause) mid-execution holds pending jobs
  4. Resume resumes cleanly with no duplicate executions

All tests use the real ProcessorWorker and real RedisJobQueue wired to a
fakeredis.FakeRedis instance (synchronous client, matching the production
asyncio.to_thread dispatch path in RedisJobQueue).

Run:
  PYTHONPATH=src/mcp .venv/bin/pytest src/mcp/tests/integration/test_processor_chaos.py -v -m chaos
"""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import fakeredis
import pytest

from app.db.redis.processor_queue import RedisJobQueue
from app.processor.metrics import processor_throttled_ticks
from app.processor.worker import ProcessorWorker
from core.processor.cost import CostEstimate
from core.processor.job import BaseJob, JobRecord, JobResult, JobState
from core.processor.priority import Priority

# ---------------------------------------------------------------------------
# Module-level test job classes
# Prefixed with underscore so pytest does not try to collect them as test
# classes (pytest collects classes starting with "Test").
# ---------------------------------------------------------------------------


class _PassJob(BaseJob):
    """Minimal job that completes immediately.

    Payload: {"artifact_id": str}
    """

    job_type = "chaos_pass_job"

    def __init__(self, artifact_id: str = "pass", **kwargs: Any) -> None:
        self._artifact_id = artifact_id

    @property
    def priority(self) -> Priority:
        return Priority.LOW

    def estimate_cost(self) -> CostEstimate:
        return CostEstimate(
            estimated_tokens_in=0,
            estimated_tokens_out=0,
            model="ollama/local",
            estimated_usd=Decimal("0.00"),
            confidence="high",
        )

    async def run(self, progress_cb: Any) -> JobResult:
        await progress_cb(0.0)
        await progress_cb(1.0)
        return JobResult(
            job_id="",
            actual_tokens_in=0,
            actual_tokens_out=0,
            metadata={"artifact_id": self._artifact_id},
        )


class _SlowJob(BaseJob):
    """Job that sleeps for a configurable duration.

    Payload: {"artifact_id": str, "sleep_s": float}
    """

    job_type = "chaos_slow_job"

    def __init__(
        self,
        artifact_id: str = "slow",
        sleep_s: float = 0.3,
        **kwargs: Any,
    ) -> None:
        self._artifact_id = artifact_id
        self._sleep_s = float(sleep_s)

    @property
    def priority(self) -> Priority:
        return Priority.MEDIUM

    def estimate_cost(self) -> CostEstimate:
        return CostEstimate(
            estimated_tokens_in=0,
            estimated_tokens_out=0,
            model="ollama/local",
            estimated_usd=Decimal("0.00"),
            confidence="high",
        )

    async def run(self, progress_cb: Any) -> JobResult:
        await progress_cb(0.0)
        await asyncio.sleep(self._sleep_s)
        await progress_cb(1.0)
        return JobResult(
            job_id="",
            actual_tokens_in=0,
            actual_tokens_out=0,
            metadata={"artifact_id": self._artifact_id},
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_TEST_REGISTRY: dict[str, type[BaseJob]] = {
    _PassJob.job_type: _PassJob,
    _SlowJob.job_type: _SlowJob,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _wait_for_drain(queue: RedisJobQueue, timeout: float = 10.0) -> bool:
    """Poll size_by_priority until all buckets are zero.

    Returns True if drained within ``timeout`` seconds, False otherwise.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        sizes = await queue.size_by_priority()
        if sum(sizes.values()) == 0:
            return True
        await asyncio.sleep(0.05)
    return False


def _make_record(
    job_type: str,
    priority: Priority,
    payload: dict[str, Any] | None = None,
) -> JobRecord:
    return JobRecord(
        id=str(uuid.uuid4()),
        job_type=job_type,
        state=JobState.PENDING,
        priority=priority,
        payload=payload or {"artifact_id": str(uuid.uuid4())},
        enqueued_at=datetime.now(tz=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    """Fresh FakeRedis instance per test (decode_responses=True matches production)."""
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def queue(fake_redis: fakeredis.FakeRedis) -> RedisJobQueue:
    return RedisJobQueue(fake_redis)


@pytest.fixture
def worker(queue: RedisJobQueue, fake_redis: fakeredis.FakeRedis) -> ProcessorWorker:
    """Worker wired to the test queue and fake_redis for metrics.

    load_ceiling=999.0 disables throttling unless a test patches os.getloadavg.
    """
    return ProcessorWorker(
        queue,
        _TEST_REGISTRY,
        concurrency=2,
        poll_interval=0.05,
        load_ceiling=999.0,
        redis_client=fake_redis,
    )


# ---------------------------------------------------------------------------
# Scenario 1 — High queue depth, priority order
# ---------------------------------------------------------------------------


@pytest.mark.chaos
async def test_scenario_1_high_queue_depth_priority_order(
    queue: RedisJobQueue,
    worker: ProcessorWorker,
) -> None:
    """500 mixed-priority jobs drain without loss or duplicates.

    Priority ordering is verified with a relaxed constraint: the latest
    HIGH-priority completion timestamp must fall within the first 60% of
    the total elapsed wall-clock window.

    Judgment call: strict "all HIGH before any LOW" is not achievable with
    concurrency=2 — two workers race on the same Redis lists using RPOP.
    The 60% window catches a scheduler that ignores priority entirely while
    allowing for real concurrency jitter.

    Important: completed_at is a Unix epoch (time.time()), so start_time
    is also captured via time.time() for a consistent timescale.
    """
    # Enqueue 500 jobs: 100 HIGH, 200 MEDIUM, 200 LOW
    records_by_priority: dict[Priority, list[str]] = {
        Priority.HIGH: [],
        Priority.MEDIUM: [],
        Priority.LOW: [],
    }
    distributions = [
        (Priority.HIGH, 100),
        (Priority.MEDIUM, 200),
        (Priority.LOW, 200),
    ]
    all_ids: set[str] = set()
    for prio, count in distributions:
        for _ in range(count):
            rec = _make_record(_PassJob.job_type, prio)
            all_ids.add(rec.id)
            records_by_priority[prio].append(rec.id)
            await queue.enqueue(rec)

    # Capture start as Unix epoch so it's on the same clock as completed_at
    start_epoch = time.time()
    await worker.start()

    drained = await _wait_for_drain(queue, timeout=30.0)
    end_epoch = time.time()
    elapsed = end_epoch - start_epoch
    await worker.stop()

    assert drained, "Queue did not drain within 30 s"

    # Fetch completion records
    recent = await queue.list_recent(limit=600)
    completed_ids = {r.id for r in recent if r.state.value == "completed"}

    # No loss: every enqueued job must appear as completed
    assert len(completed_ids) == 500, (
        f"Expected 500 completed, got {len(completed_ids)}"
    )

    # No duplicates: set size == list size
    all_recent_ids = [r.id for r in recent if r.state.value == "completed"]
    assert len(all_recent_ids) == len(completed_ids), "Duplicate completion records found"

    # Priority ordering: all HIGH jobs must complete within the first 60% of
    # the elapsed window.  Both start_epoch and completed_at.timestamp() are
    # Unix epochs, so they are directly comparable.
    high_ids = set(records_by_priority[Priority.HIGH])
    high_records = [r for r in recent if r.id in high_ids]
    assert len(high_records) == 100, f"Expected 100 HIGH completions, got {len(high_records)}"

    cutoff_epoch = start_epoch + elapsed * 0.60
    latest_high_ts = max(
        r.completed_at.timestamp()  # type: ignore[union-attr]
        for r in high_records
        if r.completed_at is not None
    )
    assert latest_high_ts <= cutoff_epoch, (
        f"HIGH jobs completed too late: last HIGH at +{latest_high_ts - start_epoch:.2f}s "
        f"but 60% window ends at +{cutoff_epoch - start_epoch:.2f}s (total={elapsed:.2f}s)"
    )


# ---------------------------------------------------------------------------
# Scenario 2 — CPU load > ceiling → throttle
# ---------------------------------------------------------------------------


@pytest.mark.chaos
async def test_scenario_2_cpu_load_throttles_worker(
    queue: RedisJobQueue,
    fake_redis: fakeredis.FakeRedis,
) -> None:
    """Synthetic loadavg > ceiling → worker pauses dequeue; throttled_ticks increments.

    Constructs a worker with load_ceiling=4.0, then patches os.getloadavg
    to return (10.0, 10.0, 10.0) so the throttle is immediately triggered.
    After 1.5 s the queue must still be full (nothing dequeued) and the
    throttled_ticks metric must have been incremented at least once.
    """
    throttle_worker = ProcessorWorker(
        queue,
        _TEST_REGISTRY,
        concurrency=2,
        poll_interval=0.05,
        load_ceiling=4.0,
        redis_client=fake_redis,
    )

    for _ in range(5):
        rec = _make_record(_PassJob.job_type, Priority.MEDIUM)
        await queue.enqueue(rec)

    try:
        with patch("os.getloadavg", return_value=(10.0, 10.0, 10.0)):
            await throttle_worker.start()
            await asyncio.sleep(1.5)

        # Queue must still be full — nothing was dequeued
        sizes = await queue.size_by_priority()
        assert sum(sizes.values()) == 5, (
            f"Expected 5 pending jobs, got {sum(sizes.values())}"
        )

        # Throttled metric must have incremented
        ticks = await processor_throttled_ticks(fake_redis, window_s=60.0)
        assert ticks >= 1, f"Expected at least 1 throttled tick, got {ticks}"
    finally:
        await throttle_worker.stop()


# ---------------------------------------------------------------------------
# Scenario 3 — Disable mid-execution
# ---------------------------------------------------------------------------


@pytest.mark.chaos
async def test_scenario_3_disable_mid_execution(
    queue: RedisJobQueue,
    worker: ProcessorWorker,
) -> None:
    """Pause mid-flight: in-flight jobs finish, pending jobs are held.

    10 slow jobs (0.3 s each) are enqueued.  After 0.1 s (when 1-2 are
    in-flight) the queue is paused.  We wait 0.5 s to let any in-flight
    job finish.  The assertion checks that at least one job completed but
    the queue is not fully drained.
    """
    for _ in range(10):
        rec = _make_record(
            _SlowJob.job_type,
            Priority.MEDIUM,
            payload={"artifact_id": str(uuid.uuid4()), "sleep_s": 0.3},
        )
        await queue.enqueue(rec)

    try:
        await worker.start()
        # Let 1-2 jobs start running
        await asyncio.sleep(0.1)

        # Pause — stops new dequeues
        await queue.pause()

        # Give any in-flight jobs time to complete
        await asyncio.sleep(0.5)

        # Some jobs completed (the in-flight ones)
        recent = await queue.list_recent(limit=20)
        completed_count = sum(1 for r in recent if r.state.value == "completed")
        assert completed_count >= 1, (
            "Expected at least 1 job to have completed before/during pause"
        )

        # Queue not fully drained — remaining jobs held
        sizes = await queue.size_by_priority()
        pending_count = sum(sizes.values())
        assert pending_count > 0, (
            f"Expected some jobs to remain pending after pause, got 0 "
            f"(completed={completed_count})"
        )
    finally:
        await worker.stop()


# ---------------------------------------------------------------------------
# Scenario 4 — Re-enable cleanly
# ---------------------------------------------------------------------------


@pytest.mark.chaos
async def test_scenario_4_resume_continues(
    queue: RedisJobQueue,
    worker: ProcessorWorker,
) -> None:
    """After resume(), worker picks up pending jobs; no duplicate execution.

    10 fast jobs are enqueued.  The queue is paused immediately before the
    worker starts (so nothing drains during the 0.2 s hold).  After resume()
    the queue must fully drain and the total completion count must equal 10.
    """
    job_ids: set[str] = set()
    for _ in range(10):
        rec = _make_record(_PassJob.job_type, Priority.MEDIUM)
        job_ids.add(rec.id)
        await queue.enqueue(rec)

    # Pause before starting the worker so no jobs slip through
    await queue.pause()

    try:
        await worker.start()
        # Hold for 0.2 s — queue must stay full (paused)
        await asyncio.sleep(0.2)

        sizes = await queue.size_by_priority()
        assert sum(sizes.values()) == 10, (
            f"Expected 10 pending during pause, got {sum(sizes.values())}"
        )

        # Resume — worker should now drain
        await queue.resume()
        drained = await _wait_for_drain(queue, timeout=5.0)
        assert drained, "Queue did not drain after resume within 5 s"

    finally:
        await worker.stop()

    # Exactly 10 completions, no duplicates
    recent = await queue.list_recent(limit=20)
    completed = [r for r in recent if r.state.value == "completed" and r.id in job_ids]
    completed_ids = {r.id for r in completed}

    assert len(completed_ids) == 10, (
        f"Expected 10 unique completions, got {len(completed_ids)}"
    )
    assert len(completed) == len(completed_ids), (
        "Duplicate completion records detected"
    )
