# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for Phase O.1 cross-store ingest atomicity.

Covers:
- Happy path: ingest → both stores committed → chunk is committed
- Neo4j fails: chunk stays pending, retrieval skips it
- Recovery: pending → Neo4j retry succeeds → committed
- Recovery exhausted: pending → Neo4j fails twice → chunk purged
- Idempotency: same content+source ingested twice → only one row
- Retrieval gate: cerid_state="pending" chunks are filtered out
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_collection_mock() -> MagicMock:
    """Return a mock ChromaDB collection with a tracked metadata store."""
    coll = MagicMock()
    # Track per-chunk metadata so update() can modify it
    _stored: dict[str, dict] = {}

    def _add(ids, documents, metadatas):
        for i, chunk_id in enumerate(ids):
            _stored[chunk_id] = dict(metadatas[i] if metadatas else {})

    def _update(ids, metadatas=None):
        for i, chunk_id in enumerate(ids):
            if chunk_id in _stored and metadatas:
                _stored[chunk_id].update(metadatas[i])

    def _get(ids=None, where=None, include=None):
        if ids is not None:
            rows = {cid: _stored[cid] for cid in ids if cid in _stored}
        else:
            rows = dict(_stored)
        # Apply simple where filter: {"cerid_state": {"$eq": "pending"}}
        if where and "cerid_state" in where:
            op = where["cerid_state"]
            if isinstance(op, dict) and "$eq" in op:
                rows = {k: v for k, v in rows.items() if v.get("cerid_state") == op["$eq"]}
            elif isinstance(op, dict) and "$ne" in op:
                rows = {k: v for k, v in rows.items() if v.get("cerid_state") != op["$ne"]}
        return {
            "ids": list(rows.keys()),
            "documents": [None] * len(rows),
            "metadatas": list(rows.values()),
        }

    def _delete(ids):
        for cid in ids:
            _stored.pop(cid, None)

    coll.add.side_effect = _add
    coll.upsert.side_effect = _add  # ingest_content upserts (content-addressed ids)
    coll.update.side_effect = _update
    coll.get.side_effect = _get
    coll.delete.side_effect = _delete
    coll._stored = _stored
    return coll


def _make_neo4j_mock() -> tuple[MagicMock, MagicMock]:
    """Return (driver, session) mock pair."""
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__ = MagicMock(return_value=session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    # Default: no existing artifact (dedup check returns None)
    session.run.return_value.single.return_value = None
    return driver, session


# ---------------------------------------------------------------------------
# Tests: two-phase write (ingest_content)
# ---------------------------------------------------------------------------

class TestTwoPhaseWrite:
    """Tests for the two-phase write boundary in ingest_content."""

    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_happy_path_chunks_committed(self, mock_chroma, mock_neo4j, mock_redis):
        """Ingest completes → all Chroma chunks are cerid_state=committed."""
        collection = _make_collection_mock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver, session = _make_neo4j_mock()
        mock_neo4j.return_value = driver

        with patch("app.services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.return_value = None
            mock_graph.discover_relationships.return_value = 0

            result = _call_ingest_content("hello world content", "coding", "happy.txt")

        assert result["status"] == "success"
        # All chunks should be committed
        for meta in collection._stored.values():
            assert meta.get("cerid_state") == "committed", (
                f"Expected committed, got {meta.get('cerid_state')!r}"
            )

    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_neo4j_failure_leaves_chunks_pending(self, mock_chroma, mock_neo4j, mock_redis):
        """When Neo4j raises, Chroma chunks remain cerid_state=pending (not deleted)."""
        collection = _make_collection_mock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver, session = _make_neo4j_mock()
        mock_neo4j.return_value = driver

        with patch("app.services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.side_effect = RuntimeError("Neo4j is down")

            result = _call_ingest_content("some content", "coding", "fail.txt")

        assert result["status"] == "error"
        assert "Graph storage failed" in result["error"]
        # Chunks should still exist in Chroma (not rolled back) and be pending
        assert len(collection._stored) > 0
        for meta in collection._stored.values():
            assert meta.get("cerid_state") == "pending", (
                f"Expected pending, got {meta.get('cerid_state')!r}"
            )

    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_idempotency_key_written_to_metadata(self, mock_chroma, mock_neo4j, mock_redis):
        """cerid_idempotency_key is present in Chroma chunk metadata."""
        collection = _make_collection_mock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver, session = _make_neo4j_mock()
        mock_neo4j.return_value = driver

        with patch("app.services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.return_value = None
            mock_graph.discover_relationships.return_value = 0

            _call_ingest_content("idempotent content", "coding", "idem.txt")

        for meta in collection._stored.values():
            assert "cerid_idempotency_key" in meta
            assert len(meta["cerid_idempotency_key"]) == 64  # SHA-256 hex

    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_pending_at_written_to_metadata(self, mock_chroma, mock_neo4j, mock_redis):
        """cerid_pending_at is written as an ISO timestamp during staging."""
        collection = _make_collection_mock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver, session = _make_neo4j_mock()
        mock_neo4j.return_value = driver

        with patch("app.services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.side_effect = RuntimeError("down")

            _call_ingest_content("pending content", "coding", "pend.txt")

        for meta in collection._stored.values():
            assert "cerid_pending_at" in meta
            # Must be parseable as a datetime
            dt = datetime.fromisoformat(meta["cerid_pending_at"])
            assert dt is not None

    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_concurrent_constraint_still_rolls_back(self, mock_chroma, mock_neo4j, mock_redis):
        """Concurrent duplicate (constraint violation) still rolls back Chroma."""
        collection = _make_collection_mock()
        mock_chroma.return_value.get_or_create_collection.return_value = collection

        driver, session = _make_neo4j_mock()
        mock_neo4j.return_value = driver

        with patch("app.services.ingestion.graph") as mock_graph:
            mock_graph.find_artifact_by_filename.return_value = None
            mock_graph.create_artifact.side_effect = RuntimeError(
                "constraint violation on content_hash"
            )

            result = _call_ingest_content("content", "coding", "dup.txt")

        assert result["status"] == "duplicate"
        # Chroma should be cleaned up
        assert len(collection._stored) == 0


# ---------------------------------------------------------------------------
# Tests: retrieval gate (pending filter)
# ---------------------------------------------------------------------------

class TestRetrievalGatePendingFilter:
    """Tests for _exclude_pending in core/agents/query_agent.py."""

    def test_exclude_pending_no_where(self):
        """With no existing filter, returns pending exclusion clause."""
        from core.agents.query_agent import _exclude_pending
        with _pending_filter_enabled():
            result = _exclude_pending(None)
        assert result == {"cerid_state": {"$ne": "pending"}}

    def test_exclude_pending_with_simple_where(self):
        """Combines pending exclusion with an existing simple filter using $and."""
        from core.agents.query_agent import _exclude_pending
        with _pending_filter_enabled():
            result = _exclude_pending({"domain": "coding"})
        assert "$and" in result
        clauses = result["$and"]
        assert {"domain": "coding"} in clauses
        assert {"cerid_state": {"$ne": "pending"}} in clauses

    def test_exclude_pending_with_existing_and(self):
        """Appends to an existing $and list."""
        from core.agents.query_agent import _exclude_pending
        existing = {"$and": [{"tenant_id": "t1"}]}
        with _pending_filter_enabled():
            result = _exclude_pending(existing)
        assert "$and" in result
        assert {"tenant_id": "t1"} in result["$and"]
        assert {"cerid_state": {"$ne": "pending"}} in result["$and"]

    def test_exclude_pending_already_filtered(self):
        """Does not double-wrap when cerid_state already in where."""
        from core.agents.query_agent import _exclude_pending
        already = {"cerid_state": {"$ne": "pending"}, "domain": "coding"}
        with _pending_filter_enabled():
            result = _exclude_pending(already)
        assert result is already  # unchanged

    def test_exclude_pending_disabled_by_env(self):
        """When CERID_FILTER_PENDING_CHUNKS=false, filter is a pass-through."""
        from core.agents.query_agent import _exclude_pending
        with patch.dict(os.environ, {"CERID_FILTER_PENDING_CHUNKS": "false"}):
            result = _exclude_pending({"domain": "coding"})
        assert result == {"domain": "coding"}

    def test_idempotency_key_helper(self):
        """_idempotency_key is deterministic and changes on any input change."""
        from app.services.ingestion import _idempotency_key
        key1 = _idempotency_key("content", "uri", "tenant")
        key2 = _idempotency_key("content", "uri", "tenant")
        key3 = _idempotency_key("content2", "uri", "tenant")
        assert key1 == key2
        assert key1 != key3
        assert len(key1) == 64  # SHA-256 hex


# ---------------------------------------------------------------------------
# Tests: recovery service
# ---------------------------------------------------------------------------

class TestIngestRecoveryService:
    """Unit tests for app/services/ingest_recovery.py."""

    @pytest.mark.asyncio
    async def test_scan_orphans_empty(self):
        """scan_orphans returns empty list when no collections or no pending chunks."""
        chroma_client = MagicMock()
        chroma_client.list_collections.return_value = []

        with patch("app.services.ingest_recovery.get_chroma", return_value=chroma_client):
            from app.services.ingest_recovery import scan_orphans
            result = await scan_orphans()

        assert result == []

    @pytest.mark.asyncio
    async def test_scan_orphans_finds_stale_pending(self):
        """scan_orphans returns orphans whose cerid_pending_at is old enough."""
        from app.services.ingest_recovery import scan_orphans

        old_ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=120)).isoformat()
        recent_ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=10)).isoformat()

        collection = MagicMock()
        collection.get.return_value = {
            "ids": ["chunk-old", "chunk-recent"],
            "documents": ["doc1", "doc2"],
            "metadatas": [
                {"cerid_state": "pending", "cerid_pending_at": old_ts, "artifact_id": "art1", "domain": "coding"},
                {"cerid_state": "pending", "cerid_pending_at": recent_ts, "artifact_id": "art2", "domain": "coding"},
            ],
        }

        raw_coll = MagicMock()
        raw_coll.name = "coll-coding"

        chroma_client = MagicMock()
        chroma_client.list_collections.return_value = [raw_coll]
        chroma_client.get_collection.return_value = collection

        with patch("app.services.ingest_recovery.get_chroma", return_value=chroma_client):
            result = await scan_orphans(max_age_seconds=60)

        assert len(result) == 1
        assert result[0].chunk_id == "chunk-old"
        assert result[0].artifact_id == "art1"

    @pytest.mark.asyncio
    async def test_recover_orphan_neo4j_success(self):
        """recover_orphan returns COMMITTED when Neo4j succeeds."""
        from app.services.ingest_recovery import OrphanRecord, RecoveryAction, recover_orphan

        old_ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=120)).isoformat()
        orphan = OrphanRecord(
            chunk_id="c1",
            artifact_id="art1",
            domain="coding",
            collection_name="coll-coding",
            idempotency_key="abc",
            pending_at=old_ts,
            document="doc text",
            metadata={"filename": "f.txt", "quality_score": "0.5"},
            retry_count=0,
        )

        collection = MagicMock()
        chroma_client = MagicMock()
        chroma_client.get_collection.return_value = collection
        driver = MagicMock()

        with (
            patch("app.services.ingest_recovery.get_chroma", return_value=chroma_client),
            patch("app.services.ingest_recovery.get_neo4j", return_value=driver),
            patch("app.services.ingest_recovery.graph") as mock_graph,
        ):
            mock_graph.create_artifact.return_value = None
            action = await recover_orphan(orphan)

        assert action == RecoveryAction.COMMITTED
        # update should have been called with cerid_state=committed
        update_calls = collection.update.call_args_list
        committed_calls = [
            c for c in update_calls
            if c[1].get("metadatas") and any(
                m.get("cerid_state") == "committed"
                for m in c[1]["metadatas"]
            )
        ]
        assert committed_calls, "Expected collection.update with cerid_state=committed"

    @pytest.mark.asyncio
    async def test_recover_orphan_decrypts_summary_before_neo4j_write(self):
        """Task 2.6a: an encrypted Chroma ``summary`` is decrypted before it
        reaches ``graph.create_artifact`` — Neo4j's ``summary`` property must
        stay cleartext (it's queried by value), never the ``enc:v1:`` blob
        that ``_encrypt_chroma_metadata`` writes into the Chroma-bound copy.
        """
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            pytest.skip("cryptography not installed")

        from app.services.ingest_recovery import OrphanRecord, RecoveryAction, recover_orphan
        from utils.encryption import encrypt_field, reset_encryptor

        key = Fernet.generate_key().decode()
        reset_encryptor()
        original_summary = "The quarterly roadmap in plain English."

        try:
            with patch.dict(os.environ, {"CERID_ENCRYPTION_KEY": key}):
                encrypted_summary = encrypt_field(original_summary)
                assert encrypted_summary.startswith("enc:v1:")

                old_ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=120)).isoformat()
                orphan = OrphanRecord(
                    chunk_id="c-enc",
                    artifact_id="art-enc",
                    domain="coding",
                    collection_name="coll-coding",
                    idempotency_key="enc1",
                    pending_at=old_ts,
                    document="doc text",
                    metadata={"filename": "f.txt", "summary": encrypted_summary},
                    retry_count=0,
                )

                collection = MagicMock()
                chroma_client = MagicMock()
                chroma_client.get_collection.return_value = collection
                driver = MagicMock()

                with (
                    patch("app.services.ingest_recovery.get_chroma", return_value=chroma_client),
                    patch("app.services.ingest_recovery.get_neo4j", return_value=driver),
                    patch("app.services.ingest_recovery.graph") as mock_graph,
                ):
                    mock_graph.create_artifact.return_value = None
                    action = await recover_orphan(orphan)
        finally:
            reset_encryptor()

        assert action == RecoveryAction.COMMITTED
        _, call_kwargs = mock_graph.create_artifact.call_args
        assert call_kwargs["summary"] == original_summary
        assert not call_kwargs["summary"].startswith("enc:v1:")

    @pytest.mark.asyncio
    async def test_recover_orphan_neo4j_fails_deferred(self):
        """recover_orphan returns DEFERRED when Neo4j fails and retry budget remains."""
        from app.services.ingest_recovery import OrphanRecord, RecoveryAction, recover_orphan

        old_ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=120)).isoformat()
        orphan = OrphanRecord(
            chunk_id="c2",
            artifact_id="art2",
            domain="coding",
            collection_name="coll-coding",
            idempotency_key="def",
            pending_at=old_ts,
            document="doc",
            metadata={},
            retry_count=0,  # first attempt → budget remains
        )

        collection = MagicMock()
        chroma_client = MagicMock()
        chroma_client.get_collection.return_value = collection
        driver = MagicMock()

        with (
            patch("app.services.ingest_recovery.get_chroma", return_value=chroma_client),
            patch("app.services.ingest_recovery.get_neo4j", return_value=driver),
            patch("app.services.ingest_recovery.graph") as mock_graph,
        ):
            mock_graph.create_artifact.side_effect = RuntimeError("neo4j down")
            action = await recover_orphan(orphan)

        assert action == RecoveryAction.DEFERRED

    @pytest.mark.asyncio
    async def test_recover_orphan_purged_after_max_retries(self):
        """recover_orphan purges the chunk after _MAX_RECOVERY_ATTEMPTS failures."""
        from app.services.ingest_recovery import (
            _MAX_RECOVERY_ATTEMPTS,
            OrphanRecord,
            RecoveryAction,
            recover_orphan,
        )

        old_ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=120)).isoformat()
        # retry_count is already at max-1 so this attempt exhausts the budget
        orphan = OrphanRecord(
            chunk_id="c3",
            artifact_id="art3",
            domain="coding",
            collection_name="coll-coding",
            idempotency_key="ghi",
            pending_at=old_ts,
            document="doc",
            metadata={},
            retry_count=_MAX_RECOVERY_ATTEMPTS - 1,
        )

        collection = MagicMock()
        chroma_client = MagicMock()
        chroma_client.get_collection.return_value = collection
        driver = MagicMock()

        with (
            patch("app.services.ingest_recovery.get_chroma", return_value=chroma_client),
            patch("app.services.ingest_recovery.get_neo4j", return_value=driver),
            patch("app.services.ingest_recovery.graph") as mock_graph,
        ):
            mock_graph.create_artifact.side_effect = RuntimeError("still down")
            action = await recover_orphan(orphan)

        assert action == RecoveryAction.PURGED
        # delete must have been called with the chunk ID
        collection.delete.assert_called_once_with(ids=["c3"])

    @pytest.mark.asyncio
    async def test_recover_orphan_constraint_treated_as_committed(self):
        """A content_hash constraint violation means artifact already exists → committed."""
        from app.services.ingest_recovery import OrphanRecord, RecoveryAction, recover_orphan

        old_ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=120)).isoformat()
        orphan = OrphanRecord(
            chunk_id="c4",
            artifact_id="art4",
            domain="coding",
            collection_name="coll-coding",
            idempotency_key="xyz",
            pending_at=old_ts,
            document="doc",
            metadata={},
            retry_count=0,
        )

        collection = MagicMock()
        chroma_client = MagicMock()
        chroma_client.get_collection.return_value = collection
        driver = MagicMock()

        with (
            patch("app.services.ingest_recovery.get_chroma", return_value=chroma_client),
            patch("app.services.ingest_recovery.get_neo4j", return_value=driver),
            patch("app.services.ingest_recovery.graph") as mock_graph,
        ):
            mock_graph.create_artifact.side_effect = RuntimeError(
                "constraint violation on content_hash"
            )
            action = await recover_orphan(orphan)

        assert action == RecoveryAction.COMMITTED


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _call_ingest_content(
    content: str,
    domain: str,
    filename: str,
) -> dict:
    """Thin wrapper calling ingest_content with standard test params."""
    from app.services.ingestion import ingest_content
    return ingest_content(
        content,
        domain=domain,
        metadata={"filename": filename},
        skip_quality=True,
    )


@contextmanager
def _pending_filter_enabled():
    """Ensure CERID_FILTER_PENDING_CHUNKS is "true" for the duration."""
    with patch.dict(os.environ, {"CERID_FILTER_PENDING_CHUNKS": "true"}):
        yield
