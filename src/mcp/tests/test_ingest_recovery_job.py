# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Unit tests for IngestRecoveryJob (app/processor/jobs/ingest_recovery.py).

Covers:
- Class attribute assertions
- estimate_cost() contract
- run() with mocked recovery service
- Exception propagation
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.processor.jobs.ingest_recovery import IngestRecoveryJob
from core.processor.job import BaseJob, JobResult
from core.processor.priority import Priority

# ---------------------------------------------------------------------------
# Class attribute tests
# ---------------------------------------------------------------------------

class TestIngestRecoveryJobAttrs:
    """Verify static class-level contracts."""

    def test_job_type(self):
        assert IngestRecoveryJob.job_type == "ingest_recovery"

    def test_is_base_job_subclass(self):
        assert issubclass(IngestRecoveryJob, BaseJob)

    def test_priority_is_low(self):
        job = IngestRecoveryJob()
        assert job.priority == Priority.LOW

    def test_default_max_age(self):
        job = IngestRecoveryJob()
        assert job._max_age_seconds == 60.0

    def test_custom_max_age(self):
        job = IngestRecoveryJob(max_age_seconds=120.0)
        assert job._max_age_seconds == 120.0


# ---------------------------------------------------------------------------
# estimate_cost tests
# ---------------------------------------------------------------------------

class TestEstimateCost:
    """estimate_cost should return zero-cost estimate (no LLM)."""

    def test_returns_cost_estimate(self):
        from core.processor.cost import CostEstimate
        job = IngestRecoveryJob()
        cost = job.estimate_cost()
        assert isinstance(cost, CostEstimate)

    def test_zero_tokens(self):
        job = IngestRecoveryJob()
        cost = job.estimate_cost()
        assert cost.estimated_tokens_in == 0
        assert cost.estimated_tokens_out == 0

    def test_zero_usd(self):
        job = IngestRecoveryJob()
        cost = job.estimate_cost()
        assert cost.estimated_usd == Decimal("0.00")

    def test_model_is_none_sentinel(self):
        job = IngestRecoveryJob()
        cost = job.estimate_cost()
        assert cost.model == "none"

    def test_confidence_is_high(self):
        job = IngestRecoveryJob()
        cost = job.estimate_cost()
        assert cost.confidence == "high"


# ---------------------------------------------------------------------------
# run() tests
# ---------------------------------------------------------------------------

class TestRun:
    """run() calls scan_orphans + recover_artifact and returns JobResult."""

    @pytest.mark.asyncio
    async def test_run_no_orphans(self):
        """run() succeeds with empty orphan list."""
        job = IngestRecoveryJob()
        progress_calls: list[float] = []

        async def _progress(pct: float) -> None:
            progress_calls.append(pct)

        with (
            patch(
                "app.services.ingest_recovery.scan_orphans",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.services.ingest_recovery.recover_artifact", new_callable=AsyncMock),
        ):
            result = await job.run(_progress)

        assert isinstance(result, JobResult)
        assert result.actual_tokens_in == 0
        assert result.actual_tokens_out == 0
        assert result.metadata["orphans_found"] == 0
        assert 0.0 in progress_calls
        assert 1.0 in progress_calls

    @pytest.mark.asyncio
    async def test_run_with_orphans_committed(self):
        """run() groups orphans by artifact and calls recover_artifact per group, counting committed."""
        from app.services.ingest_recovery import OrphanRecord, RecoveryAction

        job = IngestRecoveryJob()

        old_ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=120)).isoformat()
        orphan = OrphanRecord(
            chunk_id="c1",
            artifact_id="art1",
            domain="coding",
            collection_name="coll-coding",
            idempotency_key="abc",
            pending_at=old_ts,
            document="doc",
            metadata={},
            retry_count=0,
        )

        async def _progress(pct: float) -> None:
            pass

        with (
            patch(
                "app.services.ingest_recovery.scan_orphans",
                new_callable=AsyncMock,
                return_value=[orphan],
            ),
            patch(
                "app.services.ingest_recovery.recover_artifact",
                new_callable=AsyncMock,
                return_value=RecoveryAction.COMMITTED,
            ),
        ):
            result = await job.run(_progress)

        assert result.metadata["orphans_found"] == 1
        assert result.metadata["artifacts_found"] == 1
        assert result.metadata["committed"] == 1
        assert result.metadata["purged"] == 0
        assert result.metadata["deferred"] == 0
        assert result.metadata["errors"] == 0

    @pytest.mark.asyncio
    async def test_run_exception_propagates(self):
        """run() re-raises unexpected exceptions after logging."""
        job = IngestRecoveryJob()

        async def _progress(pct: float) -> None:
            pass

        with patch(
            "app.services.ingest_recovery.scan_orphans",
            new_callable=AsyncMock,
            side_effect=RuntimeError("chroma offline"),
        ):
            with pytest.raises(RuntimeError, match="chroma offline"):
                await job.run(_progress)

    @pytest.mark.asyncio
    async def test_run_individual_orphan_exception_counted_not_propagated(self):
        """Exceptions from individual recover_artifact calls are counted, not re-raised."""
        from app.services.ingest_recovery import OrphanRecord

        job = IngestRecoveryJob()

        old_ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=120)).isoformat()
        orphan = OrphanRecord(
            chunk_id="c-bad",
            artifact_id="art-bad",
            domain="coding",
            collection_name="coll",
            idempotency_key="z",
            pending_at=old_ts,
            document="d",
            metadata={},
            retry_count=0,
        )

        async def _progress(pct: float) -> None:
            pass

        with (
            patch(
                "app.services.ingest_recovery.scan_orphans",
                new_callable=AsyncMock,
                return_value=[orphan],
            ),
            patch(
                "app.services.ingest_recovery.recover_artifact",
                new_callable=AsyncMock,
                side_effect=RuntimeError("unexpected"),
            ),
        ):
            result = await job.run(_progress)

        assert result.metadata["errors"] == 1
        assert result.metadata["committed"] == 0

    @pytest.mark.asyncio
    async def test_new_record_sets_low_priority(self):
        """new_record() returns a JobRecord with LOW priority."""
        from core.processor.job import JobRecord

        job = IngestRecoveryJob()
        record = job.new_record()
        assert isinstance(record, JobRecord)
        assert record.priority == Priority.LOW
        assert record.job_type == "ingest_recovery"

    def test_discovered_by_registry(self):
        """build_default_registry() includes ingest_recovery."""
        from app.processor.worker import build_default_registry
        registry = build_default_registry()
        assert "ingest_recovery" in registry
        assert registry["ingest_recovery"] is IngestRecoveryJob
