# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Unit tests for WikiRefreshJob (Phase W.1).

All external dependencies (Neo4j, ChromaDB, LLM) are mocked. No live
infrastructure required.

Coverage:
- Class attribute assertions (job_type, priority)
- estimate_cost() returns Ollama-priced CostEstimate
- run() with mocked _run_pipeline completes; metadata reflects synthesis
- run() with skipped pipeline (entity not found) still completes
- Exception propagation from _run_pipeline
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.processor.jobs.wiki_refresh import WikiRefreshJob
from core.processor.cost import CostEstimate
from core.processor.job import JobResult
from core.processor.priority import Priority

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop_progress(pct: float) -> None:  # noqa: ARG001
    pass


def _make_job(slug: str = "person:elon-musk") -> WikiRefreshJob:
    return WikiRefreshJob(entity_slug=slug)


# ---------------------------------------------------------------------------
# Class-level attributes
# ---------------------------------------------------------------------------


class TestWikiRefreshJobAttributes:
    def test_job_type(self):
        assert WikiRefreshJob.job_type == "wiki_refresh"

    def test_priority_is_low(self):
        job = _make_job()
        assert job.priority == Priority.LOW

    def test_instantiates_with_valid_slug(self):
        job = WikiRefreshJob(entity_slug="org:tesla")
        assert job._entity_slug == "org:tesla"


# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------


class TestEstimateCost:
    def test_returns_cost_estimate_instance(self):
        job = _make_job()
        result = job.estimate_cost()
        assert isinstance(result, CostEstimate)

    def test_model_is_ollama_local(self):
        job = _make_job()
        assert job.estimate_cost().model == "ollama/local"

    def test_cost_is_zero_decimal(self):
        job = _make_job()
        estimate = job.estimate_cost()
        assert estimate.estimated_usd == Decimal("0.00")

    def test_token_estimates_are_positive(self):
        job = _make_job()
        estimate = job.estimate_cost()
        assert estimate.estimated_tokens_in > 0
        assert estimate.estimated_tokens_out > 0

    def test_confidence_is_medium(self):
        job = _make_job()
        assert job.estimate_cost().confidence == "medium"


# ---------------------------------------------------------------------------
# run() — success path (via mocked _run_pipeline)
# ---------------------------------------------------------------------------


class TestRunSuccess:
    @pytest.mark.asyncio
    async def test_run_completes_with_job_result(self):
        job = _make_job()
        pipeline_stats = {
            "summary_chars": 450,
            "artifacts_used": 3,
            "tokens_in": 3000,
            "tokens_out": 800,
        }
        with patch.object(job, "_run_pipeline", new=AsyncMock(return_value=pipeline_stats)):
            result = await job.run(_noop_progress)

        assert isinstance(result, JobResult)

    @pytest.mark.asyncio
    async def test_metadata_reflects_synthesis(self):
        job = _make_job("person:elon-musk")
        pipeline_stats = {
            "summary_chars": 512,
            "artifacts_used": 4,
            "tokens_in": 3000,
            "tokens_out": 800,
        }
        with patch.object(job, "_run_pipeline", new=AsyncMock(return_value=pipeline_stats)):
            result = await job.run(_noop_progress)

        assert result.metadata["entity_slug"] == "person:elon-musk"
        assert result.metadata["summary_chars"] == 512
        assert result.metadata["artifacts_used"] == 4

    @pytest.mark.asyncio
    async def test_skipped_path_no_exception(self):
        """When the pipeline returns a skip reason, run() still returns a JobResult."""
        job = _make_job()
        with patch.object(
            job, "_run_pipeline", new=AsyncMock(return_value={"skipped": "entity_not_found"})
        ):
            result = await job.run(_noop_progress)

        assert isinstance(result, JobResult)
        assert result.metadata.get("skipped") == "entity_not_found"

    @pytest.mark.asyncio
    async def test_progress_callback_is_called(self):
        job = _make_job()
        progress_calls: list[float] = []

        async def _capture_progress(pct: float) -> None:
            progress_calls.append(pct)

        with patch.object(
            job, "_run_pipeline", new=AsyncMock(return_value={"summary_chars": 100, "artifacts_used": 1})
        ):
            await job.run(_capture_progress)

        # At minimum the 0.0 progress call must happen before _run_pipeline
        assert 0.0 in progress_calls


# ---------------------------------------------------------------------------
# run() — exception propagation
# ---------------------------------------------------------------------------


class TestRunExceptionPropagation:
    @pytest.mark.asyncio
    async def test_exception_from_pipeline_is_reraised(self):
        job = _make_job()
        with patch.object(
            job, "_run_pipeline", new=AsyncMock(side_effect=RuntimeError("neo4j down"))
        ):
            with pytest.raises(RuntimeError, match="neo4j down"):
                await job.run(_noop_progress)

    @pytest.mark.asyncio
    async def test_exception_is_logged_before_reraise(self):
        job = _make_job()
        with (
            patch.object(
                job, "_run_pipeline", new=AsyncMock(side_effect=ValueError("bad state"))
            ),
            patch("app.processor.jobs.wiki_refresh.log_swallowed_error") as mock_log,
        ):
            with pytest.raises(ValueError):
                await job.run(_noop_progress)

        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args
        assert "processor.wiki_refresh" in call_kwargs[0]


# ---------------------------------------------------------------------------
# API.3 enrichment integration
# ---------------------------------------------------------------------------


class TestWikiRefreshJobEnrichment:
    """Tests that confirm the enrichment step is correctly gated + integrated."""

    @pytest.mark.asyncio
    async def test_enrichment_disabled_skips_enrich(self, monkeypatch):
        """When WIKI_ENRICHMENT_ENABLED=false, enrich() is never called."""
        monkeypatch.setenv("WIKI_ENRICHMENT_ENABLED", "false")

        job = _make_job()
        pipeline_stats = {
            "summary_chars": 400,
            "artifacts_used": 2,
            "tokens_in": 3000,
            "tokens_out": 800,
        }

        with (
            patch.object(job, "_run_pipeline", new=AsyncMock(return_value=pipeline_stats)),
        ):
            result = await job.run(_noop_progress)

        # Job should still succeed
        assert isinstance(result, JobResult)
        # external_refs_count should be absent or 0
        assert result.metadata.get("external_refs_count", 0) == 0

    @pytest.mark.asyncio
    async def test_enrichment_enabled_refs_appear_in_metadata(self, monkeypatch):
        """When enrichment is enabled and _run_pipeline returns refs, metadata reflects count."""
        monkeypatch.setenv("WIKI_ENRICHMENT_ENABLED", "true")

        job = _make_job("person:alan-turing")

        pipeline_stats_with_refs = {
            "summary_chars": 200,
            "artifacts_used": 1,
            "tokens_in": 3000,
            "tokens_out": 800,
            "external_refs_count": 2,
        }
        with patch.object(job, "_run_pipeline", new=AsyncMock(return_value=pipeline_stats_with_refs)):
            result = await job.run(_noop_progress)

        assert result.metadata["external_refs_count"] == 2
        assert isinstance(result, JobResult)

    @pytest.mark.asyncio
    async def test_enrichment_off_produces_zero_refs_in_metadata(self, monkeypatch):
        """WIKI_ENRICHMENT_ENABLED=false → metadata.external_refs_count == 0."""
        monkeypatch.setenv("WIKI_ENRICHMENT_ENABLED", "0")

        job = _make_job()
        pipeline_stats = {
            "summary_chars": 300,
            "artifacts_used": 2,
            "tokens_in": 3000,
            "tokens_out": 800,
            # no external_refs_count key — simulates the disabled path
        }
        with patch.object(job, "_run_pipeline", new=AsyncMock(return_value=pipeline_stats)):
            result = await job.run(_noop_progress)

        assert result.metadata.get("external_refs_count", 0) == 0




# ---------------------------------------------------------------------------
# _run_pipeline — junk-entity gate (2026-07-13)
# ---------------------------------------------------------------------------


class TestJunkEntityGate:
    """Junk-named entities are skipped at the pipeline choke point, before
    the LLM summary — this covers every producer (ingest hook, nightly
    stale sweep, manual enqueue) without touching the producers."""

    @pytest.mark.asyncio
    async def test_pipeline_skips_junk_named_entity_before_llm(self):
        from unittest.mock import MagicMock

        job = _make_job(slug="other:library-email-charset-html")
        junk_entity = {
            "name": "library/email.charset.html",
            "entity_type": "OTHER",
            "source_artifacts": [{"artifact_id": "a1"}],
        }
        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch("app.deps.get_chroma", return_value=MagicMock()),
            patch("app.db.neo4j.wiki.get_entity", return_value=junk_entity),
            patch(
                "core.utils.internal_llm.call_internal_llm", new=AsyncMock()
            ) as mock_llm,
        ):
            stats = await job._run_pipeline(_noop_progress)

        assert stats == {"skipped": "junk_entity_name"}
        mock_llm.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pipeline_proceeds_for_valid_entity_name(self):
        from unittest.mock import MagicMock

        job = _make_job(slug="org:nasa")
        valid_entity = {
            "name": "NASA",
            "entity_type": "ORG",
            "source_artifacts": [],  # forces the next skip branch
        }
        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch("app.deps.get_chroma", return_value=MagicMock()),
            patch("app.db.neo4j.wiki.get_entity", return_value=valid_entity),
        ):
            stats = await job._run_pipeline(_noop_progress)

        # Passes the junk gate and reaches the no-artifacts skip instead.
        assert stats == {"skipped": "no_source_artifacts"}


# ---------------------------------------------------------------------------
# _run_pipeline — refusing to store a summary that denies its own subject
# ---------------------------------------------------------------------------

class TestInsufficientExcerptsAreNotStored:
    """A summary that opens by denying its subject must never be written.

    The compiler asked for a summary from excerpts that only mention the entity
    in passing, and the model obliged with fluent prose about the absence:
    "Apple Inc. is not mentioned in the provided excerpts. However, the excerpts
    do discuss Kubernetes...". Stored, that page is then served as
    high-priority grounding on the answer path, so the reader is handed a
    paragraph denying the thing it was asked about. On the live corpus these
    are 1.1% of summarised entities but 27% of the thirty most-mentioned.

    These tests drive the real ``_run_pipeline`` and patch only its boundaries
    (Neo4j reads/writes, Chroma, the LLM), so the skip decision under test is
    production code rather than a mocked stand-in.
    """

    @staticmethod
    def _pipeline_with(llm_reply: str):
        """Run the real pipeline against a fixed LLM reply; report the write."""
        from app.processor.jobs.wiki_refresh import WikiRefreshJob

        job = WikiRefreshJob("org:acme")
        writes: list[tuple] = []

        async def _noop_progress(_pct):
            return None

        entity = {
            "name": "Acme Corp",
            "entity_type": "ORG",
            "source_artifacts": [{"artifact_id": "art-1"}],
        }
        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch("app.deps.get_chroma", return_value=MagicMock()),
            patch("app.db.neo4j.wiki.get_entity", return_value=entity),
            patch("app.db.neo4j.wiki.write_entity_summary",
                  side_effect=lambda *a, **k: writes.append(a)),
            patch.object(WikiRefreshJob, "_fetch_entity_chunks",
                         return_value=["Some excerpt text about other things."]),
            patch("core.utils.internal_llm.call_internal_llm",
                  new=AsyncMock(return_value=llm_reply)),
        ):
            result = asyncio.run(job._run_pipeline(_noop_progress))
        return result, writes

    def test_sentinel_reply_skips_the_write(self):
        result, writes = self._pipeline_with("INSUFFICIENT_EXCERPTS")
        assert result == {"skipped": "insufficient_excerpts"}
        assert writes == [], "no summary may be written for absent excerpts"

    def test_disclaimer_prose_skips_the_write(self):
        """The sentinel alone is not enough — an 8B model ignores it often.

        This is the shape actually observed in the live corpus, verbatim.
        """
        result, writes = self._pipeline_with(
            "Apple Inc. is not mentioned in the provided excerpts. However, "
            "the excerpts do discuss Kubernetes API versioning and its "
            "deprecation policy across releases."
        )
        assert result == {"skipped": "insufficient_excerpts"}
        assert writes == [], "disclaimer prose must not be stored as a summary"

    def test_a_real_summary_is_still_written(self):
        """Regression guard: the ordinary path must be untouched."""
        summary = (
            "Acme Corp is a manufacturing company described in the corpus as a "
            "supplier of industrial fasteners. It is associated with two "
            "procurement contracts referenced across the excerpts."
        )
        result, writes = self._pipeline_with(summary)
        assert result.get("skipped") is None, f"unexpected skip: {result}"
        assert len(writes) == 1, "a substantive summary must be written"
        assert writes[0][2] == summary

    def test_a_summary_that_scopes_itself_is_not_rejected(self):
        """A good summary may still note a limit — that is honest scoping.

        The boundary that keeps the check from eating real pages: the
        disclaimer shape always LEADS, so only the opening is inspected.
        """
        summary = (
            "Kubernetes is an API-driven container orchestration system, "
            "described in the corpus through its versioning and deprecation "
            "policy. The excerpts do not contain information about its "
            "release cadence."
        )
        result, writes = self._pipeline_with(summary)
        assert result.get("skipped") is None, f"unexpected skip: {result}"
        assert len(writes) == 1
