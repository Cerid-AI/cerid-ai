# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""IngestRecoveryJob — background job that heals orphaned pending chunks.

Phase O.1 of v0.92.  Wraps the recovery service in
``app/services/ingest_recovery.py`` as a ``BaseJob`` so it is scheduled
by the Background Processor (Phase P.1) with proper priority, cost
tracking, and progress callbacks.

Registered in ``app/processor/jobs/__init__.py`` so
``build_default_registry()`` discovers it automatically.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from core.processor.cost import CostEstimate
from core.processor.job import BaseJob, JobResult, ProgressCallback
from core.processor.priority import Priority
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.ingest_recovery")

# No LLM call — zero marginal cost.  The CPU work is negligible (one
# Chroma list + N small updates).
_EST_TOKENS_IN = 0
_EST_TOKENS_OUT = 0
_MODEL = "none"


class IngestRecoveryJob(BaseJob):
    """Scan Chroma for stale pending chunks and roll them forward or purge.

    This job should run frequently (default: every 60 s via apscheduler
    cron) to keep the window between a Neo4j failure and orphan cleanup
    short.  Because it runs at ``LOW`` priority it will not compete with
    user-triggered or latency-sensitive jobs.

    Parameters
    ----------
    max_age_seconds
        Minimum age in seconds before a ``pending`` chunk is considered
        an orphan.  Defaults to 60 to give the primary path a grace
        window before recovery interferes.
    """

    job_type = "ingest_recovery"

    def __init__(self, max_age_seconds: float = 60.0) -> None:
        self._max_age_seconds = max_age_seconds

    @property
    def priority(self) -> Priority:
        return Priority.LOW

    def estimate_cost(self) -> CostEstimate:
        """Zero USD — recovery runs entirely on local Chroma + Neo4j, no LLM."""
        return CostEstimate(
            estimated_tokens_in=_EST_TOKENS_IN,
            estimated_tokens_out=_EST_TOKENS_OUT,
            model=_MODEL,
            estimated_usd=Decimal("0.00"),
            confidence="high",
        )

    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        """Execute one recovery tick.

        Progress checkpoints
        --------------------
        0.0  — job started
        0.5  — scan complete
        1.0  — all orphans processed

        Metadata in JobResult
        ---------------------
        orphans_found   — number of stale pending chunks discovered
        committed       — successfully rolled forward to Neo4j
        deferred        — retried but budget not yet exhausted
        purged          — budget exhausted; chunk removed
        errors          — unexpected exceptions (logged; not re-raised)
        """
        await progress_cb(0.0)
        logger.info("ingest_recovery.start max_age_seconds=%s", self._max_age_seconds)

        try:
            stats = await self._run_recovery()
        except Exception as exc:
            log_swallowed_error(
                "processor.ingest_recovery",
                exc,
                context={"max_age_seconds": self._max_age_seconds},
            )
            raise

        await progress_cb(1.0)
        logger.info(
            "ingest_recovery.done orphans=%d committed=%d deferred=%d purged=%d errors=%d",
            stats["orphans_found"],
            stats["committed"],
            stats["deferred"],
            stats["purged"],
            stats["errors"],
        )
        return JobResult(
            job_id="",  # filled in by the worker after dequeue
            actual_tokens_in=0,
            actual_tokens_out=0,
            metadata=stats,
        )

    async def _run_recovery(self) -> dict:
        """Inner pipeline — separated for readability."""
        # Lazy import: avoids circular module load at app startup.  The
        # scheduler registers this job class before the app/services tree
        # is fully wired, so top-level imports from app.services would
        # race the startup lifecycle.
        from app.services.ingest_recovery import (
            RecoveryAction,
            group_orphans_by_artifact,
            recover_artifact,
            scan_orphans,
        )

        orphans = await scan_orphans(max_age_seconds=self._max_age_seconds)
        # AF-003: recover per ARTIFACT, not per chunk — one create_artifact call
        # with the real chunk_count=N so multi-chunk artifacts don't collapse to
        # chunk_count=1. committed/deferred/purged therefore count artifacts.
        groups = group_orphans_by_artifact(orphans)
        stats: dict = {
            "orphans_found": len(orphans),
            "artifacts_found": len(groups),
            "committed": 0,
            "deferred": 0,
            "purged": 0,
            "errors": 0,
        }
        if not orphans:
            return stats

        for artifact_id, group in groups.items():
            try:
                action = await recover_artifact(group)
                if action == RecoveryAction.COMMITTED:
                    stats["committed"] += 1
                elif action == RecoveryAction.DEFERRED:
                    stats["deferred"] += 1
                elif action == RecoveryAction.PURGED:
                    stats["purged"] += 1
            except Exception as exc:  # noqa: BLE001 — observability boundary
                log_swallowed_error(
                    "processor.ingest_recovery.recover_artifact",
                    exc,
                    context={"artifact_id": artifact_id},
                )
                stats["errors"] += 1

        return stats
