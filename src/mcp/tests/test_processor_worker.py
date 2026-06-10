# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for ProcessorWorker.

All queue and metrics interactions are mocked — no real Redis required.
A lightweight stub job class is defined inline.
asyncio_mode = "auto" (pyproject.toml) means every async test function
is auto-detected.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.processor.worker import ProcessorWorker, build_default_registry
from core.processor.cost import CostEstimate
from core.processor.job import BaseJob, JobRecord, JobResult, JobState
from core.processor.priority import Priority, priority_order

# ---------------------------------------------------------------------------
# Stub job (concrete BaseJob for testing)
# ---------------------------------------------------------------------------


class _StubJob(BaseJob):
    """Minimal concrete job for unit tests."""

    job_type = "stub_job"

    def __init__(self, artifact_id: str = "test-artifact", **kwargs) -> None:
        self._artifact_id = artifact_id

    @property
    def priority(self) -> Priority:
        return Priority.LOW

    def estimate_cost(self) -> CostEstimate:
        return CostEstimate(
            estimated_tokens_in=100,
            estimated_tokens_out=50,
            model="ollama/local",
            estimated_usd=Decimal("0.00"),
            confidence="high",
        )

    async def run(self, progress_cb) -> JobResult:
        await progress_cb(0.0)
        await progress_cb(1.0)
        return JobResult(
            job_id="",  # filled in by worker
            actual_tokens_in=100,
            actual_tokens_out=50,
            metadata={"artifact_id": self._artifact_id},
        )


class _FailingJob(BaseJob):
    """Stub job that always raises."""

    job_type = "failing_job"

    def __init__(self, artifact_id: str = "fail-artifact", **kwargs) -> None:
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

    async def run(self, progress_cb) -> JobResult:
        raise RuntimeError("deliberate test failure")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    job_type: str = "stub_job",
    priority: Priority = Priority.LOW,
    retry_count: int = 0,
) -> JobRecord:
    return JobRecord(
        id=str(uuid.uuid4()),
        job_type=job_type,
        state=JobState.PENDING,
        priority=priority,
        payload={"artifact_id": "test-artifact"},
        enqueued_at=datetime.now(tz=timezone.utc),
        retry_count=retry_count,
    )


def _mock_queue(record: JobRecord | None = None) -> MagicMock:
    """Return a MagicMock queue that dequeues the given record once, then None."""
    q = MagicMock()
    q.dequeue = AsyncMock(side_effect=[record, None, None, None, None])
    q.mark_running = AsyncMock()
    q.mark_completed = AsyncMock()
    q.mark_failed = AsyncMock()
    q.enqueue = AsyncMock(return_value="new-id")
    q.pause = AsyncMock()
    q.resume = AsyncMock()
    q.size_by_priority = AsyncMock(return_value={p: 0 for p in priority_order()})
    q.list_recent = AsyncMock(return_value=[])
    return q


def _make_worker(
    queue: MagicMock,
    registry: dict | None = None,
    *,
    redis_client: MagicMock | None = None,
    max_retries: int = 3,
    load_ceiling: float = 99999.0,
) -> ProcessorWorker:
    """Construct a worker for tests.

    ``load_ceiling=99999.0`` by default so the throttle check (which
    short-circuits the dequeue path when ``os.getloadavg()[0]`` exceeds
    the ceiling) never fires under CI load. Default is `cpu * 0.7` in
    production, which is easily exceeded on a 2-cpu GitHub Actions
    runner under sustained pytest load — that produced silent test
    failures in CI even though local runs (8+ cpu) passed.

    Tests that exercise the throttle path itself
    (``test_throttled_skips_dequeue`` etc.) construct the worker
    directly and pass a low ceiling — they don't use this helper.
    """
    if registry is None:
        registry = {"stub_job": _StubJob, "failing_job": _FailingJob}
    return ProcessorWorker(
        queue,
        registry,
        concurrency=1,
        poll_interval=0.01,
        redis_client=redis_client,
        max_retries=max_retries,
        load_ceiling=load_ceiling,
    )


# ---------------------------------------------------------------------------
# End-to-end: worker drains a single job
# ---------------------------------------------------------------------------


async def test_worker_drains_single_job():
    """Worker picks up a queued job, executes it, and marks it completed."""
    record = _make_record()
    queue = _mock_queue(record)
    worker = _make_worker(queue)

    await worker.start()
    # Give the worker loop a chance to drain the one job
    await asyncio.sleep(0.05)
    await worker.stop()

    queue.mark_running.assert_called_once_with(record.id)
    queue.mark_completed.assert_called_once()
    completed_args = queue.mark_completed.call_args
    result = completed_args[0][1]  # second positional arg
    assert result.job_id == record.id
    assert result.actual_tokens_in == 100


# ---------------------------------------------------------------------------
# Priority order: dequeue called with HIGH → MEDIUM → LOW
# ---------------------------------------------------------------------------


async def test_dequeue_called_with_priority_order():
    """The worker calls dequeue with priority_order() list."""
    queue = _mock_queue(None)
    worker = _make_worker(queue)

    await worker.start()
    await asyncio.sleep(0.03)
    await worker.stop()

    # At least one call should have been made with the correct priority list
    assert queue.dequeue.await_count >= 1
    call_args = queue.dequeue.call_args_list[0]
    passed_priorities = call_args[0][0]
    assert passed_priorities == [Priority.HIGH, Priority.MEDIUM, Priority.LOW]


# ---------------------------------------------------------------------------
# On job exception: mark_failed is called
# ---------------------------------------------------------------------------


async def test_job_exception_calls_mark_failed():
    """When a job raises, mark_failed is called with the error message."""
    record = _make_record(job_type="failing_job")
    queue = _mock_queue(record)
    worker = _make_worker(queue)

    await worker.start()
    await asyncio.sleep(0.05)
    await worker.stop()

    queue.mark_failed.assert_called_once()
    job_id_arg, error_msg_arg = queue.mark_failed.call_args[0]
    assert job_id_arg == record.id
    assert "deliberate test failure" in error_msg_arg


# ---------------------------------------------------------------------------
# Retry: failing job with retry_count < max_retries gets re-enqueued
# ---------------------------------------------------------------------------


async def test_retry_on_failure_reenqueues():
    """A failing job below max_retries is re-enqueued by the worker."""
    record = _make_record(job_type="failing_job", retry_count=0)
    queue = _mock_queue(record)
    worker = _make_worker(queue, max_retries=3)

    await worker.start()
    await asyncio.sleep(0.05)
    await worker.stop()

    # Job should be re-enqueued for retry
    queue.enqueue.assert_called_once()
    retry_record: JobRecord = queue.enqueue.call_args[0][0]
    assert retry_record.retry_count == 1
    assert retry_record.job_type == "failing_job"


async def test_no_retry_when_max_retries_exhausted():
    """A failing job at max_retries is NOT re-enqueued."""
    record = _make_record(job_type="failing_job", retry_count=2)  # already at max_retries-1
    queue = _mock_queue(record)
    worker = _make_worker(queue, max_retries=3)

    await worker.start()
    await asyncio.sleep(0.05)
    await worker.stop()

    # Should NOT re-enqueue
    queue.enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# Throttled: dequeue NOT called; record_throttled IS called
# ---------------------------------------------------------------------------


async def test_throttled_skips_dequeue():
    """When throttled, the worker does not call dequeue."""
    queue = _mock_queue(None)
    redis_mock = MagicMock()
    # Use the production-realistic default load_ceiling so the
    # mocked load of 999.0 actually trips the throttle. The helper's
    # default of 99999 covers the every-other-test case (CI runner
    # load > cpu*0.7 was causing silent dequeue skips).
    worker = _make_worker(queue, redis_client=redis_mock, load_ceiling=0.5)

    with patch("os.getloadavg", return_value=(999.0, 999.0, 999.0)):
        with patch("app.processor.metrics.record_throttled", new_callable=AsyncMock) as mock_rt:
            await worker.start()
            await asyncio.sleep(0.05)
            await worker.stop()

    # dequeue must not have been called (throttled)
    queue.dequeue.assert_not_called()
    # record_throttled must have been called at least once
    assert mock_rt.await_count >= 1


async def test_not_throttled_when_load_low():
    """When load is below ceiling, dequeue IS called."""
    queue = _mock_queue(None)
    worker = _make_worker(queue)

    with patch("os.getloadavg", return_value=(0.0, 0.0, 0.0)):
        await worker.start()
        await asyncio.sleep(0.03)
        await worker.stop()

    assert queue.dequeue.await_count >= 1


# ---------------------------------------------------------------------------
# stop() cleanly cancels in-flight workers
# ---------------------------------------------------------------------------


async def test_stop_cancels_in_flight():
    """stop() completes without raising even if tasks are running."""
    queue = _mock_queue(None)  # always returns None so workers idle
    worker = _make_worker(queue)

    await worker.start()
    assert len(worker._tasks) == 1
    await worker.stop()
    assert worker._tasks == []


async def test_start_idempotent():
    """Calling start() twice does not double the worker tasks."""
    queue = _mock_queue(None)
    worker = _make_worker(queue)

    await worker.start()
    await worker.start()  # second call should be a no-op
    assert len(worker._tasks) == 1
    await worker.stop()


# ---------------------------------------------------------------------------
# Windows: os.getloadavg raises AttributeError → not throttled
# ---------------------------------------------------------------------------


async def test_throttle_disabled_on_windows():
    """When os.getloadavg doesn't exist (Windows), throttling is disabled."""
    queue = _mock_queue(None)
    worker = _make_worker(queue)

    with patch("os.getloadavg", side_effect=AttributeError("no getloadavg")):
        # Should return False (not throttled)
        assert worker._is_throttled() is False


# ---------------------------------------------------------------------------
# build_default_registry
# ---------------------------------------------------------------------------


def test_build_default_registry_includes_entity_extraction():
    """build_default_registry includes EntityExtractionJob."""
    registry = build_default_registry()
    assert "entity_extraction" in registry


def test_build_default_registry_idempotent():
    """Calling build_default_registry twice returns consistent results."""
    r1 = build_default_registry()
    r2 = build_default_registry()
    assert set(r1.keys()) == set(r2.keys())


def test_build_default_registry_includes_compute_umap_3d():
    """compute_umap_3d must be registered — it is enqueued by the scheduler.

    Regression for the missing-import error class: the job file existed but
    was absent from jobs/__init__, so it never became a BaseJob subclass and
    every enqueued record failed with 'unknown job_type'. build_default_registry
    now auto-discovers all job modules, so existing-on-disk is sufficient.
    """
    registry = build_default_registry()
    assert "compute_umap_3d" in registry


def test_build_default_registry_covers_every_job_module():
    """Every job_type declared under app/processor/jobs is registered.

    Guards the auto-discovery contract so a newly-added job file can never
    silently drop out of the registry (and fail only at runtime).
    """
    import importlib
    import pkgutil

    import app.processor.jobs as jobs_pkg
    from core.processor.job import BaseJob

    declared: set[str] = set()
    for mod in pkgutil.iter_modules(jobs_pkg.__path__):
        if mod.name.startswith("_"):
            continue
        module = importlib.import_module(f"{jobs_pkg.__name__}.{mod.name}")
        for obj in vars(module).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseJob)
                and obj is not BaseJob
                and not getattr(obj, "__abstractmethods__", None)
                and getattr(obj, "job_type", "")
            ):
                declared.add(obj.job_type)

    registered = set(build_default_registry().keys())
    missing = declared - registered
    assert not missing, f"job_types on disk but not registered: {missing}"
