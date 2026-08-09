# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Phase C: ingest-time sparse indexing guards (C3.2).

The ingest pipeline must invoke ``sparse_index.index_chunks`` after BM25
when SPLADE-v3 is enabled, and must not break the two-phase commit
when sparse encoding throws.
"""

from __future__ import annotations

from core.retrieval import sparse, sparse_index


def test_sparse_index_no_op_when_unavailable(monkeypatch, tmp_path):
    """Flag-off path must not touch disk or call the encoder.

    This is the canary for the "ingest-zero-cost" guarantee — if
    sparse_index_chunks() ever calls into the encoder when the flag is
    off, this test will fail. ``encode_batch`` is patched to raise so
    any accidental call is loud.
    """
    monkeypatch.setattr(sparse, "is_available", lambda: False)
    monkeypatch.setattr(sparse_index, "SPARSE_DATA_DIR", str(tmp_path))
    sparse_index.reset_for_test()

    def _boom(_texts):
        raise AssertionError("encoder should never run when flag off")

    monkeypatch.setattr(sparse, "encode_batch", _boom)

    result = sparse_index.index_chunks(
        "code", ["c1", "c2"], ["text one", "text two"],
    )
    assert result == 0
    assert not (tmp_path / "code.jsonl").exists()


def test_sparse_index_indexes_when_available(monkeypatch, tmp_path):
    """Flag-on path encodes and persists each chunk.

    The encoder is mocked to a 1-token-per-doc deterministic output
    so we can assert exact persistence without loading a real model.
    """
    monkeypatch.setattr(sparse, "is_available", lambda: True)
    monkeypatch.setattr(
        sparse, "encode_batch",
        lambda texts: [{i + 100: 0.5} for i, _ in enumerate(texts)],
    )
    monkeypatch.setattr(sparse_index, "SPARSE_DATA_DIR", str(tmp_path))
    sparse_index.reset_for_test()

    added = sparse_index.index_chunks(
        "code", ["c1", "c2"], ["text one", "text two"],
    )
    assert added == 2
    assert (tmp_path / "code.jsonl").exists()
    lines = (tmp_path / "code.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2


def test_sparse_indexing_exception_does_not_break_callers(monkeypatch, tmp_path):
    """An encoder failure must surface as 0 added, not as a raised exception.

    Two-phase commit relies on bm25 + sparse being non-blocking — a
    sparse model OOM or hung pipe must NOT abort the ChromaDB +
    Neo4j commit transition. The ``try / log_swallowed_error``
    wrapper at the ingestion call site provides this guarantee; the
    sparse_index module itself returns 0 on encode failure.
    """
    monkeypatch.setattr(sparse, "is_available", lambda: True)

    def _boom(_texts):
        raise RuntimeError("OOM in encoder")

    monkeypatch.setattr(sparse, "encode_batch", _boom)
    monkeypatch.setattr(sparse_index, "SPARSE_DATA_DIR", str(tmp_path))
    sparse_index.reset_for_test()

    # Must not raise; must return 0.
    result = sparse_index.index_chunks("code", ["c1"], ["text"])
    assert result == 0


def test_ingestion_call_site_imports_sparse_index_chunks():
    """Smoke test: ingestion.py actually imports the new helper.

    Catches the regression where the import line is silently dropped
    by a future cleanup pass — the test is one ``importlib`` call so
    it stays cheap even on a fast suite.
    """
    import inspect

    from app.services import ingestion
    src = inspect.getsource(ingestion)
    assert "from core.retrieval.sparse_index import index_chunks as sparse_index_chunks" in src
