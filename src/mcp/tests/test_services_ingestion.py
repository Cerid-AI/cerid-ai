# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for services/ingestion.py — core ingestion service layer.

External dependencies (Neo4j, ChromaDB, Redis, etc.) are stubbed
by conftest.py. Tests focus on logic: hashing, path validation,
duplicate detection flow, and response shapes.
"""

import hashlib
import json
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

    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
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

    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
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
        with patch("app.services.ingestion.graph") as mock_graph:
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

    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_success_response_has_required_fields(self, mock_chroma, mock_neo4j, mock_redis):
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        with patch("app.services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.return_value = None
            mock_graph.discover_relationships.return_value = 0

            result = ingest_content("some content", domain="general")

        required = {"status", "artifact_id", "domain", "chunks", "relationships_created", "related", "timestamp"}
        assert required.issubset(set(result.keys()))
        assert isinstance(result["related"], list)
        assert isinstance(result["chunks"], int)

    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_default_domain_is_general(self, mock_chroma, mock_neo4j, mock_redis):
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        with patch("app.services.ingestion.graph") as mock_graph:
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

    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_constraint_error_returns_duplicate(self, mock_chroma, mock_neo4j, mock_redis):
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None  # First check passes

        with patch("app.services.ingestion.graph") as mock_graph:
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

    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_chunks_added_to_collection(self, mock_chroma, mock_neo4j, mock_redis):
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        with patch("app.services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.return_value = None
            mock_graph.discover_relationships.return_value = 0

            ingest_content("a " * 100, domain="coding")

        # Verify collection.add was called with chunk data
        collection.upsert.assert_called_once()
        call_kwargs = collection.upsert.call_args
        assert "ids" in call_kwargs.kwargs or len(call_kwargs.args) > 0

    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
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

        with patch("app.services.ingestion.graph") as mock_graph:
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

    @patch("app.services.ingestion.cache")
    @patch("app.services.ingestion.get_redis")
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_log_event_called_on_success(self, mock_chroma, mock_neo4j, mock_redis, mock_cache):
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        with patch("app.services.ingestion.graph") as mock_graph:
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
    """Test Neo4j failure handling in the two-phase ingest boundary (Phase O.1).

    Phase O.1 changed the failure semantics: when Neo4j fails, Chroma chunks
    are LEFT in ``cerid_state=pending`` (not deleted) so the IngestRecoveryJob
    can roll them forward later.  The old immediate-rollback path is only
    triggered for concurrent duplicate violations (constraint errors on
    content_hash), not for ordinary connection failures.
    """

    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_neo4j_failure_leaves_chunks_pending_for_recovery(
        self, mock_chroma, mock_neo4j, mock_redis
    ):
        """Phase O.1: on Neo4j failure, Chroma chunks remain staged as pending.

        The IngestRecoveryJob will scan for stale pending rows and either
        roll-forward or purge them.  delete() must NOT be called on the
        ordinary-failure path.
        """
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        with patch("app.services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.side_effect = Exception("Neo4j connection lost")
            mock_graph.discover_relationships.return_value = 0

            result = ingest_content("rollback test", domain="coding")

        assert result["status"] == "error"
        assert "Graph storage failed" in result["error"]
        # Phase O.1: chunks must NOT be deleted — they stay pending for recovery.
        collection.delete.assert_not_called()
        # Chunks must have been staged (update called with cerid_state=pending)
        update_calls = collection.update.call_args_list
        staged = [
            c for c in update_calls
            if c[1].get("metadatas") and any(
                m.get("cerid_state") == "pending"
                for m in c[1]["metadatas"]
            )
        ]
        assert staged, "Expected collection.update with cerid_state=pending after Neo4j failure"

    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_neo4j_failure_returns_zero_chunks(self, mock_chroma, mock_neo4j, mock_redis):
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        with patch("app.services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.side_effect = Exception("Neo4j timeout")

            result = ingest_content("test", domain="general")

        assert result["chunks"] == 0

    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
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

        with patch("app.services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.side_effect = Exception(
                "ConstraintValidationFailed content_hash uniqueness"
            )

            result = ingest_content("concurrent test", domain="coding")

        assert result["status"] == "duplicate"
        collection.delete.assert_called_once()

    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
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

        with patch("app.services.ingestion.graph") as mock_graph, \
             patch("app.services.ingestion.cache") as mock_cache:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.side_effect = Exception("Neo4j down")

            ingest_content("fail test", domain="coding")

        mock_cache.log_event.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: semantic-cache invalidation hooks (Phase 2.2)
# ---------------------------------------------------------------------------

class TestSemanticCacheInvalidationHook:
    """Corpus mutations must invalidate the semantic query cache — before
    this, ``invalidate_cache`` had no production caller at all, leaving up
    to SEMANTIC_CACHE_TTL of stale ``/agent/query`` results after every
    ingest/re-ingest. The hook call is a local import inside
    ``app.services.ingestion``, so it's mocked at its home module
    (``core.retrieval.semantic_cache``), not at the ingestion module.
    """

    @patch("core.retrieval.semantic_cache.invalidate_cache_non_blocking")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_fresh_ingest_success_invalidates_semantic_cache(
        self, mock_chroma, mock_neo4j, mock_redis, mock_sem_invalidate,
    ):
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        with patch("app.services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.return_value = None
            mock_graph.discover_relationships.return_value = 0

            result = ingest_content("fresh content for cache invalidation", domain="coding")

        assert result["status"] == "success"
        mock_sem_invalidate.assert_called_once()
        _, kwargs = mock_sem_invalidate.call_args
        assert kwargs.get("trigger") == "ingestion.ingest_content"

    @patch("core.retrieval.semantic_cache.invalidate_cache_non_blocking")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_duplicate_ingest_does_not_invalidate_semantic_cache(
        self, mock_chroma, mock_neo4j, mock_redis, mock_sem_invalidate,
    ):
        """No corpus mutation happened — invalidating would just be churn."""
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        record = {"id": "existing-id", "filename": "existing.txt", "domain": "coding"}
        session.run.return_value.single.return_value = record

        result = ingest_content(
            "duplicate content", domain="coding", metadata={"filename": "new.txt"},
        )

        assert result["status"] == "duplicate"
        mock_sem_invalidate.assert_not_called()

    @patch("core.retrieval.semantic_cache.invalidate_cache_non_blocking")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_reingest_invalidates_semantic_cache(
        self, mock_chroma, mock_neo4j, mock_redis, mock_sem_invalidate,
    ):
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None  # _check_duplicate: no match

        prev = {"id": "old-artifact-id", "content_hash": "old-hash", "chunk_ids": "[]"}
        with patch("app.services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = prev
            mock_graph.update_artifact.return_value = None

            result = ingest_content(
                "new content that differs from the old hash",
                domain="coding",
                metadata={"filename": "existing.txt"},
            )

        assert result["status"] == "updated"
        assert result["artifact_id"] == "old-artifact-id"
        mock_sem_invalidate.assert_called_once()
        _, kwargs = mock_sem_invalidate.call_args
        assert kwargs.get("trigger") == "ingestion.reingest_artifact"

    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_invalidation_hook_failure_does_not_break_ingest(
        self, mock_chroma, mock_neo4j, mock_redis,
    ):
        """The hook call is a best-effort observability boundary — a
        raising cache invalidation must not fail the ingest itself."""
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        with patch("app.services.ingestion.graph") as mock_graph, patch(
            "core.retrieval.semantic_cache.invalidate_cache_non_blocking",
            side_effect=RuntimeError("cache backend unreachable"),
        ):
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.return_value = None
            mock_graph.discover_relationships.return_value = 0

            result = ingest_content("resilience check", domain="coding")

        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# Tests: force_reindex — retroactive feature application (Phase 2.6)
# ---------------------------------------------------------------------------

class TestForceReindex:
    """force_reindex re-embeds UNCHANGED content so newly-enabled retrieval
    features apply retroactively. It must bypass the exact-hash dedup and route
    to the relationship-preserving _reingest_artifact path."""

    @patch("core.retrieval.semantic_cache.invalidate_cache_non_blocking")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_force_reindex_reingests_unchanged_content(
        self, mock_chroma, mock_neo4j, mock_redis, mock_sem,
    ):
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        # _check_duplicate WOULD find this artifact (exact-hash hit).
        session.run.return_value.single.return_value = {
            "id": "aid", "filename": "existing.txt", "domain": "coding",
        }

        content = "unchanged content"
        chash = _content_hash(content)
        prev = {"id": "aid", "content_hash": chash, "chunk_ids": "[]"}

        with patch("app.services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = prev
            mock_graph.update_artifact.return_value = None

            # Control (red for reindex): without the flag, the exact-hash dedup
            # short-circuits and nothing is re-embedded.
            dup = ingest_content(
                content, domain="coding", metadata={"filename": "existing.txt"},
            )
            assert dup["status"] == "duplicate"

            # force_reindex bypasses dedup and re-embeds via _reingest_artifact.
            result = ingest_content(
                content, domain="coding", metadata={"filename": "existing.txt"},
                force_reindex=True,
            )

        assert result["status"] == "updated"
        assert result["artifact_id"] == "aid"
        # Re-index invalidated the semantic cache (via the _reingest path).
        assert mock_sem.called

    @patch("core.retrieval.semantic_cache.invalidate_cache_non_blocking")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_reingest_preserves_pre_chunked_layout(
        self, mock_chroma, mock_neo4j, mock_redis, mock_sem,
    ):
        """_reingest_artifact with pre_chunked keeps element granularity +
        structural metadata — a re-index must not downgrade a layout-aware
        artifact to token chunks."""
        from app.services.ingestion import _reingest_artifact

        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection
        mock_neo4j.return_value = MagicMock()

        prev = {"id": "aid", "content_hash": "h", "chunk_ids": "[]"}
        pre_chunked = [
            {"text": "row one", "metadata": {"element_type": "CSVRow", "column_headers": ["a", "b"]}},
            {"text": "row two", "metadata": {"element_type": "CSVRow", "column_headers": ["a", "b"]}},
        ]

        with patch("app.services.ingestion.graph") as mock_graph:
            mock_graph.update_artifact.return_value = None
            result = _reingest_artifact(
                prev, "row one\nrow two", "coding", {"filename": "data.csv"}, "h",
                pre_chunked=pre_chunked,
            )

        assert result["status"] == "updated"
        upsert_kwargs = collection.upsert.call_args.kwargs
        # Exactly the two layout chunks (verbatim), not token-chunked.
        assert upsert_kwargs["documents"] == ["row one", "row two"]
        metas = upsert_kwargs["metadatas"]
        assert len(metas) == 2
        assert metas[0]["element_type"] == "CSVRow"
        # list metadata is JSON-coerced for ChromaDB.
        assert metas[0]["column_headers"] == json.dumps(["a", "b"])
