# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for BM25 hybrid search index (bm25s + PyStemmer)."""

import json
import time

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

    def test_remove_documents_clears_index_and_disk(self, tmp_path):
        idx = BM25Index("test_remove", data_dir=str(tmp_path))
        idx.add_documents(
            ["c1", "c2", "c3"],
            ["alpha beta gamma", "delta epsilon zeta", "eta theta iota"],
        )
        removed = idx.remove_documents(["c2"])
        assert removed == 1
        assert idx.size == 2
        # Removed chunk no longer searchable.
        assert all(cid != "c2" for cid, _ in idx.search("epsilon", top_k=5))
        # Disk corpus was rewritten (not append-only) — a fresh index agrees.
        idx2 = BM25Index("test_remove", data_dir=str(tmp_path))
        assert idx2.size == 2
        assert "c2" not in idx2._doc_id_set

    def test_remove_then_readd_refreshes_stale_text(self, tmp_path):
        """Re-ingest path: same chunk_id, new text must replace the old.

        Regression: add_documents dedup-skips known ids, so without an
        explicit remove the keyword index kept serving pre-edit text.
        """
        idx = BM25Index("test_reingest", data_dir=str(tmp_path))
        idx.add_documents(["c1", "c2"], ["zzz aaa", "filler words here"])
        # Naive re-add is a no-op (dedup) — proves the bug exists without remove.
        assert idx.add_documents(["c1"], ["bbb ccc"]) == 0
        # Remove + re-add refreshes the text under the same id.
        idx.remove_documents(["c1"])
        assert idx.add_documents(["c1"], ["bbb ccc"]) == 1
        assert idx.size == 2
        # Old term gone, new term present.
        assert any(cid == "c1" for cid, _ in idx.search("ccc", top_k=5))
        assert all(cid != "c1" for cid, _ in idx.search("zzz", top_k=5))

    def test_remove_documents_noop_on_unknown_id(self, tmp_path):
        idx = BM25Index("test_remove_noop", data_dir=str(tmp_path))
        idx.add_documents(["c1"], ["alpha beta"])
        assert idx.remove_documents(["does-not-exist"]) == 0
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

        # Index gracefully degrades to empty (no published retriever)
        assert idx._snapshot[0] is None
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

    def test_module_shim_no_longer_deprecates_tenant_omission(self, tmp_path):
        """search_bm25 without tenant_id must NOT emit a DeprecationWarning.

        The Workstream E Phase 0.5 shim that nagged on every hot-path query
        is retired; the no-tenant call is a supported mode (the caller
        applies chunk_matches_tenant on the BM25-only fallback path). Search
        still returns hits.
        """
        import warnings

        from core.retrieval.bm25 import BM25Index as _BM25Index
        from core.retrieval.bm25 import _indexes, search_bm25

        # Pre-populate the module cache so search_bm25 hits our test data.
        # Need 3+ docs so BM25 IDF is non-zero.
        idx = _BM25Index("test_dep", data_dir=str(tmp_path))
        idx.add_documents(
            ["c1", "c2", "c3"],
            ["alpha beta gamma", "alpha delta", "alpha epsilon"],
        )
        _indexes["test_dep"] = idx

        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                results = search_bm25("test_dep", "alpha")
                deprecations = [
                    w for w in caught if issubclass(w.category, DeprecationWarning)
                ]
                assert deprecations == [], "deprecation shim was not retired"
                assert results, "search_bm25 should return hits without tenant_id"
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


# ---------------------------------------------------------------------------
# Phase 2.3: deferred (debounced) rebuild off the ingest path
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not is_available(), reason="bm25s not installed")
class TestBM25DeferredRebuild:
    """The whole-corpus rebuild no longer runs inline on every ingest add."""

    def test_add_does_not_retokenize_corpus(self, tmp_path, monkeypatch):
        """Adding docs one-by-one must not re-tokenize the whole corpus.

        Regression guard for the multi-minute indexing lag: the old
        ``add_documents`` called ``_rebuild()`` on every call, re-tokenizing
        the entire (growing) corpus — 20 single-doc adds meant 20 whole-corpus
        tokenizations. RED against that implementation (corpus count == 20);
        GREEN here (0 during adds, exactly 1 deferred rebuild at first query).
        """
        import core.retrieval.bm25 as bm25_mod

        idx = BM25Index("test_no_retok", data_dir=str(tmp_path))
        calls = {"corpus": 0}
        real_tokenize = bm25_mod.bm25s.tokenize

        def spy(text, *args, **kwargs):
            # Corpus tokenization passes a list of texts; a query passes a str.
            if isinstance(text, (list, tuple)):
                calls["corpus"] += 1
            return real_tokenize(text, *args, **kwargs)

        monkeypatch.setattr(bm25_mod.bm25s, "tokenize", spy)

        for i in range(20):
            assert idx.add_documents([f"c{i}"], [f"word{i} shared alpha"]) == 1

        assert calls["corpus"] == 0, (
            f"add path re-tokenized the whole corpus {calls['corpus']}x "
            "(old inline-rebuild behavior); expected 0 with deferred rebuild"
        )

        # First search performs exactly one deferred full rebuild.
        hits = idx.search("shared", top_k=25)
        assert calls["corpus"] == 1
        assert len(hits) == 20

    def test_added_docs_searchable_on_first_query(self, tmp_path):
        """Fresh-index adds are visible at the first query (retriever is None
        so the debounce cooldown is bypassed)."""
        idx = BM25Index("test_vis_fresh", data_dir=str(tmp_path))
        idx.add_documents(
            ["c1", "c2", "c3"],
            ["alpha beta", "alpha gamma", "alpha delta"],
        )
        result_ids = {cid for cid, _ in idx.search("alpha", top_k=10)}
        assert result_ids == {"c1", "c2", "c3"}

    def test_added_docs_visible_within_debounce_window(self, tmp_path, monkeypatch):
        """A doc added after the retriever exists becomes searchable at the
        next query once the cooldown elapses (documents the
        BM25_REBUILD_DEBOUNCE_SECONDS bound; forced to 0 for determinism)."""
        monkeypatch.setattr(
            "core.retrieval.bm25.BM25_REBUILD_DEBOUNCE_SECONDS", 0.0
        )
        idx = BM25Index("test_vis_window", data_dir=str(tmp_path))
        idx.add_documents(
            ["c1", "c2", "c3"],
            ["alpha beta", "alpha gamma", "alpha delta"],
        )
        assert idx.search("alpha")  # builds the retriever

        idx.add_documents(["c4"], ["alpha zeta shared"])
        result_ids = {cid for cid, _ in idx.search("zeta", top_k=10)}
        assert "c4" in result_ids

    def test_removed_doc_does_not_resurface_within_window(self, tmp_path):
        """A removal takes effect immediately even before the next rebuild:
        the tombstoned id is filtered out of results."""
        idx = BM25Index("test_rm_window", data_dir=str(tmp_path))
        idx.add_documents(
            ["c1", "c2", "c3"],
            ["alpha beta", "gamma delta", "epsilon zeta"],
        )
        # Build the retriever (snapshot now holds c2).
        assert any(cid == "c2" for cid, _ in idx.search("gamma", top_k=5))

        # Remove within the debounce window — no rebuild, but must not resurface.
        assert idx.remove_documents(["c2"]) == 1
        results = idx.search("gamma", top_k=5)
        assert all(cid != "c2" for cid, _ in results)
        assert idx.size == 2

    def test_reingest_within_window_never_serves_stale_text(self, tmp_path, monkeypatch):
        """Remove-then-readd under the same id never serves the pre-edit text,
        even inside the debounce window (the stale snapshot entry is filtered;
        the fresh text appears after the next rebuild)."""
        idx = BM25Index("test_reingest_window", data_dir=str(tmp_path))
        idx.add_documents(
            ["c1", "c2", "c3"],
            ["zzz old", "filler one", "filler two"],
        )
        assert any(cid == "c1" for cid, _ in idx.search("zzz", top_k=5))

        # Re-ingest c1 with new text inside the window (no rebuild yet).
        idx.remove_documents(["c1"])
        assert idx.add_documents(["c1"], ["yyy new"]) == 1
        # The stale "zzz old" entry must not surface.
        assert all(cid != "c1" for cid, _ in idx.search("zzz", top_k=5))

        # After the cooldown (forced to 0) the fresh text is live.
        monkeypatch.setattr(
            "core.retrieval.bm25.BM25_REBUILD_DEBOUNCE_SECONDS", 0.0
        )
        assert any(cid == "c1" for cid, _ in idx.search("yyy", top_k=5))

    def test_rebuild_all_forces_clean_reload(self, tmp_path, monkeypatch):
        """rebuild_all() still performs a clean full rebuild from disk and the
        reloaded index is immediately queryable (not left dirty)."""
        import config
        import core.retrieval.bm25 as bm25_mod

        monkeypatch.setattr(config, "BM25_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(config, "DOMAINS", ["coding"])
        bm25_mod._indexes.clear()
        try:
            idx = bm25_mod.get_index("coding")
            idx.add_documents(
                ["c1", "c2", "c3"],
                ["alpha beta", "alpha gamma", "alpha delta"],
            )
            assert bm25_mod.rebuild_all() == 1
            idx2 = bm25_mod.get_index("coding")
            assert idx2._dirty is False
            assert len(idx2.search("alpha", top_k=10)) == 3
        finally:
            bm25_mod._indexes.clear()

    def test_get_index_evicts_lru_beyond_max_loaded_domains(self, tmp_path, monkeypatch):
        """get_index() bounds the in-memory index dict at
        BM25_MAX_LOADED_DOMAINS via LRU eviction: the least-recently-used
        domain is dropped from memory (not from disk) once the cap is
        exceeded, and re-fetching it transparently reloads from its
        durable JSONL corpus."""
        import config
        import core.retrieval.bm25 as bm25_mod

        monkeypatch.setattr(config, "BM25_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(bm25_mod, "BM25_MAX_LOADED_DOMAINS", 2)
        bm25_mod._indexes.clear()
        try:
            idx_a = bm25_mod.get_index("domain_a")
            idx_a.add_documents(["c1"], ["alpha text"])
            bm25_mod.get_index("domain_b")
            assert list(bm25_mod._indexes.keys()) == ["domain_a", "domain_b"]

            # Third distinct domain pushes the dict over the cap; the
            # least-recently-used entry (domain_a) is evicted from memory.
            bm25_mod.get_index("domain_c")
            assert len(bm25_mod._indexes) == 2
            assert "domain_a" not in bm25_mod._indexes
            assert set(bm25_mod._indexes.keys()) == {"domain_b", "domain_c"}

            # domain_a's corpus survived on disk — re-fetching reloads it.
            idx_a_reloaded = bm25_mod.get_index("domain_a")
            assert idx_a_reloaded is not idx_a
            assert idx_a_reloaded.size == 1
            assert any(
                cid == "c1" for cid, _ in idx_a_reloaded.search("alpha", top_k=5)
            )
        finally:
            bm25_mod._indexes.clear()

    def test_get_index_and_rebuild_all_serialize_on_new_domain(
        self, tmp_path, monkeypatch
    ):
        """get_index() and rebuild_all() both do a check-then-construct-or-
        reuse against the same module-level `_indexes` dict, so both must be
        guarded by the same `_indexes_lock`.

        Regression guard: rebuild_all() previously performed its
        check-then-set unlocked. If a live /admin/kb/rebuild-index call
        (rebuild_all) races an ordinary ingestion call (get_index) for a
        domain neither has cached yet, the unlocked path let both construct
        their own BM25Index and the second write orphaned the first —
        along with any documents it had already accepted in memory. With
        both call sites serialized on `_indexes_lock`, exactly one instance
        is ever constructed for the domain, regardless of which caller wins
        the race.
        """
        import threading

        import config
        import core.retrieval.bm25 as bm25_mod

        monkeypatch.setattr(config, "BM25_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(config, "DOMAINS", ["race_domain"])
        bm25_mod._indexes.clear()

        construct_count = {"n": 0}
        real_init = bm25_mod.BM25Index.__init__

        def slow_init(self, *args, **kwargs):
            construct_count["n"] += 1
            time.sleep(0.05)
            real_init(self, *args, **kwargs)

        monkeypatch.setattr(bm25_mod.BM25Index, "__init__", slow_init)

        start_barrier = threading.Barrier(2)
        results: dict = {}

        def call_get_index():
            start_barrier.wait(timeout=5)
            results["get_index"] = bm25_mod.get_index("race_domain")

        def call_rebuild_all():
            start_barrier.wait(timeout=5)
            bm25_mod.rebuild_all()

        try:
            t1 = threading.Thread(target=call_get_index)
            t2 = threading.Thread(target=call_rebuild_all)
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)

            assert construct_count["n"] == 1, (
                "expected exactly one BM25Index construction when "
                "get_index() races rebuild_all() on the same new domain, "
                f"got {construct_count['n']} — rebuild_all's unlocked "
                "check-then-set let the second write orphan the first index"
            )
            assert len(bm25_mod._indexes) == 1
            assert bm25_mod._indexes["race_domain"] is results["get_index"]
        finally:
            bm25_mod._indexes.clear()

    def test_get_index_reuses_existing_and_marks_recently_used(self, tmp_path, monkeypatch):
        """A repeat get_index() call for a live domain returns the same
        instance and moves it to the MRU end, protecting it from the next
        eviction."""
        import config
        import core.retrieval.bm25 as bm25_mod

        monkeypatch.setattr(config, "BM25_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(bm25_mod, "BM25_MAX_LOADED_DOMAINS", 2)
        bm25_mod._indexes.clear()
        try:
            idx_a = bm25_mod.get_index("domain_a")
            bm25_mod.get_index("domain_b")
            # Touch domain_a again so it becomes MRU instead of domain_b.
            assert bm25_mod.get_index("domain_a") is idx_a
            assert list(bm25_mod._indexes.keys()) == ["domain_b", "domain_a"]

            # domain_c pushes past the cap; domain_b (now LRU) is evicted.
            bm25_mod.get_index("domain_c")
            assert set(bm25_mod._indexes.keys()) == {"domain_a", "domain_c"}
        finally:
            bm25_mod._indexes.clear()

    def test_slow_rebuild_serves_stale_snapshot_without_blocking(
        self, tmp_path, monkeypatch,
    ):
        """A rebuild slower than the inline-wait cap must not stall search.

        Plant-fault for kb-idle-zero: whole-corpus re-tokenization of large
        domains ran inline on the query path and serialized into 20s+ of
        vector_search, exhausting the retrieval budget. With the bounded
        wait, the query is served from the previous snapshot immediately and
        the rebuild completes on the worker thread.
        """
        import time as _time

        import core.retrieval.bm25 as bm25_mod

        idx = BM25Index("test_slow_rebuild", data_dir=str(tmp_path))
        idx.add_documents(["c1", "c2"], ["alpha beta", "alpha gamma"])
        assert idx.search("alpha")  # first (inline) build

        monkeypatch.setattr(bm25_mod, "BM25_REBUILD_DEBOUNCE_SECONDS", 0.0)
        monkeypatch.setattr(bm25_mod, "BM25_REBUILD_MAX_INLINE_WAIT_SECONDS", 0.05)

        real_rebuild = BM25Index._rebuild_locked

        def slow_rebuild(self_idx):
            _time.sleep(0.4)
            real_rebuild(self_idx)

        monkeypatch.setattr(BM25Index, "_rebuild_locked", slow_rebuild)

        idx.add_documents(["c4"], ["alpha zeta"])
        start = _time.monotonic()
        hits = {cid for cid, _ in idx.search("alpha", top_k=10)}
        elapsed = _time.monotonic() - start

        # Served from the stale snapshot, well under the rebuild duration.
        assert elapsed < 0.3, f"search blocked on the rebuild ({elapsed:.2f}s)"
        assert hits == {"c1", "c2"}

        # The worker-thread rebuild lands; the new doc becomes searchable.
        inflight = idx._rebuild_inflight
        assert inflight is not None
        inflight[1].wait(timeout=5.0)
        assert "c4" in {cid for cid, _ in idx.search("alpha", top_k=10)}

    def test_rebuild_single_flight(self, tmp_path, monkeypatch):
        """Concurrent searches during a slow rebuild share one worker thread."""
        import time as _time

        import core.retrieval.bm25 as bm25_mod

        idx = BM25Index("test_single_flight", data_dir=str(tmp_path))
        idx.add_documents(["c1"], ["alpha beta"])
        assert idx.search("alpha")

        monkeypatch.setattr(bm25_mod, "BM25_REBUILD_DEBOUNCE_SECONDS", 0.0)
        monkeypatch.setattr(bm25_mod, "BM25_REBUILD_MAX_INLINE_WAIT_SECONDS", 0.0)

        rebuild_calls = {"n": 0}
        real_rebuild = BM25Index._rebuild_locked

        def slow_rebuild(self_idx):
            rebuild_calls["n"] += 1
            _time.sleep(0.2)
            real_rebuild(self_idx)

        monkeypatch.setattr(BM25Index, "_rebuild_locked", slow_rebuild)

        idx.add_documents(["c2"], ["alpha gamma"])
        for _ in range(5):
            idx.search("alpha")
        inflight = idx._rebuild_inflight
        assert inflight is not None
        inflight[1].wait(timeout=5.0)
        assert rebuild_calls["n"] == 1


class TestWarmIndexes:
    """kb-cold-first-touch: boot pays the cold load, not the first query."""

    def test_warm_indexes_materialises_domains_from_disk(
        self, tmp_path, monkeypatch,
    ):
        import config
        import core.retrieval.bm25 as bm25_mod

        monkeypatch.setattr(config, "BM25_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(config, "DOMAINS", ["alpha", "beta"])
        bm25_mod._indexes.clear()
        try:
            # Populate two domains, then drop them from memory so the next
            # touch is a genuine cold load off the JSONL corpus.
            bm25_mod.get_index("alpha").add_documents(["a1"], ["alpha text"])
            bm25_mod.get_index("beta").add_documents(
                ["b1", "b2"], ["beta text", "more beta"],
            )
            bm25_mod._indexes.clear()

            warmed = bm25_mod.warm_indexes()

            assert warmed == {"alpha": 1, "beta": 2}
            assert set(bm25_mod._indexes) == {"alpha", "beta"}
        finally:
            bm25_mod._indexes.clear()

    def test_warm_indexes_stops_at_the_lru_bound(self, tmp_path, monkeypatch):
        """Warming past the LRU cap would evict what it just warmed."""
        import config
        import core.retrieval.bm25 as bm25_mod

        monkeypatch.setattr(config, "BM25_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(config, "DOMAINS", ["d1", "d2", "d3", "d4"])
        monkeypatch.setattr(bm25_mod, "BM25_MAX_LOADED_DOMAINS", 2)
        bm25_mod._indexes.clear()
        try:
            warmed = bm25_mod.warm_indexes()
            assert list(warmed) == ["d1", "d2"]
            assert len(bm25_mod._indexes) == 2
        finally:
            bm25_mod._indexes.clear()
