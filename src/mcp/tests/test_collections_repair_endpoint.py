# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for POST /admin/collections/repair."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """FastAPI test client with Chroma, Neo4j, and embedder mocks."""
    # Redirect backup writes to a tmp dir so the test is hermetic.
    import config

    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path), raising=False)

    from app.main import app  # noqa: E402

    chroma = MagicMock()
    neo4j = MagicMock()

    # Collection mock — used for both backup fetch and peek.
    coll = MagicMock()
    coll.name = "domain_general"
    coll.peek = MagicMock(return_value={"embeddings": [[0.0] * 768]})
    coll.get = MagicMock(return_value={
        "ids": ["doc_1"],
        "documents": ["The quick brown fox."],
        "metadatas": [{"filename": "note.txt", "domain": "general"}],
    })
    chroma.get_collection.return_value = coll
    chroma.get_or_create_collection.return_value = coll
    chroma.delete_collection = MagicMock()

    with (
        patch("app.routers.kb_admin.get_chroma", return_value=chroma),
        patch("app.routers.kb_admin.get_neo4j", return_value=neo4j),
        patch("app.routers.kb_admin.list_artifacts", return_value=[
            {"id": "a1", "filename": "note.txt", "domain": "general"},
        ]),
    ):
        yield TestClient(app, raise_server_exceptions=False), chroma, neo4j


class TestRepairEndpointDryRun:
    def test_dry_run_reports_without_mutation(self, client):
        tc, chroma, _neo4j = client
        with patch(
            "core.utils.embeddings.get_embedding_dim", return_value=384,
        ):
            res = tc.post(
                "/admin/collections/repair",
                json={"collection_name": "domain_general", "dry_run": True},
            )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "dry_run"
        assert body["dry_run"] is True
        assert body["expected_dim"] == 384
        assert body["actual_dim"] == 768
        assert body["artifacts_found"] == 1
        assert body["rebuilt_documents"] == 0
        assert body["backup_path"] is None
        # Critically, no mutating calls must have fired.
        chroma.delete_collection.assert_not_called()
        chroma.get_or_create_collection.assert_not_called()


class TestRepairEndpointApply:
    def test_apply_backs_up_deletes_recreates_and_replays(self, client):
        tc, chroma, _neo4j = client
        with (
            patch("core.utils.embeddings.get_embedding_dim", return_value=384),
            patch(
                "app.services.ingestion.ingest_content",
                return_value={"status": "success", "chunks": 1},
            ) as mock_ingest,
            patch(
                "app.routers.kb_admin.invalidate_cache_non_blocking",
                new_callable=AsyncMock,
            ),
        ):
            res = tc.post(
                "/admin/collections/repair",
                json={"collection_name": "domain_general", "dry_run": False},
            )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "repaired"
        assert body["dry_run"] is False
        assert body["rebuilt_documents"] == 1
        assert body["backup_path"] is not None
        assert body["backup_path"].endswith(".jsonl")
        # Ordering: backup (get), delete, recreate, replay via ingest.
        chroma.get_collection.assert_called()
        chroma.delete_collection.assert_called_once_with(name="domain_general")
        chroma.get_or_create_collection.assert_called_once_with(name="domain_general")
        mock_ingest.assert_called_once()

    def test_apply_rejects_unknown_collection_name(self, client):
        tc, _chroma, _neo4j = client
        with patch("core.utils.embeddings.get_embedding_dim", return_value=384):
            res = tc.post(
                "/admin/collections/repair",
                json={"collection_name": "not_a_real_prefix", "dry_run": True},
            )
        assert res.status_code == 400
        assert "does not map to a known domain" in res.json()["detail"]


class TestRepairEndpointBackupFormat:
    def test_backup_file_is_valid_jsonl(self, client, tmp_path):
        import json
        from pathlib import Path

        tc, _chroma, _neo4j = client
        with (
            patch("core.utils.embeddings.get_embedding_dim", return_value=384),
            patch(
                "app.services.ingestion.ingest_content",
                return_value={"status": "success", "chunks": 1},
            ),
            patch(
                "app.routers.kb_admin.invalidate_cache_non_blocking",
                new_callable=AsyncMock,
            ),
        ):
            res = tc.post(
                "/admin/collections/repair",
                json={"collection_name": "domain_general", "dry_run": False},
            )
        assert res.status_code == 200, res.text
        backup_path = Path(res.json()["backup_path"])
        assert backup_path.exists()
        lines = [ln for ln in backup_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["id"] == "doc_1"
        assert rec["document"] == "The quick brown fox."
        assert rec["metadata"]["filename"] == "note.txt"
