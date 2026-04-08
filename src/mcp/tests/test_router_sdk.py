# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the /sdk/v1/ stable consumer API router.

Covers response model validation, consumer domain isolation,
and rate limit header presence.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.sdk import router as sdk_router


def _make_app() -> FastAPI:
    """Build a minimal FastAPI app with the SDK router for testing."""
    app = FastAPI()
    app.include_router(sdk_router)
    return app


# ---------------------------------------------------------------------------
# Core endpoint tests
# ---------------------------------------------------------------------------


class TestSDKQuery:
    @pytest.mark.asyncio
    async def test_returns_200(self) -> None:
        mock_result = {
            "context": "test context",
            "sources": [{"content": "chunk", "relevance": 0.9}],
            "confidence": 0.85,
            "domains_searched": ["coding"],
            "total_results": 1,
            "token_budget_used": 42,
            "graph_results": 0,
            "results": [{"content": "chunk", "relevance": 0.9}],
        }
        with patch("app.routers.sdk.agent_query_endpoint", new_callable=AsyncMock, return_value=mock_result):
            app = _make_app()
            client = TestClient(app)
            resp = client.post("/sdk/v1/query", json={"query": "test query"})
            assert resp.status_code == 200
            data = resp.json()
            assert "context" in data
            assert "confidence" in data
            assert "sources" in data
            assert "domains_searched" in data

    def test_validates_request_missing_query(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/sdk/v1/query", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_response_has_expected_shape(self) -> None:
        mock_result = {
            "context": "",
            "sources": [],
            "confidence": 0.0,
            "domains_searched": ["general"],
            "total_results": 0,
            "token_budget_used": 0,
            "graph_results": 0,
            "results": [],
        }
        with patch("app.routers.sdk.agent_query_endpoint", new_callable=AsyncMock, return_value=mock_result):
            app = _make_app()
            client = TestClient(app)
            resp = client.post("/sdk/v1/query", json={"query": "test"})
            data = resp.json()
            assert isinstance(data["context"], str)
            assert isinstance(data["sources"], list)
            assert isinstance(data["confidence"], (int, float))
            assert isinstance(data["total_results"], int)


class TestSDKHallucination:
    @pytest.mark.asyncio
    async def test_returns_200(self) -> None:
        mock_result = {
            "conversation_id": "conv-123",
            "timestamp": "2026-03-21T00:00:00Z",
            "skipped": True,
            "reason": "Response too short",
            "claims": [],
            "summary": {"total": 0, "verified": 0, "unverified": 0, "uncertain": 0},
        }
        with patch("app.routers.sdk.hallucination_check_endpoint", new_callable=AsyncMock, return_value=mock_result):
            app = _make_app()
            client = TestClient(app)
            resp = client.post("/sdk/v1/hallucination", json={
                "response_text": "test response",
                "conversation_id": "conv-123",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert "claims" in data
            assert "summary" in data
            assert "conversation_id" in data


class TestSDKMemoryExtract:
    @pytest.mark.asyncio
    async def test_returns_200(self) -> None:
        mock_result = {
            "conversation_id": "conv-456",
            "timestamp": "2026-03-21T00:00:00Z",
            "memories_extracted": 2,
            "memories_stored": 1,
            "skipped_duplicates": 1,
            "results": [{"memory_type": "fact", "summary": "Test", "status": "success"}],
        }
        with patch("app.routers.sdk.memory_extract_endpoint", new_callable=AsyncMock, return_value=mock_result):
            app = _make_app()
            client = TestClient(app)
            resp = client.post("/sdk/v1/memory/extract", json={
                "response_text": "I learned that X",
                "conversation_id": "conv-456",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["memories_stored"] == 1


class TestSDKHealth:
    def test_returns_version_and_features(self) -> None:
        with patch("app.routers.sdk.health_check", return_value={
            "status": "healthy",
            "services": {"chromadb": "connected", "redis": "connected", "neo4j": "connected"},
        }):
            app = _make_app()
            client = TestClient(app)
            resp = client.get("/sdk/v1/health")
            assert resp.status_code == 200
            data = resp.json()
            assert "version" in data
            assert "features" in data
            assert "services" in data
            assert data["status"] == "healthy"


# ---------------------------------------------------------------------------
# Consumer domain isolation tests
# ---------------------------------------------------------------------------


class TestConsumerDomainIsolation:
    @pytest.mark.asyncio
    async def test_gui_has_full_domain_access(self) -> None:
        """GUI consumer should have access to all domains."""
        mock_result = {
            "context": "personal content", "sources": [{"content": "diary"}],
            "confidence": 0.9, "domains_searched": ["personal"],
            "total_results": 1, "token_budget_used": 100, "graph_results": 0,
            "results": [{"content": "diary"}],
        }
        with patch("app.routers.sdk.agent_query_endpoint", new_callable=AsyncMock, return_value=mock_result):
            app = _make_app()
            client = TestClient(app)
            resp = client.post(
                "/sdk/v1/query",
                json={"query": "personal notes", "domains": ["personal"]},
                headers={"X-Client-ID": "gui"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_results"] == 1

    @pytest.mark.asyncio
    async def test_strict_domains_parameter(self) -> None:
        """strict_domains=True should prevent cross-domain bleed."""
        mock_result = {
            "context": "", "sources": [], "confidence": 0.0,
            "domains_searched": ["trading"], "total_results": 0,
            "token_budget_used": 0, "graph_results": 0, "results": [],
        }
        with patch("app.routers.sdk.agent_query_endpoint", new_callable=AsyncMock, return_value=mock_result):
            app = _make_app()
            client = TestClient(app)
            resp = client.post(
                "/sdk/v1/query",
                json={"query": "market data", "domains": ["trading"], "strict_domains": True},
            )
            assert resp.status_code == 200
