# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for GET /artifacts/{artifact_id} endpoint."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app():
    """Create minimal FastAPI app with just the artifacts router."""
    from routers.artifacts import router

    app = FastAPI()
    app.include_router(router)
    return app


def _sample_artifact(
    artifact_id="art-123",
    filename="example.py",
    domain="coding",
    chunk_ids=None,
):
    return {
        "id": artifact_id,
        "filename": filename,
        "domain": domain,
        "sub_category": "general",
        "tags": '["python", "utils"]',
        "keywords": '["example"]',
        "summary": "A sample artifact",
        "chunk_count": 2,
        "chunk_ids": json.dumps(chunk_ids or ["c1", "c2"]),
        "ingested_at": "2026-03-01T00:00:00Z",
        "recategorized_at": None,
    }


class TestArtifactDetailEndpoint:
    """Tests for the artifact detail endpoint."""

    @patch("routers.artifacts.get_redis")
    @patch("routers.artifacts.get_chroma")
    @patch("routers.artifacts.get_neo4j")
    @patch("routers.artifacts.graph")
    def test_returns_artifact_with_chunks(self, mock_graph, mock_neo4j, mock_chroma, _mock_redis):
        client = TestClient(_make_app())

        mock_graph.get_artifact.return_value = _sample_artifact()

        collection = MagicMock()
        collection.get.return_value = {
            "ids": ["c1", "c2"],
            "documents": ["chunk one content", "chunk two content"],
            "metadatas": [{"chunk_index": 0}, {"chunk_index": 1}],
        }
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        response = client.get("/artifacts/art-123")
        assert response.status_code == 200

        data = response.json()
        assert data["artifact_id"] == "art-123"
        assert data["filename"] == "example.py"
        assert data["domain"] == "coding"
        assert data["chunk_count"] == 2
        assert len(data["chunks"]) == 2
        assert data["chunks"][0]["index"] == 0
        assert data["chunks"][1]["index"] == 1
        assert "chunk one content" in data["total_content"]
        assert "chunk two content" in data["total_content"]

    @patch("routers.artifacts.get_redis")
    @patch("routers.artifacts.get_chroma")
    @patch("routers.artifacts.get_neo4j")
    @patch("routers.artifacts.graph")
    def test_returns_404_when_not_found(self, mock_graph, mock_neo4j, mock_chroma, _mock_redis):
        client = TestClient(_make_app())

        mock_graph.get_artifact.return_value = None

        response = client.get("/artifacts/nonexistent")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @patch("routers.artifacts.get_redis")
    @patch("routers.artifacts.get_chroma")
    @patch("routers.artifacts.get_neo4j")
    @patch("routers.artifacts.graph")
    def test_chunks_sorted_by_index(self, mock_graph, mock_neo4j, mock_chroma, _mock_redis):
        client = TestClient(_make_app())

        mock_graph.get_artifact.return_value = _sample_artifact(
            chunk_ids=["c2", "c1", "c3"]
        )

        collection = MagicMock()
        # Return chunks out of order
        collection.get.return_value = {
            "ids": ["c2", "c1", "c3"],
            "documents": ["second chunk", "first chunk", "third chunk"],
            "metadatas": [{"chunk_index": 2}, {"chunk_index": 0}, {"chunk_index": 1}],
        }
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        response = client.get("/artifacts/art-123")
        assert response.status_code == 200

        data = response.json()
        assert data["chunks"][0]["index"] == 0
        assert data["chunks"][0]["text"] == "first chunk"
        assert data["chunks"][1]["index"] == 1
        assert data["chunks"][1]["text"] == "third chunk"
        assert data["chunks"][2]["index"] == 2
        assert data["chunks"][2]["text"] == "second chunk"

    @patch("routers.artifacts.get_redis")
    @patch("routers.artifacts.get_chroma")
    @patch("routers.artifacts.get_neo4j")
    @patch("routers.artifacts.graph")
    def test_total_content_assembled_in_order(self, mock_graph, mock_neo4j, mock_chroma, _mock_redis):
        client = TestClient(_make_app())

        mock_graph.get_artifact.return_value = _sample_artifact(
            chunk_ids=["c1", "c2"]
        )

        collection = MagicMock()
        collection.get.return_value = {
            "ids": ["c1", "c2"],
            "documents": ["AAA", "BBB"],
            "metadatas": [{"chunk_index": 1}, {"chunk_index": 0}],
        }
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        response = client.get("/artifacts/art-123")
        data = response.json()

        # total_content should be assembled in sorted order: BBB then AAA
        assert data["total_content"] == "BBB\n\nAAA"

    @patch("routers.artifacts.get_redis")
    @patch("routers.artifacts.get_chroma")
    @patch("routers.artifacts.get_neo4j")
    @patch("routers.artifacts.graph")
    def test_empty_chunk_ids_returns_empty_content(self, mock_graph, mock_neo4j, mock_chroma, _mock_redis):
        client = TestClient(_make_app())

        artifact = _sample_artifact()
        artifact["chunk_ids"] = "[]"
        artifact["chunk_count"] = 0
        mock_graph.get_artifact.return_value = artifact

        response = client.get("/artifacts/art-123")
        assert response.status_code == 200

        data = response.json()
        assert data["chunks"] == []
        # With no chunks, the endpoint falls back to the artifact's summary
        assert data["total_content"] == "A sample artifact"

    @patch("routers.artifacts.get_redis")
    @patch("routers.artifacts.get_chroma")
    @patch("routers.artifacts.get_neo4j")
    @patch("routers.artifacts.graph")
    def test_metadata_fields_included(self, mock_graph, mock_neo4j, mock_chroma, _mock_redis):
        client = TestClient(_make_app())

        mock_graph.get_artifact.return_value = _sample_artifact()

        collection = MagicMock()
        collection.get.return_value = {
            "ids": ["c1"],
            "documents": ["text"],
            "metadatas": [{"chunk_index": 0}],
        }
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        response = client.get("/artifacts/art-123")
        data = response.json()

        assert "metadata" in data
        assert data["metadata"]["sub_category"] == "general"
        assert data["metadata"]["summary"] == "A sample artifact"
        assert data["metadata"]["ingested_at"] == "2026-03-01T00:00:00Z"
