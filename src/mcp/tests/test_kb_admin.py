# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for KB admin endpoints."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    """Create a test client with mocked dependencies."""
    from main import app  # noqa: E402 — triggers router imports

    with (
        patch("routers.kb_admin.get_neo4j", return_value=MagicMock()),
        patch("routers.kb_admin.get_chroma", return_value=MagicMock()),
    ):
        yield TestClient(app, raise_server_exceptions=False)


class TestRebuildIndexes:
    def test_rebuild_indexes_success(self, client: TestClient):
        with patch("routers.kb_admin.rebuild_bm25_all", return_value=5):
            with patch("routers.kb_admin.invalidate_cache_non_blocking", new_callable=AsyncMock):
                res = client.post("/admin/kb/rebuild-index")
        assert res.status_code == 200
        data = res.json()
        assert data["domains_rebuilt"] == 5
        assert "5 domains" in data["message"]

    def test_rebuild_indexes_failure(self, client: TestClient):
        with patch("routers.kb_admin.rebuild_bm25_all", side_effect=RuntimeError("disk error")):
            res = client.post("/admin/kb/rebuild-index")
        assert res.status_code == 500
        assert "disk error" in res.json()["detail"]


class TestRescore:
    def test_rescore_all(self, client: TestClient):
        mock_result = {
            "artifacts_scored": 42,
            "avg_quality_score": 0.75,
            "artifacts_stored": 42,
            "synopses_generated": 0,
            "score_distribution": {"excellent": 10, "good": 20, "fair": 10, "poor": 2},
            "domains_scored": ["code"],
            "low_quality_artifacts": [],
            "timestamp": "2026-03-09T00:00:00Z",
            "mode": "audit",
        }
        with patch("routers.kb_admin.curate", new_callable=AsyncMock, return_value=mock_result):
            with patch("routers.kb_admin.invalidate_cache_non_blocking", new_callable=AsyncMock):
                res = client.post("/admin/kb/rescore")
        assert res.status_code == 200
        data = res.json()
        assert data["artifacts_scored"] == 42
        assert data["avg_quality_score"] == 0.75

    def test_rescore_with_domain_filter(self, client: TestClient):
        mock_result = {
            "artifacts_scored": 10,
            "avg_quality_score": 0.80,
            "artifacts_stored": 10,
            "synopses_generated": 0,
            "score_distribution": {"excellent": 5, "good": 5, "fair": 0, "poor": 0},
            "domains_scored": ["finance"],
            "low_quality_artifacts": [],
            "timestamp": "2026-03-09T00:00:00Z",
            "mode": "audit",
        }
        with patch("routers.kb_admin.curate", new_callable=AsyncMock, return_value=mock_result):
            with patch("routers.kb_admin.invalidate_cache_non_blocking", new_callable=AsyncMock):
                res = client.post(
                    "/admin/kb/rescore",
                    json={"domains": ["finance"], "max_artifacts": 50},
                )
        assert res.status_code == 200
        assert res.json()["artifacts_scored"] == 10


class TestRegenerateSummaries:
    def test_regenerate_success(self, client: TestClient):
        mock_result = {
            "artifacts_scored": 20,
            "avg_quality_score": 0.65,
            "artifacts_stored": 20,
            "synopses_generated": 8,
            "score_distribution": {"excellent": 5, "good": 10, "fair": 5, "poor": 0},
            "domains_scored": ["code"],
            "low_quality_artifacts": [],
            "timestamp": "2026-03-09T00:00:00Z",
            "mode": "audit",
        }
        with patch("routers.kb_admin.curate", new_callable=AsyncMock, return_value=mock_result):
            with patch("routers.kb_admin.invalidate_cache_non_blocking", new_callable=AsyncMock):
                res = client.post("/admin/kb/regenerate-summaries")
        assert res.status_code == 200
        data = res.json()
        assert data["synopses_generated"] == 8


class TestClearDomain:
    def test_clear_requires_confirm(self, client: TestClient):
        res = client.post(
            "/admin/kb/clear-domain/code",
            json={"confirm": False},
        )
        assert res.status_code == 400
        assert "confirm" in res.json()["detail"].lower()

    def test_clear_unknown_domain(self, client: TestClient):
        res = client.post(
            "/admin/kb/clear-domain/nonexistent_domain_xyz",
            json={"confirm": True},
        )
        assert res.status_code == 404

    def test_clear_domain_success(self, client: TestClient):
        mock_artifacts = [
            {"id": "art-1", "filename": "test.py"},
            {"id": "art-2", "filename": "test2.py"},
        ]
        delete_result = {"deleted": True, "artifact_id": "art-1", "domain": "code", "filename": "test.py", "chunk_ids": ["c1", "c2"]}

        with (
            patch("routers.kb_admin.list_artifacts", return_value=mock_artifacts),
            patch("routers.kb_admin.delete_artifact", return_value=delete_result),
            patch("routers.kb_admin.invalidate_cache_non_blocking", new_callable=AsyncMock),
            patch("routers.kb_admin.get_chroma") as mock_chroma,
            patch("routers.kb_admin.get_neo4j") as mock_neo4j,
            patch("routers.kb_admin.config") as mock_config,
        ):
            mock_config.DOMAINS = ["code", "finance"]
            mock_config.collection_name.return_value = "domain_code"
            res = client.post(
                "/admin/kb/clear-domain/code",
                json={"confirm": True},
            )

        assert res.status_code == 200
        data = res.json()
        assert data["artifacts_deleted"] == 2
        assert data["domain"] == "code"


class TestDeleteArtifact:
    def test_delete_not_found(self, client: TestClient):
        with (
            patch("routers.kb_admin.get_neo4j"),
            patch("routers.kb_admin.get_chroma"),
            patch("routers.kb_admin.delete_artifact", return_value={"deleted": False, "reason": "not_found"}),
        ):
            res = client.delete("/admin/artifacts/nonexistent-id")
        assert res.status_code == 404

    def test_delete_success(self, client: TestClient):
        delete_result = {
            "deleted": True,
            "artifact_id": "art-123",
            "domain": "code",
            "filename": "test.py",
            "chunk_ids": ["c1", "c2", "c3"],
        }
        mock_collection = MagicMock()
        mock_chroma = MagicMock()
        mock_chroma.get_collection.return_value = mock_collection

        with (
            patch("routers.kb_admin.get_neo4j"),
            patch("routers.kb_admin.get_chroma", return_value=mock_chroma),
            patch("routers.kb_admin.delete_artifact", return_value=delete_result),
            patch("routers.kb_admin.invalidate_cache_non_blocking", new_callable=AsyncMock),
            patch("routers.kb_admin.config") as mock_config,
        ):
            mock_config.collection_name.return_value = "domain_code"
            res = client.delete("/admin/artifacts/art-123")

        assert res.status_code == 200
        data = res.json()
        assert data["deleted"] is True
        assert data["chunks_removed"] == 3


class TestKBStats:
    def test_stats_success(self, client: TestClient):
        mock_artifacts = [
            {"id": "a1", "filename": "f1.py", "summary": "A good summary for this test.", "quality_score": 0.8},
            {"id": "a2", "filename": "f2.py", "summary": "", "quality_score": 0.4},
        ]
        mock_collection = MagicMock()
        mock_collection.count.return_value = 10

        with (
            patch("routers.kb_admin.get_neo4j"),
            patch("routers.kb_admin.get_chroma") as mock_chroma_fn,
            patch("routers.kb_admin.list_artifacts", return_value=mock_artifacts),
            patch("routers.kb_admin.config") as mock_config,
        ):
            mock_config.DOMAINS = ["code"]
            mock_config.collection_name.return_value = "domain_code"
            mock_chroma_fn.return_value.get_collection.return_value = mock_collection
            res = client.get("/admin/kb/stats")

        assert res.status_code == 200
        data = res.json()
        assert data["total_artifacts"] == 2
        assert data["total_chunks"] == 10
        assert "code" in data["domains"]
        assert data["domains"]["code"]["artifacts"] == 2
        assert data["domains"]["code"]["chunks"] == 10
