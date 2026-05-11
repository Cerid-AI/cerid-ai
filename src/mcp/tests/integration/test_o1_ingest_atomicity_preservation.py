# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Preservation gate I19 — cross-store ingest atomicity (Phase O.1).

This is a **synthetic** preservation test: it does NOT require a live
stack.  It wires together real service code with in-memory mocks to
assert the two-phase-write and recovery-worker contracts end-to-end.

I19 invariant:
  a) A staged (pending) chunk is invisible to the retrieval gate.
  b) When Neo4j fails, chunks remain pending (not deleted).
  c) The recovery worker commits or purges orphans deterministically.

NOTE: Do NOT register I19 in docs/PRESERVATION.md until v0.92 cut.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

# Mark the whole module as a preservation gate.
pytestmark = pytest.mark.preservation


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_tracked_collection() -> MagicMock:
    """Return a mock Chroma collection with real in-memory metadata tracking."""
    coll = MagicMock()
    _store: dict[str, dict] = {}

    def _add(ids, documents, metadatas):
        for i, cid in enumerate(ids):
            _store[cid] = dict(metadatas[i] if metadatas else {})

    def _update(ids, metadatas=None):
        for i, cid in enumerate(ids):
            if cid in _store and metadatas:
                _store[cid].update(metadatas[i])

    def _get(ids=None, where=None, include=None):
        rows = {cid: _store[cid] for cid in (ids or _store) if cid in _store}
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
            _store.pop(cid, None)

    coll.add.side_effect = _add
    coll.update.side_effect = _update
    coll.get.side_effect = _get
    coll.delete.side_effect = _delete
    coll._store = _store
    return coll


# ---------------------------------------------------------------------------
# I19.a — staged pending chunk is invisible to retrieval gate
# ---------------------------------------------------------------------------

def test_i19a_pending_chunk_invisible_to_retrieval_gate():
    """Retrieval gate must exclude cerid_state=pending chunks.

    The _exclude_pending helper in query_agent must produce a where clause
    that ChromaDB's $ne operator would use to exclude pending rows.
    """
    from core.agents.query_agent import _exclude_pending

    with patch.dict(os.environ, {"CERID_FILTER_PENDING_CHUNKS": "true"}):
        where = _exclude_pending(None)

    # Must contain the exclusion clause
    assert where is not None, "Expected a filter, got None"

    # Either top-level or inside $and
    def _has_ne_pending(clause):
        if isinstance(clause, dict):
            if "cerid_state" in clause:
                v = clause["cerid_state"]
                return isinstance(v, dict) and v.get("$ne") == "pending"
            if "$and" in clause:
                return any(_has_ne_pending(c) for c in clause["$and"])
        return False

    assert _has_ne_pending(where), (
        f"Expected cerid_state $ne pending in filter, got: {where!r}"
    )


# ---------------------------------------------------------------------------
# I19.b — Neo4j failure leaves Chroma rows pending (not deleted)
# ---------------------------------------------------------------------------

def test_i19b_neo4j_failure_leaves_chunks_pending():
    """When Neo4j fails, Chroma rows must remain in pending state."""
    from app.services.ingestion import ingest_content

    collection = _make_tracked_collection()

    with (
        patch("app.services.ingestion.get_redis", return_value=MagicMock()),
        patch("app.services.ingestion.get_neo4j", return_value=MagicMock()),
        patch("app.services.ingestion.get_chroma") as mock_chroma,
        patch("app.services.ingestion.graph") as mock_graph,
    ):
        mock_chroma.return_value.get_or_create_collection.return_value = collection
        mock_graph.find_artifact_by_filename.return_value = None
        mock_graph.create_artifact.side_effect = RuntimeError("Neo4j is down")

        result = ingest_content(
            "synthetic artifact for I19",
            domain="coding",
            metadata={"filename": "i19_test.txt"},
            skip_quality=True,
        )

    # Status must be error (caller receives the failure)
    assert result["status"] == "error", f"Expected error status, got {result['status']!r}"

    # Chroma rows must NOT have been deleted (two-phase: leave pending for recovery)
    assert len(collection._store) > 0, "Expected Chroma rows to remain for recovery"

    for meta in collection._store.values():
        assert meta.get("cerid_state") == "pending", (
            f"Expected pending state, got {meta.get('cerid_state')!r}"
        )


# ---------------------------------------------------------------------------
# I19.c — recovery worker: committed path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_i19c_recovery_worker_commits_orphan():
    """Recovery worker flips pending → committed when Neo4j becomes available."""
    from app.services.ingest_recovery import OrphanRecord, RecoveryAction, recover_orphan

    old_ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=120)).isoformat()
    orphan = OrphanRecord(
        chunk_id="i19-chunk",
        artifact_id="i19-art",
        domain="coding",
        collection_name="coll-coding",
        idempotency_key="i19key",
        pending_at=old_ts,
        document="synthetic",
        metadata={"filename": "i19.txt"},
        retry_count=0,
    )

    collection = MagicMock()
    chroma_client = MagicMock()
    chroma_client.get_collection.return_value = collection

    with (
        patch("app.services.ingest_recovery.get_chroma", return_value=chroma_client),
        patch("app.services.ingest_recovery.get_neo4j", return_value=MagicMock()),
        patch("app.services.ingest_recovery.graph") as mock_graph,
    ):
        mock_graph.create_artifact.return_value = None
        action = await recover_orphan(orphan)

    assert action == RecoveryAction.COMMITTED

    # Verify that collection.update was called with cerid_state=committed
    committed_seen = False
    for call_args in collection.update.call_args_list:
        metas = call_args[1].get("metadatas") or (call_args[0][1] if len(call_args[0]) > 1 else [])
        if any(isinstance(m, dict) and m.get("cerid_state") == "committed" for m in metas):
            committed_seen = True
            break
    assert committed_seen, "Expected collection.update with cerid_state=committed"


# ---------------------------------------------------------------------------
# I19.d — recovery worker: purge path after max retries
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_i19d_recovery_worker_purges_after_max_retries():
    """Recovery worker purges chunk and adds Sentry breadcrumb after max retries."""
    from app.services.ingest_recovery import (
        _MAX_RECOVERY_ATTEMPTS,
        OrphanRecord,
        RecoveryAction,
        recover_orphan,
    )

    old_ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=120)).isoformat()
    orphan = OrphanRecord(
        chunk_id="i19-purge-chunk",
        artifact_id="i19-purge-art",
        domain="coding",
        collection_name="coll-coding",
        idempotency_key="purge-key",
        pending_at=old_ts,
        document="purge doc",
        metadata={},
        retry_count=_MAX_RECOVERY_ATTEMPTS - 1,
    )

    collection = MagicMock()
    chroma_client = MagicMock()
    chroma_client.get_collection.return_value = collection

    with (
        patch("app.services.ingest_recovery.get_chroma", return_value=chroma_client),
        patch("app.services.ingest_recovery.get_neo4j", return_value=MagicMock()),
        patch("app.services.ingest_recovery.graph") as mock_graph,
    ):
        mock_graph.create_artifact.side_effect = RuntimeError("permanent failure")
        action = await recover_orphan(orphan)

    assert action == RecoveryAction.PURGED
    collection.delete.assert_called_once_with(ids=["i19-purge-chunk"])
