# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for services/ingestion.py — core ingestion service layer.

External dependencies (Neo4j, ChromaDB, Redis, etc.) are stubbed
by conftest.py. Tests focus on logic: hashing, path validation,
duplicate detection flow, and response shapes.
"""

import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest

import app.services.ingestion as ingestion_module
from app.services.ingestion import (
    _content_hash,
    _rollback_chromadb,
    ingest_batch,
    ingest_content,
    validate_file_path,
)
from errors import StorageLimitExceededError

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

        with patch("app.services.ingestion.graph") as mock_graph, patch(
            "app.services.content_lifecycle.remove_orphan_chunks"
        ) as mock_rollback:
            mock_graph.find_artifact_by_filename.return_value = None
            # Simulate a constraint violation on create
            mock_graph.create_artifact.side_effect = Exception(
                "Neo.ClientError.Schema.ConstraintValidationFailed: "
                "Node already exists with label 'Artifact' and property 'content_hash'"
            )

            result = ingest_content("race condition content", domain="coding")

        assert result["status"] == "duplicate"
        assert result["duplicate_of"] == "(concurrent)"
        # Cleanup fans across all stores via the coordinator, not just Chroma.
        mock_rollback.assert_called_once()


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

    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_chunks_stamped_with_embedding_version(self, mock_chroma, mock_neo4j, mock_redis):
        """Phase 4.4 — every chunk written at ingest carries embedding_model
        + embedding_model_version metadata, sourced from the active config
        (not a caller-supplied override)."""
        import config as cfg

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

            # A caller-supplied embedding_model_version must NOT survive —
            # it describes what actually computed the vector, not a claim
            # a caller can make (same trust boundary as tenant_id).
            ingest_content(
                "stamp test " * 20,
                domain="coding",
                metadata={"embedding_model_version": "attacker-supplied"},
            )

        collection.upsert.assert_called_once()
        metadatas = collection.upsert.call_args.kwargs["metadatas"]
        assert metadatas, "expected at least one chunk metadata dict"
        for meta in metadatas:
            assert meta["embedding_model"] == cfg.EMBEDDING_MODEL
            assert meta["embedding_model_version"] == cfg.embedding_version_for_domain("coding")
            assert meta["embedding_model_version"] != "attacker-supplied"


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
    """Test the compensating transaction helper. Post-CL-2 it fans the rollback
    across ALL chunk-bearing stores (Chroma + BM25 + SPLADE) via the
    content-lifecycle coordinator's ``remove_orphan_chunks`` (chunk-only, no
    Neo4j node), with cache busting skipped (staged pending chunks are never
    retrievable). It no longer deletes only the Chroma collection directly."""

    def test_fans_rollback_across_stores(self):
        with patch("app.services.content_lifecycle.remove_orphan_chunks") as mock_rollback:
            _rollback_chromadb(["id1", "id2", "id3"], "coding")
        mock_rollback.assert_called_once_with(
            ["id1", "id2", "id3"], "coding", chroma=None, bust_caches=False,
        )

    def test_handles_rollback_failure(self):
        with patch(
            "app.services.content_lifecycle.remove_orphan_chunks",
            side_effect=Exception("stores unavailable"),
        ):
            # Should not raise — logs a CRITICAL orphan warning instead.
            _rollback_chromadb(["id1"], "coding")

    def test_empty_chunk_ids(self):
        with patch("app.services.content_lifecycle.remove_orphan_chunks") as mock_rollback:
            _rollback_chromadb([], "coding")
        mock_rollback.assert_called_once_with([], "coding", chroma=None, bust_caches=False)


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

        with patch("app.services.ingestion.graph") as mock_graph, patch(
            "app.services.content_lifecycle.remove_orphan_chunks"
        ) as mock_rollback:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.side_effect = Exception(
                "ConstraintValidationFailed content_hash uniqueness"
            )

            result = ingest_content("concurrent test", domain="coding")

        assert result["status"] == "duplicate"
        mock_rollback.assert_called_once()

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
    """Corpus mutations must invalidate BOTH query-result caches (C1 flat +
    C2 semantic) through the unified CL-14 contract. Before CL-14 the ingest
    hook busted only C2, leaving the flat cache C1 stale for every service-layer
    ingest path (AF-095). The hook call is a local import inside
    ``app.services.ingestion``, so it's mocked at its home module
    (``utils.query_cache.invalidate_query_caches_threaded``), not at the
    ingestion module.
    """

    @patch("utils.query_cache.invalidate_query_caches_threaded")
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

    @patch("utils.query_cache.invalidate_query_caches_threaded")
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

    @patch("utils.query_cache.invalidate_query_caches_threaded")
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
            "utils.query_cache.invalidate_query_caches_threaded",
            side_effect=RuntimeError("cache backend unreachable"),
        ):
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.return_value = None
            mock_graph.discover_relationships.return_value = 0

            result = ingest_content("resilience check", domain="coding")

        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# Tests: CL-1 source-node write path
# ---------------------------------------------------------------------------

class TestSourceLinking:
    """CL-1 — ingest_content links the artifact to its :Source and bumps that
    source's counters, but ONLY when metadata carries a source_id that resolves
    to a real :Source node (existence-checked), so an external-capture id can
    never create a dangling FROM_SOURCE edge or spurious counter."""

    def _drive(self, filename_meta, discovered_edges: int = 0):
        with patch("app.services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.return_value = None
            mock_graph.discover_relationships.return_value = discovered_edges
            return ingest_content("content from a source", domain="coding", metadata=filename_meta)

    @patch("app.db.neo4j.sources.increment_source_counters")
    @patch("app.db.neo4j.sources.link_artifact")
    @patch("app.db.neo4j.sources.get_source")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_links_when_source_resolves(
        self, mock_chroma, mock_neo4j, mock_redis, mock_get_source, mock_link, mock_incr,
    ):
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection
        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None
        mock_get_source.return_value = {"id": "src-uuid", "quality_floor": 0.0}

        result = self._drive({"source_id": "src-uuid"})

        assert result["status"] == "success"
        mock_get_source.assert_called()  # existence check ran
        mock_link.assert_called_once()
        # link_artifact(driver, artifact_id, source_id) — source_id is the 3rd arg
        assert mock_link.call_args.args[2] == "src-uuid"
        mock_incr.assert_called_once()
        assert mock_incr.call_args.kwargs.get("artifacts") == 1
        # AF-023: edges= must always be passed, even when nothing was discovered.
        assert mock_incr.call_args.kwargs.get("edges") == 0

    @patch("app.db.neo4j.sources.increment_source_counters")
    @patch("app.db.neo4j.sources.link_artifact")
    @patch("app.db.neo4j.sources.get_source")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_edges_counter_reflects_discovered_relationships(
        self, mock_chroma, mock_neo4j, mock_redis, mock_get_source, mock_link, mock_incr,
    ):
        """AF-023: increment_source_counters must be called with edges= set to
        whatever discover_relationships actually found, not always 0."""
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection
        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None
        mock_get_source.return_value = {"id": "src-uuid", "quality_floor": 0.0}

        result = self._drive({"source_id": "src-uuid"}, discovered_edges=3)

        assert result["status"] == "success"
        mock_incr.assert_called_once()
        assert mock_incr.call_args.kwargs.get("edges") == 3

    @patch("app.db.neo4j.sources.increment_source_counters")
    @patch("app.db.neo4j.sources.link_artifact")
    @patch("app.db.neo4j.sources.get_source")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_does_not_link_when_source_missing(
        self, mock_chroma, mock_neo4j, mock_redis, mock_get_source, mock_link, mock_incr,
    ):
        """An external-capture id in source_id that has no :Source node must NOT
        create a dangling edge — get_source returns None → no link/counter."""
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection
        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None
        mock_get_source.return_value = None  # external id, no :Source node

        result = self._drive({"source_id": "external-app-id-123"})

        assert result["status"] == "success"
        mock_get_source.assert_called()
        mock_link.assert_not_called()
        mock_incr.assert_not_called()

    @patch("app.db.neo4j.sources.link_artifact")
    @patch("app.db.neo4j.sources.get_source")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_no_source_id_skips_lookup_entirely(
        self, mock_chroma, mock_neo4j, mock_redis, mock_get_source, mock_link,
    ):
        """upload/text ingests carry no source_id — no source lookup at all."""
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection
        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        result = self._drive({"filename": "note.txt"})

        assert result["status"] == "success"
        mock_get_source.assert_not_called()
        mock_link.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: ingest_content — dedup-by-external_id (AF-052)
# ---------------------------------------------------------------------------

class TestExternalIdDedup:
    """AF-007/AF-052 — connectors pass a stable external_id (Apple Notes id,
    Message-ID) that is NOT a :Source UUID. A re-ingest of the same
    (source_kind, external_id) with edited content must UPDATE the existing
    artifact in place, not create a second one — even when the payload carries
    no `filename` (apple_notes). A genuine :Source UUID keeps flowing to the
    quality floor."""

    @patch("utils.query_cache.invalidate_query_caches_threaded")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_external_id_collision_updates_in_place(
        self, mock_chroma, mock_neo4j, mock_redis, mock_sem,
    ):
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None  # _check_duplicate: no exact hit

        prev = {"id": "note-artifact-id", "content_hash": "old-hash", "chunk_ids": "[]"}
        with patch("app.services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_external_id.return_value = prev
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.update_artifact.return_value = None

            result = ingest_content(
                "edited note body",
                domain="notes",
                metadata={
                    "external_id": "apple_notes:42",
                    "source_kind": "apple_notes",
                    "filename": "My Note",
                },
            )

        # Routed to the edit path: same artifact, no second one created.
        assert result["status"] == "updated"
        assert result["artifact_id"] == "note-artifact-id"
        mock_graph.create_artifact.assert_not_called()
        # Looked up by the (source_kind, external_id) pair, not by content_hash.
        mock_graph.find_artifact_by_external_id.assert_called_once()
        assert mock_graph.find_artifact_by_external_id.call_args.args[1:] == (
            "apple_notes", "apple_notes:42",
        )

    @patch("utils.query_cache.invalidate_query_caches_threaded")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_no_filename_repeated_external_id_dedups(
        self, mock_chroma, mock_neo4j, mock_redis, mock_sem,
    ):
        """apple_notes posts a title, not a filename — the filename re-ingest
        branch can never fire for it. The external_id branch must still catch
        the edit so a second artifact is never created."""
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        prev = {"id": "aid", "content_hash": "old-hash", "chunk_ids": "[]"}
        with patch("app.services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_external_id.return_value = prev
            mock_graph.update_artifact.return_value = None

            result = ingest_content(
                "new content for a note that has no filename",
                domain="notes",
                metadata={"external_id": "apple_notes:7", "source_kind": "apple_notes"},
            )

        assert result["status"] == "updated"
        assert result["artifact_id"] == "aid"
        mock_graph.create_artifact.assert_not_called()
        # The filename branch was never even consulted (fname == "text_input").
        mock_graph.find_artifact_by_filename.assert_not_called()

    @patch("utils.query_cache.invalidate_query_caches_threaded")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_new_external_id_creates_artifact_and_stamps_it(
        self, mock_chroma, mock_neo4j, mock_redis, mock_sem,
    ):
        """First delivery of an external item: no prior match → fresh create,
        and create_artifact receives external_id + source_kind so the NEXT
        edit can find it."""
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        with patch("app.services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_external_id.return_value = None
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.return_value = None
            mock_graph.discover_relationships.return_value = 0

            result = ingest_content(
                "brand new note",
                domain="notes",
                metadata={"external_id": "apple_notes:99", "source_kind": "apple_notes"},
            )

        assert result["status"] == "success"
        create_kwargs = mock_graph.create_artifact.call_args.kwargs
        assert create_kwargs.get("external_id") == "apple_notes:99"
        assert create_kwargs.get("source_kind") == "apple_notes"

    @patch("app.db.neo4j.sources.get_source", return_value=None)
    @patch("app.services.quality_floors.should_drop", return_value=False)
    @patch("utils.query_cache.invalidate_query_caches_threaded")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_source_uuid_still_flows_to_quality_floor(
        self, mock_chroma, mock_neo4j, mock_redis, mock_sem, mock_should_drop, mock_get_source,
    ):
        """A genuine :Source UUID in source_id (never routed to external_id)
        still reaches should_drop, so per-source quality floors keep working."""
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver = MagicMock()
        session = MagicMock()
        mock_neo4j.return_value = driver
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        source_uuid = "550e8400-e29b-41d4-a716-446655440000"
        with patch("app.services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.find_artifact_by_external_id.return_value = None
            mock_graph.create_artifact.return_value = None
            mock_graph.discover_relationships.return_value = 0

            result = ingest_content(
                "content from a registered source",
                domain="coding",
                metadata={"source_id": source_uuid, "filename": "x.txt"},
            )

        assert result["status"] == "success"
        mock_should_drop.assert_called_once()
        # should_drop received the :Source UUID, not None.
        assert mock_should_drop.call_args.args[0] == source_uuid

    @patch("app.db.neo4j.sources.get_source", return_value=None)
    @patch("app.services.quality_floors.should_drop", return_value=False)
    @patch("utils.query_cache.invalidate_query_caches_threaded")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_external_id_never_reaches_quality_floor(
        self, mock_chroma, mock_neo4j, mock_redis, mock_sem, mock_should_drop, mock_get_source,
    ):
        """The external-capture case: an external_id (and no source_id) means
        should_drop is invoked with None, so the per-source floor is a no-op —
        the external id can never be mistaken for a :Source UUID."""
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
            mock_graph.find_artifact_by_external_id.return_value = None
            mock_graph.create_artifact.return_value = None
            mock_graph.discover_relationships.return_value = 0

            ingest_content(
                "external capture body",
                domain="notes",
                metadata={"external_id": "apple_notes:5", "source_kind": "apple_notes"},
            )

        mock_should_drop.assert_called_once()
        assert mock_should_drop.call_args.args[0] is None


# ---------------------------------------------------------------------------
# Tests: force_reindex — retroactive feature application (Phase 2.6)
# ---------------------------------------------------------------------------

class TestForceReindex:
    """force_reindex re-embeds UNCHANGED content so newly-enabled retrieval
    features apply retroactively. It must bypass the exact-hash dedup and route
    to the relationship-preserving _reingest_artifact path."""

    @patch("utils.query_cache.invalidate_query_caches_threaded")
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

    @patch("utils.query_cache.invalidate_query_caches_threaded")
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


# ---------------------------------------------------------------------------
# Tests: _reingest_artifact — Phase 4.3 re-ingest hygiene
# ---------------------------------------------------------------------------

class TestReingestEntityReExtraction:
    """On content change, _reingest_artifact must clear the artifact's
    stale MENTIONS edges and re-enqueue entity extraction through the same
    mechanism first-ingest uses. Unchanged content (the force_reindex
    path) must touch neither — those MENTIONS are still accurate."""

    @patch("utils.query_cache.invalidate_query_caches_threaded")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_changed_content_clears_mentions_and_reextracts(
        self, mock_chroma, mock_neo4j, mock_redis, mock_sem,
    ):
        driver = MagicMock()
        mock_neo4j.return_value = driver
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        from app.services.ingestion import _reingest_artifact

        prev = {"id": "aid", "content_hash": "old-hash", "chunk_ids": "[]"}
        with (
            patch("app.services.ingestion.graph") as mock_graph,
            patch(
                "app.services.ingestion._enqueue_entity_extraction_if_enabled",
            ) as mock_enqueue,
        ):
            mock_graph.update_artifact.return_value = None
            result = _reingest_artifact(
                prev, "brand new content", "coding",
                {"filename": "note.txt"}, "new-hash",
            )

        assert result["status"] == "updated"
        mock_graph.remove_mentions_for_artifact.assert_called_once_with(driver, "aid")
        mock_enqueue.assert_called_once_with(artifact_id="aid")

    @patch("utils.query_cache.invalidate_query_caches_threaded")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_identical_content_skips_mentions_and_reextraction(
        self, mock_chroma, mock_neo4j, mock_redis, mock_sem,
    ):
        """force_reindex re-embeds UNCHANGED content (same hash reaches
        _reingest_artifact) — nothing about the entities changed."""
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        from app.services.ingestion import _reingest_artifact

        prev = {"id": "aid", "content_hash": "same-hash", "chunk_ids": "[]"}
        with (
            patch("app.services.ingestion.graph") as mock_graph,
            patch(
                "app.services.ingestion._enqueue_entity_extraction_if_enabled",
            ) as mock_enqueue,
        ):
            mock_graph.update_artifact.return_value = None
            result = _reingest_artifact(
                prev, "unchanged content", "coding",
                {"filename": "note.txt"}, "same-hash",
            )

        assert result["status"] == "updated"
        mock_graph.remove_mentions_for_artifact.assert_not_called()
        mock_enqueue.assert_not_called()

    @patch("utils.query_cache.invalidate_query_caches_threaded")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_failed_graph_update_skips_mentions_clear_and_reextraction(
        self, mock_chroma, mock_neo4j, mock_redis, mock_sem,
    ):
        """Atomicity: if the Neo4j content_hash update itself fails, the
        artifact node still reflects the OLD content — clearing MENTIONS
        ahead of a graph write that never landed would leave the artifact
        with neither old nor new MENTIONS. Both new hooks must be gated on
        update_artifact succeeding first.

        AF-005 (CL-3): a failed re-ingest Neo4j write now returns ``status
        "error"`` and leaves the new chunks staged ``pending`` for the recovery
        job — it must NOT report ``"updated"`` (the old swallow-and-continue
        behavior left the node pointing at deleted chunk_ids with the new chunks
        unrecoverable). The mentions-clear + re-extraction stay gated on success."""
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        from app.services.ingestion import _reingest_artifact

        prev = {"id": "aid", "content_hash": "old-hash", "chunk_ids": "[]"}
        with (
            patch("app.services.ingestion.graph") as mock_graph,
            patch(
                "app.services.ingestion._enqueue_entity_extraction_if_enabled",
            ) as mock_enqueue,
        ):
            mock_graph.update_artifact.side_effect = RuntimeError("neo4j write failed")
            result = _reingest_artifact(
                prev, "brand new content", "coding",
                {"filename": "note.txt"}, "new-hash",
            )

        # AF-005: failed graph write → error (not a false "updated"); neither
        # success-gated hook fires.
        assert result["status"] == "error"
        mock_graph.remove_mentions_for_artifact.assert_not_called()
        mock_enqueue.assert_not_called()

    @patch("utils.query_cache.invalidate_query_caches_threaded")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_mentions_clear_failure_does_not_block_reextraction_enqueue(
        self, mock_chroma, mock_neo4j, mock_redis, mock_sem,
    ):
        """remove_mentions_for_artifact is a non-blocking observability
        boundary like every other post-commit hook in this module — a
        raise there must not stop the re-extraction enqueue and must not
        fail the re-ingest."""
        collection = MagicMock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        from app.services.ingestion import _reingest_artifact

        prev = {"id": "aid", "content_hash": "old-hash", "chunk_ids": "[]"}
        with (
            patch("app.services.ingestion.graph") as mock_graph,
            patch(
                "app.services.ingestion._enqueue_entity_extraction_if_enabled",
            ) as mock_enqueue,
        ):
            mock_graph.update_artifact.return_value = None
            mock_graph.remove_mentions_for_artifact.side_effect = RuntimeError("neo4j down")
            result = _reingest_artifact(
                prev, "brand new content", "coding",
                {"filename": "note.txt"}, "new-hash",
            )

        assert result["status"] == "updated"
        mock_enqueue.assert_called_once_with(artifact_id="aid")


# ---------------------------------------------------------------------------
# Tests: ingest backpressure (AF-042)
# ---------------------------------------------------------------------------

class TestIngestStorageBackpressure:
    """STORAGE_LIMIT_MB / WARN_PCT / CRITICAL_PCT wired into the ingest hot
    path. Backpressure only: below WARN proceeds silently, WARN..CRITICAL
    proceeds but logs once, at/above CRITICAL rejects with a 507-mapped
    ``StorageLimitExceededError``. See app/services/storage_metrics.py for
    the shared threshold classification tested directly.
    """

    def setup_method(self):
        # _storage_warn_logged is module state that survives across tests.
        ingestion_module._storage_warn_logged = False

    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    @patch("app.services.ingestion.get_storage_report")
    def test_healthy_status_proceeds(self, mock_report, mock_chroma, mock_neo4j, mock_redis):
        mock_report.return_value = {"status": "healthy", "usage_pct": 10, "limit_mb": 2048}
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
            result = ingest_content("healthy band content", domain="coding")

        assert result["status"] == "success"

    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    @patch("app.services.ingestion.get_storage_report")
    @patch("app.services.ingestion.logger")
    def test_warning_status_proceeds_and_logs_once(
        self, mock_logger, mock_report, mock_chroma, mock_neo4j, mock_redis,
    ):
        mock_report.return_value = {
            "status": "warning", "usage_pct": 65, "limit_mb": 2048, "warn_pct": 60,
        }
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
            r1 = ingest_content("warn band content one", domain="coding")
            r2 = ingest_content("warn band content two", domain="coding")

        assert r1["status"] == "success"
        assert r2["status"] == "success"
        warn_calls = [
            c for c in mock_logger.warning.call_args_list
            if c.args and c.args[0] == "storage_backpressure_warning usage_pct=%s limit_mb=%s warn_pct=%s"
        ]
        assert len(warn_calls) == 1, "warning must log once per transition into the band, not per ingest"

    @patch("app.services.ingestion.get_storage_report")
    def test_critical_status_rejects_with_507(self, mock_report):
        mock_report.return_value = {
            "status": "critical", "usage_pct": 85, "limit_mb": 2048, "critical_pct": 80,
        }

        with pytest.raises(StorageLimitExceededError) as exc_info:
            ingest_content("this must never be written", domain="coding")

        assert exc_info.value.http_status == 507

    @patch("app.services.ingestion.get_storage_report")
    def test_critical_status_rejects_before_any_write(self, mock_report):
        """The reject happens before get_chroma()/get_neo4j() are ever
        touched — a rejected ingest must not partially write."""
        mock_report.return_value = {"status": "critical", "usage_pct": 90}

        with (
            patch("app.services.ingestion.get_chroma") as mock_chroma,
            patch("app.services.ingestion.get_neo4j") as mock_neo4j,
            pytest.raises(StorageLimitExceededError),
        ):
            ingest_content("rejected content", domain="coding")

        mock_chroma.assert_not_called()
        mock_neo4j.assert_not_called()

    @patch("app.services.ingestion.get_storage_report")
    @pytest.mark.asyncio
    async def test_critical_status_rejects_batch_before_any_item_runs(self, mock_report):
        mock_report.return_value = {"status": "critical", "usage_pct": 90}

        with pytest.raises(StorageLimitExceededError):
            await ingest_batch([{"content": "one"}, {"content": "two"}])

    @patch("config.STORAGE_BACKPRESSURE_ENABLED", False)
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    @patch("app.services.ingestion.get_storage_report")
    def test_disabled_flag_skips_enforcement_even_at_critical(
        self, mock_report, mock_chroma, mock_neo4j, mock_redis,
    ):
        mock_report.return_value = {"status": "critical", "usage_pct": 99}
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
            result = ingest_content("flag disabled content", domain="coding")

        assert result["status"] == "success"
        mock_report.assert_not_called()

    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    @patch("app.services.ingestion.get_storage_report")
    def test_report_failure_fails_open(self, mock_report, mock_chroma, mock_neo4j, mock_redis):
        """A broken storage report (Chroma/Neo4j/Redis unreachable) must not
        also take down ingest — a monitoring outage fails open."""
        mock_report.side_effect = RuntimeError("storage report unavailable")
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
            result = ingest_content("fail-open content", domain="coding")

        assert result["status"] == "success"
