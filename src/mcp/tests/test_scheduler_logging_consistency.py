# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""AF-039 regression: several ``_run_*`` scheduler jobs disagreed on logging
conventions — some early-return / driver-None paths left no execution-log
trace, some outer exception handlers never fed the swallowed-error counter.
This file covers the fix points not already exercised by their own
dedicated test module (email_poll, daily_digest, inbox_triage each have one)."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

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


@pytest.mark.asyncio
async def test_config_recommender_outer_exception_reaches_swallowed_counter(monkeypatch):
    swallowed = _capture_swallowed(monkeypatch)
    log_calls = _capture_log(monkeypatch)
    # app.state.processor_queue is only set by the FastAPI startup event, so
    # under test (no lifespan run) the queue probe naturally resolves None
    # and the function falls through to the direct-call path being exercised.
    with patch("app.deps.get_neo4j", return_value=None), \
         patch("app.deps.get_redis", return_value=None), \
         patch(
             "app.processor.jobs.config_recommender.run_recommender_sync",
             side_effect=RuntimeError("recommender crash"),
         ):
        await sched._run_config_recommender()

    assert log_calls and log_calls[0][0] == "config_recommender" and log_calls[0][1] == "error"
    assert any(c[0] == "app.scheduler" for c in swallowed)


@pytest.mark.asyncio
async def test_config_recommender_inner_probe_failures_are_not_silent(monkeypatch):
    """The get_neo4j/get_redis inner try/except-and-fall-back catches must each
    report through log_swallowed_error instead of discarding the exception."""
    swallowed = _capture_swallowed(monkeypatch)
    with patch("app.deps.get_neo4j", side_effect=RuntimeError("neo4j down")), \
         patch("app.deps.get_redis", side_effect=RuntimeError("redis down")), \
         patch(
             "app.processor.jobs.config_recommender.run_recommender_sync",
             return_value={"corpus_size": 0, "recommendations_written": 0},
         ):
        await sched._run_config_recommender()

    modules = [c[0] for c in swallowed]
    assert "app.scheduler.config_recommender.get_neo4j" in modules
    assert "app.scheduler.config_recommender.get_redis" in modules


@pytest.mark.asyncio
async def test_config_recommender_queue_probe_failure_is_not_silent(monkeypatch):
    """The ``from app.main import app`` queue probe's except must also report."""
    swallowed = _capture_swallowed(monkeypatch)
    with patch.dict(sys.modules, {"app.main": None}), \
         patch("app.deps.get_neo4j", return_value=None), \
         patch("app.deps.get_redis", return_value=None), \
         patch(
             "app.processor.jobs.config_recommender.run_recommender_sync",
             return_value={"corpus_size": 0, "recommendations_written": 0},
         ):
        await sched._run_config_recommender()

    modules = [c[0] for c in swallowed]
    assert "app.scheduler.config_recommender.queue_probe" in modules


@pytest.mark.asyncio
async def test_memory_consolidation_sweep_outer_exception_reaches_swallowed_counter(monkeypatch):
    swallowed = _capture_swallowed(monkeypatch)
    log_calls = _capture_log(monkeypatch)
    with patch("app.deps.get_neo4j", return_value=MagicMock()), \
         patch(
             "core.agents.memory.archive_old_memories",
             side_effect=RuntimeError("boom"),
         ):
        await sched._run_memory_consolidation_sweep()

    assert log_calls and log_calls[0][0] == "memory_consolidation_sweep" and log_calls[0][1] == "error"
    assert any(c[0] == "app.scheduler" for c in swallowed)


@pytest.mark.asyncio
async def test_wiki_drift_lint_logs_skipped_when_neo4j_unavailable(monkeypatch):
    log_calls = _capture_log(monkeypatch)
    with patch("app.deps.get_neo4j", return_value=None):
        await sched._run_wiki_drift_lint()

    assert log_calls and log_calls[0][0] == "wiki_drift_lint" and log_calls[0][1] == "skipped"


@pytest.mark.asyncio
async def test_wiki_stale_sweep_logs_skipped_when_neo4j_unavailable(monkeypatch):
    log_calls = _capture_log(monkeypatch)
    with patch("app.deps.get_neo4j", return_value=None):
        await sched._run_wiki_stale_sweep()

    assert log_calls and log_calls[0][0] == "wiki_stale_sweep" and log_calls[0][1] == "skipped"


@pytest.mark.asyncio
async def test_k_program_metrics_logs_skipped_when_script_missing(monkeypatch, tmp_path):
    log_calls = _capture_log(monkeypatch)
    # scheduler.py resolves the script by walking Path(__file__).resolve().parents
    # looking for scripts/k_program_metrics.py; point it at an empty tree so
    # the "script not present" branch is the one taken.
    fake_file = tmp_path / "app" / "scheduler.py"
    fake_file.parent.mkdir(parents=True)
    fake_file.write_text("# stub")
    with patch.object(sched, "__file__", str(fake_file)):
        await sched._run_k_program_metrics()

    assert log_calls and log_calls[0][0] == "k_program_metrics" and log_calls[0][1] == "skipped"
