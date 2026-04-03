# Copyright 2026 Cerid AI. Apache-2.0 license.
"""Tests for the SDK contract endpoints at /sdk/v1/.

Verifies response shapes match the typed Pydantic models, ensuring
external consumers get a stable API surface.
"""

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from starlette.testclient import TestClient

from routers.sdk import router


def _make_app():
    app = FastAPI()
    app.include_router(router)
    return app


class TestSDKHealth:
    """GET /sdk/v1/health should return version, tier, and service statuses."""

    @patch("routers.sdk.health_check")
    @patch("routers.sdk.config")
    def test_health_response_shape(self, mock_config, mock_health):
        mock_health.return_value = {
            "status": "healthy",
            "services": {
                "chromadb": "connected",
                "redis": "connected",
                "neo4j": "connected",
            },
        }
        mock_config.INTERNAL_LLM_PROVIDER = "openrouter"
        mock_config.INTERNAL_LLM_MODEL = "anthropic/claude-sonnet-4"
        mock_config.OLLAMA_DEFAULT_MODEL = "llama3.2:3b"

        with patch("config.features.FEATURE_TOGGLES", {
            "enable_hallucination_check": True,
            "enable_memory_extraction": True,
        }):
            client = TestClient(_make_app())
            resp = client.get("/sdk/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert "services" in data
        assert isinstance(data["services"], dict)


class TestSDKQuery:
    """POST /sdk/v1/query should return results list with metadata."""

    @patch("routers.sdk.agent_query_endpoint", new_callable=AsyncMock)
    def test_query_response_shape(self, mock_agent_query):
        mock_agent_query.return_value = {
            "results": [
                {
                    "content": "test result",
                    "relevance": 0.85,
                    "domain": "coding",
                    "artifact_id": "art-1",
                    "filename": "test.py",
                }
            ],
            "domains_searched": ["coding"],
            "total_results": 1,
        }

        client = TestClient(_make_app())
        resp = client.post(
            "/sdk/v1/query",
            json={"query": "test query", "domains": ["coding"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert isinstance(data["results"], list)


class TestSDKHallucination:
    """POST /sdk/v1/hallucination should return claims with verdicts."""

    @patch("routers.sdk.hallucination_check_endpoint", new_callable=AsyncMock)
    def test_hallucination_response_shape(self, mock_hall):
        mock_hall.return_value = {
            "claims": [
                {"claim": "Python was created in 1991", "status": "verified", "confidence": 0.9}
            ],
            "overall_status": "mostly_verified",
        }

        client = TestClient(_make_app())
        resp = client.post(
            "/sdk/v1/hallucination",
            json={"response_text": "Python was created in 1991.", "conversation_id": "conv-1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "claims" in data


class TestSDKMemoryExtract:
    """POST /sdk/v1/memory/extract should return extraction results."""

    @patch("routers.sdk.memory_extract_endpoint", new_callable=AsyncMock)
    def test_memory_extract_response_shape(self, mock_mem):
        mock_mem.return_value = {
            "status": "success",
            "memories_extracted": 1,
            "memories_stored": 1,
        }

        client = TestClient(_make_app())
        resp = client.post(
            "/sdk/v1/memory/extract",
            json={"response_text": "Python uses a GIL for thread safety.", "conversation_id": "conv-1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
