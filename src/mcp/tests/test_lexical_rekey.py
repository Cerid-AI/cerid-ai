# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""BM25 and SPLADE corpora rename chunk ids in place for the chunk-id migration."""
from __future__ import annotations

import json
from pathlib import Path

from core.retrieval.bm25 import BM25Index
from core.retrieval.sparse_index import SparseIndex


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_bm25_rekey_renames_and_persists(tmp_path: Path):
    corpus = tmp_path / "general.jsonl"
    corpus.write_text(
        json.dumps({"id": "a_chunk_0", "text": "alpha words", "tenant_id": "t1"}) + "\n"
        + json.dumps({"id": "a_chunk_1", "text": "beta words", "tenant_id": "t1"}) + "\n"
        + json.dumps({"id": "b_x", "text": "gamma", "tenant_id": "t2"}) + "\n"
    )
    idx = BM25Index("general", data_dir=str(tmp_path))
    assert idx.rekey({"a_chunk_0": "a_new0", "a_chunk_1": "a_new1"}) == 2
    assert [(r["id"], r["text"], r["tenant_id"]) for r in _lines(corpus)] == [
        ("a_new0", "alpha words", "t1"), ("a_new1", "beta words", "t1"), ("b_x", "gamma", "t2"),
    ]
    assert BM25Index("general", data_dir=str(tmp_path))._doc_id_set == {"a_new0", "a_new1", "b_x"}


def test_bm25_rekey_onto_an_existing_id_keeps_one_entry(tmp_path: Path):
    corpus = tmp_path / "general.jsonl"
    corpus.write_text(
        json.dumps({"id": "a_new0", "text": "alpha", "tenant_id": "t1"}) + "\n"
        + json.dumps({"id": "a_chunk_0", "text": "alpha", "tenant_id": "t1"}) + "\n"
    )
    idx = BM25Index("general", data_dir=str(tmp_path))
    idx.rekey({"a_chunk_0": "a_new0"})
    assert [r["id"] for r in _lines(corpus)] == ["a_new0"]


def test_bm25_rekey_without_hits_leaves_the_file(tmp_path: Path):
    corpus = tmp_path / "general.jsonl"
    corpus.write_text(json.dumps({"id": "b_x", "text": "gamma", "tenant_id": "t2"}) + "\n")
    before = corpus.stat().st_mtime_ns
    assert BM25Index("general", data_dir=str(tmp_path)).rekey({"a_chunk_0": "a_new0"}) == 0
    assert corpus.stat().st_mtime_ns == before


def test_sparse_rekey_renames_postings_and_persists(tmp_path: Path):
    corpus = tmp_path / "general.jsonl"
    corpus.write_text(
        json.dumps({"id": "a_chunk_0", "tenant_id": "t1", "v": {"7": 0.5}}) + "\n"
        + json.dumps({"id": "a_new0", "tenant_id": "t1", "v": {"7": 0.5}}) + "\n"
        + json.dumps({"id": "b_x", "tenant_id": "t2", "v": {"9": 1.0}}) + "\n"
    )
    idx = SparseIndex("general", data_dir=str(tmp_path))
    assert idx.rekey({"a_chunk_0": "a_new0"}) == 1
    assert sorted(r["id"] for r in _lines(corpus)) == ["a_new0", "b_x"]
    assert idx._postings[7] == [("a_new0", 0.5)]
    assert SparseIndex("general", data_dir=str(tmp_path))._doc_tenant == {"a_new0": "t1", "b_x": "t2"}
