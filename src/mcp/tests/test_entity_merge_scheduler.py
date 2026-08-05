# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the scheduled Tier-C entity embedding-merge sweep — AF-032 (CL-8).

Ingest runs only Tiers A+B of resolve_canonical (lean); the embedding-based
Tier-C merge is a deliberate out-of-band sweep. ``_run_entity_embedding_merge``
drives it and ``start_scheduler`` registers the cron only when the operator
opts in via ``CERID_ENTITY_MERGE_CRON_ENABLED``.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("deps", MagicMock())

from app.scheduler import (  # noqa: E402
    get_job_status,
    start_scheduler,
    stop_scheduler,
)


def _job_ids() -> set[str]:
    return {j["id"] for j in get_job_status()["jobs"]}


@pytest.mark.asyncio
async def test_run_entity_merge_applies_and_logs(monkeypatch):
    from app.scheduler import _run_entity_embedding_merge

    fake = MagicMock(return_value={"dry_run": False, "merge_clusters": 3})
    with (
        patch("app.scheduler.get_neo4j", return_value=MagicMock()),
        patch("scripts.merge_entity_aliases.run_embedding_resolution", fake),
    ):
        await _run_entity_embedding_merge()

    fake.assert_called_once()
    # Applies merges (not dry-run) with the injected driver.
    assert fake.call_args.kwargs["dry_run"] is False


@pytest.mark.asyncio
async def test_run_entity_merge_swallows_exception(caplog):
    from app.scheduler import _run_entity_embedding_merge

    with (
        patch("app.scheduler.get_neo4j", return_value=MagicMock()),
        patch("scripts.merge_entity_aliases.run_embedding_resolution",
              side_effect=RuntimeError("neo4j down")),
    ):
        await _run_entity_embedding_merge()  # must not raise

    assert any("entity embedding-merge failed" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_cron_registered_when_opted_in(monkeypatch):
    monkeypatch.setenv("CERID_ENTITY_MERGE_CRON_ENABLED", "true")
    stop_scheduler()
    try:
        start_scheduler()
        assert "entity_embedding_merge" in _job_ids()
    finally:
        stop_scheduler()


@pytest.mark.asyncio
async def test_cron_absent_by_default(monkeypatch):
    monkeypatch.delenv("CERID_ENTITY_MERGE_CRON_ENABLED", raising=False)
    stop_scheduler()
    try:
        start_scheduler()
        assert "entity_embedding_merge" not in _job_ids()
    finally:
        stop_scheduler()


def test_schedule_entity_merge_setting_exposed():
    from config import settings

    assert hasattr(settings, "SCHEDULE_ENTITY_MERGE")
    # Named weekday, not `0`: APScheduler maps day-of-week 0 to *Monday*, so
    # the old "30 5 * * 0" fired a day after the "Sunday" the comment claimed.
    # test_curator_scheduler.py asserts the resolved fire day for every weekly
    # schedule, which is the guard that actually prevents the off-by-one.
    assert settings.SCHEDULE_ENTITY_MERGE == "30 5 * * sun"
