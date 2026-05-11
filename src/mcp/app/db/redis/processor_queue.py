# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

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
from datetime import datetime, timezone
from typing import Any

from core.processor.job import JobRecord, JobResult, JobState
from core.processor.priority import Priority, priority_order
from core.utils.swallowed import log_swallowed_error

# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------

_PREFIX = "cerid:proc"


def _queue_key(priority: Priority) -> str:
    return f"{_PREFIX}:queue:{priority.value}"


def _job_key(job_id: str) -> str:
    return f"{_PREFIX}:job:{job_id}"


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


def enqueue_job(job: Any, *, redis_client: Any | None = None) -> str:
    """Synchronously enqueue a ``BaseJob`` — for sync call sites.

    Bridges the async-only ``RedisJobQueue.enqueue`` for sync paths like
    the ingestion pipeline's HyPE hook (Phase R.3). Bypasses the
    ``asyncio.to_thread`` wrapper since the underlying Redis calls are
    already sync.

    Parameters
    ----------
    job
        A concrete ``BaseJob`` instance.  ``job.new_record()`` is called
        to produce the persisted ``JobRecord``.
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
    record = job.new_record()
    mapping = _record_to_mapping(record)
    # redis-py's hset typing requires Mapping[str|bytes, bytes|float|int|str];
    # our values are all str so this is correct at runtime.
    redis_client.hset(_job_key(record.id), mapping=mapping)  # type: ignore[arg-type]
    redis_client.lpush(_queue_key(record.priority), record.id)
    return record.id


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

    async def mark_completed(self, job_id: str, result: JobResult) -> None:
        """Transition running → completed; persist token actuals."""
        record = await self._load_record(job_id)
        if record is None:
            return
        record.state = JobState.COMPLETED
        record.completed_at = datetime.now(tz=timezone.utc)
        record.actual_tokens_in = result.actual_tokens_in
        record.actual_tokens_out = result.actual_tokens_out
        await self._save_record(record)
        await self._run(self._r.srem, _RUNNING_KEY, job_id)
        # Add to recent sorted set (score = epoch seconds for ordering)
        score = record.completed_at.timestamp()
        await self._run(self._r.zadd, _RECENT_KEY, {job_id: score})

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

    async def list_recent(self, limit: int) -> list[JobRecord]:
        """Return up to ``limit`` most recently completed/failed jobs (newest first).

        Uses the ``cerid:proc:recent`` sorted set scored by completion
        epoch, so ordering is stable across restarts.
        """
        # ZREVRANGE returns members in descending score order
        job_ids = await self._run(
            self._r.zrevrange, _RECENT_KEY, 0, limit - 1
        )
        records: list[JobRecord] = []
        for jid in job_ids:
            jid_str = jid.decode() if isinstance(jid, bytes) else str(jid)
            record = await self._load_record(jid_str)
            if record is not None:
                records.append(record)
        return records
