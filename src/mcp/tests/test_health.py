# Copyright 2026 Cerid AI. Apache-2.0 license.
"""Tests for health check endpoints: /health, /health/ready, /health/live."""

from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from routers.health import health_check, router


def _make_app():
    """Create a minimal FastAPI app with just the health router."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    return app


class TestHealthCheckFunction:
    """Test the health_check() function directly."""

    @patch("app.routers.health.get_redis")
    @patch("app.routers.health.get_neo4j")
    @patch("app.routers.health.get_chroma")
    def test_all_connected(self, mock_chroma, mock_neo4j, mock_redis):
        """All services connected returns healthy status."""
        mock_chroma.return_value = MagicMock()
        driver = MagicMock()
        session = MagicMock()
        session.run.return_value.consume.return_value = MagicMock()
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_neo4j.return_value = driver
        mock_redis.return_value = MagicMock()

        result = health_check()
        assert result["services"]["chromadb"] == "connected"
        assert result["services"]["redis"] == "connected"
        assert result["services"]["neo4j"] == "connected"

    @patch("app.routers.health.get_redis")
    @patch("app.routers.health.get_neo4j")
    @patch("app.routers.health.get_chroma")
    def test_chroma_down(self, mock_chroma, mock_neo4j, mock_redis):
        """ChromaDB failure should report error, not crash."""
        mock_chroma.side_effect = RuntimeError("connection refused")
        mock_neo4j.return_value = None
        mock_redis.return_value = MagicMock()

        result = health_check()
        assert "error" in result["services"]["chromadb"]
        assert result["services"]["redis"] == "connected"

    @patch("app.routers.health.get_redis")
    @patch("app.routers.health.get_neo4j")
    @patch("app.routers.health.get_chroma")
    def test_neo4j_disabled(self, mock_chroma, mock_neo4j, mock_redis):
        """None driver means lightweight mode."""
        mock_chroma.return_value = MagicMock()
        mock_neo4j.return_value = None
        mock_redis.return_value = MagicMock()

        result = health_check()
        assert "disabled" in result["services"]["neo4j"] or "lightweight" in result["services"]["neo4j"]


class TestHealthEndpoints:
    """Test HTTP endpoints via TestClient."""

    @patch("app.routers.health.get_redis")
    @patch("app.routers.health.get_neo4j")
    @patch("app.routers.health.get_chroma")
    def test_health_live_always_200(self, mock_chroma, mock_neo4j, mock_redis):
        """Liveness probe should always return 200."""
        mock_chroma.return_value = MagicMock()
        mock_neo4j.return_value = None
        mock_redis.return_value = MagicMock()

        client = TestClient(_make_app())
        resp = client.get("/health/live")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "alive"

    @patch("app.routers.health.get_redis")
    @patch("app.routers.health.get_neo4j")
    @patch("app.routers.health.get_chroma")
    def test_health_endpoint_returns_json(self, mock_chroma, mock_neo4j, mock_redis):
        """Main /health returns JSON with service statuses."""
        mock_chroma.return_value = MagicMock()
        mock_neo4j.return_value = None
        mock_redis.return_value = MagicMock()

        client = TestClient(_make_app())
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "chromadb" in data["services"]
        assert "redis" in data["services"]
