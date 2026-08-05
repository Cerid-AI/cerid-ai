# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for ``app.processor.jobs.digest_run``.

The digest agent is monkeypatched — these tests exercise the job
wrapper contract (persist=True call, metadata shape, skipped pass-through)
plus the queue-introspection helpers the run-now endpoint uses. The
digest pipeline itself is covered by the daily_digest agent tests.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.processor.jobs.digest_run import (
    DigestRunJob,
    active_digest_run_jobs,
    enqueue_digest_run_job,
)
from core.processor.priority import Priority


async def _noop_progress(_pct: float) -> None:
    return None


def test_job_registered_in_default_registry():
    from app.processor.worker import build_default_registry

    registry = build_default_registry()
    assert registry.get("digest_run") is DigestRunJob


def test_job_is_high_priority_and_free():
    job = DigestRunJob()
    assert job.priority is Priority.HIGH
    estimate = job.estimate_cost()
    assert estimate.estimated_tokens_in == 0
    assert float(estimate.estimated_usd) == 0.0


def test_record_payload_round_trips_for_worker_dispatch():
    """Worker re-instantiates as job_class(**record.payload)."""
    job = DigestRunJob()
    record = job.new_record(payload={})
    clone = DigestRunJob(**record.payload)
    assert clone.job_type == "digest_run"


@pytest.mark.asyncio
async def test_run_generates_and_persists_digest():
    from core.agents.daily_digest import DigestResult

    fake = DigestResult(
        digest_id="did-job",
        generated_at="2026-07-12T07:00:00Z",
        window_hours=24,
        artifact_count=5,
        flagged_count=1,
        persisted_artifact_id="art:did-job",
    )
    with patch(
        "core.agents.daily_digest.generate_daily_digest",
        new_callable=AsyncMock, return_value=fake,
    ) as mock_generate:
        result = await DigestRunJob().run(progress_cb=_noop_progress)

    mock_generate.assert_awaited_once_with(persist=True)
    assert result.metadata["digest_id"] == "did-job"
    assert result.metadata["artifact_count"] == 5
    assert result.metadata["persisted_artifact_id"] == "art:did-job"
    assert result.metadata["skipped"] is False


@pytest.mark.asyncio
async def test_run_passes_through_skipped_digest():
    """Feature toggled off between enqueue and execution → the job still
    completes (no retry loop); the skip reason lands in metadata."""
    from core.agents.daily_digest import DigestResult

    fake = DigestResult(digest_id="did-skip", skipped=True, skip_reason="feature_gated")
    with patch(
        "core.agents.daily_digest.generate_daily_digest",
        new_callable=AsyncMock, return_value=fake,
    ):
        result = await DigestRunJob().run(progress_cb=_noop_progress)

    assert result.metadata["skipped"] is True
    assert result.metadata["skip_reason"] == "feature_gated"


# ── queue helpers ─────────────────────────────────────────────────────────

def _fake_redis(queued: dict[str, list[str]], running: list[str], job_types: dict[str, str]):
    """Stub the three Redis reads active_digest_run_jobs performs."""
    from app.db.redis.processor_queue import _job_key, _queue_key
    from core.processor.priority import priority_order

    queue_keys = {_queue_key(p): queued.get(p.value, []) for p in priority_order()}
    type_keys = {_job_key(jid): jt for jid, jt in job_types.items()}

    client = MagicMock()
    client.lrange.side_effect = lambda key, s, e: queue_keys.get(key, [])
    client.smembers.return_value = running
    client.hget.side_effect = lambda key, field: type_keys.get(key)
    return client


def test_active_digest_run_jobs_filters_by_job_type():
    client = _fake_redis(
        queued={"high": ["j-digest", "j-pack"]},
        running=["j-running"],
        job_types={
            "j-digest": "digest_run",
            "j-pack": "knowledge_pack_install",
            "j-running": "digest_run",
        },
    )
    assert active_digest_run_jobs(redis_client=client) == ["j-digest", "j-running"]


def test_active_digest_run_jobs_empty_queue():
    client = _fake_redis(queued={}, running=[], job_types={})
    assert active_digest_run_jobs(redis_client=client) == []


def test_enqueue_digest_run_job_returns_job_id():
    client = MagicMock()
    job_id = enqueue_digest_run_job(redis_client=client)
    assert isinstance(job_id, str) and job_id
    client.hset.assert_called_once()
    client.lpush.assert_called_once()
