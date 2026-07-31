# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the background curator cron — AF-030 (CL-8).

Quality scores were computed at ingest but never re-run, so they drifted as
artifacts accrued edits and relationships. ``_run_curator`` re-scores in
audit mode (local scoring + a graph write, no LLM) and ``start_scheduler``
registers it only when the operator opts in via ``CERID_CURATOR_CRON_ENABLED``.
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# scheduler.py imports from deps at module level; stub it like test_scheduler.py.
sys.modules.setdefault("deps", MagicMock())

from app.scheduler import (  # noqa: E402
    get_job_status,
    start_scheduler,
    stop_scheduler,
)


def _job_ids() -> set[str]:
    return {j["id"] for j in get_job_status()["jobs"]}


# ---------------------------------------------------------------------------
# Job body
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_run_curator_calls_curate_in_audit_mode():
    from app.scheduler import _run_curator

    with (
        patch("app.scheduler.get_neo4j", return_value=MagicMock()),
        patch("app.scheduler.get_chroma", return_value=MagicMock()),
        patch(
            "app.agents.curator.curate",
            new_callable=AsyncMock,
            return_value={"artifacts_scored": 12, "avg_quality_score": 0.71},
        ) as mock_curate,
    ):
        await _run_curator()

    mock_curate.assert_awaited_once()
    assert mock_curate.await_args.kwargs["mode"] == "audit"
    # Real stores are threaded in (no synopsis args → no LLM path).
    assert "neo4j_driver" in mock_curate.await_args.kwargs
    assert "chroma_client" in mock_curate.await_args.kwargs
    assert "generate_synopses" not in mock_curate.await_args.kwargs


@pytest.mark.asyncio
async def test_run_curator_swallows_exception(caplog):
    """A curate() failure must not escape the scheduled task."""
    from app.scheduler import _run_curator

    with (
        patch("app.scheduler.get_neo4j", return_value=MagicMock()),
        patch("app.scheduler.get_chroma", return_value=MagicMock()),
        patch(
            "app.agents.curator.curate",
            new_callable=AsyncMock,
            side_effect=RuntimeError("graph down"),
        ),
    ):
        await _run_curator()  # must not raise

    assert any("curator failed" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# Registration gating (the AF-030 claim: the cron now exists)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_curator_cron_registered_when_opted_in(monkeypatch):
    monkeypatch.setenv("CERID_CURATOR_CRON_ENABLED", "true")
    stop_scheduler()  # ensure a fresh scheduler (start_scheduler is idempotent)
    try:
        start_scheduler()
        assert "curator" in _job_ids()
    finally:
        stop_scheduler()


@pytest.mark.asyncio
async def test_curator_cron_absent_by_default(monkeypatch):
    monkeypatch.delenv("CERID_CURATOR_CRON_ENABLED", raising=False)
    stop_scheduler()
    try:
        start_scheduler()
        assert "curator" not in _job_ids()
    finally:
        stop_scheduler()


def test_schedule_curator_setting_exposed():
    from config import settings

    assert hasattr(settings, "SCHEDULE_CURATOR")
    assert settings.SCHEDULE_CURATOR == "30 4 * * sun"


def test_weekly_schedules_fire_on_their_documented_day():
    """Assert the day a job actually fires, not the literal cron string.

    APScheduler's ``CronTrigger.from_crontab`` maps day-of-week ``0`` to
    **Monday**, unlike standard cron. Every weekly default was written as ``0``
    and commented "Sunday", so they all fired a day late — and this test pinned
    the wrong string in place, asserting ``"30 4 * * 0"  # Sunday 4:30 AM``.

    Named weekdays remove the ambiguity; asserting the resolved fire day means a
    future edit back to a bare integer fails here instead of silently shifting
    the maintenance window.
    """
    from datetime import datetime, timezone

    from apscheduler.triggers.cron import CronTrigger

    from config import settings

    # A Thursday, so "next fire" is unambiguous for any weekday.
    base = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    expected = {
        "SCHEDULE_CURATOR": "Sun",
        "SCHEDULE_COMMUNITY_REFRESH": "Sun",
        "SCHEDULE_MEMORY_CONSOLIDATION": "Sun",
        "SCHEDULE_STALE_DETECTION": "Sun",
        "SCHEDULE_WIKI_DRIFT_LINT": "Sun",
        "SCHEDULE_ENTITY_MERGE": "Sun",
        "SCHEDULE_MODEL_AUTO_UPDATE": "Mon",
    }
    wrong: dict[str, str] = {}
    for name, want_day in expected.items():
        expr = getattr(settings, name, "")
        if not expr:  # empty = disabled by design
            continue
        fires = CronTrigger.from_crontab(expr, timezone=timezone.utc).get_next_fire_time(
            None, base
        )
        got = fires.strftime("%a")
        if got != want_day:
            wrong[name] = f"{expr!r} fires {got}, expected {want_day}"

    assert not wrong, f"weekly schedules fire on the wrong day: {wrong}"
