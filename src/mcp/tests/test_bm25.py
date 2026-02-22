"""Tests for BM25 hybrid search index (Phase 4B.1)."""


import pytest

from utils.bm25 import BM25Index, _tokenize, is_available


def test_tokenize_basic():
    tokens = _tokenize("Hello, World! This is a test.")
    assert "hello" in tokens
    assert "world" in tokens
    assert "test" in tokens
    # Single-char tokens should be filtered
    assert "a" not in tokens


def test_tokenize_code():
    tokens = _tokenize("import os\nfrom pathlib import Path")
    assert "import" in tokens
    assert "pathlib" in tokens
    assert "path" in tokens


@pytest.mark.skipif(not is_available(), reason="rank_bm25 not installed")
class TestBM25Index:
    def test_add_and_search(self, tmp_path):
        idx = BM25Index("test_domain", data_dir=str(tmp_path))
        added = idx.add_documents(
            ["chunk_1", "chunk_2", "chunk_3"],
            [
                "Python is a programming language used for web development",
                "JavaScript runs in the browser and on Node.js servers",
                "Python can also be used for data science and machine learning",
            ],
        )
        assert added == 3
        assert idx.size == 3

        results = idx.search("Python programming", top_k=2)
        assert len(results) > 0
        # Python chunks should rank higher
        top_id = results[0][0]
        assert top_id in ("chunk_1", "chunk_3")
        # Scores should be in [0, 1]
        assert all(0 <= score <= 1 for _, score in results)

    def test_deduplication(self, tmp_path):
        idx = BM25Index("test_dedup", data_dir=str(tmp_path))
        idx.add_documents(["c1"], ["hello world"])
        idx.add_documents(["c1"], ["hello world again"])  # same ID
        assert idx.size == 1

    def test_persistence(self, tmp_path):
        # Need 3+ docs so BM25 IDF is non-zero (2-doc corpus gives log(1)=0)
        idx1 = BM25Index("test_persist", data_dir=str(tmp_path))
        idx1.add_documents(
            ["c1", "c2", "c3"],
            [
                "alpha beta gamma alpha",
                "delta epsilon zeta",
                "alpha theta kappa",
            ],
        )

        # Create a new index from the same data dir
        idx2 = BM25Index("test_persist", data_dir=str(tmp_path))
        assert idx2.size == 3

        # Search should still work
        results = idx2.search("alpha beta")
        assert len(results) > 0
        assert results[0][0] == "c1"

    def test_empty_search(self, tmp_path):
        idx = BM25Index("test_empty", data_dir=str(tmp_path))
        results = idx.search("anything")
        assert results == []

    def test_no_match(self, tmp_path):
        idx = BM25Index("test_nomatch", data_dir=str(tmp_path))
        idx.add_documents(["c1"], ["apple banana cherry"])
        results = idx.search("xylophone")
        assert results == []
