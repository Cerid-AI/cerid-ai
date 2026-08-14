# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the daily digest scheduler job — Phase K Day 2."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_no_op_when_feature_off(monkeypatch):
    from app import scheduler as sched
    from app.scheduler import _run_daily_digest

    calls: list[tuple] = []
    monkeypatch.setattr(sched, "_log_execution", lambda *a, **k: calls.append(a))
    monkeypatch.setenv("CERID_DAILY_DIGEST_ENABLED", "true")
    with (
        patch("config.features.is_feature_enabled", return_value=False),
        patch("core.agents.daily_digest.generate_daily_digest",
              new_callable=AsyncMock) as mock_agent,
    ):
        await _run_daily_digest()

    mock_agent.assert_not_called()
    # AF-039: an early-return skip must still leave an execution-log trace.
    assert calls and calls[0][0] == "daily_digest" and calls[0][1] == "skipped"


@pytest.mark.asyncio
async def test_no_op_when_env_toggle_off(monkeypatch):
    from app import scheduler as sched
    from app.scheduler import _run_daily_digest

    calls: list[tuple] = []
    monkeypatch.setattr(sched, "_log_execution", lambda *a, **k: calls.append(a))
    monkeypatch.delenv("CERID_DAILY_DIGEST_ENABLED", raising=False)
    with (
        patch("config.features.is_feature_enabled", return_value=True),
        patch("core.agents.daily_digest.generate_daily_digest",
              new_callable=AsyncMock) as mock_agent,
    ):
        await _run_daily_digest()

    mock_agent.assert_not_called()
    assert calls and calls[0][0] == "daily_digest" and calls[0][1] == "skipped"


@pytest.mark.asyncio
async def test_fires_digest_ready_webhook_on_success(monkeypatch):
    from app.scheduler import _run_daily_digest
    from core.agents.daily_digest import DigestResult

    monkeypatch.setenv("CERID_DAILY_DIGEST_ENABLED", "true")
    fake = DigestResult(
        digest_id="did-1",
        generated_at="2026-05-22T07:00:00Z",
        window_hours=24,
        artifact_count=10,
        flagged_count=2,
        inbox_urgent_count=1,
        persisted_artifact_id="art:abc",
    )
    with (
        patch("config.features.is_feature_enabled", return_value=True),
        patch("core.agents.daily_digest.generate_daily_digest",
              new_callable=AsyncMock, return_value=fake),
        patch("utils.webhooks.fire_event",
              new_callable=AsyncMock, return_value=1) as mock_webhook,
    ):
        await _run_daily_digest()

    mock_webhook.assert_awaited_once()
    event_name, payload = mock_webhook.await_args.args
    assert event_name == "digest.ready"
    assert payload["digest_id"] == "did-1"
    assert payload["artifact_count"] == 10
    assert payload["persisted_artifact_id"] == "art:abc"


@pytest.mark.asyncio
async def test_no_webhook_when_skipped(monkeypatch):
    """If the digest skipped (feature gated etc.), don't fire the
    'ready' event — there's nothing to deliver."""
    from app.scheduler import _run_daily_digest
    from core.agents.daily_digest import DigestResult

    monkeypatch.setenv("CERID_DAILY_DIGEST_ENABLED", "true")
    fake = DigestResult(skipped=True, skip_reason="neo4j_unavailable")
    with (
        patch("config.features.is_feature_enabled", return_value=True),
        patch("core.agents.daily_digest.generate_daily_digest",
              new_callable=AsyncMock, return_value=fake),
        patch("utils.webhooks.fire_event",
              new_callable=AsyncMock) as mock_webhook,
    ):
        await _run_daily_digest()

    mock_webhook.assert_not_called()


@pytest.mark.asyncio
async def test_swallows_agent_exception(monkeypatch, caplog):
    from app.scheduler import _run_daily_digest

    monkeypatch.setenv("CERID_DAILY_DIGEST_ENABLED", "true")
    with (
        patch("config.features.is_feature_enabled", return_value=True),
        patch("core.agents.daily_digest.generate_daily_digest",
              new_callable=AsyncMock, side_effect=RuntimeError("agent crash")),
    ):
        # Must not propagate
        await _run_daily_digest()
    assert any("daily digest failed" in r.message.lower() for r in caplog.records)


def test_schedule_setting_exposed():
    from config import settings
    assert hasattr(settings, "SCHEDULE_DAILY_DIGEST")
    # Default is 7 AM UTC daily
    assert settings.SCHEDULE_DAILY_DIGEST == "0 7 * * *"
