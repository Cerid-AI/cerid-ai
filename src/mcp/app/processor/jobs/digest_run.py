# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""DigestRunJob — queued on-demand daily-digest pass.

``POST /digests/run-now`` used to run the full digest generation inline:
minutes on a populated corpus, which timed out a 60-second client during
beta (2026-07-12 triage — same defect class as the synchronous pack
install). Wrapping the pass in a ``BaseJob`` moves it onto the background
processor; the endpoint acks 202 and clients poll ``GET /digests/latest``
until ``generated_at`` advances.

Idempotency: a re-run simply regenerates the digest from the current KB
window and persists a fresh artifact — ``/digests/latest`` always serves
the newest one, so retried or double-enqueued jobs converge on the same
observable state. Duplicate enqueues are additionally collapsed at the
endpoint via :func:`active_digest_run_jobs`.

Discovered automatically by ``build_default_registry()``.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from core.processor.cost import CostEstimate
from core.processor.job import BaseJob, JobResult, ProgressCallback
from core.processor.priority import Priority

logger = logging.getLogger("ai-companion.processor.digest_run")

# No direct LLM call at this layer — the digest agent's own call sites
# carry their stage= breadcrumbs and are billed there.
_EST_TOKENS_IN = 0
_EST_TOKENS_OUT = 0
_MODEL = "none"


class DigestRunJob(BaseJob):
    """Run one daily-digest generation pass (``persist=True``)."""

    job_type = "digest_run"

    @property
    def priority(self) -> Priority:
        # User-triggered from /digests/run-now — the user is actively
        # polling /digests/latest for the fresh digest.
        return Priority.HIGH

    def estimate_cost(self) -> CostEstimate:
        return CostEstimate(
            estimated_tokens_in=_EST_TOKENS_IN,
            estimated_tokens_out=_EST_TOKENS_OUT,
            model=_MODEL,
            estimated_usd=Decimal("0.00"),
            confidence="high",
        )

    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        # Lazy import: keep job discovery (build_default_registry imports
        # this module at startup) free of the agent wiring.
        from core.agents.daily_digest import generate_daily_digest

        await progress_cb(0.0)
        logger.info("digest_run.start")
        result = await generate_daily_digest(persist=True)
        await progress_cb(1.0)
        logger.info(
            "digest_run.done digest_id=%s artifacts=%d skipped=%s",
            result.digest_id, result.artifact_count, result.skipped,
        )
        return JobResult(
            job_id="",  # filled in by the worker after dequeue
            actual_tokens_in=0,
            actual_tokens_out=0,
            metadata={
                "digest_id": result.digest_id,
                "generated_at": result.generated_at,
                "artifact_count": result.artifact_count,
                "flagged_count": result.flagged_count,
                "persisted_artifact_id": result.persisted_artifact_id,
                "skipped": result.skipped,
                "skip_reason": result.skip_reason,
            },
        )


# ── Queue helpers (used by the /digests/run-now endpoint) ────────────────

def active_digest_run_jobs(redis_client: Any | None = None) -> list[str]:
    """Return job ids of queued or running ``digest_run`` jobs.

    Lets the run-now endpoint return the existing job instead of
    double-enqueueing when the trigger fires twice. Reads the processor
    queue's own key layout (pending priority lists + running set) so no
    parallel bookkeeping can drift from the queue.
    """
    from app.db.redis.processor_queue import (  # noqa: PLC0415
        _RUNNING_KEY,
        _job_key,
        _queue_key,
    )
    from core.processor.priority import priority_order  # noqa: PLC0415

    if redis_client is None:
        from app.deps import get_redis  # noqa: PLC0415
        redis_client = get_redis()

    def _s(v: Any) -> str:
        return v.decode() if isinstance(v, bytes) else str(v)

    job_ids: list[str] = []
    for priority in priority_order():
        job_ids.extend(_s(j) for j in redis_client.lrange(_queue_key(priority), 0, -1))
    job_ids.extend(_s(j) for j in redis_client.smembers(_RUNNING_KEY))

    active: list[str] = []
    for job_id in job_ids:
        job_type = redis_client.hget(_job_key(job_id), "job_type")
        if job_type is not None and _s(job_type) == DigestRunJob.job_type:
            active.append(job_id)
    return active


def enqueue_digest_run_job(redis_client: Any | None = None) -> str:
    """Enqueue a :class:`DigestRunJob` and return its job id."""
    from app.db.redis.processor_queue import enqueue_job  # noqa: PLC0415

    return enqueue_job(DigestRunJob(), payload={}, redis_client=redis_client)
