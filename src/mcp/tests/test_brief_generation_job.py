# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for BriefGenerationJob.

All external dependencies (Neo4j, BriefService, LLM) are mocked via the
module-level factory ``_get_brief_service`` in
``app.processor.jobs.brief_generation``. No real infrastructure is required.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.processor.jobs.brief_generation import BriefGenerationJob
from core.processor.cost import CostEstimate
from core.processor.job import JobResult
from core.processor.priority import Priority

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop_progress(pct: float) -> None:  # noqa: ARG001
    pass


def _make_job(target_date: str = "2026-05-10") -> BriefGenerationJob:
    return BriefGenerationJob(target_date=target_date)


def _make_brief_record(brief_id: str = "br-001", status: str = "generated") -> MagicMock:
    record = MagicMock()
    record.brief_id = brief_id
    record.status = status
    return record


# ---------------------------------------------------------------------------
# Class-level attributes
# ---------------------------------------------------------------------------


class TestBriefGenerationJobAttributes:
    def test_job_type(self):
        assert BriefGenerationJob.job_type == "brief_generation"

    def test_priority_is_low(self):
        job = _make_job()
        assert job.priority == Priority.LOW

    def test_instantiates_with_target_date(self):
        job = BriefGenerationJob(target_date="2026-01-15")
        assert job._target_date == "2026-01-15"


# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------


class TestEstimateCost:
    def test_returns_cost_estimate_instance(self):
        assert isinstance(_make_job().estimate_cost(), CostEstimate)

    def test_model_is_ollama_local(self):
        assert _make_job().estimate_cost().model == "ollama/local"

    def test_cost_is_zero(self):
        assert _make_job().estimate_cost().estimated_usd == Decimal("0.00")

    def test_token_estimates_are_positive(self):
        est = _make_job().estimate_cost()
        assert est.estimated_tokens_in > 0
        assert est.estimated_tokens_out > 0

    def test_tokens_in_greater_than_entity_extraction(self):
        # Brief is a larger context than entity extraction (6000 vs 2500)
        est = _make_job().estimate_cost()
        assert est.estimated_tokens_in >= 6_000

    def test_confidence_is_medium(self):
        assert _make_job().estimate_cost().confidence == "medium"


# ---------------------------------------------------------------------------
# run() — success path
# ---------------------------------------------------------------------------


class TestRunSuccess:
    async def test_run_returns_job_result(self):
        job = _make_job()
        record = _make_brief_record()

        with _patch_pipeline(record):
            result = await job.run(_noop_progress)

        assert isinstance(result, JobResult)
        assert result.metadata["brief_id"] == "br-001"
        assert result.metadata["status"] == "generated"
        assert result.metadata["target_date"] == "2026-05-10"

    async def test_run_fires_progress_callbacks(self):
        job = _make_job()
        record = _make_brief_record()
        progress_calls: list[float] = []

        async def record_progress(pct: float) -> None:
            progress_calls.append(pct)

        with _patch_pipeline(record):
            await job.run(record_progress)

        assert 0.0 in progress_calls
        assert 1.0 in progress_calls
        # Must include checkpoints 0.3 and 0.7 per spec
        assert any(p == pytest.approx(0.3) for p in progress_calls)
        assert any(p == pytest.approx(0.7) for p in progress_calls)
        # Non-decreasing
        for a, b in zip(progress_calls, progress_calls[1:]):
            assert b >= a

    async def test_run_calls_generate_daily(self):
        job = _make_job()
        record = _make_brief_record()

        mock_service = AsyncMock()
        mock_service.generate_daily.return_value = record
        mock_service.store.return_value = None

        with _patch_service_factory(mock_service), _patch_neo4j_and_corpus():
            await job.run(_noop_progress)

        mock_service.generate_daily.assert_awaited_once()

    async def test_run_calls_store_with_record(self):
        job = _make_job()
        record = _make_brief_record()

        mock_service = AsyncMock()
        mock_service.generate_daily.return_value = record
        mock_service.store.return_value = None

        with _patch_service_factory(mock_service), _patch_neo4j_and_corpus():
            await job.run(_noop_progress)

        mock_service.store.assert_awaited_once()
        args = mock_service.store.call_args[0]
        assert args[0] is record


# ---------------------------------------------------------------------------
# run() — failure path
# ---------------------------------------------------------------------------


class TestRunFailure:
    async def test_run_propagates_exception(self):
        """Job must NOT swallow exceptions — worker handles retries."""
        job = _make_job()

        with _patch_pipeline_raising(RuntimeError("neo4j down")):
            with pytest.raises(RuntimeError, match="neo4j down"):
                await job.run(_noop_progress)

    async def test_run_logs_swallowed_error_before_reraise(self):
        """log_swallowed_error is called for observability even on reraise."""
        job = _make_job()

        with _patch_pipeline_raising(ValueError("llm failed")):
            with patch(
                "app.processor.jobs.brief_generation.log_swallowed_error"
            ) as mock_log:
                with pytest.raises(ValueError):
                    await job.run(_noop_progress)
                mock_log.assert_called_once()


# ---------------------------------------------------------------------------
# Patch helpers
# ---------------------------------------------------------------------------


def _patch_pipeline(record: MagicMock):
    """Patch _run_pipeline to return a successful JobResult without real I/O."""
    from contextlib import ExitStack

    async def _fake_pipeline(self, progress_cb):
        await progress_cb(0.0)
        await progress_cb(0.3)
        await progress_cb(0.7)
        await progress_cb(1.0)
        return JobResult(
            job_id="",
            actual_tokens_in=6000,
            actual_tokens_out=1500,
            metadata={
                "target_date": self._target_date,
                "brief_id": record.brief_id,
                "status": record.status,
            },
        )

    stack = ExitStack()
    stack.enter_context(
        patch(
            "app.processor.jobs.brief_generation.BriefGenerationJob._run_pipeline",
            new=_fake_pipeline,
        )
    )
    return stack


def _patch_pipeline_raising(exc: Exception):
    """Patch _run_pipeline to raise immediately after 0.0 progress."""
    from contextlib import ExitStack

    async def _raising(self, progress_cb):
        await progress_cb(0.0)
        raise exc

    stack = ExitStack()
    stack.enter_context(
        patch(
            "app.processor.jobs.brief_generation.BriefGenerationJob._run_pipeline",
            new=_raising,
        )
    )
    return stack


def _patch_service_factory(mock_service: AsyncMock):
    """Patch the module-level factory to return a mock BriefService."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch(
            "app.processor.jobs.brief_generation._get_brief_service",
            return_value=mock_service,
        )
    )
    return stack


def _patch_neo4j_and_corpus():
    """Patch _get_neo4j and the corpus assembly helper to avoid real DB calls."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch("app.processor.jobs.brief_generation._get_neo4j", return_value=MagicMock())
    )
    stack.enter_context(
        patch(
            "app.processor.jobs.brief_generation._assemble_corpus",
            return_value=("inbox text", "notes text"),
        )
    )
    return stack
