# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for Step 4.7 active-learning enrichment in agent_query.

Two invariants:

1. Artifacts with ``flag_reason`` set are filtered out of the result
   set (default-retrieval exclusion). They reappear when the flag is
   cleared.
2. ``endorsement_weight != 1.0`` is recorded on the result as
   ``_endorsement_weight`` and applied AFTER reranking (Step 5.05). It is no
   longer pre-multiplied into relevance here, because the cross-encoder rerank
   overwrites relevance downstream — a pre-rerank multiply was a silent no-op
   under the GPU/sidecar paths. Boosted artifacts rise, demoted ones sink in
   the post-rerank pass.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.agents.query_agent import _apply_active_learning_signals


def _fake_driver(meta_by_id: dict[str, dict]):
    """Build a MagicMock neo4j driver returning the given metadata."""
    fake_session = MagicMock()

    def fake_run(query, **kwargs):
        ids = kwargs.get("ids") or []
        result = MagicMock()
        rows = [
            {
                "id": aid,
                "weight": meta_by_id.get(aid, {}).get("weight", 1.0),
                "flag": meta_by_id.get(aid, {}).get("flag", ""),
                # The real query RETURNs coalesce(a.archived, false) AS archived;
                # keep this fake row faithful to the join shape (AF-001).
                "archived": meta_by_id.get(aid, {}).get("archived", False),
            }
            for aid in ids if aid in meta_by_id
        ]
        result.__iter__ = lambda self: iter(rows)
        return result

    fake_session.run.side_effect = fake_run
    fake_driver = MagicMock()
    fake_driver.session.return_value.__enter__ = MagicMock(return_value=fake_session)
    fake_driver.session.return_value.__exit__ = MagicMock(return_value=False)
    return fake_driver


def test_endorsement_weight_recorded_for_postrerank():
    results = [
        {"artifact_id": "a-endorsed", "relevance": 0.5, "text": "x"},
        {"artifact_id": "a-plain",    "relevance": 0.5, "text": "y"},
        {"artifact_id": "a-demoted",  "relevance": 0.5, "text": "z"},
    ]
    driver = _fake_driver({
        "a-endorsed": {"weight": 2.0, "flag": ""},
        "a-plain":    {"weight": 1.0, "flag": ""},
        "a-demoted":  {"weight": 0.5, "flag": ""},
    })

    out = _apply_active_learning_signals(results, driver)
    by_id = {r["artifact_id"]: r for r in out}

    # Relevance is UNCHANGED at this stage — the reranker overwrites it, so the
    # weight is recorded and applied post-rerank (Step 5.05) instead.
    assert by_id["a-endorsed"]["relevance"] == pytest.approx(0.5)
    assert by_id["a-endorsed"]["_endorsement_weight"] == pytest.approx(2.0)
    assert by_id["a-plain"]["relevance"] == pytest.approx(0.5)
    assert "_endorsement_weight" not in by_id["a-plain"]  # weight 1.0 → not stamped
    assert by_id["a-demoted"]["relevance"] == pytest.approx(0.5)
    assert by_id["a-demoted"]["_endorsement_weight"] == pytest.approx(0.5)


def test_flagged_artifacts_filtered_out():
    results = [
        {"artifact_id": "a-clean",    "relevance": 0.7, "text": "x"},
        {"artifact_id": "a-flagged",  "relevance": 0.7, "text": "y"},
    ]
    driver = _fake_driver({
        "a-clean":   {"weight": 1.0, "flag": ""},
        "a-flagged": {"weight": 1.0, "flag": "outdated"},
    })

    out = _apply_active_learning_signals(results, driver)
    ids = {r["artifact_id"] for r in out}
    assert "a-clean" in ids
    assert "a-flagged" not in ids


def test_archived_artifacts_filtered_out():
    """AF-001: soft-deleted / quarantined (archived) artifacts must NOT surface
    as RAG evidence on the vector arm. The post-retrieval join drops them exactly
    as it drops flagged artifacts; clearing the flag restores the artifact."""
    results = [
        {"artifact_id": "a-live",     "relevance": 0.7, "text": "x"},
        {"artifact_id": "a-archived", "relevance": 0.9, "text": "secret"},
    ]
    driver = _fake_driver({
        "a-live":     {"weight": 1.0, "flag": "", "archived": False},
        "a-archived": {"weight": 1.0, "flag": "", "archived": True},
    })

    out = _apply_active_learning_signals(results, driver)
    ids = {r["artifact_id"] for r in out}
    assert "a-live" in ids
    assert "a-archived" not in ids


def test_results_with_no_artifact_id_passthrough_unchanged():
    """Some retrieval paths (eval harness, synthetic results) lack an
    artifact_id. The function must not crash on them."""
    results = [
        {"relevance": 0.5, "text": "no artifact id at all"},
        {"artifact_id": "", "relevance": 0.5, "text": "empty id"},
    ]
    driver = _fake_driver({})
    out = _apply_active_learning_signals(results, driver)
    assert len(out) == 2  # neither touched


def test_missing_artifact_in_graph_passes_through_untouched():
    """Chunk metadata can point at an artifact_id that no longer
    exists in Neo4j (race with delete). Treat as default weight=1.0
    + unflagged so the chunk survives."""
    results = [
        {"artifact_id": "a-orphaned", "relevance": 0.6, "text": "x"},
    ]
    driver = _fake_driver({})  # graph is empty — id not found
    out = _apply_active_learning_signals(results, driver)
    assert len(out) == 1
    assert out[0]["relevance"] == 0.6
    assert "_endorsement_weight" not in out[0]
