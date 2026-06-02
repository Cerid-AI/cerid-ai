# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for /analytics REST surface — Phase L."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app() -> FastAPI:
    from app.routers.analytics import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture()
def client():
    return TestClient(_make_app())


@pytest.fixture(autouse=True)
def _pro_tier():
    """The /analytics surface is the Pro `advanced_analytics` feature, so the
    functional tests run at Pro tier. Restores the original tier afterward."""
    from config.features import FEATURE_TIER, set_tier

    original = FEATURE_TIER
    set_tier("pro")
    try:
        yield
    finally:
        set_tier(original)


# ── advanced_analytics gate ───────────────────────────────────────────

class TestAdvancedAnalyticsGate:
    ENDPOINTS = [
        "/analytics/ingestion-by-day",
        "/analytics/cost-by-stage",
        "/analytics/quality-timeline?window_days=7",
    ]

    def test_community_tier_is_denied(self, client):
        from config.features import set_tier

        set_tier("community")
        for path in self.ENDPOINTS:
            resp = client.get(path)
            assert resp.status_code == 403, f"{path} should be Pro-gated"

    def test_pro_tier_is_allowed(self, client):
        # _pro_tier fixture already set Pro; gate must not block.
        with patch("app.deps.get_neo4j", side_effect=RuntimeError("down")):
            resp = client.get("/analytics/ingestion-by-day")
        assert resp.status_code == 200


# ── ingestion-by-day ──────────────────────────────────────────────────

class TestIngestionByDay:
    def test_empty_when_neo4j_unavailable(self, client):
        with patch("app.deps.get_neo4j", side_effect=RuntimeError("down")):
            resp = client.get("/analytics/ingestion-by-day")
        assert resp.status_code == 200
        body = resp.json()
        assert body["buckets"] == []
        assert body["total"] == 0

    def test_buckets_by_day_with_intensity(self, client):
        rows = [
            {"day": "2026-05-20", "domain": "notes", "n": 5},
            {"day": "2026-05-20", "domain": "mail", "n": 3},
            {"day": "2026-05-21", "domain": "notes", "n": 10},
            {"day": "2026-05-22", "domain": "notes", "n": 2},
        ]
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch("app.routers.analytics._run_cypher", return_value=rows),
        ):
            resp = client.get("/analytics/ingestion-by-day")
        body = resp.json()
        assert body["total"] == 20
        assert body["peak_count"] == 10
        # 3 distinct days
        assert len(body["buckets"]) == 3
        # Peak day has intensity == 1.0
        peak = next(b for b in body["buckets"] if b["date"] == "2026-05-21")
        assert peak["count"] == 10
        assert peak["intensity"] == 1.0
        # Lowest day intensity proportional to peak
        low = next(b for b in body["buckets"] if b["date"] == "2026-05-22")
        assert low["intensity"] == 0.2

    def test_combines_domains_per_day(self, client):
        rows = [
            {"day": "2026-05-20", "domain": "notes", "n": 4},
            {"day": "2026-05-20", "domain": "mail", "n": 6},
        ]
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch("app.routers.analytics._run_cypher", return_value=rows),
        ):
            body = client.get("/analytics/ingestion-by-day").json()
        b = body["buckets"][0]
        assert b["count"] == 10
        assert b["domains"]["notes"] == 4
        assert b["domains"]["mail"] == 6

    def test_window_param_validation(self, client):
        with patch("app.deps.get_neo4j", side_effect=RuntimeError("down")):
            resp = client.get("/analytics/ingestion-by-day?window_days=0")
        assert resp.status_code == 422
        resp2 = client.get("/analytics/ingestion-by-day?window_days=10000")
        assert resp2.status_code == 422


# ── cost-by-stage ─────────────────────────────────────────────────────

class TestCostByStage:
    def _stub_collector(self, points: list[Any]):
        c = MagicMock()
        c.get_metrics.return_value = points
        return c

    def test_no_points_returns_empty(self, client):
        with patch(
            "utils.metrics.get_metrics_collector",
            return_value=self._stub_collector([]),
        ):
            resp = client.get("/analytics/cost-by-stage")
        body = resp.json()
        assert body["total_cost_usd"] == 0.0
        assert body["stages"] == []
        assert body["edges"] == []

    def test_aggregates_by_stage_tag(self, client):
        points = [
            SimpleNamespace(value=0.01, tags={"stage": "entity_extraction", "model": "x"}),
            SimpleNamespace(value=0.02, tags={"stage": "entity_extraction", "model": "x"}),
            SimpleNamespace(value=0.05, tags={"stage": "daily_digest", "model": "y"}),
        ]
        with patch(
            "utils.metrics.get_metrics_collector",
            return_value=self._stub_collector(points),
        ):
            body = client.get("/analytics/cost-by-stage").json()
        assert body["total_cost_usd"] == pytest.approx(0.08)
        # Sorted by cost desc
        assert body["stages"][0]["stage"] == "daily_digest"
        assert body["stages"][0]["cost_usd"] == pytest.approx(0.05)
        assert body["stages"][1]["stage"] == "entity_extraction"
        assert body["stages"][1]["call_count"] == 2

    def test_unknown_stage_buckets_to_other(self, client):
        points = [SimpleNamespace(value=0.03, tags={"stage": "bogus_stage"})]
        with patch(
            "utils.metrics.get_metrics_collector",
            return_value=self._stub_collector(points),
        ):
            body = client.get("/analytics/cost-by-stage").json()
        assert any(s["stage"] == "other" for s in body["stages"])

    def test_sankey_edges_emit_provider_source(self, client):
        points = [SimpleNamespace(value=0.1, tags={"stage": "daily_digest"})]
        with patch(
            "utils.metrics.get_metrics_collector",
            return_value=self._stub_collector(points),
        ):
            body = client.get("/analytics/cost-by-stage").json()
        assert len(body["edges"]) == 1
        edge = body["edges"][0]
        # daily_digest → pro_features provider per the static mapping
        assert edge["source"] == "pro_features"
        assert edge["target"] == "daily_digest"
        assert edge["value"] == pytest.approx(0.1)

    def test_no_value_points_skipped_from_edges(self, client):
        # Zero-cost points still count toward stages but not edges
        points = [SimpleNamespace(value=0.0, tags={"stage": "entity_extraction"})]
        with patch(
            "utils.metrics.get_metrics_collector",
            return_value=self._stub_collector(points),
        ):
            body = client.get("/analytics/cost-by-stage").json()
        assert body["edges"] == []


# ── quality-timeline ──────────────────────────────────────────────────

class TestQualityTimeline:
    def test_all_zero_when_no_points(self, client):
        c = MagicMock()
        c.get_metrics.return_value = []
        with patch("utils.metrics.get_metrics_collector", return_value=c):
            body = client.get("/analytics/quality-timeline?window_days=7").json()
        assert len(body["points"]) == 7
        # Every metric null on every day
        for p in body["points"]:
            assert p["ndcg"] is None
            assert p["faithfulness"] is None
        assert body["latest"]["ndcg"] is None

    def test_aggregates_points_to_daily_average(self, client):
        # Use today's date so the test points always land inside the
        # rolling 7-day window. A hardcoded date would silently fall out
        # of the window the moment the calendar moved past it + 7 days,
        # turning this into a time-bomb test.
        today_iso = datetime.now(tz=timezone.utc).date().isoformat()
        ndcg_pts = [
            SimpleNamespace(timestamp=f"{today_iso}T10:00:00Z", value=0.8, tags={}),
            SimpleNamespace(timestamp=f"{today_iso}T22:00:00Z", value=0.9, tags={}),
        ]

        def _get(name, _window):
            if name == "retrieval_ndcg":
                return ndcg_pts
            return []

        c = MagicMock()
        c.get_metrics.side_effect = _get
        with patch("utils.metrics.get_metrics_collector", return_value=c):
            body = client.get("/analytics/quality-timeline?window_days=7").json()

        # Find today's entry and check averaged ndcg
        day = next((p for p in body["points"] if p["date"] == today_iso), None)
        assert day is not None, f"today ({today_iso}) should be in the rolling window"
        assert day["ndcg"] == pytest.approx(0.85)
        # latest carries the avg
        assert body["latest"]["ndcg"] == pytest.approx(0.85)

    def test_window_validation(self, client):
        c = MagicMock()
        c.get_metrics.return_value = []
        with patch("utils.metrics.get_metrics_collector", return_value=c):
            resp = client.get("/analytics/quality-timeline?window_days=3")
        assert resp.status_code == 422  # min 7
        with patch("utils.metrics.get_metrics_collector", return_value=c):
            resp = client.get("/analytics/quality-timeline?window_days=400")
        assert resp.status_code == 422  # max 365
