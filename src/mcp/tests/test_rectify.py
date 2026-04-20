# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for agents/rectify.py — knowledge graph conflict detection and resolution."""

from unittest.mock import MagicMock, patch

from core.agents.rectify import (
    analyze_domain_distribution,
    cleanup_orphaned_chunks,
    find_duplicate_artifacts,
    find_orphaned_chunks,
    find_similar_artifacts,
    find_stale_artifacts,
    resolve_duplicates,
)

# ---------------------------------------------------------------------------
# Tests: find_duplicate_artifacts
# ---------------------------------------------------------------------------

class TestFindDuplicateArtifacts:
    def test_no_duplicates(self, mock_neo4j):
        driver, session = mock_neo4j
        session.run.return_value = iter([])  # No records

        result = find_duplicate_artifacts(driver)
        assert result == []

    def test_finds_duplicates(self, mock_neo4j):
        driver, session = mock_neo4j

        record = {
            "hash": "abc123",
            "artifacts": [
                {"id": "a1", "filename": "file1.py", "domain": "coding", "ingested_at": "2026-01-01"},
                {"id": "a2", "filename": "file2.py", "domain": "coding", "ingested_at": "2026-01-02"},
            ],
        }
        session.run.return_value = iter([record])

        result = find_duplicate_artifacts(driver)
        assert len(result) == 1
        assert result[0]["content_hash"] == "abc123"
        assert result[0]["count"] == 2
        assert len(result[0]["artifacts"]) == 2

    def test_multiple_duplicate_sets(self, mock_neo4j):
        driver, session = mock_neo4j

        records = [
            {"hash": "h1", "artifacts": [{"id": "a1"}, {"id": "a2"}]},
            {"hash": "h2", "artifacts": [{"id": "a3"}, {"id": "a4"}, {"id": "a5"}]},
        ]
        session.run.return_value = iter(records)

        result = find_duplicate_artifacts(driver)
        assert len(result) == 2
        assert result[1]["count"] == 3


# ---------------------------------------------------------------------------
# Tests: find_similar_artifacts
# ---------------------------------------------------------------------------

class TestFindSimilarArtifacts:
    def test_finds_similar(self, mock_chroma):
        client, collection = mock_chroma
        collection.query.return_value = {
            "ids": [["chunk_1", "chunk_2"]],
            "distances": [[0.05, 0.12]],
            "metadatas": [[
                {"artifact_id": "a1", "filename": "f1.py", "domain": "coding"},
                {"artifact_id": "a2", "filename": "f2.py", "domain": "coding"},
            ]],
        }

        result = find_similar_artifacts("test query", "coding", client, threshold=0.15)
        assert len(result) == 2
        assert result[0]["distance"] == 0.05
        assert result[1]["artifact_id"] == "a2"

    def test_threshold_filters(self, mock_chroma):
        client, collection = mock_chroma
        collection.query.return_value = {
            "ids": [["chunk_1", "chunk_2"]],
            "distances": [[0.05, 0.30]],
            "metadatas": [[
                {"artifact_id": "a1", "filename": "f1.py"},
                {"artifact_id": "a2", "filename": "f2.py"},
            ]],
        }

        result = find_similar_artifacts("test", "coding", client, threshold=0.15)
        assert len(result) == 1  # Only chunk_1 below threshold

    def test_collection_not_found(self, mock_chroma):
        client, _ = mock_chroma
        client.get_collection.side_effect = RuntimeError("Not found")

        result = find_similar_artifacts("test", "coding", client)
        assert result == []

    def test_empty_results(self, mock_chroma):
        client, collection = mock_chroma
        collection.query.return_value = {
            "ids": [[]],
            "distances": [[]],
            "metadatas": [[]],
        }

        result = find_similar_artifacts("test", "coding", client)
        assert result == []


# ---------------------------------------------------------------------------
# Tests: find_stale_artifacts
# ---------------------------------------------------------------------------

class TestFindStaleArtifacts:
    def test_finds_stale(self, mock_neo4j):
        driver, session = mock_neo4j

        records = [
            {
                "id": "a1", "filename": "old.py", "domain": "coding",
                "ingested_at": "2025-01-01T00:00:00", "chunk_count": 3,
                "chunk_ids": '["c1", "c2", "c3"]',
            },
        ]
        session.run.return_value = iter(records)

        result = find_stale_artifacts(driver, days_threshold=90)
        assert len(result) == 1
        assert result[0]["id"] == "a1"
        assert result[0]["age_indicator"] == "stale"

    def test_no_stale(self, mock_neo4j):
        driver, session = mock_neo4j
        session.run.return_value = iter([])

        result = find_stale_artifacts(driver, days_threshold=90)
        assert result == []

    def test_passes_parameters(self, mock_neo4j):
        driver, session = mock_neo4j
        session.run.return_value = iter([])

        find_stale_artifacts(driver, days_threshold=30, limit=50)
        call_kwargs = session.run.call_args
        assert call_kwargs.kwargs["limit"] == 50


# ---------------------------------------------------------------------------
# Tests: find_orphaned_chunks
# ---------------------------------------------------------------------------

class TestFindOrphanedChunks:
    @patch("core.agents.rectify.config")
    def test_finds_orphans(self, mock_config, mock_neo4j, mock_chroma):
        mock_config.DOMAINS = ["coding"]
        mock_config.collection_name = lambda d: f"domain_{d}"

        driver, session = mock_neo4j
        # Neo4j has artifact a1
        session.run.return_value = iter([{"id": "a1"}])

        client, collection = mock_chroma
        # ChromaDB has chunks referencing a1 and a2 (a2 is orphaned)
        collection.get.return_value = {
            "ids": ["chunk_1", "chunk_2"],
            "metadatas": [
                {"artifact_id": "a1", "filename": "valid.py"},
                {"artifact_id": "a2", "filename": "orphan.py"},
            ],
        }

        result = find_orphaned_chunks(driver, client)
        assert "coding" in result
        assert len(result["coding"]) == 1
        assert result["coding"][0]["artifact_id"] == "a2"

    @patch("core.agents.rectify.config")
    def test_no_orphans(self, mock_config, mock_neo4j, mock_chroma):
        mock_config.DOMAINS = ["coding"]
        mock_config.collection_name = lambda d: f"domain_{d}"

        driver, session = mock_neo4j
        session.run.return_value = iter([{"id": "a1"}])

        client, collection = mock_chroma
        collection.get.return_value = {
            "ids": ["chunk_1"],
            "metadatas": [{"artifact_id": "a1"}],
        }

        result = find_orphaned_chunks(driver, client)
        assert result == {}


# ---------------------------------------------------------------------------
# Tests: resolve_duplicates
# ---------------------------------------------------------------------------

class TestResolveDuplicates:
    def test_removes_non_kept_artifacts(self, mock_neo4j, mock_chroma):
        driver, session = mock_neo4j
        client, collection = mock_chroma

        # Two artifacts with same hash
        records = [
            {"id": "keep-me", "filename": "keep.py", "domain": "coding", "chunk_ids": '["c1"]'},
            {"id": "remove-me", "filename": "dupe.py", "domain": "coding", "chunk_ids": '["c2"]'},
        ]
        session.run.return_value = iter(records)

        result = resolve_duplicates(driver, client, "hash123", "keep-me")
        assert result["kept"] == "keep-me"
        assert result["removed_count"] == 1
        assert result["removed"][0]["id"] == "remove-me"

    def test_deletes_chunks_from_chroma(self, mock_neo4j, mock_chroma):
        driver, session = mock_neo4j
        client, collection = mock_chroma

        records = [
            {"id": "keep", "filename": "k.py", "domain": "coding", "chunk_ids": '["c1"]'},
            {"id": "rm", "filename": "r.py", "domain": "coding", "chunk_ids": '["c2", "c3"]'},
        ]
        session.run.return_value = iter(records)

        resolve_duplicates(driver, client, "hash", "keep")
        collection.delete.assert_called_once_with(ids=["c2", "c3"])

    def test_logs_to_redis(self, mock_neo4j, mock_chroma):
        driver, session = mock_neo4j
        client, collection = mock_chroma
        redis = MagicMock()

        records = [
            {"id": "keep", "filename": "k.py", "domain": "coding", "chunk_ids": "[]"},
            {"id": "rm", "filename": "r.py", "domain": "coding", "chunk_ids": "[]"},
        ]
        session.run.return_value = iter(records)

        with patch("core.agents.rectify.log_event") as mock_log:
            resolve_duplicates(driver, client, "hash", "keep", redis_client=redis)
            mock_log.assert_called_once()

    def test_no_duplicates_to_remove(self, mock_neo4j, mock_chroma):
        driver, session = mock_neo4j
        client, collection = mock_chroma

        records = [
            {"id": "only-one", "filename": "only.py", "domain": "coding", "chunk_ids": "[]"},
        ]
        session.run.return_value = iter(records)

        result = resolve_duplicates(driver, client, "hash", "only-one")
        assert result["removed_count"] == 0


# ---------------------------------------------------------------------------
# Tests: cleanup_orphaned_chunks
# ---------------------------------------------------------------------------

class TestCleanupOrphanedChunks:
    @patch("core.agents.rectify.config")
    def test_cleans_orphans(self, mock_config, mock_chroma):
        mock_config.collection_name = lambda d: f"domain_{d}"
        client, collection = mock_chroma

        orphaned = {
            "coding": [
                {"chunk_id": "c1", "artifact_id": "a1", "filename": "f1.py"},
                {"chunk_id": "c2", "artifact_id": "a2", "filename": "f2.py"},
            ],
        }

        result = cleanup_orphaned_chunks(client, orphaned)
        assert result["coding"] == 2
        collection.delete.assert_called_once_with(ids=["c1", "c2"])

    @patch("core.agents.rectify.config")
    def test_empty_orphans(self, mock_config, mock_chroma):
        client, collection = mock_chroma
        result = cleanup_orphaned_chunks(client, {})
        assert result == {}

    @patch("core.agents.rectify.config")
    def test_error_returns_zero(self, mock_config, mock_chroma):
        mock_config.collection_name = lambda d: f"domain_{d}"
        client, collection = mock_chroma
        collection.delete.side_effect = RuntimeError("ChromaDB error")

        orphaned = {"coding": [{"chunk_id": "c1"}]}
        result = cleanup_orphaned_chunks(client, orphaned)
        assert result["coding"] == 0


# ---------------------------------------------------------------------------
# Tests: analyze_domain_distribution
# ---------------------------------------------------------------------------

class TestAnalyzeDomainDistribution:
    @patch("core.agents.rectify.config")
    def test_basic_distribution(self, mock_config, mock_neo4j):
        mock_config.DOMAINS = ["coding", "general"]
        driver, session = mock_neo4j

        records = [
            {"domain": "coding", "count": 10, "total_chunks": 50},
            {"domain": "general", "count": 5, "total_chunks": 20},
        ]
        session.run.return_value = iter(records)

        result = analyze_domain_distribution(driver)
        assert result["total_artifacts"] == 15
        assert result["total_chunks"] == 70
        assert result["distribution"]["coding"]["artifacts"] == 10

    @patch("core.agents.rectify.config")
    def test_missing_domain_padded_with_zeros(self, mock_config, mock_neo4j):
        mock_config.DOMAINS = ["coding", "general", "finance"]
        driver, session = mock_neo4j

        records = [
            {"domain": "coding", "count": 5, "total_chunks": 20},
        ]
        session.run.return_value = iter(records)

        result = analyze_domain_distribution(driver)
        assert result["distribution"]["finance"] == {"artifacts": 0, "chunks": 0}
        assert result["distribution"]["general"] == {"artifacts": 0, "chunks": 0}

    @patch("core.agents.rectify.config")
    def test_null_chunk_count(self, mock_config, mock_neo4j):
        mock_config.DOMAINS = ["coding"]
        driver, session = mock_neo4j

        records = [{"domain": "coding", "count": 3, "total_chunks": None}]
        session.run.return_value = iter(records)

        result = analyze_domain_distribution(driver)
        assert result["distribution"]["coding"]["chunks"] == 0
        assert result["total_chunks"] == 0
