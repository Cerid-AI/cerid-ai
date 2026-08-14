# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""AF-013: unit coverage for ``_enqueue_and_await_completion`` (the
compute_entity_embeddings / compute_umap_3d / compute_trust_state /
derive_domains graph-pipeline stage poller), and for the per-slug
try/except + enqueue_failed counter added to ``_run_wiki_drift_lint`` and
``_run_wiki_stale_sweep`` so a raising ``enqueue_refresh`` for one slug
does not abort the batch.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Satisfy scheduler module-level stub requirement (mirrors test_scheduler.py).
sys.modules.setdefault("deps", MagicMock())

from app import scheduler as sched  # noqa: E402


def _capture_log(monkeypatch) -> list[tuple]:
    calls: list[tuple] = []
    monkeypatch.setattr(sched, "_log_execution", lambda *a, **k: calls.append(a))
    return calls


def _capture_swallowed(monkeypatch) -> list[tuple]:
    calls: list[tuple] = []
    monkeypatch.setattr(sched, "log_swallowed_error", lambda *a, **k: calls.append(a))
    return calls


def _fast_poll(monkeypatch) -> None:
    """Shrink the poll/timeout constants so deadline-loop tests run in ms."""
    monkeypatch.setattr(sched, "_STAGE_COMPLETION_POLL_S", 0.01)
    monkeypatch.setattr(sched, "_STAGE_COMPLETION_TIMEOUT_S", 0.03)


def _make_job(job_id: str = "job-1") -> MagicMock:
    job = MagicMock()
    job.new_record.return_value = MagicMock()
    return job


# ---------------------------------------------------------------------------
# _enqueue_and_await_completion
# ---------------------------------------------------------------------------

class TestEnqueueAndAwaitCompletion:
    @pytest.mark.asyncio
    async def test_collapsed_duplicate_returns_none_without_polling(self, monkeypatch):
        """enqueue_if_absent returning None (duplicate pending/running) short-
        circuits before any polling — list_recent must never be reached."""
        log_calls = _capture_log(monkeypatch)
        queue = MagicMock()
        queue.enqueue_if_absent = AsyncMock(return_value=None)
        queue.list_recent = AsyncMock()
        job = _make_job()

        result = await sched._enqueue_and_await_completion(queue, job, "compute_entity_embeddings", 0.0)

        assert result is None
        queue.list_recent.assert_not_called()
        assert log_calls[-1][0] == "compute_entity_embeddings"
        assert log_calls[-1][1] == "skipped"

    @pytest.mark.asyncio
    async def test_missing_list_recent_short_circuits(self, monkeypatch):
        """A queue implementation without list_recent can't observe terminal
        state — the function returns the job_id and leaves the "enqueued"
        log from _enqueue_periodic as the only trace, adding no other log."""
        log_calls = _capture_log(monkeypatch)

        class _EnqueueOnlyQueue:
            async def enqueue_if_absent(self, record):
                return "job-42"

        job = _make_job()

        result = await sched._enqueue_and_await_completion(
            _EnqueueOnlyQueue(), job, "compute_umap_3d", 0.0,
        )

        assert result == "job-42"
        assert len(log_calls) == 1
        assert log_calls[0][0] == "compute_umap_3d"
        assert log_calls[0][1] == "enqueued"

    @pytest.mark.asyncio
    async def test_success_state_logs_success(self, monkeypatch):
        _fast_poll(monkeypatch)
        log_calls = _capture_log(monkeypatch)

        from core.processor.job import JobState

        queue = MagicMock()
        queue.enqueue_if_absent = AsyncMock(return_value="job-1")
        record = SimpleNamespace(id="job-1", state=JobState.COMPLETED, error_message=None)
        queue.list_recent = AsyncMock(return_value=[record])
        job = _make_job()

        result = await sched._enqueue_and_await_completion(queue, job, "compute_trust_state", 0.0)

        assert result == "job-1"
        statuses = [c[:2] for c in log_calls]
        assert ("compute_trust_state", "enqueued") in statuses
        assert ("compute_trust_state", "success") in statuses

    @pytest.mark.asyncio
    async def test_failed_state_logs_error_with_error_message(self, monkeypatch):
        _fast_poll(monkeypatch)
        log_calls = _capture_log(monkeypatch)

        from core.processor.job import JobState

        queue = MagicMock()
        queue.enqueue_if_absent = AsyncMock(return_value="job-1")
        record = SimpleNamespace(id="job-1", state=JobState.FAILED, error_message="boom")
        queue.list_recent = AsyncMock(return_value=[record])
        job = _make_job()

        result = await sched._enqueue_and_await_completion(queue, job, "derive_domains", 0.0)

        assert result == "job-1"
        error_calls = [c for c in log_calls if c[1] == "error"]
        assert error_calls
        assert error_calls[0][0] == "derive_domains"
        assert error_calls[0][3] == "boom"

    @pytest.mark.asyncio
    async def test_held_state_logs_held(self, monkeypatch):
        _fast_poll(monkeypatch)
        log_calls = _capture_log(monkeypatch)

        from core.processor.job import JobState

        queue = MagicMock()
        queue.enqueue_if_absent = AsyncMock(return_value="job-1")
        record = SimpleNamespace(id="job-1", state=JobState.HELD, error_message=None)
        queue.list_recent = AsyncMock(return_value=[record])
        job = _make_job()

        result = await sched._enqueue_and_await_completion(queue, job, "compute_entity_embeddings", 0.0)

        assert result == "job-1"
        held_calls = [c for c in log_calls if c[1] == "held"]
        assert held_calls
        assert held_calls[0][0] == "compute_entity_embeddings"
        assert held_calls[0][3] == "cost-cap hold"

    @pytest.mark.asyncio
    async def test_poll_exception_fast_fails_to_timeout(self, monkeypatch):
        """A list_recent exception must not crash the scheduler — it breaks
        the poll loop immediately (no retry) and the job is logged timeout,
        with the exception reported through log_swallowed_error."""
        _fast_poll(monkeypatch)
        # Give the timeout a wide berth so we can prove the break was
        # immediate rather than merely coincidental with a real deadline.
        monkeypatch.setattr(sched, "_STAGE_COMPLETION_TIMEOUT_S", 5.0)
        log_calls = _capture_log(monkeypatch)
        swallowed = _capture_swallowed(monkeypatch)

        queue = MagicMock()
        queue.enqueue_if_absent = AsyncMock(return_value="job-1")
        queue.list_recent = AsyncMock(side_effect=RuntimeError("redis down"))
        job = _make_job()

        result = await sched._enqueue_and_await_completion(queue, job, "compute_umap_3d", 0.0)

        assert result == "job-1"
        queue.list_recent.assert_awaited_once()  # broke after the first failure, no retry
        timeout_calls = [c for c in log_calls if c[1] == "timeout"]
        assert timeout_calls and timeout_calls[0][0] == "compute_umap_3d"
        assert any(c[0] == "app.scheduler.await_completion" for c in swallowed)

    @pytest.mark.asyncio
    async def test_bounded_deadline_logs_timeout_when_never_observed(self, monkeypatch):
        """A job_id that never appears in list_recent's terminal records
        must not hang the scheduler forever — the loop is bounded by
        _STAGE_COMPLETION_TIMEOUT_S and logs 'timeout'."""
        _fast_poll(monkeypatch)
        log_calls = _capture_log(monkeypatch)

        queue = MagicMock()
        queue.enqueue_if_absent = AsyncMock(return_value="job-1")
        # Always returns records for a different job — job-1 never resolves.
        other = SimpleNamespace(id="other-job", state=None, error_message=None)
        queue.list_recent = AsyncMock(return_value=[other])
        job = _make_job()

        result = await sched._enqueue_and_await_completion(queue, job, "derive_domains", 0.0)

        assert result == "job-1"
        timeout_calls = [c for c in log_calls if c[1] == "timeout"]
        assert timeout_calls and timeout_calls[0][0] == "derive_domains"
        assert "job_id=job-1" in timeout_calls[0][3]
        # Loop must actually have polled more than once within the bounded window.
        assert queue.list_recent.await_count >= 1


# ---------------------------------------------------------------------------
# Shared neo4j-session mocking helper for the wiki batch tests
# ---------------------------------------------------------------------------

def _mock_driver_with_run_results(*run_results: list[dict]) -> MagicMock:
    """Build a MagicMock neo4j driver whose ``session().run(...)`` calls
    return the given row-lists in order (each row is a plain dict, which
    supports the ``r["slug"]`` access the scan functions use)."""
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    driver.session.return_value.__exit__.return_value = False
    session.run.side_effect = list(run_results)
    return driver


# ---------------------------------------------------------------------------
# _run_wiki_drift_lint — per-slug try/except + enqueue_failed counter
# ---------------------------------------------------------------------------

class TestWikiDriftLintEnqueueFailure:
    @pytest.mark.asyncio
    async def test_raising_enqueue_for_one_contradiction_slug_does_not_abort_batch(self, monkeypatch):
        log_calls = _capture_log(monkeypatch)
        swallowed = _capture_swallowed(monkeypatch)

        driver = _mock_driver_with_run_results(
            [{"slug": "bad-slug"}, {"slug": "good-slug"}],  # contradiction rows
            [],  # gap rows
        )

        enqueue_refresh = MagicMock(side_effect=[RuntimeError("redis down"), True])

        with patch("app.deps.get_neo4j", return_value=driver), \
             patch(
                 "app.processor.subscribers.wiki_refresh.enqueue_refresh",
                 enqueue_refresh,
             ), \
             patch(
                 "app.processor.subscribers.wiki_refresh.HUMAN_EDIT_PROTECT_WINDOW_S",
                 3600,
             ):
            await sched._run_wiki_drift_lint()

        assert enqueue_refresh.call_count == 2  # both slugs attempted; the raise did not abort the loop
        final = log_calls[-1]
        assert final[0] == "wiki_drift_lint"
        assert final[1] == "error"  # not "success" — enqueue_failed > 0
        assert "failed=1" in final[3]
        assert "forced=1" in final[3]  # the second (non-raising) slug still counted
        assert any(
            c[0] == "app.scheduler.wiki_drift_lint.enqueue_contradiction" for c in swallowed
        )

    @pytest.mark.asyncio
    async def test_raising_enqueue_for_one_gap_slug_does_not_abort_batch(self, monkeypatch):
        log_calls = _capture_log(monkeypatch)
        swallowed = _capture_swallowed(monkeypatch)

        driver = _mock_driver_with_run_results(
            [],  # contradiction rows
            [{"slug": "bad-gap"}, {"slug": "good-gap"}],  # gap rows
        )

        enqueue_refresh = MagicMock(side_effect=[RuntimeError("redis down"), True])

        with patch("app.deps.get_neo4j", return_value=driver), \
             patch(
                 "app.processor.subscribers.wiki_refresh.enqueue_refresh",
                 enqueue_refresh,
             ), \
             patch(
                 "app.processor.subscribers.wiki_refresh.HUMAN_EDIT_PROTECT_WINDOW_S",
                 3600,
             ):
            await sched._run_wiki_drift_lint()

        assert enqueue_refresh.call_count == 2
        final = log_calls[-1]
        assert final[0] == "wiki_drift_lint"
        assert final[1] == "error"
        assert "failed=1" in final[3]
        assert "enqueued=1" in final[3]
        assert any(c[0] == "app.scheduler.wiki_drift_lint.enqueue_gap" for c in swallowed)

    @pytest.mark.asyncio
    async def test_no_failures_still_logs_success(self, monkeypatch):
        """Control: when enqueue_failed stays 0, status is still success —
        proves the error branch above is genuinely conditioned on the counter."""
        log_calls = _capture_log(monkeypatch)

        driver = _mock_driver_with_run_results(
            [{"slug": "ok-slug"}],
            [],
        )
        enqueue_refresh = MagicMock(return_value=True)

        with patch("app.deps.get_neo4j", return_value=driver), \
             patch(
                 "app.processor.subscribers.wiki_refresh.enqueue_refresh",
                 enqueue_refresh,
             ), \
             patch(
                 "app.processor.subscribers.wiki_refresh.HUMAN_EDIT_PROTECT_WINDOW_S",
                 3600,
             ):
            await sched._run_wiki_drift_lint()

        final = log_calls[-1]
        assert final[0] == "wiki_drift_lint"
        assert final[1] == "success"
        assert "failed=0" in final[3]


# ---------------------------------------------------------------------------
# _run_wiki_stale_sweep — per-slug try/except + enqueue_failed counter
# ---------------------------------------------------------------------------

class TestWikiStaleSweepEnqueueFailure:
    @pytest.mark.asyncio
    async def test_raising_enqueue_for_one_slug_does_not_abort_batch(self, monkeypatch):
        log_calls = _capture_log(monkeypatch)
        swallowed = _capture_swallowed(monkeypatch)

        driver = _mock_driver_with_run_results(
            [{"slug": "bad-stale"}, {"slug": "good-stale"}],
        )

        enqueue_refresh = MagicMock(side_effect=[RuntimeError("redis down"), True])

        with patch("app.deps.get_neo4j", return_value=driver), \
             patch(
                 "app.processor.subscribers.wiki_refresh.enqueue_refresh",
                 enqueue_refresh,
             ), \
             patch(
                 "app.processor.subscribers.wiki_refresh.HUMAN_EDIT_PROTECT_WINDOW_S",
                 3600,
             ):
            await sched._run_wiki_stale_sweep()

        assert enqueue_refresh.call_count == 2
        final = log_calls[-1]
        assert final[0] == "wiki_stale_sweep"
        assert final[1] == "error"  # not "success" — enqueue_failed > 0
        assert "failed=1" in final[3]
        assert "enqueued=1" in final[3]
        assert any(
            c[0] == "app.scheduler.wiki_stale_sweep.enqueue" for c in swallowed
        )

    @pytest.mark.asyncio
    async def test_no_failures_still_logs_success(self, monkeypatch):
        log_calls = _capture_log(monkeypatch)

        driver = _mock_driver_with_run_results(
            [{"slug": "ok-stale"}],
        )
        enqueue_refresh = MagicMock(return_value=True)

        with patch("app.deps.get_neo4j", return_value=driver), \
             patch(
                 "app.processor.subscribers.wiki_refresh.enqueue_refresh",
                 enqueue_refresh,
             ), \
             patch(
                 "app.processor.subscribers.wiki_refresh.HUMAN_EDIT_PROTECT_WINDOW_S",
                 3600,
             ):
            await sched._run_wiki_stale_sweep()

        final = log_calls[-1]
        assert final[0] == "wiki_stale_sweep"
        assert final[1] == "success"
        assert "failed=0" in final[3]
