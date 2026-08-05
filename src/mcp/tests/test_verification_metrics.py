# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Unit tests for app.observability.verification_metrics (Phase 0.4a).

All tests use in-process FakeRedis or a monkeypatched app.deps.get_redis
so no real Redis is required.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
import pytest

from app.observability.verification_metrics import (
    _day_key,
    get_verification_rates,
    record_verification_report,
)


@pytest.fixture
def redis_client():
    server = fakeredis.FakeServer()
    return fakeredis.FakeRedis(server=server, decode_responses=True)


@pytest.fixture(autouse=True)
def _patch_get_redis(monkeypatch, redis_client):
    """Route every ``from app.deps import get_redis`` call in this module
    to the fakeredis fixture, so tests never touch a real Redis."""
    monkeypatch.setattr("app.deps.get_redis", lambda: redis_client, raising=False)
    return redis_client


# ---------------------------------------------------------------------------
# record_verification_report + get_verification_rates round-trip
# ---------------------------------------------------------------------------


def test_record_then_read_today(redis_client):
    record_verification_report(claims_total=10, uncertain_count=3, timeout_count=1)
    rates = get_verification_rates()
    today = rates["today"]
    assert today["claims_total"] == 10
    assert today["uncertain_count"] == 3
    assert today["timeout_count"] == 1
    assert today["reports_total"] == 1
    assert today["uncertain_rate"] == pytest.approx(0.3)
    assert today["timeout_rate"] == pytest.approx(0.1)


def test_multiple_reports_accumulate(redis_client):
    record_verification_report(claims_total=4, uncertain_count=1, timeout_count=0)
    record_verification_report(claims_total=6, uncertain_count=1, timeout_count=2)
    rates = get_verification_rates()
    today = rates["today"]
    assert today["claims_total"] == 10
    assert today["uncertain_count"] == 2
    assert today["timeout_count"] == 2
    assert today["reports_total"] == 2


def test_last_7d_includes_today(redis_client):
    record_verification_report(claims_total=5, uncertain_count=1, timeout_count=1)
    rates = get_verification_rates()
    assert rates["last_7d"]["claims_total"] == 5
    assert rates["last_7d"]["reports_total"] == 1


def test_last_7d_includes_prior_day(redis_client):
    from datetime import datetime, timedelta, timezone

    yesterday = _day_key(datetime.now(timezone.utc) - timedelta(days=1))
    redis_client.incrby(f"cerid:metrics:verification:{yesterday}:claims_total", 7)
    redis_client.incrby(f"cerid:metrics:verification:{yesterday}:reports_total", 1)

    record_verification_report(claims_total=3, uncertain_count=0, timeout_count=0)
    rates = get_verification_rates()
    assert rates["last_7d"]["claims_total"] == 10
    assert rates["last_7d"]["reports_total"] == 2
    # today alone must NOT include yesterday's counters
    assert rates["today"]["claims_total"] == 3


def test_rates_are_none_when_no_claims(redis_client):
    """No data yet — rates must be None, not a misleading 0.0."""
    rates = get_verification_rates()
    assert rates["today"]["claims_total"] == 0
    assert rates["today"]["timeout_rate"] is None
    assert rates["today"]["uncertain_rate"] is None


# ---------------------------------------------------------------------------
# Defensive: Redis errors never raise
# ---------------------------------------------------------------------------


def test_record_defensive_on_error(monkeypatch):
    broken = MagicMock()
    broken.pipeline.side_effect = ConnectionError("redis down")
    monkeypatch.setattr("app.deps.get_redis", lambda: broken, raising=False)
    # Must not raise
    record_verification_report(claims_total=1, uncertain_count=1, timeout_count=1)


def test_get_rates_defensive_on_error(monkeypatch):
    def _broken():
        raise ConnectionError("redis down")

    monkeypatch.setattr("app.deps.get_redis", _broken, raising=False)
    rates = get_verification_rates()
    assert rates["today"]["claims_total"] == 0
    assert rates["today"]["timeout_rate"] is None
    assert rates["last_7d"]["claims_total"] == 0
