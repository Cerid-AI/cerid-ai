# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Queue contract (Protocol) for the background processor.

Defines the interface that any concrete queue implementation must satisfy.
The Redis-backed adapter lives in ``app/db/redis/processor_queue.py``
(Phase 3); this module contains only the abstract contract so that
``core/`` stays free of infrastructure dependencies.

Callers that need a queue accept ``JobQueueProtocol`` — they never
import the concrete Redis class directly.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from core.processor.job import JobRecord, JobResult
from core.processor.priority import Priority


@runtime_checkable
class JobQueueProtocol(Protocol):
    """Contract for the background-job queue.

    Implementations must be async-safe. All methods are coroutines so
    that in-process (asyncio) and out-of-process (Redis) backends share
    the same call signature.

    ``runtime_checkable`` is set so that test stubs can be verified with
    ``isinstance(stub, JobQueueProtocol)`` without subclassing.
    """

    async def enqueue(self, job_record: JobRecord) -> str:
        """Persist a new job and return its ID.

        The record's ``id`` field is used as-is; the implementation MUST
        NOT generate a new ID.
        """
        ...

    async def dequeue(self, priorities: list[Priority]) -> JobRecord | None:
        """Pop the next ready job respecting the given priority order.

        Returns ``None`` when the queue is empty at all supplied
        priorities. Callers pass the output of ``priority_order()`` for
        normal execution.
        """
        ...

    async def mark_running(self, job_id: str) -> None:
        """Transition a job from ``pending`` to ``running``.

        The implementation sets ``started_at`` to now.
        """
        ...

    async def mark_completed(self, job_id: str, result: JobResult) -> None:
        """Transition a job to ``completed`` and persist token actuals + metadata."""
        ...

    async def mark_held(self, job_id: str, result: JobResult) -> None:
        """Transition a job to ``held`` — stopped by a cost-cap / budget hold.

        A distinct terminal state from ``completed`` so a held job is never
        counted as a success (CL-5/AF-017). Persists the held marker in
        ``result.metadata``.
        """
        ...

    async def update_progress(self, job_id: str, progress: float) -> None:
        """Persist the latest progress checkpoint (0.0–1.0) onto the job record.

        Best-effort and lightweight (single-field write) so it can be called on
        every progress tick of a long-running job.
        """
        ...

    async def mark_failed(self, job_id: str, error_message: str) -> None:
        """Transition a job to ``failed`` and record the error message."""
        ...

    async def pause(self) -> None:
        """Halt new dequeues without discarding queued jobs.

        In-flight jobs are allowed to complete. Subsequent calls to
        ``dequeue()`` return ``None`` until ``resume()`` is called.
        """
        ...

    async def resume(self) -> None:
        """Lift the pause imposed by ``pause()``."""
        ...

    async def size_by_priority(self) -> dict[Priority, int]:
        """Return current queue depth for each priority level."""
        ...

    async def list_recent(
        self,
        limit: int,
        *,
        job_type: str | None = None,
        per_type_cap: int | None = None,
    ) -> list[JobRecord]:
        """Return up to ``limit`` most recent terminal jobs, newest first.

        ``job_type`` restricts the listing to one type (uncapped).
        ``per_type_cap`` bounds how many records any single type may occupy
        in the default mix, so a high-frequency job (wiki_refresh) cannot
        displace every other type. ``None``/``0`` disables the cap.
        """
        ...
