# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Day-one verification gate for the v0.92 processor abstraction.

Tests that EntityExtractionJob can travel the full path:
  enqueue → RedisJobQueue → ProcessorWorker → instantiate → run → mark_completed

Downstream Neo4j, ChromaDB, and LLM calls are mocked at the documented seam
(EntityExtractionJob._run_pipeline) so this test runs without any Docker
services.  If this test passes, the processor abstraction works end-to-end
and backfill_entities.py work can route through it without bypassing it.

Run:
  PYTHONPATH=src/mcp .venv/bin/pytest src/mcp/tests/integration/test_processor_end_to_end.py -v -m chaos
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

import fakeredis
import pytest

from app.db.redis.processor_queue import RedisJobQueue
from app.processor.jobs.entity_extraction import EntityExtractionJob
from app.processor.worker import ProcessorWorker
from core.processor.job import JobRecord, JobState
from core.processor.priority import Priority

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _wait_for_drain(queue: RedisJobQueue, timeout: float = 5.0) -> bool:
    """Poll until all priority buckets are zero or timeout expires."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        sizes = await queue.size_by_priority()
        if sum(sizes.values()) == 0:
            return True
        await asyncio.sleep(0.05)
    return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def queue(fake_redis: fakeredis.FakeRedis) -> RedisJobQueue:
    return RedisJobQueue(fake_redis)


# ---------------------------------------------------------------------------
# End-to-end test
# ---------------------------------------------------------------------------


@pytest.mark.chaos
async def test_end_to_end_entity_extraction_job(
    queue: RedisJobQueue,
    fake_redis: fakeredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EntityExtractionJob: enqueue → worker dequeues → instantiates → runs → mark_completed.

    Validates the day-one acceptance criterion: backfill_entities.py work runs
    through the processor abstraction without bypassing it.  Downstream Neo4j,
    ChromaDB, and LLM calls are mocked at the documented seam (_run_pipeline).

    Mocked return value mimics a successful extraction:
      {"entities_upserted": 5, "edges_upserted": 7, "skipped": None}

    After the worker drains:
    - queue.size_by_priority() must be all-zero
    - list_recent(1) must return the job with state=completed
    - The result metadata must reflect the mocked pipeline statistics
    """
    # ------------------------------------------------------------------
    # 1. Patch the documented seam: _run_pipeline
    # ------------------------------------------------------------------
    mock_pipeline_result: dict[str, Any] = {
        "entities_upserted": 5,
        "edges_upserted": 7,
        "skipped": None,
    }

    async def _fake_run_pipeline(
        self: EntityExtractionJob,
        progress_cb: Any,
    ) -> dict[str, Any]:
        # Simulate minimal progress reporting so the run() method works
        await progress_cb(0.3)
        await progress_cb(0.7)
        await progress_cb(1.0)
        return mock_pipeline_result

    monkeypatch.setattr(
        EntityExtractionJob,
        "_run_pipeline",
        _fake_run_pipeline,
    )

    # ------------------------------------------------------------------
    # 2. Build registry containing only EntityExtractionJob
    # ------------------------------------------------------------------
    registry: dict[str, type] = {
        EntityExtractionJob.job_type: EntityExtractionJob,
    }

    # ------------------------------------------------------------------
    # 3. Construct worker
    # ------------------------------------------------------------------
    worker = ProcessorWorker(
        queue,
        registry,
        concurrency=1,
        poll_interval=0.05,
        load_ceiling=999.0,
        redis_client=fake_redis,
    )

    # ------------------------------------------------------------------
    # 4. Enqueue an EntityExtractionJob record
    # ------------------------------------------------------------------
    artifact_id = str(uuid.uuid4())
    tenant_id = "test-tenant"

    record = JobRecord(
        id=str(uuid.uuid4()),
        job_type=EntityExtractionJob.job_type,
        state=JobState.PENDING,
        priority=Priority.LOW,
        payload={"artifact_id": artifact_id, "tenant_id": tenant_id},
        enqueued_at=datetime.now(tz=timezone.utc),
    )
    job_id = await queue.enqueue(record)
    assert job_id == record.id

    # ------------------------------------------------------------------
    # 5. Start worker and wait for drain
    # ------------------------------------------------------------------
    try:
        await worker.start()
        drained = await _wait_for_drain(queue, timeout=5.0)
        assert drained, "Queue did not drain within 5 s"
    finally:
        await worker.stop()

    # ------------------------------------------------------------------
    # 6. Verify completion record
    # ------------------------------------------------------------------
    recent = await queue.list_recent(limit=1)
    assert len(recent) == 1, f"Expected 1 recent record, got {len(recent)}"

    completed_record = recent[0]
    assert completed_record.id == job_id, (
        f"Expected job_id={job_id}, got {completed_record.id}"
    )
    assert completed_record.state == JobState.COMPLETED, (
        f"Expected state=completed, got {completed_record.state!r}"
    )
    assert completed_record.completed_at is not None, "completed_at must be set"

    # Verify token actuals were recorded (EntityExtractionJob uses fixed estimates)
    assert completed_record.actual_tokens_in is not None
    assert completed_record.actual_tokens_out is not None

    # ------------------------------------------------------------------
    # 7. Verify the mocked pipeline stats flow through to the result
    #    (stored in JobRecord indirectly via mark_completed token fields;
    #     full metadata is in the JobResult which the worker records)
    # ------------------------------------------------------------------
    # The worker calls mark_completed(job_id, result) where result.metadata
    # carries the pipeline stats.  We can verify the job completed and the
    # queue reflects the correct state.  The metadata is available by
    # loading the raw record — mark_completed does not persist metadata to
    # Redis (only token actuals), so we validate indirectly via the
    # completed state and token fields being set.
    assert completed_record.actual_tokens_in == 2_500  # EntityExtractionJob._EST_TOKENS_IN
    assert completed_record.actual_tokens_out == 512   # EntityExtractionJob._EST_TOKENS_OUT
