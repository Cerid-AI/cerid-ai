# Copyright (c) 2026 Cerid AI. Apache-2.0 license.
"""Tests for all 12 SDK endpoints at /sdk/v1/.

Covers every endpoint in routers/sdk.py with mocked backend services,
verifying status codes, response shapes, and delegation wiring.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from routers.sdk import router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# 1. POST /sdk/v1/query
# ---------------------------------------------------------------------------


class TestSDKQuery:
    """POST /sdk/v1/query delegates to agent_query_endpoint."""

    @patch("routers.sdk.agent_query_endpoint", new_callable=AsyncMock)
    def test_query_success(self, mock_query):
        mock_query.return_value = {
            "results": [
                {
                    "content": "circuit breaker prevents cascading failures",
                    "relevance": 0.92,
                    "domain": "coding",
                    "artifact_id": "art-101",
                    "filename": "circuit_breaker.py",
                }
            ],
            "domains_searched": ["coding"],
            "total_results": 1,
        }

        client = TestClient(_make_app())
        resp = client.post(
            "/sdk/v1/query",
            json={"query": "circuit breaker pattern", "domains": ["coding"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert isinstance(data["results"], list)
        assert len(data["results"]) == 1
        assert data["results"][0]["domain"] == "coding"

    @patch("routers.sdk.agent_query_endpoint", new_callable=AsyncMock)
    def test_query_empty_results(self, mock_query):
        mock_query.return_value = {
            "results": [],
            "domains_searched": ["general"],
            "total_results": 0,
        }

        client = TestClient(_make_app())
        resp = client.post("/sdk/v1/query", json={"query": "nonexistent topic"})
        assert resp.status_code == 200
        assert resp.json()["results"] == []


# ---------------------------------------------------------------------------
# 2. POST /sdk/v1/hallucination
# ---------------------------------------------------------------------------


class TestSDKHallucination:
    """POST /sdk/v1/hallucination delegates to hallucination_check_endpoint."""

    @patch("routers.sdk.hallucination_check_endpoint", new_callable=AsyncMock)
    def test_hallucination_verified(self, mock_hall):
        mock_hall.return_value = {
            "claims": [
                {"claim": "Python uses a GIL", "status": "verified", "confidence": 0.95}
            ],
            "overall_status": "verified",
        }

        client = TestClient(_make_app())
        resp = client.post(
            "/sdk/v1/hallucination",
            json={
                "response_text": "Python uses a GIL for thread safety.",
                "query": "How does Python handle threads?",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "claims" in data
        assert data["claims"][0]["status"] == "verified"

    @patch("routers.sdk.hallucination_check_endpoint", new_callable=AsyncMock)
    def test_hallucination_unverified(self, mock_hall):
        mock_hall.return_value = {
            "claims": [
                {"claim": "Redis uses port 6380", "status": "unverified", "confidence": 0.1}
            ],
            "overall_status": "unverified",
        }

        client = TestClient(_make_app())
        resp = client.post(
            "/sdk/v1/hallucination",
            json={
                "response_text": "Redis uses port 6380 by default.",
                "context": "Redis configuration",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["claims"][0]["status"] == "unverified"


# ---------------------------------------------------------------------------
# 3. POST /sdk/v1/memory/extract
# ---------------------------------------------------------------------------


class TestSDKMemoryExtract:
    """POST /sdk/v1/memory/extract delegates to memory_extract_endpoint."""

    @patch("routers.sdk.memory_extract_endpoint", new_callable=AsyncMock)
    def test_memory_extract_success(self, mock_mem):
        mock_mem.return_value = {
            "status": "success",
            "memories_extracted": 2,
            "memories_stored": 2,
        }

        client = TestClient(_make_app())
        resp = client.post(
            "/sdk/v1/memory/extract",
            json={
                "text": "We use PostgreSQL 16 and Redis 7 for caching.",
                "conversation_id": "conv-42",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["memories_extracted"] == 2

    @patch("routers.sdk.memory_extract_endpoint", new_callable=AsyncMock)
    def test_memory_extract_no_memories(self, mock_mem):
        mock_mem.return_value = {
            "status": "success",
            "memories_extracted": 0,
            "memories_stored": 0,
        }

        client = TestClient(_make_app())
        resp = client.post(
            "/sdk/v1/memory/extract",
            json={"text": "Hello, how are you?", "conversation_id": "conv-43"},
        )
        assert resp.status_code == 200
        assert resp.json()["memories_extracted"] == 0


# ---------------------------------------------------------------------------
# 4. GET /sdk/v1/health
# ---------------------------------------------------------------------------


class TestSDKHealth:
    """GET /sdk/v1/health returns version, tier, services, and features."""

    @patch("routers.sdk.health_check")
    @patch("routers.sdk.config")
    def test_health_response_shape(self, mock_config, mock_health):
        mock_health.return_value = {
            "status": "healthy",
            "tier": "community",
            "services": {
                "chromadb": "connected",
                "redis": "connected",
                "neo4j": "connected",
            },
        }
        mock_config.INTERNAL_LLM_PROVIDER = "openrouter"
        mock_config.INTERNAL_LLM_MODEL = "anthropic/claude-sonnet-4"
        mock_config.OLLAMA_DEFAULT_MODEL = "llama3.2:3b"

        with patch("routers.sdk.config.features.FEATURE_TOGGLES", {
            "enable_hallucination_check": True,
            "enable_feedback_loop": True,
            "enable_self_rag": True,
            "enable_memory_extraction": True,
            "enable_some_internal_flag": False,
        }):
            client = TestClient(_make_app())
            resp = client.get("/sdk/v1/health")

        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert "services" in data
        assert isinstance(data["services"], dict)

    @patch("routers.sdk.health_check")
    @patch("routers.sdk.config")
    def test_health_includes_internal_llm(self, mock_config, mock_health):
        mock_health.return_value = {
            "status": "healthy",
            "tier": "community",
            "services": {},
        }
        mock_config.INTERNAL_LLM_PROVIDER = "ollama"
        mock_config.INTERNAL_LLM_MODEL = ""
        mock_config.OLLAMA_DEFAULT_MODEL = "llama3.2:3b"

        with patch("routers.sdk.config.features.FEATURE_TOGGLES", {}):
            client = TestClient(_make_app())
            resp = client.get("/sdk/v1/health")

        assert resp.status_code == 200
        data = resp.json()
        assert "internal_llm" in data
        assert data["internal_llm"]["provider"] == "ollama"


# ---------------------------------------------------------------------------
# 5. POST /sdk/v1/ingest
# ---------------------------------------------------------------------------


class TestSDKIngest:
    """POST /sdk/v1/ingest delegates to services.ingestion.ingest_content."""

    @patch("routers.sdk.ingest_content")
    def test_ingest_text_success(self, mock_ingest):
        mock_ingest.return_value = {
            "status": "success",
            "artifact_id": "art-200",
            "chunks": 3,
            "domain": "coding",
        }

        client = TestClient(_make_app())
        resp = client.post(
            "/sdk/v1/ingest",
            json={
                "content": "def hello(): pass",
                "domain": "coding",
                "tags": ["python", "example"],
            },
            headers={"x-client-id": "test-consumer"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["artifact_id"] == "art-200"
        assert data["chunks"] == 3

    @patch("routers.sdk.ingest_content")
    def test_ingest_without_tags(self, mock_ingest):
        mock_ingest.return_value = {
            "status": "success",
            "artifact_id": "art-201",
            "chunks": 1,
            "domain": "general",
        }

        client = TestClient(_make_app())
        resp = client.post(
            "/sdk/v1/ingest",
            json={"content": "Some general knowledge.", "domain": "general"},
        )
        assert resp.status_code == 200
        assert resp.json()["domain"] == "general"


# ---------------------------------------------------------------------------
# 6. POST /sdk/v1/ingest/file
# ---------------------------------------------------------------------------


class TestSDKIngestFile:
    """POST /sdk/v1/ingest/file delegates to services.ingestion.ingest_file."""

    @patch("routers.sdk.ingest_file", new_callable=AsyncMock)
    def test_ingest_file_success(self, mock_ingest_file):
        mock_ingest_file.return_value = {
            "status": "success",
            "artifact_id": "art-300",
            "chunks": 12,
            "domain": "finance",
        }

        client = TestClient(_make_app())
        resp = client.post(
            "/sdk/v1/ingest/file",
            json={
                "file_path": "/data/reports/q4.pdf",
                "domain": "finance",
                "tags": ["quarterly"],
            },
            headers={"x-client-id": "finance-dashboard"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["chunks"] == 12

    @patch("routers.sdk.ingest_file", new_callable=AsyncMock)
    def test_ingest_file_minimal(self, mock_ingest_file):
        mock_ingest_file.return_value = {
            "status": "success",
            "artifact_id": "art-301",
            "chunks": 1,
            "domain": "",
        }

        client = TestClient(_make_app())
        resp = client.post(
            "/sdk/v1/ingest/file",
            json={"file_path": "/data/notes.md"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 7. GET /sdk/v1/collections
# ---------------------------------------------------------------------------


class TestSDKCollections:
    """GET /sdk/v1/collections delegates to routers.health.list_collections."""

    @patch("routers.sdk.list_collections")
    def test_collections_success(self, mock_list):
        mock_list.return_value = {
            "collections": [
                {"name": "coding", "count": 1500},
                {"name": "finance", "count": 320},
            ],
            "total": 2,
        }

        client = TestClient(_make_app())
        resp = client.get("/sdk/v1/collections")
        assert resp.status_code == 200
        data = resp.json()
        assert "collections" in data
        assert data["total"] == 2
        assert len(data["collections"]) == 2

    @patch("routers.sdk.list_collections")
    def test_collections_empty(self, mock_list):
        mock_list.return_value = {"collections": [], "total": 0}

        client = TestClient(_make_app())
        resp = client.get("/sdk/v1/collections")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# 8. GET /sdk/v1/taxonomy
# ---------------------------------------------------------------------------


class TestSDKTaxonomy:
    """GET /sdk/v1/taxonomy delegates to config.taxonomy.DOMAINS/TAXONOMY."""

    @patch("routers.sdk.TAXONOMY", {"coding": {"tags": ["python", "rust"]}, "finance": {"tags": ["budget"]}})
    @patch("routers.sdk.DOMAINS", ["coding", "finance", "general"])
    def test_taxonomy_success(self):
        client = TestClient(_make_app())
        resp = client.get("/sdk/v1/taxonomy")
        assert resp.status_code == 200
        data = resp.json()
        assert "domains" in data
        assert "taxonomy" in data
        assert "coding" in data["domains"]
        assert isinstance(data["taxonomy"], dict)

    @patch("routers.sdk.TAXONOMY", {})
    @patch("routers.sdk.DOMAINS", [])
    def test_taxonomy_empty(self):
        client = TestClient(_make_app())
        resp = client.get("/sdk/v1/taxonomy")
        assert resp.status_code == 200
        assert resp.json()["domains"] == []


# ---------------------------------------------------------------------------
# 9. GET /sdk/v1/health/detailed
# ---------------------------------------------------------------------------


class TestSDKHealthDetailed:
    """GET /sdk/v1/health/detailed delegates to routers.health.degradation_status."""

    @patch("routers.sdk.degradation_status")
    def test_detailed_health_success(self, mock_degrad):
        mock_degrad.return_value = {
            "tier": "FULL",
            "services": {"chromadb": "up", "neo4j": "up", "redis": "up"},
            "circuit_breakers": {"chromadb": "closed", "neo4j": "closed"},
            "uptime_seconds": 86400,
        }

        client = TestClient(_make_app())
        resp = client.get("/sdk/v1/health/detailed")
        assert resp.status_code == 200
        data = resp.json()
        assert "tier" in data or "services" in data

    @patch("routers.sdk.degradation_status")
    def test_detailed_health_degraded(self, mock_degrad):
        mock_degrad.return_value = {
            "tier": "LITE",
            "services": {"chromadb": "down", "neo4j": "up", "redis": "up"},
            "circuit_breakers": {"chromadb": "open"},
            "uptime_seconds": 3600,
        }

        client = TestClient(_make_app())
        resp = client.get("/sdk/v1/health/detailed")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 10. GET /sdk/v1/settings
# ---------------------------------------------------------------------------


class TestSDKSettings:
    """GET /sdk/v1/settings delegates to config.features.FEATURE_FLAGS/FEATURE_TIER."""

    @patch("routers.sdk.FEATURE_TIER", "pro")
    @patch("routers.sdk.FEATURE_FLAGS", {"hallucination_check": True, "workflow_engine": True})
    def test_settings_success(self):
        client = TestClient(_make_app())
        resp = client.get("/sdk/v1/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == "1.1.0"
        assert data["tier"] == "pro"
        assert isinstance(data["features"], dict)

    @patch("routers.sdk.FEATURE_TIER", "community")
    @patch("routers.sdk.FEATURE_FLAGS", {})
    def test_settings_community_tier(self):
        client = TestClient(_make_app())
        resp = client.get("/sdk/v1/settings")
        assert resp.status_code == 200
        assert resp.json()["tier"] == "community"


# ---------------------------------------------------------------------------
# 11. POST /sdk/v1/search
# ---------------------------------------------------------------------------


class TestSDKSearch:
    """POST /sdk/v1/search delegates to routers.query.query_knowledge."""

    @patch("routers.sdk.query_knowledge")
    def test_search_success(self, mock_qk):
        mock_qk.return_value = {
            "sources": [
                {"title": "auth.py", "chunk_text": "JWT token validation", "similarity": 0.88}
            ],
            "confidence": 0.88,
        }

        client = TestClient(_make_app())
        resp = client.post(
            "/sdk/v1/search",
            json={"query": "JWT authentication", "domain": "coding", "top_k": 5},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert "total_results" in data
        assert data["total_results"] == 1

    @patch("routers.sdk.query_knowledge")
    def test_search_no_results(self, mock_qk):
        mock_qk.return_value = {"sources": [], "confidence": 0.0}

        client = TestClient(_make_app())
        resp = client.post(
            "/sdk/v1/search",
            json={"query": "nonexistent topic"},
        )
        assert resp.status_code == 200
        assert resp.json()["total_results"] == 0


# ---------------------------------------------------------------------------
# 12. GET /sdk/v1/plugins
# ---------------------------------------------------------------------------


class TestSDKPlugins:
    """GET /sdk/v1/plugins delegates to routers.plugins.list_plugins."""

    @patch("routers.sdk.list_plugins")
    def test_plugins_success(self, mock_lp):
        mock_lp.return_value = {
            "plugins": [
                {"name": "audio", "status": "loaded", "tier": "pro"},
                {"name": "vision", "status": "loaded", "tier": "pro"},
            ],
            "total": 2,
        }

        client = TestClient(_make_app())
        resp = client.get("/sdk/v1/plugins")
        assert resp.status_code == 200
        data = resp.json()
        assert "plugins" in data
        assert data["total"] == 2

    @patch("routers.sdk.list_plugins")
    def test_plugins_empty(self, mock_lp):
        mock_lp.return_value = {"plugins": [], "total": 0}

        client = TestClient(_make_app())
        resp = client.get("/sdk/v1/plugins")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# OpenAPI spec endpoint (auto-generated by FastAPI)
# ---------------------------------------------------------------------------


class TestSDKOpenAPISpec:
    """GET /sdk/v1/openapi.json should return a valid OpenAPI document."""

    def test_openapi_json(self):
        app = _make_app()
        # FastAPI serves the OpenAPI spec at /openapi.json by default
        client = TestClient(app)
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        data = resp.json()
        assert "openapi" in data
        assert "paths" in data
        # Verify SDK paths are present
        paths = list(data["paths"].keys())
        assert any("/sdk/v1/query" in p for p in paths)
        assert any("/sdk/v1/health" in p for p in paths)
        assert any("/sdk/v1/plugins" in p for p in paths)
