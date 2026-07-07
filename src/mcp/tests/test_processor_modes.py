# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the PROCESSOR_MODE contract (Task 2.5a).

Covers: mode resolution, the pure select_job_model truth table, the
monthly-spend Redis aggregate, the resolve_job_model app-layer wrapper's
cap-fallback warning, and the worker's disabled-mode dequeue gate.

asyncio_mode = "auto" (pyproject.toml) means every async test function
is auto-detected.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import pytest

from app.processor.metrics import processor_cost_usd_month, record_completion
from app.processor.model_policy import resolve_job_model
from config import settings
from core.processor.mode import (
    ModelDecision,
    processor_is_disabled,
    resolve_processor_mode,
    select_job_model,
)
from tests.test_processor_worker import _make_worker, _mock_queue

# ---------------------------------------------------------------------------
# resolve_processor_mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("local", "local"),
        ("hybrid", "hybrid"),
        ("disabled", "disabled"),
        ("LOCAL", "local"),
        ("Hybrid", "hybrid"),
        (" disabled ", "disabled"),
        ("bogus", "local"),
        ("", "local"),
        (None, "local"),
    ],
)
def test_resolve_processor_mode(raw, expected):
    assert resolve_processor_mode(raw) == expected


def test_processor_is_disabled():
    assert processor_is_disabled("disabled") is True
    assert processor_is_disabled("local") is False
    assert processor_is_disabled("hybrid") is False


# ---------------------------------------------------------------------------
# select_job_model truth table
# ---------------------------------------------------------------------------


def test_select_job_model_local_mode_ignores_tokens():
    decision = select_job_model(
        mode="local",
        estimated_tokens=999_999,
        api_threshold_tokens=4000,
        monthly_spend_usd=Decimal("0"),
        cap_usd=Decimal("5"),
        cap_fallback="local",
        default_local="ollama/local",
        api_model="anthropic/claude-sonnet-4-6",
    )
    assert decision == ModelDecision(model="ollama/local", hold=False, reason="mode_local")


def test_select_job_model_disabled_mode():
    decision = select_job_model(
        mode="disabled",
        estimated_tokens=999_999,
        api_threshold_tokens=4000,
        monthly_spend_usd=Decimal("0"),
        cap_usd=Decimal("5"),
        cap_fallback="local",
        default_local="ollama/local",
        api_model="anthropic/claude-sonnet-4-6",
    )
    assert decision == ModelDecision(model="ollama/local", hold=False, reason="mode_disabled")


def test_select_job_model_hybrid_small_job_stays_local():
    decision = select_job_model(
        mode="hybrid",
        estimated_tokens=100,
        api_threshold_tokens=4000,
        monthly_spend_usd=Decimal("0"),
        cap_usd=Decimal("5"),
        cap_fallback="local",
        default_local="ollama/local",
        api_model="anthropic/claude-sonnet-4-6",
    )
    assert decision == ModelDecision(
        model="ollama/local", hold=False, reason="hybrid_under_threshold"
    )


def test_select_job_model_hybrid_big_job_under_cap_routes_to_api():
    decision = select_job_model(
        mode="hybrid",
        estimated_tokens=5000,
        api_threshold_tokens=4000,
        monthly_spend_usd=Decimal("1.00"),
        cap_usd=Decimal("5"),
        cap_fallback="local",
        default_local="ollama/local",
        api_model="anthropic/claude-sonnet-4-6",
    )
    assert decision == ModelDecision(
        model="anthropic/claude-sonnet-4-6", hold=False, reason="hybrid_api_routed"
    )


def test_select_job_model_hybrid_big_job_at_cap_falls_back_local():
    decision = select_job_model(
        mode="hybrid",
        estimated_tokens=5000,
        api_threshold_tokens=4000,
        monthly_spend_usd=Decimal("5"),  # == cap: not < cap, so cap is "reached"
        cap_usd=Decimal("5"),
        cap_fallback="local",
        default_local="ollama/local",
        api_model="anthropic/claude-sonnet-4-6",
    )
    assert decision == ModelDecision(
        model="ollama/local", hold=False, reason="hybrid_cap_fallback_local"
    )


def test_select_job_model_hybrid_big_job_over_cap_falls_back_local():
    decision = select_job_model(
        mode="hybrid",
        estimated_tokens=5000,
        api_threshold_tokens=4000,
        monthly_spend_usd=Decimal("9.99"),
        cap_usd=Decimal("5"),
        cap_fallback="local",
        default_local="ollama/local",
        api_model="anthropic/claude-sonnet-4-6",
    )
    assert decision.model == "ollama/local"
    assert decision.hold is False
    assert decision.reason == "hybrid_cap_fallback_local"


def test_select_job_model_hybrid_big_job_at_cap_holds():
    decision = select_job_model(
        mode="hybrid",
        estimated_tokens=5000,
        api_threshold_tokens=4000,
        monthly_spend_usd=Decimal("5"),
        cap_usd=Decimal("5"),
        cap_fallback="hold",
        default_local="ollama/local",
        api_model="anthropic/claude-sonnet-4-6",
    )
    assert decision == ModelDecision(model=None, hold=True, reason="hybrid_cap_hold")


# ---------------------------------------------------------------------------
# processor_cost_usd_month
# ---------------------------------------------------------------------------


@pytest.fixture
def redis_client():
    """Isolated in-process FakeRedis per test."""
    server = fakeredis.FakeServer()
    return fakeredis.FakeRedis(server=server, decode_responses=True)


async def test_cost_usd_month_sums_only_current_month(redis_client):
    now = datetime.now(timezone.utc)
    this_month_ts = now.timestamp()
    # Guaranteed to land in the previous calendar month regardless of today's date.
    last_month_ts = (now - timedelta(days=40)).timestamp()

    await record_completion(
        redis_client,
        "this-month-job",
        completed_at=this_month_ts,
        actual_cost_usd=Decimal("1.50"),
    )
    await record_completion(
        redis_client,
        "last-month-job",
        completed_at=last_month_ts,
        actual_cost_usd=Decimal("99.00"),
    )

    total = await processor_cost_usd_month(redis_client)
    assert total == Decimal("1.50")


async def test_cost_usd_month_empty(redis_client):
    total = await processor_cost_usd_month(redis_client)
    assert total == Decimal("0")


async def test_cost_usd_month_defensive_on_error():
    mock = MagicMock()
    mock.zrangebyscore.side_effect = ConnectionError("Redis unreachable")
    total = await processor_cost_usd_month(mock)
    assert total == Decimal("0")


# ---------------------------------------------------------------------------
# resolve_job_model wrapper — cap-fallback WARNING fires exactly once
# ---------------------------------------------------------------------------


async def test_resolve_job_model_warns_once_on_cap_fallback(monkeypatch, caplog):
    monkeypatch.setattr(settings, "PROCESSOR_MODE", "hybrid", raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_API_THRESHOLD_TOKENS", 4000, raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_MONTHLY_CAP_USD", 5.0, raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_API_CAP_FALLBACK", "local", raising=False)

    with patch(
        "app.processor.metrics.processor_cost_usd_month",
        new_callable=AsyncMock,
        return_value=Decimal("10.00"),
    ):
        with caplog.at_level(logging.WARNING, logger="ai-companion.processor.model_policy"):
            decision = await resolve_job_model(
                MagicMock(),
                api_model="anthropic/claude-sonnet-4-6",
                estimated_tokens=5000,
            )

    assert decision.model == "ollama/local"
    assert decision.hold is False
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


async def test_resolve_job_model_no_warning_under_cap(monkeypatch, caplog):
    monkeypatch.setattr(settings, "PROCESSOR_MODE", "hybrid", raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_API_THRESHOLD_TOKENS", 4000, raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_MONTHLY_CAP_USD", 5.0, raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_API_CAP_FALLBACK", "local", raising=False)

    with patch(
        "app.processor.metrics.processor_cost_usd_month",
        new_callable=AsyncMock,
        return_value=Decimal("0.00"),
    ):
        with caplog.at_level(logging.WARNING, logger="ai-companion.processor.model_policy"):
            decision = await resolve_job_model(
                MagicMock(),
                api_model="anthropic/claude-sonnet-4-6",
                estimated_tokens=5000,
            )

    assert decision.model == "anthropic/claude-sonnet-4-6"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 0


async def test_resolve_job_model_no_warning_in_local_mode(monkeypatch, caplog):
    monkeypatch.setattr(settings, "PROCESSOR_MODE", "local", raising=False)

    with caplog.at_level(logging.WARNING, logger="ai-companion.processor.model_policy"):
        decision = await resolve_job_model(
            MagicMock(),
            api_model="anthropic/claude-sonnet-4-6",
            estimated_tokens=999_999,
        )

    assert decision.model == "ollama/local"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 0


# ---------------------------------------------------------------------------
# Worker: disabled mode halts dequeue; default mode does not regress
# ---------------------------------------------------------------------------


async def test_disabled_mode_skips_dequeue(monkeypatch):
    monkeypatch.setattr(settings, "PROCESSOR_MODE", "disabled", raising=False)

    queue = _mock_queue(None)
    worker = _make_worker(queue)

    with patch("os.getloadavg", return_value=(0.0, 0.0, 0.0)):
        await worker.start()
        await asyncio.sleep(0.05)
        await worker.stop()

    queue.dequeue.assert_not_called()


async def test_default_local_mode_dequeues_normally(monkeypatch):
    monkeypatch.setattr(settings, "PROCESSOR_MODE", "local", raising=False)

    queue = _mock_queue(None)
    worker = _make_worker(queue)

    with patch("os.getloadavg", return_value=(0.0, 0.0, 0.0)):
        await worker.start()
        await asyncio.sleep(0.03)
        await worker.stop()

    assert queue.dequeue.await_count >= 1
