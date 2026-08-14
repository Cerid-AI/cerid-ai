# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Redis-backed implementation of JobQueueProtocol.

Key pattern
-----------
  cerid:proc:queue:<priority>   — RPUSH/BLPOP list of job_ids (FIFO)
  cerid:proc:job:<job_id>       — Hash with JSON-serialised JobRecord fields
  cerid:proc:running            — Set of currently-running job_ids
  cerid:proc:paused             — Key holding "0" or "1"
  cerid:proc:recent             — Sorted set: job_id → completed_at epoch (for list_recent)

All methods are async. The existing ``get_redis()`` returns a synchronous
``redis.Redis`` client; every Redis call is wrapped in
``asyncio.to_thread`` to avoid blocking the event loop.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from core.processor.job import JobRecord, JobResult, JobState
from core.processor.priority import Priority, priority_order
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor_queue")

# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------

_PREFIX = "cerid:proc"


def _queue_key(priority: Priority) -> str:
    return f"{_PREFIX}:queue:{priority.value}"


def _job_key(job_id: str) -> str:
    return f"{_PREFIX}:job:{job_id}"


# Job records are the processor's audit trail, not durable state, and had no
# expiry at all — 104,873 keys and growing ~1.4k/day on a single-user install.
# That unbounded keyspace is what made the delete-path cache invalidation (a
# full-keyspace SCAN) take 30-150s per delete. The TTL is refreshed on every
# write, so an in-flight or repeatedly-updated job never expires mid-run;
# finished records simply age out.
JOB_RECORD_TTL_S = 14 * 24 * 3600  # 14 days

# The recent-index sorted set had no retention at all: job hashes age out via
# JOB_RECORD_TTL_S but their index entries stayed forever, so the set grew
# without bound and list_recent silently under-filled as its newest members
# pointed at expired hashes. Bounded two ways on every terminal-state write:
# by score (nothing older than the record TTL — an index entry without a hash
# is pure noise) and by rank as a backstop for high-throughput bursts.
RECENT_INDEX_MAX = 1000


def _touch_job_ttl(redis_client: Any, job_id: str) -> None:
    """(Re)arm the retention window on a job record. Best-effort."""
    try:
        redis_client.expire(_job_key(job_id), JOB_RECORD_TTL_S)
    except Exception as exc:  # noqa: BLE001 — retention is housekeeping, never fatal
        log_swallowed_error("app.db.redis.processor_queue.ttl", exc)


def _trim_recent_index(redis_client: Any, now_ts: float) -> None:
    """Apply both retention bounds to the recent index. Best-effort."""
    try:
        redis_client.zremrangebyscore(_RECENT_KEY, "-inf", now_ts - JOB_RECORD_TTL_S)
        redis_client.zremrangebyrank(_RECENT_KEY, 0, -(RECENT_INDEX_MAX + 1))
    except Exception as exc:  # noqa: BLE001 — retention is housekeeping, never fatal
        log_swallowed_error("app.db.redis.processor_queue.trim_recent", exc)


_RUNNING_KEY = f"{_PREFIX}:running"
_PAUSED_KEY = f"{_PREFIX}:paused"
_RECENT_KEY = f"{_PREFIX}:recent"

# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _record_to_mapping(record: JobRecord) -> dict[str, str]:
    """Flatten JobRecord to a Redis hash (all values as strings)."""
    d = record.to_dict()
    # payload and other dicts → JSON strings
    result: dict[str, str] = {}
    for k, v in d.items():
        if v is None:
            result[k] = ""
        elif isinstance(v, (dict, list)):
            result[k] = json.dumps(v)
        else:
            result[k] = str(v)
    return result


def _mapping_to_record(mapping: dict[str, Any]) -> JobRecord:
    """Reconstruct a JobRecord from a Redis hash mapping."""
    d: dict[str, Any] = {}
    for k, v in mapping.items():
        # Redis may return bytes or str depending on decode_responses
        k_str = k.decode() if isinstance(k, bytes) else str(k)
        v_str = v.decode() if isinstance(v, bytes) else str(v)
        d[k_str] = v_str if v_str != "" else None

    # Re-parse JSON fields
    if d.get("payload"):
        d["payload"] = json.loads(d["payload"])
    else:
        d["payload"] = {}

    # CL-5: metadata round-trips as a JSON string; progress is float-coerced in
    # JobRecord.from_dict, so only metadata needs re-parsing here.
    if d.get("metadata"):
        try:
            d["metadata"] = json.loads(d["metadata"])
        except (TypeError, ValueError):
            d["metadata"] = {}
    else:
        d["metadata"] = {}

    # Booleans
    if "requires_llm" in d and d["requires_llm"] is not None:
        d["requires_llm"] = d["requires_llm"].lower() in ("true", "1")

    # Integers
    for int_field in (
        "retry_count",
        "estimated_tokens_in",
        "estimated_tokens_out",
        "actual_tokens_in",
        "actual_tokens_out",
    ):
        val = d.get(int_field)
        if val is not None:
            try:
                d[int_field] = int(val)
            except (TypeError, ValueError):
                d[int_field] = None if int_field.startswith("actual") else 0

    return JobRecord.from_dict(d)


# ---------------------------------------------------------------------------
# Sync convenience helper for sync call sites
# ---------------------------------------------------------------------------


def enqueue_job(
    job: Any,
    *,
    payload: dict[str, Any] | None = None,
    redis_client: Any | None = None,
) -> str:
    """Synchronously enqueue a ``BaseJob`` — for sync call sites.

    Bridges the async-only ``RedisJobQueue.enqueue`` for sync paths like
    the ingestion pipeline's HyPE hook (Phase R.3). Bypasses the
    ``asyncio.to_thread`` wrapper since the underlying Redis calls are
    already sync.

    Parameters
    ----------
    job
        A concrete ``BaseJob`` instance.  ``job.new_record(payload=...)``
        is called to produce the persisted ``JobRecord``.
    payload
        Optional mapping captured into ``JobRecord.payload``. The worker
        re-instantiates jobs as ``job_class(**record.payload)`` — call
        sites with non-trivial ``__init__`` args MUST pass them here or
        the dispatch will fail with ``instantiation error: missing
        required positional argument``.
    redis_client
        Optional connected ``redis.Redis`` instance. When omitted, fetched
        via ``app.deps.get_redis``.

    Returns
    -------
    str
        The persisted ``record.id``.
    """
    if redis_client is None:
        from app.deps import get_redis  # noqa: PLC0415
        redis_client = get_redis()
    record = job.new_record(payload=payload)
    mapping = _record_to_mapping(record)
    # redis-py's hset typing requires Mapping[str|bytes, bytes|float|int|str];
    # our values are all str so this is correct at runtime.
    redis_client.hset(_job_key(record.id), mapping=mapping)  # type: ignore[arg-type]
    _touch_job_ttl(redis_client, record.id)
    redis_client.lpush(_queue_key(record.priority), record.id)
    return record.id


def _pending_stale_ttl_s() -> int:
    """Age beyond which a pending marker is an orphan, not a duplicate."""
    import config  # noqa: PLC0415 — keep this module importable without config side effects

    return int(getattr(config, "PROCESSOR_PENDING_STALE_TTL_S", 21600))


def _is_stale_pending(enqueued_at_raw: Any) -> bool:
    """True when a pending marker's enqueued_at is older than the stale TTL.

    An unparsable/missing timestamp also counts as stale: such a record can
    never be reasoned about and would otherwise absorb duplicates forever.
    """
    if not enqueued_at_raw:
        return True
    raw = enqueued_at_raw.decode() if isinstance(enqueued_at_raw, bytes) else str(enqueued_at_raw)
    try:
        enqueued_at = datetime.fromisoformat(raw)
    except ValueError:
        return True
    if enqueued_at.tzinfo is None:
        enqueued_at = enqueued_at.replace(tzinfo=timezone.utc)
    age_s = (datetime.now(tz=timezone.utc) - enqueued_at).total_seconds()
    return age_s > _pending_stale_ttl_s()


def _prune_stale_pending(redis_client: Any, job_id: str, queue_list_key: str) -> None:
    """Remove an orphaned pending marker so it cannot absorb future enqueues.

    The record is marked FAILED (with an explicit reason) and surfaced on the
    recent set rather than deleted, so the supersession is observable. SF-2:
    a marker orphaned by a container restart collapsed every later enqueue —
    including manual run-now — into itself, with nothing ever running.
    """
    try:
        redis_client.lrem(queue_list_key, 0, job_id)
        now = datetime.now(tz=timezone.utc)
        redis_client.hset(
            _job_key(job_id),
            mapping={
                "state": JobState.FAILED.value,
                "completed_at": now.isoformat(),
                "error_message": (
                    "stale pending marker superseded "
                    "(older than PROCESSOR_PENDING_STALE_TTL_S; likely orphaned by a restart)"
                ),
            },
        )
        redis_client.zadd(_RECENT_KEY, {job_id: now.timestamp()})
        _trim_recent_index(redis_client, now.timestamp())
        logger.info("pruned stale pending job marker %s from %s", job_id, queue_list_key)
    except Exception as exc:  # noqa: BLE001 — pruning is housekeeping, never fatal
        log_swallowed_error(
            "processor.redis_queue.prune_stale", exc, context={"job_id": job_id}
        )


def find_active_job_id(
    redis_client: Any,
    job_type: str,
    *,
    payload: dict[str, Any] | None = None,
) -> str | None:
    """Return the id of a pending-or-running job matching ``job_type``.

    When ``payload`` is given, the stored payload must also be equal
    (compared as parsed dicts, so JSON key order is irrelevant); when
    ``None``, any payload of the type matches. Walks the queue's own key
    layout (pending priority lists + running set) — the same convention
    the knowledge-pack install and digest-run collapse paths use — so no
    parallel bookkeeping can drift from the queue.

    A matching PENDING marker older than ``PROCESSOR_PENDING_STALE_TTL_S``
    is not returned: it is pruned (marked failed, removed from its list) and
    the scan continues, so a marker orphaned by a restart can never absorb
    new enqueues indefinitely (SF-2). Running-set entries are exempt — long
    runs are legitimate, and dead-worker ghosts are recovered by
    ``recover_orphaned_running`` at worker start.

    Fail-open: any Redis error during the scan returns ``None`` ("no
    duplicate found") so a broken dedupe check can never block real work —
    the worst case is one stacked duplicate, the pre-collapse behaviour.
    """
    def _s(v: Any) -> str:
        return v.decode() if isinstance(v, bytes) else str(v)

    try:
        # (job_id, pending-list key) — key None for running-set entries.
        job_ids: list[tuple[str, str | None]] = []
        for priority in priority_order():
            key = _queue_key(priority)
            job_ids.extend((_s(j), key) for j in redis_client.lrange(key, 0, -1))
        job_ids.extend((_s(j), None) for j in redis_client.smembers(_RUNNING_KEY))

        for job_id, list_key in job_ids:
            stored_type, stored_payload_raw, stored_enqueued_at = redis_client.hmget(
                _job_key(job_id), ["job_type", "payload", "enqueued_at"]
            )
            if stored_type is None or _s(stored_type) != job_type:
                continue
            if payload is not None:
                try:
                    stored_payload = (
                        json.loads(_s(stored_payload_raw)) if stored_payload_raw else {}
                    )
                except (TypeError, ValueError) as exc:
                    log_swallowed_error(
                        "processor.redis_queue.find_active", exc, context={"job_id": job_id}
                    )
                    continue
                if stored_payload != payload:
                    continue
            if list_key is not None and _is_stale_pending(stored_enqueued_at):
                _prune_stale_pending(redis_client, job_id, list_key)
                continue
            return job_id
    except Exception as exc:  # noqa: BLE001 — fail-open, see docstring
        log_swallowed_error(
            "processor.redis_queue.find_active", exc, context={"job_type": job_type}
        )
    return None


def enqueue_job_if_absent(
    job: Any,
    *,
    payload: dict[str, Any] | None = None,
    redis_client: Any | None = None,
) -> str | None:
    """Enqueue like :func:`enqueue_job` unless an equivalent job is already
    pending or running.

    Equivalence = same ``job_type`` AND equal payload (the payload the
    worker re-instantiates from). Duplicate collapse for recurring enqueue
    sites (schedulers, ingest-event subscribers): a pending duplicate would
    scan the same state as the original, so stacking it only grows the
    queue — observed live 2026-07-13 when the 60 s ``ingest_recovery`` cron
    accumulated 1,459 pending copies behind slow LOW-priority jobs.
    User-triggered one-shot enqueues keep using :func:`enqueue_job`.

    Returns the new ``record.id``, or ``None`` when collapsed onto an
    existing pending/running job.
    """
    if redis_client is None:
        from app.deps import get_redis  # noqa: PLC0415
        redis_client = get_redis()
    existing = find_active_job_id(redis_client, job.job_type, payload=payload or {})
    if existing is not None:
        return None
    return enqueue_job(job, payload=payload, redis_client=redis_client)


# ---------------------------------------------------------------------------
# RedisJobQueue
# ---------------------------------------------------------------------------


class RedisJobQueue:
    """Redis-backed implementation of ``JobQueueProtocol``.

    Accepts the synchronous ``redis.Redis`` client returned by
    ``app.deps.get_redis``. All blocking Redis calls are dispatched via
    ``asyncio.to_thread`` so they do not stall the event loop.

    Parameters
    ----------
    redis_client
        A connected ``redis.Redis`` instance (``decode_responses=True``
        is strongly recommended — the implementation handles both cases
        but string responses avoid an extra decode step).
    """

    def __init__(self, redis_client: Any) -> None:
        self._r = redis_client

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run(self, fn, *args: Any, **kwargs: Any) -> Any:
        """Execute a synchronous Redis call in a thread pool."""
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def _is_paused(self) -> bool:
        val = await self._run(self._r.get, _PAUSED_KEY)
        return val == "1"

    async def _save_record(self, record: JobRecord) -> None:
        mapping = _record_to_mapping(record)
        await self._run(self._r.hset, _job_key(record.id), mapping=mapping)
        await self._run(self._r.expire, _job_key(record.id), JOB_RECORD_TTL_S)

    async def _load_record(self, job_id: str) -> JobRecord | None:
        mapping = await self._run(self._r.hgetall, _job_key(job_id))
        if not mapping:
            return None
        try:
            return _mapping_to_record(mapping)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("processor.redis_queue", exc, context={"job_id": job_id})
            return None

    # ------------------------------------------------------------------
    # JobQueueProtocol implementation
    # ------------------------------------------------------------------

    async def enqueue(self, job_record: JobRecord) -> str:
        """Persist the job hash and push its ID onto the priority list."""
        await self._save_record(job_record)
        await self._run(
            self._r.lpush,
            _queue_key(job_record.priority),
            job_record.id,
        )
        return job_record.id

    async def enqueue_if_absent(self, job_record: JobRecord) -> str | None:
        """Enqueue unless an equivalent job (same ``job_type`` + equal
        payload) is already pending or running.

        Duplicate collapse for periodic enqueues — a pending duplicate
        scans the same state as the original, so it is pure queue growth;
        the cadence tick that fires after the active job settles
        re-enqueues, so skipping never loses work. Follows the pack-install
        collapse convention: presence in a pending priority list OR the
        running set counts as active (``recover_orphaned_running`` keeps
        the running set free of dead-worker ghosts).

        Returns the enqueued ``record.id``, or ``None`` when collapsed.
        """
        existing = await self._run(
            find_active_job_id,
            self._r,
            job_record.job_type,
            payload=job_record.payload,
        )
        if existing is not None:
            return None
        return await self.enqueue(job_record)

    async def get(self, job_id: str) -> JobRecord | None:
        """Return the persisted record for ``job_id``, or ``None`` if it is
        absent or has aged past its retention TTL.

        Read-only single-job fetch for status polling (e.g. the SDK
        ``/memory/extract/jobs/{job_id}`` endpoint). Public wrapper over the
        internal loader so callers don't reach into ``_load_record``.
        """
        return await self._load_record(job_id)

    async def dequeue(self, priorities: list[Priority]) -> JobRecord | None:
        """Pop the next ready job in priority order.

        Returns ``None`` when paused or all queues are empty.
        Uses a non-blocking RPOP scan (not BRPOP) so the caller's worker
        loop can implement its own sleep/polling strategy.
        """
        if await self._is_paused():
            return None

        for priority in priorities:
            job_id = await self._run(self._r.rpop, _queue_key(priority))
            if job_id:
                job_id_str = job_id.decode() if isinstance(job_id, bytes) else str(job_id)
                return await self._load_record(job_id_str)

        return None

    async def mark_running(self, job_id: str) -> None:
        """Transition pending → running; record started_at and add to running set."""
        record = await self._load_record(job_id)
        if record is None:
            return
        record.state = JobState.RUNNING
        record.started_at = datetime.now(tz=timezone.utc)
        await self._save_record(record)
        await self._run(self._r.sadd, _RUNNING_KEY, job_id)

    async def update_progress(self, job_id: str, progress: float) -> None:
        """Persist the latest progress checkpoint (0.0–1.0) onto the job hash.

        Lightweight single-field HSET — avoids a full record load+save on every
        progress tick. Best-effort: a Redis failure never breaks the running job
        (CL-5 — progress was previously only debug-logged and never persisted).
        """
        try:
            await self._run(self._r.hset, _job_key(job_id), "progress", str(float(progress)))
            await self._run(self._r.expire, _job_key(job_id), JOB_RECORD_TTL_S)
        except Exception as exc:  # noqa: BLE001 — progress persistence is best-effort
            log_swallowed_error("processor.redis_queue.update_progress", exc, context={"job_id": job_id})

    async def mark_completed(self, job_id: str, result: JobResult) -> None:
        """Transition running → completed; persist token actuals + result metadata."""
        record = await self._load_record(job_id)
        if record is None:
            return
        record.state = JobState.COMPLETED
        record.completed_at = datetime.now(tz=timezone.utc)
        record.actual_tokens_in = result.actual_tokens_in
        record.actual_tokens_out = result.actual_tokens_out
        # CL-5: carry the job's outcome data onto the persisted record so
        # /processor/recent can surface it (was silently dropped — AF-008/076/083).
        record.metadata = dict(result.metadata or {})
        record.progress = 1.0
        await self._save_record(record)
        await self._run(self._r.srem, _RUNNING_KEY, job_id)
        # Add to recent sorted set (score = epoch seconds for ordering)
        score = record.completed_at.timestamp()
        await self._run(self._r.zadd, _RECENT_KEY, {job_id: score})
        await self._run(_trim_recent_index, self._r, score)

    async def mark_held(self, job_id: str, result: JobResult) -> None:
        """Transition running → HELD; a cost-cap / budget hold stopped the job.

        Distinct terminal state from COMPLETED so a held job is never counted as
        a success (CL-5/AF-017). Persists the held marker (in ``result.metadata``)
        and surfaces on /processor/recent like any other terminal outcome.
        """
        record = await self._load_record(job_id)
        if record is None:
            return
        record.state = JobState.HELD
        record.completed_at = datetime.now(tz=timezone.utc)
        record.actual_tokens_in = result.actual_tokens_in
        record.actual_tokens_out = result.actual_tokens_out
        record.metadata = dict(result.metadata or {})
        await self._save_record(record)
        await self._run(self._r.srem, _RUNNING_KEY, job_id)
        score = record.completed_at.timestamp()
        await self._run(self._r.zadd, _RECENT_KEY, {job_id: score})
        await self._run(_trim_recent_index, self._r, score)

    async def mark_failed(self, job_id: str, error_message: str) -> None:
        """Transition running → failed; record error_message."""
        record = await self._load_record(job_id)
        if record is None:
            return
        record.state = JobState.FAILED
        record.completed_at = datetime.now(tz=timezone.utc)
        record.error_message = error_message
        await self._save_record(record)
        await self._run(self._r.srem, _RUNNING_KEY, job_id)
        score = record.completed_at.timestamp()
        await self._run(self._r.zadd, _RECENT_KEY, {job_id: score})
        await self._run(_trim_recent_index, self._r, score)

    async def recover_orphaned_running(self) -> list[str]:
        """Requeue jobs stranded in the running set by a dead worker.

        A single worker process serves this queue, so any job still marked
        running when a worker STARTS was orphaned by a crash/restart — it
        will never complete and, worse, sits in the running set forever,
        where duplicate-enqueue collapse (pack installs, digest runs) reads
        it as "already in flight" and refuses new work. Observed live
        2026-07-13: 46 ghosts accumulated across container restarts blocked
        a pack install indefinitely. Called from ProcessorWorker.start().
        """
        recovered: list[str] = []
        job_ids = await self._run(self._r.smembers, _RUNNING_KEY)
        for raw_id in job_ids or []:
            job_id = raw_id.decode() if isinstance(raw_id, bytes) else str(raw_id)
            record = await self._load_record(job_id)
            if record is None:
                await self._run(self._r.srem, _RUNNING_KEY, job_id)
                continue
            if record.state == JobState.RUNNING:
                record.state = JobState.PENDING
                record.started_at = None
                await self._save_record(record)
                await self._run(self._r.lpush, _queue_key(record.priority), job_id)
                recovered.append(job_id)
            await self._run(self._r.srem, _RUNNING_KEY, job_id)
        return recovered

    async def pause(self) -> None:
        """Set the paused flag; subsequent dequeue() calls return None."""
        await self._run(self._r.set, _PAUSED_KEY, "1")

    async def resume(self) -> None:
        """Clear the paused flag."""
        await self._run(self._r.set, _PAUSED_KEY, "0")

    async def size_by_priority(self) -> dict[Priority, int]:
        """Return current queue depth (pending items) for each priority."""
        result: dict[Priority, int] = {}
        for priority in priority_order():
            length = await self._run(self._r.llen, _queue_key(priority))
            result[priority] = int(length)
        return result

    async def list_recent(
        self,
        limit: int,
        *,
        job_type: str | None = None,
        per_type_cap: int | None = None,
    ) -> list[JobRecord]:
        """Return up to ``limit`` most recent terminal jobs (newest first).

        Uses the ``cerid:proc:recent`` sorted set scored by completion
        epoch, so ordering is stable across restarts. The full (bounded)
        index is walked newest-first until ``limit`` records are collected,
        so an index entry whose hash has expired no longer shrinks the
        response — it is dropped from the index instead (self-heal).

        ``job_type``
            Only return records of this type (uncapped) — the drill-down
            for a high-frequency type the default mix caps.
        ``per_type_cap``
            At most this many records of any single job type. De-noises the
            default listing: 88 of the 100 most recent records were
            wiki_refresh live, displacing every other job type. ``None`` or
            ``0`` disables capping.
        """
        # ZREVRANGE returns members in descending score order. The index is
        # bounded at RECENT_INDEX_MAX, so fetching it whole stays cheap.
        job_ids = await self._run(
            self._r.zrevrange, _RECENT_KEY, 0, RECENT_INDEX_MAX - 1
        )
        records: list[JobRecord] = []
        seen_by_type: dict[str, int] = {}
        for jid in job_ids:
            if len(records) >= limit:
                break
            jid_str = jid.decode() if isinstance(jid, bytes) else str(jid)
            record = await self._load_record(jid_str)
            if record is None:
                # Hash expired (or never existed): the index entry is noise.
                try:
                    await self._run(self._r.zrem, _RECENT_KEY, jid_str)
                except Exception as exc:  # noqa: BLE001 — self-heal is best-effort
                    log_swallowed_error(
                        "processor.redis_queue.list_recent_heal",
                        exc,
                        context={"job_id": jid_str},
                    )
                continue
            if job_type is not None and record.job_type != job_type:
                continue
            if job_type is None and per_type_cap:
                count = seen_by_type.get(record.job_type, 0)
                if count >= per_type_cap:
                    continue
                seen_by_type[record.job_type] = count + 1
            records.append(record)
        return records
