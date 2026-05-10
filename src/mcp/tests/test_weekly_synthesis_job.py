# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for WeeklySynthesisJob.

All external dependencies (Neo4j, BriefService, contradiction_log, LLM)
are mocked via the module-level factory ``_get_brief_service`` in
``app.processor.jobs.weekly_synthesis`` and via patches on
``app.services.contradiction_log.list_recent``. No real infrastructure
is required.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.processor.jobs.weekly_synthesis import WeeklySynthesisJob
from core.processor.cost import CostEstimate
from core.processor.job import JobResult
from core.processor.priority import Priority

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop_progress(pct: float) -> None:  # noqa: ARG001
    pass


def _make_job(week_ending: str = "2026-05-11") -> WeeklySynthesisJob:
    return WeeklySynthesisJob(week_ending=week_ending)


def _make_brief_record(brief_id: str = "wk-001", status: str = "generated") -> MagicMock:
    record = MagicMock()
    record.brief_id = brief_id
    record.status = status
    return record


def _make_contradiction(severity: str = "high") -> MagicMock:
    finding = MagicMock()
    finding.severity = severity
    finding.claim_a_text = "Claim A text"
    finding.claim_b_text = "Claim B text"
    return finding


# ---------------------------------------------------------------------------
# Class-level attributes
# ---------------------------------------------------------------------------


class TestWeeklySynthesisJobAttributes:
    def test_job_type(self):
        assert WeeklySynthesisJob.job_type == "weekly_synthesis"

    def test_priority_is_low(self):
        assert _make_job().priority == Priority.LOW

    def test_instantiates_with_week_ending(self):
        job = WeeklySynthesisJob(week_ending="2026-01-13")
        assert job._week_ending == "2026-01-13"


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

    def test_token_estimates_larger_than_daily(self):
        # Weekly synthesis processes full vault: 12000 in vs daily 6000
        est = _make_job().estimate_cost()
        assert est.estimated_tokens_in >= 12_000
        assert est.estimated_tokens_out >= 2_500

    def test_confidence_is_medium(self):
        assert _make_job().estimate_cost().confidence == "medium"


# ---------------------------------------------------------------------------
# run() — success path
# ---------------------------------------------------------------------------


class TestRunSuccess:
    async def test_run_returns_job_result(self):
        job = _make_job()
        record = _make_brief_record()

        with _patch_pipeline(record, contradictions_count=2):
            result = await job.run(_noop_progress)

        assert isinstance(result, JobResult)
        assert result.metadata["brief_id"] == "wk-001"
        assert result.metadata["status"] == "generated"
        assert result.metadata["week_ending"] == "2026-05-11"
        assert result.metadata["contradictions_included"] == 2

    async def test_run_fires_progress_callbacks(self):
        job = _make_job()
        record = _make_brief_record()
        progress_calls: list[float] = []

        async def record_progress(pct: float) -> None:
            progress_calls.append(pct)

        with _patch_pipeline(record, contradictions_count=1):
            await job.run(record_progress)

        assert 0.0 in progress_calls
        assert 1.0 in progress_calls
        # Weekly spec: checkpoints 0.2, 0.4, 0.7 in addition to 0.0 and 1.0
        assert any(p == pytest.approx(0.2) for p in progress_calls)
        assert any(p == pytest.approx(0.4) for p in progress_calls)
        assert any(p == pytest.approx(0.7) for p in progress_calls)
        # Non-decreasing
        for a, b in zip(progress_calls, progress_calls[1:]):
            assert b >= a

    async def test_contradictions_passed_to_generate_weekly(self):
        """Contradictions from the last 7 days must be forwarded to LLM call."""
        job = _make_job()
        record = _make_brief_record()
        contradiction = _make_contradiction(severity="high")

        mock_service = AsyncMock()
        mock_service.generate_weekly.return_value = record
        mock_service.store.return_value = None

        with (
            _patch_service_factory(mock_service),
            _patch_neo4j_and_snapshot(),
            _patch_contradiction_log([contradiction]),
        ):
            await job.run(_noop_progress)

        mock_service.generate_weekly.assert_awaited_once()
        call_kwargs = mock_service.generate_weekly.call_args
        # Second positional arg is contradiction_log_recent
        contradiction_text = call_kwargs[0][1]
        assert "HIGH" in contradiction_text
        assert "Claim A text" in contradiction_text

    async def test_empty_contradictions_handled(self):
        """Zero contradictions must not raise and must produce an empty string."""
        job = _make_job()
        record = _make_brief_record()

        mock_service = AsyncMock()
        mock_service.generate_weekly.return_value = record
        mock_service.store.return_value = None

        with (
            _patch_service_factory(mock_service),
            _patch_neo4j_and_snapshot(),
            _patch_contradiction_log([]),
        ):
            result = await job.run(_noop_progress)

        assert result.metadata["contradictions_included"] == 0
        call_kwargs = mock_service.generate_weekly.call_args
        contradiction_text = call_kwargs[0][1]
        assert contradiction_text == ""

    async def test_run_calls_store_with_record(self):
        job = _make_job()
        record = _make_brief_record()

        mock_service = AsyncMock()
        mock_service.generate_weekly.return_value = record
        mock_service.store.return_value = None

        with (
            _patch_service_factory(mock_service),
            _patch_neo4j_and_snapshot(),
            _patch_contradiction_log([]),
        ):
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

        with _patch_pipeline_raising(RuntimeError("llm down")):
            with pytest.raises(RuntimeError, match="llm down"):
                await job.run(_noop_progress)

    async def test_run_logs_swallowed_error_before_reraise(self):
        """log_swallowed_error is called for observability even on reraise."""
        job = _make_job()

        with _patch_pipeline_raising(ValueError("neo4j connection reset")):
            with patch(
                "app.processor.jobs.weekly_synthesis.log_swallowed_error"
            ) as mock_log:
                with pytest.raises(ValueError):
                    await job.run(_noop_progress)
                mock_log.assert_called_once()


# ---------------------------------------------------------------------------
# Patch helpers
# ---------------------------------------------------------------------------


def _patch_pipeline(record: MagicMock, *, contradictions_count: int = 0):
    """Patch _run_pipeline to return a successful JobResult without real I/O."""
    from contextlib import ExitStack

    async def _fake_pipeline(self, progress_cb):
        await progress_cb(0.0)
        await progress_cb(0.2)
        await progress_cb(0.4)
        await progress_cb(0.7)
        await progress_cb(1.0)
        return JobResult(
            job_id="",
            actual_tokens_in=12_000,
            actual_tokens_out=2_500,
            metadata={
                "week_ending": self._week_ending,
                "brief_id": record.brief_id,
                "status": record.status,
                "contradictions_included": contradictions_count,
            },
        )

    stack = ExitStack()
    stack.enter_context(
        patch(
            "app.processor.jobs.weekly_synthesis.WeeklySynthesisJob._run_pipeline",
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
            "app.processor.jobs.weekly_synthesis.WeeklySynthesisJob._run_pipeline",
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
            "app.processor.jobs.weekly_synthesis._get_brief_service",
            return_value=mock_service,
        )
    )
    return stack


def _patch_neo4j_and_snapshot():
    """Patch _get_neo4j and the vault snapshot helper to avoid real DB calls."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch("app.processor.jobs.weekly_synthesis._get_neo4j", return_value=MagicMock())
    )
    stack.enter_context(
        patch(
            "app.processor.jobs.weekly_synthesis._build_vault_snapshot",
            return_value="vault snapshot text",
        )
    )
    return stack


def _patch_contradiction_log(findings: list):
    """Patch contradiction_log.list_recent to return the given findings."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch(
            "app.services.contradiction_log.list_recent",
            new=AsyncMock(return_value=findings),
        )
    )
    return stack
