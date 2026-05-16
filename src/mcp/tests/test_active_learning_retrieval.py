# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for Step 4.7 active-learning enrichment in agent_query.

Two invariants:

1. Artifacts with ``flag_reason`` set are filtered out of the result
   set (default-retrieval exclusion). They reappear when the flag is
   cleared.
2. ``endorsement_weight != 1.0`` multiplies the result's relevance
   score before reranking — boosted artifacts rise, demoted ones sink.
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


def test_endorsement_weight_multiplies_relevance():
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
    rel_by_id = {r["artifact_id"]: r["relevance"] for r in out}

    assert rel_by_id["a-endorsed"] == pytest.approx(1.0)  # 0.5 * 2.0
    assert rel_by_id["a-plain"] == pytest.approx(0.5)     # unchanged
    assert rel_by_id["a-demoted"] == pytest.approx(0.25)  # 0.5 * 0.5


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
