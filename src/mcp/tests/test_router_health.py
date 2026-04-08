# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for health, collections, scheduler, and plugins endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app():
    from app.routers.health import router

    app = FastAPI()
    app.include_router(router)
    return app


class TestHealthEndpoint:
    def setup_method(self):
        """Reset the health cache between tests."""
        import app.routers.health as h
        h._health_cache = {}
        h._health_cache_ts = 0.0

    @patch("app.routers.health.get_redis")
    @patch("app.routers.health.get_chroma")
    @patch("app.routers.health.get_neo4j")
    def test_healthy_when_all_connected(self, mock_neo4j, mock_chroma, mock_redis):
        driver = MagicMock()
        session = MagicMock()
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_neo4j.return_value = driver

        client = TestClient(_make_app())
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["services"]["chromadb"] == "connected"
        assert data["services"]["redis"] == "connected"
        assert data["services"]["neo4j"] == "connected"

    @patch("app.routers.health.get_redis")
    @patch("app.routers.health.get_chroma")
    @patch("app.routers.health.get_neo4j")
    def test_degraded_when_service_down(self, mock_neo4j, mock_chroma, mock_redis):
        mock_neo4j.side_effect = ConnectionError("Neo4j unreachable")

        client = TestClient(_make_app())
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert "error" in data["services"]["neo4j"]

    @patch("app.routers.health.get_redis", side_effect=Exception("Redis down"))
    @patch("app.routers.health.get_chroma", side_effect=Exception("Chroma down"))
    @patch("app.routers.health.get_neo4j", side_effect=Exception("Neo4j down"))
    def test_degraded_when_all_services_down(self, mock_neo4j, mock_chroma, mock_redis):
        client = TestClient(_make_app())
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        for svc in data["services"].values():
            assert "error" in svc


class TestCollectionsEndpoint:
    @patch("app.routers.health.get_chroma")
    def test_returns_collection_list(self, mock_chroma):
        coll1, coll2 = MagicMock(), MagicMock()
        coll1.name = "kb_coding"
        coll2.name = "kb_finance"
        mock_chroma.return_value.list_collections.return_value = [coll1, coll2]

        client = TestClient(_make_app())
        response = client.get("/collections")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert "kb_coding" in data["collections"]
        assert "kb_finance" in data["collections"]

    @patch("app.routers.health.get_chroma")
    def test_returns_empty_when_no_collections(self, mock_chroma):
        mock_chroma.return_value.list_collections.return_value = []

        client = TestClient(_make_app())
        response = client.get("/collections")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["collections"] == []


class TestSchedulerEndpoint:
    def test_returns_job_status(self):
        mock_status = MagicMock(
            return_value={"jobs": [{"name": "rectify", "next_run": "2026-03-05T03:00:00"}]}
        )

        with patch.dict("sys.modules", {"scheduler": MagicMock(get_job_status=mock_status)}):
            client = TestClient(_make_app())
            response = client.get("/scheduler")

        assert response.status_code == 200


class TestPluginsEndpoint:
    def test_returns_plugins_and_features(self):
        mock_plugins = MagicMock(get_loaded_plugins=MagicMock(return_value=[]))
        mock_features = MagicMock(
            get_feature_status=MagicMock(
                return_value={"features": {"encryption": False}, "tier": "community"}
            )
        )

        with patch.dict(
            "sys.modules",
            {"plugins": mock_plugins, "utils.features": mock_features},
        ):
            client = TestClient(_make_app())
            response = client.get("/plugins")

        assert response.status_code == 200
        data = response.json()
        assert "plugins" in data
