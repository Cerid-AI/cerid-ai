# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the inbox_triage scheduler job — Phase J Day 2."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_no_op_when_feature_off(monkeypatch):
    """Even with the env toggle set, the scheduler job must not run
    inbox_triage when the feature flag is off (community tier)."""
    from app.scheduler import _run_inbox_triage

    monkeypatch.setenv("CERID_INBOX_TRIAGE_ENABLED", "true")
    with (
        patch("config.features.is_feature_enabled", return_value=False),
        patch("core.agents.inbox_triage.triage_inboxes",
              new_callable=AsyncMock) as mock_triage,
    ):
        await _run_inbox_triage()

    mock_triage.assert_not_called()


@pytest.mark.asyncio
async def test_no_op_when_env_toggle_off(monkeypatch):
    """Feature flag on but operator hasn't set CERID_INBOX_TRIAGE_ENABLED."""
    from app.scheduler import _run_inbox_triage

    monkeypatch.delenv("CERID_INBOX_TRIAGE_ENABLED", raising=False)
    with (
        patch("config.features.is_feature_enabled", return_value=True),
        patch("core.agents.inbox_triage.triage_inboxes",
              new_callable=AsyncMock) as mock_triage,
    ):
        await _run_inbox_triage()

    mock_triage.assert_not_called()


@pytest.mark.asyncio
async def test_runs_when_both_gates_open(monkeypatch):
    from app.scheduler import _run_inbox_triage
    from core.agents.inbox_triage import TriageResult

    monkeypatch.setenv("CERID_INBOX_TRIAGE_ENABLED", "true")
    fake_result = TriageResult(threads=[], by_category={}, sources_queried=["gmail"])
    with (
        patch("config.features.is_feature_enabled", return_value=True),
        patch("core.agents.inbox_triage.triage_inboxes",
              new_callable=AsyncMock, return_value=fake_result) as mock_triage,
    ):
        await _run_inbox_triage()

    mock_triage.assert_awaited_once()


@pytest.mark.asyncio
async def test_handles_agent_failure_silently(monkeypatch, caplog):
    """An exception from triage_inboxes must NOT propagate — the
    scheduler keeps running other jobs even if one cron throws."""
    from app.scheduler import _run_inbox_triage

    monkeypatch.setenv("CERID_INBOX_TRIAGE_ENABLED", "true")
    with (
        patch("config.features.is_feature_enabled", return_value=True),
        patch("core.agents.inbox_triage.triage_inboxes",
              new_callable=AsyncMock,
              side_effect=RuntimeError("agent crash")),
    ):
        # Must not raise
        await _run_inbox_triage()
    # And we should have logged the error
    assert any("inbox triage failed" in r.message.lower() for r in caplog.records)


def test_schedule_setting_exposed():
    """Settings must expose SCHEDULE_INBOX_TRIAGE so the start_scheduler
    pathway can read it. Default cadence is every 15 minutes."""
    from config import settings
    assert hasattr(settings, "SCHEDULE_INBOX_TRIAGE")
    # Default value is the 15-min cron
    assert settings.SCHEDULE_INBOX_TRIAGE == "*/15 * * * *"
