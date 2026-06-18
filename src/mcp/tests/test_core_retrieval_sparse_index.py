# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Mocked unit tests for the per-domain SPLADE sparse index (C3.2)."""

from __future__ import annotations

import json

import pytest

from core.retrieval import sparse, sparse_index

# ---------------------------------------------------------------------------
# Shared fixture — mocks the sparse encoder so tests don't need the model.
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_encoder(monkeypatch, tmp_path):
    """Provides a deterministic 3-doc fixture without loading any model.

    Each text encodes to a hand-built sparse vector so the test can
    assert exact dot-product / ranking behavior.
    """
    # Token-id → weight maps designed so query "alpha beta" hits
    # doc1 strongly, doc2 weakly, doc3 not at all.
    canned = {
        "alpha gamma": {10: 0.9, 20: 0.4},  # doc1
        "alpha beta delta": {10: 0.7, 11: 0.6, 30: 0.3},  # doc2
        "epsilon zeta": {40: 0.5, 50: 0.4},  # doc3
        "alpha beta": {10: 0.8, 11: 0.5},  # query
    }

    def fake_encode_batch(texts):
        return [canned.get(t, {}) for t in texts]

    def fake_encode_text(text):
        return canned.get(text, {})

    monkeypatch.setattr(sparse, "is_available", lambda: True)
    monkeypatch.setattr(sparse, "encode_batch", fake_encode_batch)
    monkeypatch.setattr(sparse, "encode_text", fake_encode_text)
    monkeypatch.setattr(sparse_index, "SPARSE_DATA_DIR", str(tmp_path))
    sparse_index.reset_for_test()
    return canned


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_index_chunks_returns_zero_when_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(sparse, "is_available", lambda: False)
    monkeypatch.setattr(sparse_index, "SPARSE_DATA_DIR", str(tmp_path))
    sparse_index.reset_for_test()
    assert sparse_index.index_chunks("code", ["c1"], ["text"]) == 0


def test_search_returns_empty_when_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(sparse, "is_available", lambda: False)
    monkeypatch.setattr(sparse_index, "SPARSE_DATA_DIR", str(tmp_path))
    sparse_index.reset_for_test()
    assert sparse_index.search_sparse("code", "anything") == []


def test_remove_chunks_drops_from_index_postings_and_disk(mock_encoder):
    sparse_index.index_chunks(
        "code",
        ["doc1", "doc2", "doc3"],
        ["alpha gamma", "alpha beta delta", "epsilon zeta"],
    )
    removed = sparse_index.remove_chunks("code", ["doc2"])
    assert removed == 1

    idx = sparse_index.get_index("code")
    assert idx.size == 2
    assert "doc2" not in idx._docs
    # Inverted index was rebuilt — no posting still references doc2.
    assert all(
        doc_id != "doc2"
        for postings in idx._postings.values()
        for doc_id, _w in postings
    )
    # Disk was rewritten (not append-only): a fresh index agrees.
    sparse_index.reset_for_test()
    idx2 = sparse_index.get_index("code")
    assert idx2.size == 2
    assert "doc2" not in idx2._docs


def test_remove_then_readd_refreshes_stale_vector(mock_encoder):
    """Re-ingest: same chunk_id, new text must replace the stale vector."""
    sparse_index.index_chunks("code", ["doc1"], ["alpha gamma"])
    # Naive re-add is a dedup no-op (proves the staleness bug).
    assert sparse_index.index_chunks("code", ["doc1"], ["epsilon zeta"]) == 0
    sparse_index.remove_chunks("code", ["doc1"])
    assert sparse_index.index_chunks("code", ["doc1"], ["epsilon zeta"]) == 1
    # Now matches the new text's tokens, not the old.
    hits = {cid for cid, _ in sparse_index.search_sparse("code", "epsilon zeta", top_k=5)}
    assert "doc1" in hits


def test_remove_chunks_noop_when_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(sparse, "is_available", lambda: False)
    monkeypatch.setattr(sparse_index, "SPARSE_DATA_DIR", str(tmp_path))
    sparse_index.reset_for_test()
    assert sparse_index.remove_chunks("code", ["c1"]) == 0


def test_add_and_search_roundtrip(mock_encoder):
    added = sparse_index.index_chunks(
        "code",
        ["doc1", "doc2", "doc3"],
        ["alpha gamma", "alpha beta delta", "epsilon zeta"],
    )
    assert added == 3

    hits = sparse_index.search_sparse("code", "alpha beta", top_k=5)
    assert len(hits) == 2  # doc3 has zero overlap

    # doc2 ranks above doc1 because the query overlaps both tokens 10 and 11.
    ids = [hit[0] for hit in hits]
    assert ids[0] == "doc2"
    assert ids[1] == "doc1"

    # Scores normalized to [0, 1] by top hit.
    assert hits[0][1] == 1.0
    assert hits[1][1] < 1.0


def test_idempotent_re_add(mock_encoder):
    """Re-adding the same chunk_ids must be a no-op (matches BM25)."""
    sparse_index.index_chunks(
        "code", ["doc1", "doc2"], ["alpha gamma", "alpha beta delta"],
    )
    added = sparse_index.index_chunks(
        "code", ["doc1", "doc2"], ["alpha gamma", "alpha beta delta"],
    )
    assert added == 0
    assert sparse_index.get_index("code").size == 2


def test_persistence_roundtrip(mock_encoder, tmp_path):
    sparse_index.index_chunks(
        "code", ["doc1", "doc2"], ["alpha gamma", "alpha beta delta"],
    )

    # Drop in-memory cache; the JSONL on disk must rehydrate the index.
    sparse_index.reset_for_test()

    idx = sparse_index.get_index("code")
    assert idx.size == 2
    assert "doc1" in idx._docs
    assert "doc2" in idx._docs

    hits = idx.search("alpha beta", top_k=5)
    assert {h[0] for h in hits} == {"doc1", "doc2"}


def test_corrupted_line_tolerance(mock_encoder, tmp_path):
    """A truncated JSONL line must not abort the whole load.

    Matches the BM25 corrupted-line tolerance — a SIGKILL between
    append() and the OS flush can leave one trailing line malformed;
    the index should drop just that entry and keep the rest.
    """
    sparse_index.index_chunks(
        "code", ["doc1", "doc2"], ["alpha gamma", "alpha beta delta"],
    )

    # Append a corrupted line; older valid lines stay intact.
    corpus = tmp_path / "code.jsonl"
    with open(corpus, "a") as f:
        f.write("{not-json")

    sparse_index.reset_for_test()
    idx = sparse_index.get_index("code")
    # Both valid entries survived; the corrupted one was skipped.
    assert idx.size == 2


def test_tenant_scoping_at_index_layer(mock_encoder):
    """A search with tenant_id must filter cross-tenant hits at the index.

    Mirrors Workstream E Phase 0 BM25 contract.
    """
    sparse_index.index_chunks(
        "code", ["doc1"], ["alpha gamma"], tenant_id="tenant-a",
    )
    sparse_index.index_chunks(
        "code", ["doc2"], ["alpha beta delta"], tenant_id="tenant-b",
    )

    hits_a = sparse_index.search_sparse(
        "code", "alpha beta", tenant_id="tenant-a", top_k=10,
    )
    hits_b = sparse_index.search_sparse(
        "code", "alpha beta", tenant_id="tenant-b", top_k=10,
    )
    assert [h[0] for h in hits_a] == ["doc1"]
    assert [h[0] for h in hits_b] == ["doc2"]


def test_empty_query_returns_no_hits(mock_encoder):
    sparse_index.index_chunks(
        "code", ["doc1"], ["alpha gamma"],
    )
    # encoder returns {} for any text not in the canned map
    assert sparse_index.search_sparse("code", "uncanned text") == []


def test_disk_format_uses_string_keys(mock_encoder, tmp_path):
    """JSON has no integer keys — assert the on-disk format casts to str."""
    sparse_index.index_chunks("code", ["doc1"], ["alpha gamma"])
    corpus = tmp_path / "code.jsonl"
    line = json.loads(corpus.read_text().strip())
    assert all(isinstance(k, str) for k in line["v"])
