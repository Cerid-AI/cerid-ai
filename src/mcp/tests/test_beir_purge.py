# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for Phase 0 data-hygiene tooling:

* ``scripts/seed_beir_corpus.py`` — ``_eval_seed_allowed`` guard.
* ``scripts/purge_eval_seed.py``  — ``_is_eval_seed`` predicate and
  dry-run / execute behaviour with mocked stores.

No live Neo4j, Chroma, or Redis is required.  The scripts are loaded
via ``importlib`` so the repo-root ``scripts/`` does not need to be on
``PYTHONPATH``.
"""
from __future__ import annotations

import importlib.util
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Script loading helpers
# ---------------------------------------------------------------------------

def _load_script(rel_path: str):
    """Load a repo-root script by path without requiring it on PYTHONPATH.

    Uses ``importlib.util`` so each load is isolated to its own module
    object; test isolation is preserved across parametrized tests.

    Falls back to ``None`` (caller must ``pytest.skip``) when the repo
    root cannot be found — matches the ``scripts_dir()`` contract from
    ``_helpers.py``.
    """
    from tests._helpers import repo_root
    root = repo_root()
    if root is None:
        return None
    script_path = root / rel_path
    if not script_path.exists():
        return None
    spec = importlib.util.spec_from_file_location(
        f"_cerid_test_script_{script_path.stem}",
        script_path,
    )
    mod = importlib.util.module_from_spec(spec)
    # Do NOT exec_module here — the scripts import app modules at
    # function scope (inside main / _ingest_dataset), so the module-
    # level code is safe to execute on the test host without a stack.
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _seed_beir_module():
    return _load_script("scripts/seed_beir_corpus.py")


def _purge_module():
    return _load_script("scripts/purge_eval_seed.py")


# ---------------------------------------------------------------------------
# Task 2: _eval_seed_allowed guard
# ---------------------------------------------------------------------------

class TestEvalSeedGuard:
    """``_eval_seed_allowed`` must gate on the explicit opt-in env var."""

    @pytest.fixture(scope="class")
    def mod(self):
        m = _seed_beir_module()
        if m is None:
            pytest.skip("scripts/seed_beir_corpus.py not reachable from this env")
        return m

    def test_allowed_when_var_is_one(self, mod):
        assert mod._eval_seed_allowed({"CERID_ALLOW_EVAL_SEED": "1"}) is True

    def test_refused_when_var_absent(self, mod):
        assert mod._eval_seed_allowed({}) is False

    def test_refused_when_var_is_zero(self, mod):
        assert mod._eval_seed_allowed({"CERID_ALLOW_EVAL_SEED": "0"}) is False

    def test_refused_when_var_is_truthy_but_not_one(self, mod):
        # "true", "yes", "True" are NOT accepted — only the literal "1".
        assert mod._eval_seed_allowed({"CERID_ALLOW_EVAL_SEED": "true"}) is False
        assert mod._eval_seed_allowed({"CERID_ALLOW_EVAL_SEED": "yes"}) is False

    def test_refused_when_var_is_empty_string(self, mod):
        assert mod._eval_seed_allowed({"CERID_ALLOW_EVAL_SEED": ""}) is False

    def test_whitespace_is_stripped(self, mod):
        # Operators sometimes copy-paste with a trailing space.
        assert mod._eval_seed_allowed({"CERID_ALLOW_EVAL_SEED": " 1 "}) is True


# ---------------------------------------------------------------------------
# Task 1: _is_eval_seed selection predicate
# ---------------------------------------------------------------------------

class TestIsEvalSeed:
    """``_is_eval_seed`` identifies contamination artifacts correctly."""

    @pytest.fixture(scope="class")
    def mod(self):
        m = _purge_module()
        if m is None:
            pytest.skip("scripts/purge_eval_seed.py not reachable from this env")
        return m

    # ── BEIR family ────────────────────────────────────────────────────────

    def test_matches_beir_scifact(self, mod):
        assert mod._is_eval_seed(
            {"client_source": "seed-beir-scifact", "filename": "MED-1234.md"}
        )

    def test_matches_beir_nfcorpus(self, mod):
        assert mod._is_eval_seed(
            {"client_source": "seed-beir-nfcorpus", "filename": "4983.md"}
        )

    def test_matches_arbitrary_beir_prefix(self, mod):
        assert mod._is_eval_seed(
            {"client_source": "seed-beir-newdataset", "filename": "doc.md"}
        )

    # ── Beta-smoke family ──────────────────────────────────────────────────

    def test_matches_beta_smoke_url_filename(self, mod):
        assert mod._is_eval_seed({"filename": "beta-smoke://1", "client_source": ""})

    def test_matches_beta_smoke_url_with_number(self, mod):
        assert mod._is_eval_seed({"filename": "beta-smoke://2", "client_source": ""})

    # ── Must NOT match ─────────────────────────────────────────────────────

    def test_does_not_match_real_artifact(self, mod):
        assert not mod._is_eval_seed(
            {"client_source": "upload", "filename": "tax_return_2024.pdf"}
        )

    def test_does_not_match_empty_fields(self, mod):
        assert not mod._is_eval_seed({"client_source": "", "filename": ""})

    def test_does_not_match_seed_eval_corpus(self, mod):
        # seed-eval-corpus is a separate family handled by wipe_eval_corpus.py
        assert not mod._is_eval_seed(
            {"client_source": "seed-eval-corpus", "filename": "doc.md"}
        )

    def test_pack_id_always_excluded(self, mod):
        # A hypothetical pack that accidentally matches the prefix must be safe.
        assert not mod._is_eval_seed(
            {
                "client_source": "seed-beir-scifact",
                "filename": "doc.md",
                "pack_id": "some-pack",
            }
        )

    def test_none_fields_handled_safely(self, mod):
        # Artifacts from older ingest paths may have None for these fields.
        assert not mod._is_eval_seed(
            {"client_source": None, "filename": None, "pack_id": None}
        )


# ---------------------------------------------------------------------------
# Task 1: dry-run with mocked stores
# ---------------------------------------------------------------------------

class TestPurgeDryRun:
    """``_dry_run`` prints counts, mutates nothing."""

    @pytest.fixture(scope="class")
    def mod(self):
        m = _purge_module()
        if m is None:
            pytest.skip("scripts/purge_eval_seed.py not reachable from this env")
        return m

    def _make_candidates(self):
        return [
            {
                "id": "aaa-111",
                "client_source": "seed-beir-scifact",
                "filename": "MED-10.md",
                "domain": "research",
                "chunk_ids": '["aaa-111_chunk_0"]',
                "pack_id": None,
            },
            {
                "id": "bbb-222",
                "client_source": "seed-beir-nfcorpus",
                "filename": "4983.md",
                "domain": "general",
                "chunk_ids": '["bbb-222_chunk_0", "bbb-222_chunk_1"]',
                "pack_id": None,
            },
            {
                "id": "ccc-333",
                "client_source": "",
                "filename": "beta-smoke://1",
                "domain": "general",
                "chunk_ids": '["ccc-333_chunk_0"]',
                "pack_id": None,
            },
        ]

    def test_dry_run_does_not_mutate(self, mod, capsys):
        """_dry_run must not call delete_artifact or any Chroma mutation."""
        candidates = self._make_candidates()

        with (
            patch("app.db.neo4j.artifacts.delete_artifact") as mock_delete,
        ):
            mod._dry_run(candidates)
            mock_delete.assert_not_called()

    def test_dry_run_empty_is_silent(self, mod, capsys):
        """_dry_run on an empty list must not raise and should log cleanly."""
        mod._dry_run([])  # must not raise

    def test_dry_run_counts_are_correct(self, mod, caplog):
        """_dry_run reports the correct total artifact count."""
        import logging
        candidates = self._make_candidates()
        with caplog.at_level(logging.INFO, logger="purge-eval-seed"):
            mod._dry_run(candidates)
        # The summary line contains the count.
        combined = " ".join(caplog.messages)
        assert "3" in combined

    def test_query_candidates_neo4j_integration(self, mod):
        """_query_candidates passes correct Cypher and returns filtered rows."""
        # Build a fake Neo4j driver whose session().run().data() returns
        # a mix of eval-seed and non-eval-seed rows.
        fake_rows = [
            {
                "id": "aaa",
                "filename": "MED-10.md",
                "domain": "research",
                "client_source": "seed-beir-scifact",
                "chunk_ids": "[]",
                "pack_id": None,
            },
            {
                "id": "bbb",
                "filename": "beta-smoke://2",
                "domain": "general",
                "client_source": "",
                "chunk_ids": "[]",
                "pack_id": None,
            },
            # A pack artifact that somehow slipped past the Cypher filter:
            # Python-side predicate must still exclude it.
            {
                "id": "ccc",
                "filename": "pack-doc.md",
                "domain": "general",
                "client_source": "seed-beir-scifact",
                "chunk_ids": "[]",
                "pack_id": "some-pack-id",
            },
        ]

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.run.return_value.data.return_value = fake_rows

        mock_driver = MagicMock()
        mock_driver.session.return_value = mock_session

        result = mod._query_candidates(mock_driver)

        # "ccc" has pack_id so it must be excluded.
        assert len(result) == 2
        returned_ids = {r["id"] for r in result}
        assert "aaa" in returned_ids
        assert "bbb" in returned_ids
        assert "ccc" not in returned_ids


# ---------------------------------------------------------------------------
# Task 1: execute path with mocked stores
# ---------------------------------------------------------------------------

class TestPurgeExecute:
    """``_execute`` calls delete_artifact + Chroma delete; idempotent on second run."""

    @pytest.fixture(scope="class")
    def mod(self):
        m = _purge_module()
        if m is None:
            pytest.skip("scripts/purge_eval_seed.py not reachable from this env")
        return m

    def _make_single_candidate(self):
        return [
            {
                "id": "dead-beef",
                "client_source": "seed-beir-scifact",
                "filename": "MED-999.md",
                "domain": "research",
                "chunk_ids": '["dead-beef_chunk_0", "dead-beef_chunk_1"]',
                "pack_id": None,
            }
        ]

    def test_execute_calls_delete_artifact(self, mod):
        """_execute must call delete_artifact once per candidate."""
        candidates = self._make_single_candidate()

        fake_neo4j = MagicMock()
        fake_chroma = MagicMock()

        mock_delete = MagicMock(return_value={
            "deleted": True,
            "artifact_id": "dead-beef",
            "domain": "research",
            "filename": "MED-999.md",
            "chunk_ids": ["dead-beef_chunk_0", "dead-beef_chunk_1"],
        })
        mock_collection = MagicMock()
        fake_chroma.get_collection.return_value = mock_collection

        with (
            patch("app.db.neo4j.artifacts.delete_artifact", mock_delete),
            patch("config.collection_name", return_value="research_v1"),
        ):
            summary = mod._execute(candidates, fake_neo4j, fake_chroma)

        mock_delete.assert_called_once_with(fake_neo4j, "dead-beef")
        assert summary["deleted"] == 1
        assert summary["missing"] == 0
        assert summary["chunks_removed"] == 2

    def test_execute_idempotent_when_already_deleted(self, mod):
        """A second run on already-deleted artifacts returns deleted=0, missing=N."""
        candidates = self._make_single_candidate()

        fake_neo4j = MagicMock()
        fake_chroma = MagicMock()

        # Simulate artifact already absent from Neo4j.
        mock_delete = MagicMock(return_value={
            "deleted": False,
            "reason": "not_found",
        })
        mock_collection = MagicMock()
        fake_chroma.get_collection.return_value = mock_collection

        with (
            patch("app.db.neo4j.artifacts.delete_artifact", mock_delete),
            patch("config.collection_name", return_value="research_v1"),
        ):
            summary = mod._execute(candidates, fake_neo4j, fake_chroma)

        assert summary["deleted"] == 0
        assert summary["missing"] == 1

    def test_execute_does_not_touch_pack_artifacts(self, mod):
        """Artifacts with pack_id must never reach _execute (predicate gate)."""
        pack_artifact = {
            "id": "pack-artifact-id",
            "client_source": "seed-beir-scifact",
            "filename": "doc.md",
            "domain": "general",
            "chunk_ids": "[]",
            "pack_id": "some-pack",
        }
        # The predicate gate in _query_candidates filters these out; confirm
        # _is_eval_seed returns False so the caller never includes them.
        assert not mod._is_eval_seed(pack_artifact)
