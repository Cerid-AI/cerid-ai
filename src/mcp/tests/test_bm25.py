# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for BM25 hybrid search index (bm25s + PyStemmer)."""

import json

import pytest

from core.retrieval.bm25 import BM25Index, _tokenize, is_available


def test_tokenize_basic():
    tokens = _tokenize("Hello, World! This is a test.")
    # Stemmed: "hello" → "hello", "world" → "world", "test" → "test"
    # Stopwords removed: "this", "is", "a"
    assert "hello" in tokens
    assert "world" in tokens
    assert "test" in tokens
    assert "a" not in tokens
    assert "is" not in tokens
    assert "this" not in tokens


def test_tokenize_code():
    tokens = _tokenize("import os\nfrom pathlib import Path")
    assert "import" in tokens
    assert "pathlib" in tokens
    assert "path" in tokens


def test_tokenize_stemming():
    """Stemmer should reduce words to their root form."""
    tokens = _tokenize("running programming languages")
    # "running" → "run", "programming" → "program", "languages" → "languag"
    assert any("run" in t for t in tokens)
    assert any("program" in t for t in tokens)


def test_tokenize_empty():
    assert _tokenize("") == []
    assert _tokenize("   ") == []


@pytest.mark.skipif(not is_available(), reason="bm25s not installed")
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

    def test_persistence_format(self, tmp_path):
        """Verify JSONL uses new text format (not pre-tokenized)."""
        idx = BM25Index("test_fmt", data_dir=str(tmp_path))
        idx.add_documents(["c1"], ["Hello world testing"])

        corpus_file = tmp_path / "test_fmt.jsonl"
        with open(corpus_file) as f:
            entry = json.loads(f.readline())
        assert "text" in entry
        assert "tokens" not in entry
        assert entry["text"] == "Hello world testing"

    def test_old_format_migration(self, tmp_path):
        """Old format (pre-tokenized) should be loadable."""
        corpus_file = tmp_path / "test_migrate.jsonl"
        # Write old format entries
        with open(corpus_file, "w") as f:
            f.write(json.dumps({"id": "c1", "tokens": ["python", "web", "dev"]}) + "\n")
            f.write(json.dumps({"id": "c2", "tokens": ["java", "server", "backend"]}) + "\n")
            f.write(json.dumps({"id": "c3", "tokens": ["python", "data", "science"]}) + "\n")

        idx = BM25Index("test_migrate", data_dir=str(tmp_path))
        assert idx.size == 3

        # Search should work with migrated data
        results = idx.search("python")
        assert len(results) > 0

    def test_empty_search(self, tmp_path):
        idx = BM25Index("test_empty", data_dir=str(tmp_path))
        results = idx.search("anything")
        assert results == []

    def test_no_match(self, tmp_path):
        idx = BM25Index("test_nomatch", data_dir=str(tmp_path))
        idx.add_documents(
            ["c1", "c2", "c3"],
            [
                "apple banana cherry",
                "grape mango peach",
                "kiwi lemon orange",
            ],
        )
        results = idx.search("xylophone")
        assert results == []

    def test_empty_text_skipped(self, tmp_path):
        idx = BM25Index("test_skip", data_dir=str(tmp_path))
        added = idx.add_documents(["c1", "c2"], ["", "valid text here"])
        assert added == 1
        assert idx.size == 1


# ---------------------------------------------------------------------------
# Observability: Sentry capture tests (R1-3)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not is_available(), reason="bm25s not installed")
class TestBM25SentryCapture:
    """Assert Sentry.capture_exception fires at every silent-catch site."""

    def test_index_load_failed_captured(self, tmp_path):
        """_load() swallows corrupt corpus and reports to Sentry."""
        from unittest.mock import patch

        from core.retrieval.bm25 import BM25Index as _BM25Index

        # Write a corrupt corpus file so _load() raises
        corpus_file = tmp_path / "broken_domain.jsonl"
        corpus_file.write_text("not valid json at all!!!")

        with patch("sentry_sdk.capture_exception") as mock_capture:
            idx = _BM25Index("broken_domain", data_dir=str(tmp_path))

        # Index gracefully degrades to empty
        assert idx._retriever is None
        mock_capture.assert_called_once()

    def test_persist_failed_captured(self, tmp_path):
        """_append_to_disk() swallows write errors and reports to Sentry."""
        from unittest.mock import patch

        from core.retrieval.bm25 import BM25Index as _BM25Index

        idx = _BM25Index("write_fail_domain", data_dir=str(tmp_path))
        # Make the corpus file unwritable by patching open
        with patch("builtins.open", side_effect=OSError("read-only fs")), \
             patch("sentry_sdk.capture_exception") as mock_capture:
            idx._append_to_disk([{"id": "c1", "text": "hello world"}])

        mock_capture.assert_called_once()


# ---------------------------------------------------------------------------
# Workstream E Phase 0: tenant isolation + fsync durability
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not is_available(), reason="bm25s not installed")
class TestBM25TenantIsolation:
    """Phase 0: tenant_id parameter scopes BM25 search at the index layer."""

    def test_index_layer_filter_isolates_tenants(self, tmp_path):
        """search(tenant_id='alice') returns only alice's chunks."""
        idx = BM25Index("test_tenant_iso", data_dir=str(tmp_path))
        idx.add_documents(
            ["alice_c1", "alice_c2", "alice_c3"],
            [
                "alpha beta gamma alpha",
                "delta epsilon zeta",
                "alpha theta kappa",
            ],
            tenant_id="alice",
        )
        idx.add_documents(
            ["bob_c1", "bob_c2", "bob_c3"],
            [
                "alpha beta gamma alpha",
                "alpha sigma tau",
                "alpha chi psi",
            ],
            tenant_id="bob",
        )

        # Without tenant_id: both tenants' results are returned
        all_results = idx.search("alpha", top_k=10)
        all_ids = {cid for cid, _ in all_results}
        assert any(cid.startswith("alice_") for cid in all_ids)
        assert any(cid.startswith("bob_") for cid in all_ids)

        # With tenant_id='alice': only alice's chunks
        alice_results = idx.search("alpha", top_k=10, tenant_id="alice")
        for cid, _ in alice_results:
            assert cid.startswith("alice_"), f"leaked bob's chunk: {cid}"

        # With tenant_id='bob': only bob's chunks
        bob_results = idx.search("alpha", top_k=10, tenant_id="bob")
        for cid, _ in bob_results:
            assert cid.startswith("bob_"), f"leaked alice's chunk: {cid}"

    def test_legacy_corpus_defaults_to_default_tenant(self, tmp_path):
        """Pre-Phase-0 corpora (no tenant_id field) load as DEFAULT_TENANT_ID."""
        import config

        # Write an old-format corpus (no tenant_id field)
        corpus_file = tmp_path / "legacy_tenant.jsonl"
        with open(corpus_file, "w") as f:
            f.write(json.dumps({"id": "old_c1", "text": "alpha beta"}) + "\n")
            f.write(json.dumps({"id": "old_c2", "text": "alpha gamma"}) + "\n")
            f.write(json.dumps({"id": "old_c3", "text": "alpha delta"}) + "\n")

        idx = BM25Index("legacy_tenant", data_dir=str(tmp_path))
        assert idx.size == 3
        # All entries should be associated with DEFAULT_TENANT_ID
        for cid in ("old_c1", "old_c2", "old_c3"):
            assert idx._doc_tenant[cid] == config.DEFAULT_TENANT_ID

        # Searching with the default tenant returns all
        default_results = idx.search(
            "alpha", top_k=10, tenant_id=config.DEFAULT_TENANT_ID,
        )
        assert len(default_results) == 3

        # Searching with a different tenant returns empty (no leakage)
        other_results = idx.search("alpha", top_k=10, tenant_id="other_tenant")
        assert other_results == []

    def test_persisted_corpus_carries_tenant_field(self, tmp_path):
        """JSONL writes include tenant_id so a reload restores tenant scope."""
        idx1 = BM25Index("test_persist_tenant", data_dir=str(tmp_path))
        idx1.add_documents(
            ["a1", "a2", "a3"],
            ["alpha beta", "alpha gamma", "alpha delta"],
            tenant_id="alice",
        )

        # Verify on-disk format includes tenant_id
        corpus_file = tmp_path / "test_persist_tenant.jsonl"
        with open(corpus_file) as f:
            for line in f:
                entry = json.loads(line)
                assert entry.get("tenant_id") == "alice"

        # Reopen from disk and confirm tenant scope still works
        idx2 = BM25Index("test_persist_tenant", data_dir=str(tmp_path))
        results = idx2.search("alpha", top_k=10, tenant_id="alice")
        assert len(results) == 3
        empty = idx2.search("alpha", top_k=10, tenant_id="bob")
        assert empty == []

    def test_module_shim_emits_deprecation_when_tenant_omitted(self, tmp_path):
        """search_bm25 without tenant_id raises DeprecationWarning."""
        import warnings

        from core.retrieval.bm25 import BM25Index as _BM25Index
        from core.retrieval.bm25 import _indexes, search_bm25

        # Pre-populate the module cache so search_bm25 hits our test data
        idx = _BM25Index("test_dep", data_dir=str(tmp_path))
        idx.add_documents(["c1"], ["alpha beta gamma"])
        _indexes["test_dep"] = idx

        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                search_bm25("test_dep", "alpha")
                deprecation = [
                    w for w in caught if issubclass(w.category, DeprecationWarning)
                ]
                assert len(deprecation) == 1
                assert "tenant_id" in str(deprecation[0].message)
        finally:
            _indexes.pop("test_dep", None)


@pytest.mark.skipif(not is_available(), reason="bm25s not installed")
class TestBM25Durability:
    """Phase 0: explicit fsync closes the kill-9 corpus-drift window."""

    def test_append_calls_fsync(self, tmp_path):
        """_append_to_disk explicitly flushes and fsyncs the corpus file."""
        from unittest.mock import patch

        idx = BM25Index("test_fsync", data_dir=str(tmp_path))
        with patch("os.fsync") as mock_fsync:
            idx.add_documents(
                ["c1", "c2", "c3"],
                [
                    "alpha beta gamma alpha",
                    "delta epsilon zeta",
                    "alpha theta kappa",
                ],
            )

        # add_documents → _append_to_disk → one fsync per call
        assert mock_fsync.call_count >= 1, "os.fsync was not called on append"

    def test_fsync_failure_is_logged_not_raised(self, tmp_path):
        """A spurious fsync OSError is swallowed via log_swallowed_error."""
        from unittest.mock import patch

        idx = BM25Index("test_fsync_fail", data_dir=str(tmp_path))
        # First add succeeds and the file is created
        idx.add_documents(
            ["c1", "c2", "c3"],
            ["alpha beta gamma", "delta epsilon zeta", "alpha theta kappa"],
        )

        # Second add: fsync raises but the operation as a whole survives
        with patch("os.fsync", side_effect=OSError("ebadf")), \
             patch("core.retrieval.bm25.log_swallowed_error") as mock_log:
            added = idx.add_documents(["c4"], ["sigma tau upsilon"])

        assert added == 1
        mock_log.assert_called_once()
        args, _ = mock_log.call_args
        assert args[0] == "core.retrieval.bm25.fsync"
