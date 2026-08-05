# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the session-summarization scan in the scheduler cadence
(bi-temporal memory plan Phase E).

Verifies:
- ``_run_session_summaries`` is a clean no-op when ENABLE_SESSION_SUMMARIZATION
  is off (dark by default) and when Neo4j is unavailable.
- It enqueues one job per idle conversation the scan returns.
- The idle threshold + per-scan cap are honored (cutoff moves with
  SESSION_SUMMARY_IDLE_MIN; cap passed to the query from SESSION_SUMMARY_SCAN_LIMIT).
- Duplicate enqueues collapsing to None are counted correctly.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# Satisfy the scheduler module-level stub requirement (mirrors test_scheduler.py).
sys.modules.setdefault("deps", MagicMock())


def _fake_driver(rows):
    """A Neo4j driver whose session.run() yields ``rows`` and records calls."""
    driver = MagicMock()
    sess = MagicMock()
    sess.run.return_value = list(rows)
    ctx = MagicMock()
    ctx.__enter__.return_value = sess
    ctx.__exit__.return_value = False
    driver.session.return_value = ctx
    return driver, sess


@pytest.mark.asyncio
async def test_scan_no_op_when_flag_off(monkeypatch):
    monkeypatch.setattr("config.features.ENABLE_SESSION_SUMMARIZATION", False)
    from app.scheduler import _run_session_summaries

    with (
        patch("app.scheduler.get_neo4j") as mock_neo4j,
        patch(
            "app.processor.jobs.session_summary.enqueue_session_summary_job"
        ) as mock_enqueue,
    ):
        await _run_session_summaries()

    mock_neo4j.assert_not_called()  # flag is checked before any store access
    mock_enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_scan_neo4j_unavailable_is_clean_skip(monkeypatch):
    monkeypatch.setattr("config.features.ENABLE_SESSION_SUMMARIZATION", True)
    from app.scheduler import _run_session_summaries

    with (
        patch("app.scheduler.get_neo4j", return_value=None),
        patch(
            "app.processor.jobs.session_summary.enqueue_session_summary_job"
        ) as mock_enqueue,
        patch("app.scheduler._log_execution") as mock_log,
    ):
        await _run_session_summaries()

    mock_enqueue.assert_not_called()
    assert mock_log.call_args.args[1] == "skipped"


@pytest.mark.asyncio
async def test_scan_enqueues_idle_conversations(monkeypatch):
    monkeypatch.setattr("config.features.ENABLE_SESSION_SUMMARIZATION", True)
    from app.scheduler import _run_session_summaries

    driver, _sess = _fake_driver([{"cid": "c1"}, {"cid": "c2"}])

    with (
        patch("app.scheduler.get_neo4j", return_value=driver),
        patch(
            "app.processor.jobs.session_summary.enqueue_session_summary_job",
            return_value="job-x",
        ) as mock_enqueue,
    ):
        await _run_session_summaries()

    assert mock_enqueue.call_count == 2
    enqueued_cids = [c.args[0] for c in mock_enqueue.call_args_list]
    assert enqueued_cids == ["c1", "c2"]


@pytest.mark.asyncio
async def test_scan_passes_cap_from_env(monkeypatch):
    monkeypatch.setattr("config.features.ENABLE_SESSION_SUMMARIZATION", True)
    monkeypatch.setenv("SESSION_SUMMARY_SCAN_LIMIT", "7")
    from app.scheduler import _run_session_summaries

    driver, sess = _fake_driver([])  # empty → no enqueue, but the query still runs

    with (
        patch("app.scheduler.get_neo4j", return_value=driver),
        patch("app.processor.jobs.session_summary.enqueue_session_summary_job"),
    ):
        await _run_session_summaries()

    assert sess.run.call_args.kwargs["cap"] == 7


@pytest.mark.asyncio
async def test_scan_idle_threshold_moves_cutoff(monkeypatch):
    """A larger SESSION_SUMMARY_IDLE_MIN yields an earlier cutoff — proving the
    idle threshold is applied to the scan window."""
    monkeypatch.setattr("config.features.ENABLE_SESSION_SUMMARIZATION", True)
    from app.scheduler import _run_session_summaries

    async def _cutoff_for(idle_min: str) -> str:
        monkeypatch.setenv("SESSION_SUMMARY_IDLE_MIN", idle_min)
        driver, sess = _fake_driver([])
        with (
            patch("app.scheduler.get_neo4j", return_value=driver),
            patch("app.processor.jobs.session_summary.enqueue_session_summary_job"),
        ):
            await _run_session_summaries()
        return sess.run.call_args.kwargs["cutoff"]

    cutoff_30 = await _cutoff_for("30")
    cutoff_120 = await _cutoff_for("120")
    assert cutoff_120 < cutoff_30  # 120 min ago is earlier than 30 min ago


@pytest.mark.asyncio
async def test_scan_counts_only_non_collapsed_enqueues(monkeypatch):
    """enqueue_session_summary_job returns None when a duplicate collapses; the
    scan's ``enqueued`` count must reflect only the jobs it actually created."""
    monkeypatch.setattr("config.features.ENABLE_SESSION_SUMMARIZATION", True)
    from app.scheduler import _run_session_summaries

    driver, _sess = _fake_driver([{"cid": "c1"}, {"cid": "c2"}])

    with (
        patch("app.scheduler.get_neo4j", return_value=driver),
        patch(
            "app.processor.jobs.session_summary.enqueue_session_summary_job",
            side_effect=[None, "job-2"],  # c1 collapsed, c2 enqueued
        ),
        patch("app.scheduler._log_execution") as mock_log,
    ):
        await _run_session_summaries()

    detail = mock_log.call_args.args[3]
    assert "idle_conversations=2" in detail
    assert "enqueued=1" in detail
