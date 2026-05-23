# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for /digests REST surface — Phase K Day 2."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app() -> FastAPI:
    from app.routers.digests import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture()
def client():
    return TestClient(_make_app())


# Sample artifact rows as returned by graph_db.list_artifacts
def _sample_artifact(date: str = "2026-05-22", urgent: int = 0):
    return {
        "id": f"art:{date}",
        "domain": "digests",
        "filename": f"Daily Digest — {date}",
        "tags": {
            "digest_id": f"did-{date}",
            "generated_at": f"{date}T07:00:00Z",
            "window_hours": "24",
            "artifact_count": "12",
            "flagged_count": "2",
            "inbox_urgent_count": str(urgent),
        },
    }


class TestFeatureGate:
    def test_latest_blocked_when_feature_off(self, client):
        with patch("config.features.is_feature_enabled", return_value=False):
            resp = client.get("/digests/latest")
        assert resp.status_code == 403

    def test_run_now_blocked_when_feature_off(self, client):
        with patch("config.features.is_feature_enabled", return_value=False):
            resp = client.post("/digests/run-now")
        assert resp.status_code == 403


class TestLatest:
    def test_returns_none_when_no_digests(self, client):
        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch("app.deps.get_neo4j", return_value=object()),
            patch("app.routers.digests._list_digest_artifacts", return_value=[]),
        ):
            resp = client.get("/digests/latest")
        assert resp.status_code == 200
        assert resp.json() is None

    def test_returns_summary_shape(self, client):
        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch("app.deps.get_neo4j", return_value=object()),
            patch(
                "app.routers.digests._list_digest_artifacts",
                return_value=[_sample_artifact("2026-05-22", urgent=3)],
            ),
        ):
            resp = client.get("/digests/latest")
        body = resp.json()
        assert body["digest_id"] == "did-2026-05-22"
        assert body["artifact_count"] == 12
        assert body["inbox_urgent_count"] == 3
        assert body["has_urgent"] is True

    def test_no_urgent_flag_false(self, client):
        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch("app.deps.get_neo4j", return_value=object()),
            patch(
                "app.routers.digests._list_digest_artifacts",
                return_value=[_sample_artifact(urgent=0)],
            ),
        ):
            body = client.get("/digests/latest").json()
        assert body["has_urgent"] is False


class TestRecent:
    def test_returns_summary_list(self, client):
        artifacts = [
            _sample_artifact("2026-05-22"),
            _sample_artifact("2026-05-21"),
            _sample_artifact("2026-05-20"),
        ]
        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch("app.deps.get_neo4j", return_value=object()),
            patch("app.routers.digests._list_digest_artifacts", return_value=artifacts),
        ):
            resp = client.get("/digests/recent")
        body = resp.json()
        assert len(body) == 3

    def test_clamps_limit(self, client):
        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch("app.deps.get_neo4j", return_value=object()),
            patch("app.routers.digests._list_digest_artifacts", return_value=[]),
        ):
            # limit=1000 clamps to 30 internally — we just verify
            # the request doesn't 422 / 500
            resp = client.get("/digests/recent?limit=1000")
        assert resp.status_code == 200


class TestByDate:
    def test_invalid_date_returns_400(self, client):
        with patch("config.features.is_feature_enabled", return_value=True):
            resp = client.get("/digests/notadate")
        assert resp.status_code == 400

    def test_returns_match_when_present(self, client):
        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch("app.deps.get_neo4j", return_value=object()),
            patch(
                "app.routers.digests._list_digest_artifacts",
                return_value=[_sample_artifact("2026-05-21"), _sample_artifact("2026-05-20")],
            ),
        ):
            resp = client.get("/digests/2026-05-21")
        body = resp.json()
        assert body["digest_id"] == "did-2026-05-21"

    def test_returns_none_when_no_match(self, client):
        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch("app.deps.get_neo4j", return_value=object()),
            patch(
                "app.routers.digests._list_digest_artifacts",
                return_value=[_sample_artifact("2026-05-20")],
            ),
        ):
            resp = client.get("/digests/2026-05-01")
        assert resp.json() is None


class TestRunNow:
    def test_triggers_digest_pass(self, client):
        from core.agents.daily_digest import DigestResult

        fake = DigestResult(
            digest_id="did-test",
            generated_at="2026-05-22T07:00:00Z",
            window_hours=24,
            artifact_count=5,
        )
        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch(
                "core.agents.daily_digest.generate_daily_digest",
                new_callable=AsyncMock, return_value=fake,
            ),
        ):
            resp = client.post("/digests/run-now")
        assert resp.status_code == 200
        body = resp.json()
        assert body["digest_id"] == "did-test"
        assert body["artifact_count"] == 5
