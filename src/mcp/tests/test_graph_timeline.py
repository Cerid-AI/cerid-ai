# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for GET /graph/timeline — Phase M Day 1-2."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app() -> FastAPI:
    from app.routers.graph import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture()
def client():
    return TestClient(_make_app())


# ── helpers ─────────────────────────────────────────────────────────

class TestGranularityResolution:
    def test_explicit_overrides_window(self):
        from app.routers.graph import _resolve_granularity
        assert _resolve_granularity(30, "month") == "month"
        assert _resolve_granularity(500, "day") == "day"

    def test_short_window_picks_day(self):
        from app.routers.graph import _resolve_granularity
        assert _resolve_granularity(7, None) == "day"
        assert _resolve_granularity(90, None) == "day"

    def test_medium_window_picks_week(self):
        from app.routers.graph import _resolve_granularity
        assert _resolve_granularity(180, None) == "week"
        assert _resolve_granularity(365, None) == "week"

    def test_long_window_picks_month(self):
        from app.routers.graph import _resolve_granularity
        assert _resolve_granularity(500, None) == "month"

    def test_unknown_explicit_falls_through_to_auto(self):
        from app.routers.graph import _resolve_granularity
        assert _resolve_granularity(30, "bogus") == "day"


class TestBucketKey:
    def test_day_bucket(self):
        from app.routers.graph import _bucket_key
        assert _bucket_key("2026-05-22T14:30:00+00:00", "day") == "2026-05-22"

    def test_month_bucket(self):
        from app.routers.graph import _bucket_key
        assert _bucket_key("2026-05-22T14:30:00+00:00", "month") == "2026-05"

    def test_week_bucket_returns_monday(self):
        from app.routers.graph import _bucket_key
        # 2026-05-22 was a Friday → Monday is 2026-05-18
        assert _bucket_key("2026-05-22T14:30:00+00:00", "week") == "2026-05-18"

    def test_empty_string_safe(self):
        from app.routers.graph import _bucket_key
        assert _bucket_key("", "day") == ""

    def test_malformed_falls_back_to_prefix(self):
        from app.routers.graph import _bucket_key
        # Invalid ISO → just take the first 10 chars (defensive)
        assert _bucket_key("garbage-string", "week") == "garbage-st"


class TestPeriodParsing:
    def test_7d_30d_90d_365d(self):
        from app.routers.graph import _parse_period
        assert _parse_period("7d") == 7
        assert _parse_period("30d") == 30
        assert _parse_period("90d") == 90
        assert _parse_period("365d") == 365

    def test_invalid_falls_back_to_30(self):
        from app.routers.graph import _parse_period
        assert _parse_period("") == 30
        assert _parse_period("bogus") == 30
        assert _parse_period("30") == 30  # missing 'd'

    def test_clamps_to_730_max(self):
        from app.routers.graph import _parse_period
        assert _parse_period("9999d") == 730


# ── endpoint behavior ──────────────────────────────────────────────

class TestEndpointSurface:
    def test_returns_empty_when_neo4j_unavailable(self, client):
        with patch("app.deps.get_neo4j", side_effect=RuntimeError("down")):
            resp = client.get("/graph/timeline?period=7d")
        assert resp.status_code == 200
        body = resp.json()
        assert body["buckets"] == []
        assert body["total_mentions"] == 0

    def test_buckets_global_mentions(self, client):
        rows = [
            {"ts": "2026-05-20T10:00:00+00:00", "is_birth": False},
            {"ts": "2026-05-20T11:00:00+00:00", "is_birth": False},
            {"ts": "2026-05-20T12:00:00+00:00", "is_birth": True},
            {"ts": "2026-05-21T09:00:00+00:00", "is_birth": False},
        ]
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch("app.routers.graph._run_timeline_cypher", return_value=rows),
            patch("app.deps.get_redis", return_value=None),
        ):
            resp = client.get("/graph/timeline?period=7d")
        body = resp.json()
        # Two distinct days
        assert len(body["buckets"]) == 2
        day0 = next(b for b in body["buckets"] if b["date"] == "2026-05-20")
        assert day0["mention_count"] == 2
        assert day0["entities_introduced"] == 1
        day1 = next(b for b in body["buckets"] if b["date"] == "2026-05-21")
        assert day1["mention_count"] == 1
        assert day1["entities_introduced"] == 0

    def test_aggregated_day_count_is_honored(self, client):
        """The Cypher now returns one row per day with a count(*); the bucket
        must reflect that count, not 1-per-row (the perf fix)."""
        rows = [
            {"ts": "2026-05-20", "is_birth": False, "c": 5},
            {"ts": "2026-05-20", "is_birth": True, "c": 2},
            {"ts": "2026-05-21", "is_birth": False, "c": 3},
        ]
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch("app.routers.graph._run_timeline_cypher", return_value=rows),
            patch("app.deps.get_redis", return_value=None),
        ):
            resp = client.get("/graph/timeline?period=7d")
        body = resp.json()
        day0 = next(b for b in body["buckets"] if b["date"] == "2026-05-20")
        assert day0["mention_count"] == 5
        assert day0["entities_introduced"] == 2
        assert body["total_mentions"] == 8  # 5 + 3

    def test_timeline_cypher_aggregates_in_db(self, client):
        """Guard the perf fix: the query must count(*) per day, not return a
        row per MENTIONS edge."""
        captured: dict = {}

        def _capture(_driver, cypher, _params):
            captured["cypher"] = cypher
            return []

        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch("app.routers.graph._run_timeline_cypher", side_effect=_capture),
            patch("app.deps.get_redis", return_value=None),
        ):
            client.get("/graph/timeline?period=7d")
        assert "count(*)" in captured["cypher"]
        assert "substring(m.created_at, 0, 10)" in captured["cypher"]

    def test_entity_filter_passes_through(self, client):
        captured: dict = {}

        def _capture_cypher(_driver, _cypher, params):
            captured.update(params)
            return []

        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch("app.routers.graph._run_timeline_cypher", side_effect=_capture_cypher),
            patch("app.deps.get_redis", return_value=None),
        ):
            client.get("/graph/timeline?entity=alice&period=7d")
        assert captured.get("entity") == "alice"

    def test_invalid_period_falls_back_default(self, client):
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch("app.routers.graph._run_timeline_cypher", return_value=[]),
            patch("app.deps.get_redis", return_value=None),
        ):
            resp = client.get("/graph/timeline?period=bogus")
        assert resp.status_code == 200
        body = resp.json()
        # Default 30d window resolves to day granularity
        assert body["granularity"] == "day"

    def test_rejects_inverted_window(self, client):
        with patch("app.deps.get_neo4j", side_effect=RuntimeError("ignored")):
            resp = client.get(
                "/graph/timeline?from=2026-06-01&to=2026-05-01",
            )
        assert resp.status_code == 400

    def test_rejects_oversized_window(self, client):
        with patch("app.deps.get_neo4j", side_effect=RuntimeError("ignored")):
            resp = client.get(
                "/graph/timeline?from=2020-01-01&to=2026-01-01",
            )
        assert resp.status_code == 400

    def test_rejects_invalid_iso(self, client):
        resp = client.get("/graph/timeline?from=not-a-date&to=2026-05-01")
        assert resp.status_code == 400

    def test_aggregates_at_month_granularity(self, client):
        rows = [
            {"ts": "2026-03-15T10:00:00+00:00", "is_birth": False},
            {"ts": "2026-03-22T11:00:00+00:00", "is_birth": False},
            {"ts": "2026-04-02T09:00:00+00:00", "is_birth": True},
        ]
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch("app.routers.graph._run_timeline_cypher", return_value=rows),
            patch("app.deps.get_redis", return_value=None),
        ):
            resp = client.get("/graph/timeline?period=365d&granularity=month")
        body = resp.json()
        # Two buckets — march and april
        assert {b["date"] for b in body["buckets"]} == {"2026-03", "2026-04"}
        mar = next(b for b in body["buckets"] if b["date"] == "2026-03")
        assert mar["mention_count"] == 2

    def test_totals_match_sum_of_buckets(self, client):
        rows = [
            {"ts": "2026-05-20T00:00:00+00:00", "is_birth": False},
            {"ts": "2026-05-20T00:00:00+00:00", "is_birth": True},
            {"ts": "2026-05-21T00:00:00+00:00", "is_birth": True},
        ]
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch("app.routers.graph._run_timeline_cypher", return_value=rows),
            patch("app.deps.get_redis", return_value=None),
        ):
            body = client.get("/graph/timeline?period=7d").json()
        assert body["total_mentions"] == 1
        assert body["total_entities_introduced"] == 2
