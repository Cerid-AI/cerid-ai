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
    assert settings.SCHEDULE_CURATOR == "30 4 * * 0"  # Sunday 4:30 AM
