# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for services/ingestion.py — core ingestion service layer.

External dependencies (Neo4j, ChromaDB, Redis, etc.) are stubbed
by conftest.py. Tests focus on logic: hashing, path validation,
duplicate detection flow, and response shapes.
"""

import hashlib
from unittest.mock import MagicMock, patch

import pytest

from app.services.ingestion import (
    _content_hash,
    _rollback_chromadb,
    ingest_content,
    validate_file_path,
)

# ---------------------------------------------------------------------------
# Tests: _content_hash
# ---------------------------------------------------------------------------

class TestContentHash:
    def test_returns_sha256(self):
        text = "hello world"
        expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert _content_hash(text) == expected

    def test_deterministic(self):
        assert _content_hash("test") == _content_hash("test")

    def test_different_content_differs(self):
        assert _content_hash("aaa") != _content_hash("bbb")

    def test_empty_string(self):
        result = _content_hash("")
        assert len(result) == 64  # SHA-256 hex length


# ---------------------------------------------------------------------------
# Tests: validate_file_path
# ---------------------------------------------------------------------------

class TestValidateFilePath:
    def test_valid_path_in_archive(self, tmp_path):
        archive = tmp_path / "archive"
        archive.mkdir()
        test_file = archive / "coding" / "test.py"
        test_file.parent.mkdir(parents=True)
        test_file.touch()

        with patch("config.ARCHIVE_PATH", str(archive)):
            result = validate_file_path(str(test_file))
            assert result == test_file.resolve()

    def test_path_outside_archive_raises(self, tmp_path):
        archive = tmp_path / "archive"
        archive.mkdir()
        outside = tmp_path / "elsewhere" / "secret.txt"
        outside.parent.mkdir(parents=True)
        outside.touch()

        with patch("config.ARCHIVE_PATH", str(archive)):
            with pytest.raises(ValueError, match="outside the allowed archive"):
                validate_file_path(str(outside))

    def test_traversal_attack_blocked(self, tmp_path):
        archive = tmp_path / "archive"
        archive.mkdir()
        target = tmp_path / "etc" / "passwd"
        target.parent.mkdir(parents=True)
        target.touch()

        with patch("config.ARCHIVE_PATH", str(archive)):
            with pytest.raises(ValueError):
                validate_file_path(str(archive / ".." / "etc" / "passwd"))


# ---------------------------------------------------------------------------
# Tests: ingest_content — duplicate detection
# ---------------------------------------------------------------------------

class TestIngestContentDuplicate:
    """Test that duplicate content is detected and reported."""

    @patch("services.ingestion.get_redis", return_value=MagicMock())
    @patch("services.ingestion.get_neo4j")
    @patch("services.ingestion.get_chroma")
    def test_exact_duplicate_returns_duplicate_status(self, mock_chroma, mock_neo4j, mock_redis):
        # Set up ChromaDB mock
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        # Set up Neo4j to report duplicate
        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        record = {"id": "existing-id", "filename": "existing.txt", "domain": "coding"}
        session.run.return_value.single.return_value = record

        result = ingest_content("duplicate content", domain="coding", metadata={"filename": "new.txt"})

        assert result["status"] == "duplicate"
        assert result["artifact_id"] == "existing-id"
        assert result["duplicate_of"] == "existing.txt"

    @patch("services.ingestion.get_redis", return_value=MagicMock())
    @patch("services.ingestion.get_neo4j")
    @patch("services.ingestion.get_chroma")
    def test_new_content_returns_success(self, mock_chroma, mock_neo4j, mock_redis):
        # Set up ChromaDB mock
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        # Set up Neo4j — no duplicate
        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None  # No match

        # Patch graph functions to avoid actual DB calls
        with patch("services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.return_value = None
            mock_graph.discover_relationships.return_value = 0

            result = ingest_content(
                "unique content here",
                domain="coding",
                metadata={"filename": "new.txt"},
            )

        assert result["status"] == "success"
        assert result["domain"] == "coding"
        assert result["chunks"] > 0
        assert "artifact_id" in result
        assert "timestamp" in result


# ---------------------------------------------------------------------------
# Tests: ingest_content — response shape
# ---------------------------------------------------------------------------

class TestIngestContentResponse:
    """Test the structure of ingest_content return values."""

    @patch("services.ingestion.get_redis", return_value=MagicMock())
    @patch("services.ingestion.get_neo4j")
    @patch("services.ingestion.get_chroma")
    def test_success_response_has_required_fields(self, mock_chroma, mock_neo4j, mock_redis):
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        with patch("services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.return_value = None
            mock_graph.discover_relationships.return_value = 0

            result = ingest_content("some content", domain="general")

        required = {"status", "artifact_id", "domain", "chunks", "relationships_created", "related", "timestamp"}
        assert required.issubset(set(result.keys()))
        assert isinstance(result["related"], list)
        assert isinstance(result["chunks"], int)

    @patch("services.ingestion.get_redis", return_value=MagicMock())
    @patch("services.ingestion.get_neo4j")
    @patch("services.ingestion.get_chroma")
    def test_default_domain_is_general(self, mock_chroma, mock_neo4j, mock_redis):
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        with patch("services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.return_value = None
            mock_graph.discover_relationships.return_value = 0

            result = ingest_content("test content")

        assert result["domain"] == "general"


# ---------------------------------------------------------------------------
# Tests: ingest_content — concurrent duplicate handling
# ---------------------------------------------------------------------------

class TestConcurrentDuplicate:
    """Test that constraint violations (concurrent inserts) are handled."""

    @patch("services.ingestion.get_redis", return_value=MagicMock())
    @patch("services.ingestion.get_neo4j")
    @patch("services.ingestion.get_chroma")
    def test_constraint_error_returns_duplicate(self, mock_chroma, mock_neo4j, mock_redis):
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None  # First check passes

        with patch("services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            # Simulate a constraint violation on create
            mock_graph.create_artifact.side_effect = Exception(
                "Neo.ClientError.Schema.ConstraintValidationFailed: "
                "Node already exists with label 'Artifact' and property 'content_hash'"
            )

            result = ingest_content("race condition content", domain="coding")

        assert result["status"] == "duplicate"
        assert result["duplicate_of"] == "(concurrent)"
        # Verify cleanup was attempted
        collection.delete.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: ingest_content — ChromaDB operations
# ---------------------------------------------------------------------------

class TestIngestChromaDB:
    """Test ChromaDB interaction during ingestion."""

    @patch("services.ingestion.get_redis", return_value=MagicMock())
    @patch("services.ingestion.get_neo4j")
    @patch("services.ingestion.get_chroma")
    def test_chunks_added_to_collection(self, mock_chroma, mock_neo4j, mock_redis):
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        with patch("services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.return_value = None
            mock_graph.discover_relationships.return_value = 0

            ingest_content("a " * 100, domain="coding")

        # Verify collection.add was called with chunk data
        collection.add.assert_called_once()
        call_kwargs = collection.add.call_args
        assert "ids" in call_kwargs.kwargs or len(call_kwargs.args) > 0

    @patch("services.ingestion.get_redis", return_value=MagicMock())
    @patch("services.ingestion.get_neo4j")
    @patch("services.ingestion.get_chroma")
    def test_correct_collection_name(self, mock_chroma, mock_neo4j, mock_redis):
        collection = MagicMock()
        chroma_client = MagicMock()
        chroma_client.get_or_create_collection.return_value = collection
        mock_chroma.return_value = chroma_client

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        with patch("services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.return_value = None
            mock_graph.discover_relationships.return_value = 0

            ingest_content("test", domain="finance")

        # Verify the collection name follows the domain_ pattern
        call_args = chroma_client.get_or_create_collection.call_args
        coll_name = call_args.kwargs.get("name") or call_args.args[0]
        assert coll_name.startswith("domain_")
        assert "finance" in coll_name


# ---------------------------------------------------------------------------
# Tests: ingest_content — Redis logging
# ---------------------------------------------------------------------------

class TestIngestRedisLogging:
    """Test that successful ingestion logs to Redis."""

    @patch("services.ingestion.cache")
    @patch("services.ingestion.get_redis")
    @patch("services.ingestion.get_neo4j")
    @patch("services.ingestion.get_chroma")
    def test_log_event_called_on_success(self, mock_chroma, mock_neo4j, mock_redis, mock_cache):
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        with patch("services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.return_value = None
            mock_graph.discover_relationships.return_value = 0

            ingest_content("log test", domain="coding", metadata={"filename": "log.txt"})

        mock_cache.log_event.assert_called_once()
        call_kwargs = mock_cache.log_event.call_args
        assert call_kwargs.kwargs.get("event_type") == "ingest" or call_kwargs.args[1] == "ingest"


# ---------------------------------------------------------------------------
# Tests: _rollback_chromadb helper
# ---------------------------------------------------------------------------

class TestRollbackChromaDB:
    """Test the compensating transaction helper."""

    def test_deletes_chunk_ids(self):
        collection = MagicMock()
        _rollback_chromadb(collection, ["id1", "id2", "id3"])
        collection.delete.assert_called_once_with(ids=["id1", "id2", "id3"])

    def test_handles_delete_failure(self):
        collection = MagicMock()
        collection.delete.side_effect = Exception("ChromaDB unavailable")
        # Should not raise — logs error instead
        _rollback_chromadb(collection, ["id1"])

    def test_empty_chunk_ids(self):
        collection = MagicMock()
        _rollback_chromadb(collection, [])
        collection.delete.assert_called_once_with(ids=[])


# ---------------------------------------------------------------------------
# Tests: ingest_content — compensating transaction on Neo4j failure
# ---------------------------------------------------------------------------

class TestCompensatingTransaction:
    """Test that ChromaDB chunks are rolled back when Neo4j fails."""

    @patch("services.ingestion.get_redis", return_value=MagicMock())
    @patch("services.ingestion.get_neo4j")
    @patch("services.ingestion.get_chroma")
    def test_neo4j_failure_rolls_back_chromadb(self, mock_chroma, mock_neo4j, mock_redis):
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        with patch("services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.side_effect = Exception("Neo4j connection lost")
            mock_graph.discover_relationships.return_value = 0

            result = ingest_content("rollback test", domain="coding")

        assert result["status"] == "error"
        assert "Graph storage failed" in result["error"]
        # ChromaDB chunks should have been rolled back
        collection.delete.assert_called_once()

    @patch("services.ingestion.get_redis", return_value=MagicMock())
    @patch("services.ingestion.get_neo4j")
    @patch("services.ingestion.get_chroma")
    def test_neo4j_failure_returns_zero_chunks(self, mock_chroma, mock_neo4j, mock_redis):
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        with patch("services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.side_effect = Exception("Neo4j timeout")

            result = ingest_content("test", domain="general")

        assert result["chunks"] == 0

    @patch("services.ingestion.get_redis", return_value=MagicMock())
    @patch("services.ingestion.get_neo4j")
    @patch("services.ingestion.get_chroma")
    def test_constraint_violation_still_returns_duplicate(self, mock_chroma, mock_neo4j, mock_redis):
        """Constraint violations should still return duplicate, not error."""
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        with patch("services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.side_effect = Exception(
                "ConstraintValidationFailed content_hash uniqueness"
            )

            result = ingest_content("concurrent test", domain="coding")

        assert result["status"] == "duplicate"
        collection.delete.assert_called_once()

    @patch("services.ingestion.get_redis", return_value=MagicMock())
    @patch("services.ingestion.get_neo4j")
    @patch("services.ingestion.get_chroma")
    def test_neo4j_failure_does_not_log_to_redis(self, mock_chroma, mock_neo4j, mock_redis):
        """Failed ingestion should not log an event to Redis."""
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        with patch("services.ingestion.graph") as mock_graph, \
             patch("services.ingestion.cache") as mock_cache:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.side_effect = Exception("Neo4j down")

            ingest_content("fail test", domain="coding")

        mock_cache.log_event.assert_not_called()
