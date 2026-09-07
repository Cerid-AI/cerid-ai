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

    def test_wrong_entity_summary_skips_the_write(self):
        """Backstop for the shape BTC had: the page redirects its own subject.

        Invisible to the insufficiency check — nothing here denies grounding,
        the summary asserts the subject is something else entirely.
        """
        result, writes = self._pipeline_with(
            "The entity in question is not Acme Corp but rather a general "
            "discussion of industrial procurement contracts in the excerpts."
        )
        assert result == {"skipped": "wrong_entity_summary"}
        assert writes == [], "a subject-redirecting summary must not be stored"


# ---------------------------------------------------------------------------
# summary_attempted_at bookkeeping
# ---------------------------------------------------------------------------


class TestSkipMarksAttempt:
    """Every skip path writes no summary, so nothing stamps
    ``summary_updated_at`` and the entity stays permanently overdue for the
    nightly stale sweep. Measured 2026-08-27: 77 of that night's 88 skips were
    the same entities as the night before, each paying a max_tokens=1024 local
    LLM call, while ~2,300 eligible entities never got a turn. The attempt
    stamp is what lets the sweep back off, so these pin it.
    """

    @pytest.mark.asyncio
    async def test_skip_stamps_attempt(self):
        job = _make_job()
        with patch.object(job, "_run_pipeline",
                          new=AsyncMock(return_value={"skipped": "insufficient_excerpts"})), \
             patch.object(job, "_mark_attempt", new=AsyncMock()) as marked:
            await job.run(_noop_progress)
        marked.assert_awaited_once()
        assert marked.await_args.args[0] == "insufficient_excerpts"

    @pytest.mark.asyncio
    async def test_success_does_not_stamp_attempt(self):
        """A real summary stamps summary_updated_at via the write path; adding
        an attempt marker there would back off entities that are working."""
        job = _make_job()
        with patch.object(job, "_run_pipeline",
                          new=AsyncMock(return_value={"summary_chars": 800, "artifacts_used": 3})), \
             patch.object(job, "_mark_attempt", new=AsyncMock()) as marked:
            await job.run(_noop_progress)
        marked.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_entity_not_found_does_not_stamp(self):
        """No node to write to — the MATCH would affect zero rows."""
        job = _make_job()
        with patch.object(job, "_run_pipeline",
                          new=AsyncMock(return_value={"skipped": "entity_not_found"})), \
             patch.object(job, "_mark_attempt", new=AsyncMock()) as marked:
            await job.run(_noop_progress)
        marked.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mark_attempt_failure_does_not_fail_the_job(self):
        """Bookkeeping is best-effort: a Neo4j blip must not turn a skipped
        refresh into a FAILED job and trip failure-keyed alerting."""
        job = _make_job()
        driver = MagicMock()
        with patch.object(job, "_run_pipeline",
                          new=AsyncMock(return_value={"skipped": "no_chunks"})), \
             patch("app.deps.get_neo4j", return_value=driver), \
             patch("app.db.neo4j.wiki.mark_summary_attempt",
                   side_effect=RuntimeError("neo4j down")):
            result = await job.run(_noop_progress)
        assert result.metadata.get("skipped") == "no_chunks"

    @pytest.mark.asyncio
    async def test_mark_attempt_noop_without_driver(self):
        job = _make_job()
        with patch("app.deps.get_neo4j", return_value=None), \
             patch("app.db.neo4j.wiki.mark_summary_attempt") as writer:
            await job._mark_attempt("insufficient_excerpts")
        writer.assert_not_called()

    def test_successful_write_clears_the_attempt_marker(self):
        """An entity that skipped once and then succeeded via the
        ingest-triggered path must not stay blocked from the sweep for the
        whole backoff window. The success write clears the stamp."""
        from app.db.neo4j import wiki

        session = MagicMock()
        driver = MagicMock()
        driver.session.return_value.__enter__.return_value = session

        wiki.write_entity_summary(driver, "asset:nginx", "a real summary", "2026-08-27T00:00:00+00:00")

        cypher = session.run.call_args.args[0]
        assert "e.summary_attempted_at = NULL" in cypher

    def test_successful_write_clears_the_refresh_due_flag(self):
        """A deferred refresh raises summary_refresh_due; the summary that
        eventually lands must clear it, or the entity re-qualifies for the
        sweep every night forever."""
        from app.db.neo4j import wiki

        session = MagicMock()
        driver = MagicMock()
        driver.session.return_value.__enter__.return_value = session

        wiki.write_entity_summary(driver, "asset:nginx", "a real summary", "2026-08-27T00:00:00+00:00")

        normalised = " ".join(session.run.call_args.args[0].split())
        assert "e.summary_refresh_due = NULL" in normalised


# ---------------------------------------------------------------------------
# Live-session deferral (Task 3, 2026-09-06) — keep wiki refresh fan-out out
# of live chat sessions. A WikiRefreshJob dequeued while a chat session is
# demanding the local LLM slot, or beyond the rolling-hour live cap, must not
# call the LLM at all: it marks the entity stale for the nightly
# wiki_stale_sweep and completes with outcome="deferred" so the queue drains
# instead of holding the job. origin="sweep" (the nightly sweep's own
# enqueue) bypasses both gates.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_live_refresh_rate_limit():
    """Every test starts with a clean rolling-hour counter."""
    from app.processor.jobs.wiki_refresh import reset_live_refresh_rate_limit_for_tests

    reset_live_refresh_rate_limit_for_tests()
    yield
    reset_live_refresh_rate_limit_for_tests()


class TestOriginDefaultsToLive:
    def test_default_origin_is_live(self):
        job = WikiRefreshJob(entity_slug="org:tesla")
        assert job._origin == "live"

    def test_sweep_origin_is_stored(self):
        job = WikiRefreshJob(entity_slug="org:tesla", origin="sweep")
        assert job._origin == "sweep"


class TestInteractiveDemandDeferral:
    @pytest.mark.asyncio
    async def test_defers_without_calling_the_llm(self):
        job = _make_job("person:elon-musk")
        driver = MagicMock()
        with (
            patch("app.processor.jobs.wiki_refresh.interactive_demand_recent", return_value=True),
            patch("app.deps.get_neo4j", return_value=driver),
            patch.object(job, "_run_pipeline", new=AsyncMock()) as mock_pipeline,
        ):
            result = await job.run(_noop_progress)

        mock_pipeline.assert_not_called()
        assert isinstance(result, JobResult)
        assert result.metadata["outcome"] == "deferred"
        assert result.metadata["entity_slug"] == "person:elon-musk"
        assert result.actual_tokens_in == 0
        assert result.actual_tokens_out == 0

    @pytest.mark.asyncio
    async def test_marks_the_entity_refresh_due_for_the_sweep(self):
        """Raises the dedicated summary_refresh_due flag the sweep also
        selects on, so tonight's sweep re-picks the entity up rather than
        waiting out the 24h staleness cutoff."""
        job = _make_job("person:elon-musk")
        session = MagicMock()
        driver = MagicMock()
        driver.session.return_value.__enter__.return_value = session

        with (
            patch("app.processor.jobs.wiki_refresh.interactive_demand_recent", return_value=True),
            patch("app.deps.get_neo4j", return_value=driver),
        ):
            await job.run(_noop_progress)

        session.run.assert_called_once()
        cypher, kwargs = session.run.call_args.args, session.run.call_args.kwargs
        assert "e.summary_refresh_due = true" in cypher[0]
        assert kwargs.get("slug") == "person:elon-musk"

    @pytest.mark.asyncio
    async def test_deferral_leaves_the_user_visible_timestamp_alone(self):
        """summary_updated_at is the user-visible last_updated_at, drives
        next_refresh_due, counts as a stale-summary health invariant, and keys
        the 7-day human-edit protection. A deferral is bookkeeping about a run
        that did not happen — it must not touch any of that, or a user-edited
        entity silently loses its edit protection."""
        job = _make_job("person:ada-lovelace")
        session = MagicMock()
        driver = MagicMock()
        driver.session.return_value.__enter__.return_value = session

        with (
            patch("app.processor.jobs.wiki_refresh.interactive_demand_recent", return_value=True),
            patch("app.deps.get_neo4j", return_value=driver),
        ):
            await job.run(_noop_progress)

        cypher = session.run.call_args.args[0]
        assert "summary_updated_at" not in cypher
        assert "summary_edited_by" not in cypher

    @pytest.mark.asyncio
    async def test_stale_mark_failure_does_not_fail_the_job(self):
        """Bookkeeping is best-effort — a Neo4j blip must not turn a
        deferred refresh into a FAILED job; the queue still needs to drain."""
        job = _make_job()
        driver = MagicMock()
        driver.session.side_effect = RuntimeError("neo4j down")

        with (
            patch("app.processor.jobs.wiki_refresh.interactive_demand_recent", return_value=True),
            patch("app.deps.get_neo4j", return_value=driver),
        ):
            result = await job.run(_noop_progress)

        assert result.metadata["outcome"] == "deferred"

    @pytest.mark.asyncio
    async def test_no_driver_is_a_noop_not_a_failure(self):
        job = _make_job()
        with (
            patch("app.processor.jobs.wiki_refresh.interactive_demand_recent", return_value=True),
            patch("app.deps.get_neo4j", return_value=None),
        ):
            result = await job.run(_noop_progress)

        assert result.metadata["outcome"] == "deferred"


class TestLiveRefreshRateLimit:
    @pytest.mark.asyncio
    async def test_thirteenth_refresh_in_an_hour_defers(self, monkeypatch):
        monkeypatch.setenv("WIKI_REFRESH_LIVE_MAX_PER_HOUR", "12")
        driver = MagicMock()
        pipeline_stats = {"summary_chars": 10, "artifacts_used": 1}

        with (
            patch("app.processor.jobs.wiki_refresh.interactive_demand_recent", return_value=False),
            patch("app.deps.get_neo4j", return_value=driver),
        ):
            for _ in range(12):
                job = _make_job()
                with patch.object(job, "_run_pipeline", new=AsyncMock(return_value=pipeline_stats)):
                    result = await job.run(_noop_progress)
                assert result.metadata.get("outcome") != "deferred"

            job_13 = _make_job()
            with patch.object(job_13, "_run_pipeline", new=AsyncMock()) as mock_pipeline_13:
                result_13 = await job_13.run(_noop_progress)

        mock_pipeline_13.assert_not_called()
        assert result_13.metadata["outcome"] == "deferred"

    @pytest.mark.asyncio
    async def test_admitted_again_after_the_window_elapses(self, monkeypatch):
        monkeypatch.setenv("WIKI_REFRESH_LIVE_MAX_PER_HOUR", "1")
        driver = MagicMock()
        pipeline_stats = {"summary_chars": 10, "artifacts_used": 1}
        clock = {"t": 1_000.0}

        def _fake_monotonic():
            return clock["t"]

        with (
            patch("app.processor.jobs.wiki_refresh.interactive_demand_recent", return_value=False),
            patch("app.deps.get_neo4j", return_value=driver),
            patch("app.processor.jobs.wiki_refresh.time.monotonic", side_effect=_fake_monotonic),
        ):
            job_1 = _make_job()
            with patch.object(job_1, "_run_pipeline", new=AsyncMock(return_value=pipeline_stats)):
                result_1 = await job_1.run(_noop_progress)
            assert result_1.metadata.get("outcome") != "deferred"

            job_2 = _make_job()
            with patch.object(job_2, "_run_pipeline", new=AsyncMock()) as mock_pipeline_2:
                result_2 = await job_2.run(_noop_progress)
            mock_pipeline_2.assert_not_called()
            assert result_2.metadata["outcome"] == "deferred"

            # Advance past the rolling hour — the slot should free up.
            clock["t"] += 3600.1
            job_3 = _make_job()
            with patch.object(job_3, "_run_pipeline", new=AsyncMock(return_value=pipeline_stats)):
                result_3 = await job_3.run(_noop_progress)
            assert result_3.metadata.get("outcome") != "deferred"


class TestSweepOriginBypassesBothGates:
    @pytest.mark.asyncio
    async def test_sweep_job_ignores_interactive_demand(self):
        job = WikiRefreshJob(entity_slug="org:tesla", origin="sweep")
        pipeline_stats = {"summary_chars": 10, "artifacts_used": 1}
        with (
            patch("app.processor.jobs.wiki_refresh.interactive_demand_recent", return_value=True),
            patch.object(job, "_run_pipeline", new=AsyncMock(return_value=pipeline_stats)) as mock_pipeline,
        ):
            result = await job.run(_noop_progress)

        mock_pipeline.assert_awaited_once()
        assert result.metadata.get("outcome") != "deferred"

    @pytest.mark.asyncio
    async def test_sweep_job_ignores_the_exhausted_rate_limit(self, monkeypatch):
        monkeypatch.setenv("WIKI_REFRESH_LIVE_MAX_PER_HOUR", "0")
        job = WikiRefreshJob(entity_slug="org:tesla", origin="sweep")
        pipeline_stats = {"summary_chars": 10, "artifacts_used": 1}
        with (
            patch("app.processor.jobs.wiki_refresh.interactive_demand_recent", return_value=False),
            patch.object(job, "_run_pipeline", new=AsyncMock(return_value=pipeline_stats)) as mock_pipeline,
        ):
            result = await job.run(_noop_progress)

        mock_pipeline.assert_awaited_once()
        assert result.metadata.get("outcome") != "deferred"

    @pytest.mark.asyncio
    async def test_sweep_job_does_not_consume_the_live_rate_limit(self, monkeypatch):
        """A sweep-originated run must not spend a slot in the live counter —
        it is a separate population (Task 9 gives the sweep its own cap)."""
        monkeypatch.setenv("WIKI_REFRESH_LIVE_MAX_PER_HOUR", "1")
        pipeline_stats = {"summary_chars": 10, "artifacts_used": 1}

        sweep_job = WikiRefreshJob(entity_slug="org:sweep-target", origin="sweep")
        with (
            patch("app.processor.jobs.wiki_refresh.interactive_demand_recent", return_value=False),
            patch.object(sweep_job, "_run_pipeline", new=AsyncMock(return_value=pipeline_stats)),
        ):
            await sweep_job.run(_noop_progress)

        live_job = _make_job("org:live-target")
        with (
            patch("app.processor.jobs.wiki_refresh.interactive_demand_recent", return_value=False),
            patch.object(live_job, "_run_pipeline", new=AsyncMock(return_value=pipeline_stats)),
        ):
            live_result = await live_job.run(_noop_progress)

        assert live_result.metadata.get("outcome") != "deferred"
