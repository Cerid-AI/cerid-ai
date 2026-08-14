# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Unit tests for RedisJobQueue using fakeredis.

All tests use an in-process FakeRedis server so no real Redis is needed.
``asyncio_mode = "auto"`` (set in pyproject.toml) means every async test
function is detected and run automatically without explicit markers.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import fakeredis
import pytest

from app.db.redis.processor_queue import (
    RedisJobQueue,
    enqueue_job_if_absent,
    find_active_job_id,
)
from core.processor.cost import CostEstimate
from core.processor.job import BaseJob, JobRecord, JobResult, JobState, ProgressCallback
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


# ---------------------------------------------------------------------------
# Orphan recovery — jobs stranded in the running set by a dead worker
# ---------------------------------------------------------------------------

async def test_recover_orphaned_running_requeues_job(queue):
    record = _make_record()
    await queue.enqueue(record)
    dequeued = await queue.dequeue([record.priority])
    await queue.mark_running(dequeued.id)

    recovered = await queue.recover_orphaned_running()

    assert recovered == [record.id]
    again = await queue.dequeue([record.priority])
    assert again is not None and again.id == record.id
    # Running set is clean — duplicate-enqueue collapse no longer sees a ghost.
    assert await queue.recover_orphaned_running() == []


async def test_recover_orphaned_running_drops_recordless_ids(redis_client, queue):
    redis_client.sadd("cerid:proc:running", "ghost-without-record")
    assert await queue.recover_orphaned_running() == []
    assert redis_client.smembers("cerid:proc:running") == set()


async def test_recover_orphaned_running_skips_settled_jobs(queue, redis_client):
    record = _make_record()
    await queue.enqueue(record)
    dequeued = await queue.dequeue([record.priority])
    await queue.mark_running(dequeued.id)
    # Simulate a completed job whose SREM was lost (partial write).
    await queue.mark_completed(dequeued.id, JobResult(job_id=dequeued.id, actual_tokens_in=0, actual_tokens_out=0))
    redis_client.sadd("cerid:proc:running", dequeued.id)

    assert await queue.recover_orphaned_running() == []
    assert redis_client.smembers("cerid:proc:running") == set()


# ---------------------------------------------------------------------------
# enqueue_if_absent — duplicate collapse for periodic enqueues
#
# Semantics: equivalence = same job_type AND equal payload. A match in a
# pending priority list OR the running set collapses the enqueue (pack-install
# convention) — periodic jobs are re-enqueued by their next cadence tick after
# the active one settles, so a collapse never loses work.
# ---------------------------------------------------------------------------


async def test_enqueue_if_absent_enqueues_when_no_duplicate(queue):
    record = _make_record(job_type="ingest_recovery")

    assert await queue.enqueue_if_absent(record) == record.id

    sizes = await queue.size_by_priority()
    assert sizes[Priority.LOW] == 1


async def test_enqueue_if_absent_skips_pending_duplicate(queue):
    first = _make_record(job_type="ingest_recovery")
    assert await queue.enqueue_if_absent(first) == first.id

    # Same job_type + same payload (_make_record uses a fixed payload).
    duplicate = _make_record(job_type="ingest_recovery")
    assert await queue.enqueue_if_absent(duplicate) is None

    sizes = await queue.size_by_priority()
    assert sizes[Priority.LOW] == 1


async def test_enqueue_if_absent_different_payload_same_type_enqueues_both(queue):
    first = _make_record(job_type="wiki_refresh")
    first.payload = {"entity_slug": "ada-lovelace"}
    second = _make_record(job_type="wiki_refresh")
    second.payload = {"entity_slug": "alan-turing"}

    assert await queue.enqueue_if_absent(first) == first.id
    assert await queue.enqueue_if_absent(second) == second.id

    sizes = await queue.size_by_priority()
    assert sizes[Priority.LOW] == 2


async def test_enqueue_if_absent_skips_running_duplicate(queue):
    record = _make_record(job_type="ingest_recovery")
    await queue.enqueue(record)
    dequeued = await queue.dequeue(priority_order())
    assert dequeued is not None
    await queue.mark_running(dequeued.id)

    # Running (not pending) still counts as active — the next cadence
    # tick re-enqueues once the in-flight run settles.
    duplicate = _make_record(job_type="ingest_recovery")
    assert await queue.enqueue_if_absent(duplicate) is None

    sizes = await queue.size_by_priority()
    assert sizes[Priority.LOW] == 0


async def test_enqueue_if_absent_reenqueues_after_completion(queue):
    record = _make_record(job_type="ingest_recovery")
    await queue.enqueue(record)
    dequeued = await queue.dequeue(priority_order())
    assert dequeued is not None
    await queue.mark_running(dequeued.id)
    result = JobResult(job_id=dequeued.id, actual_tokens_in=0, actual_tokens_out=0)
    await queue.mark_completed(dequeued.id, result)

    # Settled jobs leave both the pending lists and the running set, so
    # the next periodic tick enqueues normally.
    fresh = _make_record(job_type="ingest_recovery")
    assert await queue.enqueue_if_absent(fresh) == fresh.id


async def test_enqueue_if_absent_ignores_other_job_types(queue):
    other = _make_record(job_type="wiki_refresh")
    await queue.enqueue(other)

    record = _make_record(job_type="ingest_recovery")
    assert await queue.enqueue_if_absent(record) == record.id


# ---------------------------------------------------------------------------
# enqueue_job_if_absent — sync bridge (wiki_refresh subscriber path)
# ---------------------------------------------------------------------------


class _StubPeriodicJob(BaseJob):
    """Minimal zero-cost BaseJob for exercising the sync enqueue bridge."""

    job_type = "stub_periodic"

    @property
    def priority(self) -> Priority:
        return Priority.LOW

    def estimate_cost(self) -> CostEstimate:
        return CostEstimate(
            estimated_tokens_in=0,
            estimated_tokens_out=0,
            model="none",
            estimated_usd=Decimal("0.00"),
            confidence="high",
        )

    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        return JobResult(job_id="", actual_tokens_in=0, actual_tokens_out=0)


def test_enqueue_job_if_absent_collapses_pending_duplicate(redis_client):
    first = enqueue_job_if_absent(
        _StubPeriodicJob(), payload={"entity_slug": "ada-lovelace"}, redis_client=redis_client
    )
    assert first is not None

    duplicate = enqueue_job_if_absent(
        _StubPeriodicJob(), payload={"entity_slug": "ada-lovelace"}, redis_client=redis_client
    )
    assert duplicate is None

    other = enqueue_job_if_absent(
        _StubPeriodicJob(), payload={"entity_slug": "alan-turing"}, redis_client=redis_client
    )
    assert other is not None
    assert redis_client.llen("cerid:proc:queue:low") == 2


def test_enqueue_job_if_absent_empty_payload_is_type_level(redis_client):
    """Payload-less periodic jobs ({} payload) collapse at the type level."""
    first = enqueue_job_if_absent(_StubPeriodicJob(), redis_client=redis_client)
    assert first is not None

    duplicate = enqueue_job_if_absent(_StubPeriodicJob(), redis_client=redis_client)
    assert duplicate is None
    assert redis_client.llen("cerid:proc:queue:low") == 1


def test_find_active_job_id_fails_open_on_broken_client():
    """A broken Redis client must read as 'no duplicate' — the dedupe check
    can never be allowed to block real work."""
    assert find_active_job_id(None, "ingest_recovery", payload={}) is None


# ---------------------------------------------------------------------------
# Stale pending markers (SF-2) — a pending marker orphaned by a container
# restart must not absorb new enqueues forever. Older than
# PROCESSOR_PENDING_STALE_TTL_S ⇒ pruned and superseded, not "already pending".
# ---------------------------------------------------------------------------


def _make_stale_record(job_type: str = "compute_umap_3d") -> JobRecord:
    import config

    record = _make_record(job_type=job_type)
    record.payload = {}
    record.enqueued_at = datetime.now(tz=timezone.utc) - timedelta(
        seconds=config.PROCESSOR_PENDING_STALE_TTL_S + 60
    )
    return record


async def test_enqueue_if_absent_supersedes_stale_pending_marker(queue, redis_client):
    stale = _make_stale_record()
    await queue.enqueue(stale)

    fresh = _make_record(job_type="compute_umap_3d")
    fresh.payload = {}
    assert await queue.enqueue_if_absent(fresh) == fresh.id

    # The stale marker is gone from the pending list; only the fresh id remains.
    pending = redis_client.lrange("cerid:proc:queue:low", 0, -1)
    assert stale.id not in pending
    assert fresh.id in pending


async def test_stale_marker_is_marked_failed_when_pruned(queue, redis_client):
    stale = _make_stale_record()
    await queue.enqueue(stale)

    fresh = _make_record(job_type="compute_umap_3d")
    fresh.payload = {}
    await queue.enqueue_if_absent(fresh)

    mapping = redis_client.hgetall(f"cerid:proc:job:{stale.id}")
    assert mapping["state"] == JobState.FAILED.value
    assert "stale" in mapping["error_message"]


async def test_fresh_pending_duplicate_still_collapses(queue):
    fresh_marker = _make_record(job_type="compute_umap_3d")
    fresh_marker.payload = {}
    await queue.enqueue(fresh_marker)

    duplicate = _make_record(job_type="compute_umap_3d")
    duplicate.payload = {}
    assert await queue.enqueue_if_absent(duplicate) is None


async def test_stale_running_job_is_not_pruned(queue, redis_client):
    """Staleness applies to PENDING markers only — long-running jobs are
    legitimate, and running-set ghosts are owned by recover_orphaned_running."""
    stale = _make_stale_record()
    await queue.enqueue(stale)
    popped = await queue.dequeue([Priority.LOW])
    await queue.mark_running(popped.id)

    duplicate = _make_record(job_type="compute_umap_3d")
    duplicate.payload = {}
    assert await queue.enqueue_if_absent(duplicate) is None


# ---------------------------------------------------------------------------
# Recent-index retention + de-noising (processor-record-hygiene).
# Two faces of one problem: cerid:proc:recent grew without bound (job hashes
# expire after JOB_RECORD_TTL_S but their index entries never did), and
# wiki_refresh flooded /processor/recent — 88 of the 100 most recent records
# live — displacing every other job type.
# ---------------------------------------------------------------------------


def _seed_completed(
    redis_client,
    *,
    job_type: str,
    score: float,
    job_id: str | None = None,
) -> str:
    """Write a COMPLETED job hash + recent-index entry directly (read-path tests)."""
    from app.db.redis.processor_queue import _record_to_mapping

    record = _make_record(job_type=job_type, job_id=job_id)
    record.state = JobState.COMPLETED
    record.completed_at = datetime.fromtimestamp(score, tz=timezone.utc)
    redis_client.hset(f"cerid:proc:job:{record.id}", mapping=_record_to_mapping(record))
    redis_client.zadd("cerid:proc:recent", {record.id: score})
    return record.id


async def _complete_one(queue, job_type: str = "test_job") -> str:
    record = _make_record(job_type=job_type)
    await queue.enqueue(record)
    dequeued = await queue.dequeue(priority_order())
    assert dequeued is not None
    await queue.mark_running(dequeued.id)
    result = JobResult(job_id=dequeued.id, actual_tokens_in=0, actual_tokens_out=0)
    await queue.mark_completed(dequeued.id, result)
    return record.id


async def test_recent_index_membership_is_bounded(queue, redis_client, monkeypatch):
    import app.db.redis.processor_queue as pq

    monkeypatch.setattr(pq, "RECENT_INDEX_MAX", 10)
    for _ in range(15):
        await _complete_one(queue)
    assert redis_client.zcard("cerid:proc:recent") <= 10


async def test_recent_index_drops_entries_older_than_record_ttl(queue, redis_client):
    from app.db.redis.processor_queue import JOB_RECORD_TTL_S

    now = datetime.now(tz=timezone.utc).timestamp()
    redis_client.zadd("cerid:proc:recent", {"ghost-old": now - JOB_RECORD_TTL_S - 60})

    await _complete_one(queue)

    assert redis_client.zscore("cerid:proc:recent", "ghost-old") is None


async def test_list_recent_self_heals_index_entries_without_hashes(queue, redis_client):
    """An index entry whose job hash expired must not shrink the response
    below ``limit`` forever — it is dropped from the index on read."""
    now = datetime.now(tz=timezone.utc).timestamp()
    redis_client.zadd("cerid:proc:recent", {"ghost-recent": now})
    real_id = _seed_completed(redis_client, job_type="test_job", score=now - 1)

    recent = await queue.list_recent(10)

    assert [r.id for r in recent] == [real_id]
    assert redis_client.zscore("cerid:proc:recent", "ghost-recent") is None


async def test_list_recent_job_type_filter(queue, redis_client):
    now = datetime.now(tz=timezone.utc).timestamp()
    for i in range(4):
        _seed_completed(redis_client, job_type="wiki_refresh", score=now - i)
    mail_id = _seed_completed(redis_client, job_type="mail_poll", score=now - 10)

    only_mail = await queue.list_recent(10, job_type="mail_poll")
    assert [r.id for r in only_mail] == [mail_id]

    only_wiki = await queue.list_recent(10, job_type="wiki_refresh")
    assert len(only_wiki) == 4
    assert all(r.job_type == "wiki_refresh" for r in only_wiki)


async def test_list_recent_per_type_cap_keeps_other_jobs_visible(queue, redis_client):
    """The live symptom: 88 wiki_refresh in the 100 most recent records drown
    everything else. With a per-type cap the response is a mix — the cap's
    worth of wiki_refresh plus every other job type."""
    now = datetime.now(tz=timezone.utc).timestamp()
    # 88 wiki_refresh records, all newer than every other job type.
    for i in range(88):
        _seed_completed(redis_client, job_type="wiki_refresh", score=now - i)
    other_ids = {
        _seed_completed(redis_client, job_type=jt, score=now - 100 - i)
        for i, jt in enumerate(
            ["mail_poll", "digest_run", "ingest_recovery", "pack_install", "memory_promote"]
        )
    }

    recent = await queue.list_recent(20, per_type_cap=5)

    by_type: dict[str, int] = {}
    for r in recent:
        by_type[r.job_type] = by_type.get(r.job_type, 0) + 1
    assert by_type.get("wiki_refresh", 0) == 5
    # Every other job type survives the flood.
    returned_ids = {r.id for r in recent}
    assert other_ids <= returned_ids
    # Newest first is preserved across the mix.
    timestamps = [r.completed_at for r in recent]
    assert timestamps == sorted(timestamps, reverse=True)


async def test_list_recent_per_type_cap_zero_disables_capping(queue, redis_client):
    now = datetime.now(tz=timezone.utc).timestamp()
    for i in range(6):
        _seed_completed(redis_client, job_type="wiki_refresh", score=now - i)

    recent = await queue.list_recent(10, per_type_cap=0)
    assert len(recent) == 6
