# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Startup-time embedding-dim validation."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_collection(name: str, peek_embeddings):
    """Build a Chroma-style Collection mock with a peek() that returns embeddings."""
    coll = MagicMock()
    coll.name = name
    coll.peek = MagicMock(return_value={"embeddings": peek_embeddings})
    return coll


def test_validate_collection_dimensions_flags_mismatch():
    """A collection whose stored dim differs from expected is reported."""
    from app.startup import validate_collection_dimensions

    client = MagicMock()
    # Collection returns a 768-dim vector; we expect 384 → mismatch.
    mismatched = _make_collection("domain_general", [[0.1] * 768])
    client.list_collections.return_value = [mismatched]
    client.get_collection.return_value = mismatched

    result = validate_collection_dimensions(client, expected_dim=384)
    assert len(result) == 1
    assert result[0]["collection"] == "domain_general"
    assert result[0]["actual_dim"] == 768
    assert result[0]["expected_dim"] == 384


def test_validate_collection_dimensions_passes_when_matched():
    """A collection whose stored dim matches expected yields no mismatches."""
    from app.startup import validate_collection_dimensions

    client = MagicMock()
    matched = _make_collection("domain_general", [[0.1] * 384])
    client.list_collections.return_value = [matched]
    client.get_collection.return_value = matched

    assert validate_collection_dimensions(client, expected_dim=384) == []


def test_validate_collection_dimensions_skips_empty_collection():
    """An empty collection (no docs) cannot be validated — skip it."""
    from app.startup import validate_collection_dimensions

    client = MagicMock()
    empty = _make_collection("domain_empty", [])
    client.list_collections.return_value = [empty]
    client.get_collection.return_value = empty

    assert validate_collection_dimensions(client, expected_dim=768) == []


def test_validate_collection_dimensions_logs_remediation_pointer(caplog):
    """The ERROR log on mismatch must include the repair endpoint path."""
    import logging

    from app.startup import validate_collection_dimensions

    client = MagicMock()
    mismatched = _make_collection("domain_general", [[0.1] * 768])
    client.list_collections.return_value = [mismatched]
    client.get_collection.return_value = mismatched

    with caplog.at_level(logging.ERROR, logger="ai-companion.startup"):
        validate_collection_dimensions(client, expected_dim=384)

    combined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "/admin/collections/repair" in combined
    assert "embedding_dim_mismatch" in combined
    assert "domain_general" in combined


def test_run_startup_dim_check_does_not_raise_on_probe_failure():
    """If the Chroma client blows up, startup continues (returns [])."""
    from app import startup

    with patch.object(startup, "validate_collection_dimensions", side_effect=RuntimeError("boom")):
        with patch("core.utils.embeddings.get_embedding_dim", return_value=384):
            # Use a MagicMock for the chroma client
            with patch("app.deps.get_chroma", return_value=MagicMock()):
                # Should swallow the error and return [] (non-fatal)
                result = startup.run_startup_dim_check()
                assert result == []


def test_run_startup_dim_check_reports_mismatch_and_continues():
    """Mismatch is returned to caller and logged — but the function does NOT raise.

    This is the deliberate divergence from deps.py's NEO4J_PASSWORD hard-fail:
    hard-failing here would lock the operator out of the repair endpoint that
    the log message points to.
    """
    from app import startup

    mismatch = [{"collection": "domain_general", "actual_dim": 768, "expected_dim": 384}]
    with patch.object(startup, "validate_collection_dimensions", return_value=mismatch):
        with patch("core.utils.embeddings.get_embedding_dim", return_value=384):
            with patch("app.deps.get_chroma", return_value=MagicMock()):
                result = startup.run_startup_dim_check()
                assert result == mismatch
