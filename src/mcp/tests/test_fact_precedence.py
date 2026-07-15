# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase F (F2) — validity-vs-ranking precedence contract for recall_memories.

The rule (plan F2): validity filters ADMISSIBILITY first; ranking boosts order
the SURVIVING set. Concretely, the Step 3.6 interval-admission filter runs BEFORE
the Step 4 score sort, so a CLOSED candidate is dropped even when it is the
single highest-ranked candidate — no ordering or boost can resurrect a candidate
validity already removed. These tests pin that ordering by making the closed
candidate the top-ranked one (smallest vector distance → highest score) and
asserting it is gone with the filter on, while the surviving open candidates stay
ordered by score.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.agents.memory import recall_memories

_HIGH_ENTAILMENT = {"entailment": 0.9, "neutral": 0.05, "contradiction": 0.05}


def _chroma_ranked() -> MagicMock:
    """Three candidates; the CLOSED one has the smallest distance, so absent any
    admission filter it ranks #1 by adjusted_score."""
    specs = [
        # (id, distance, valid_to)   — smaller distance == higher similarity/score
        ("closed", 0.02, "2026-05-01"),  # closed interval, yet top-ranked
        ("open_hi", 0.05, ""),           # open, second by score
        ("open_lo", 0.10, ""),           # open, third by score
    ]
    ids = [s[0] for s in specs]
    metas = [
        {
            "artifact_id": mid,
            "memory_type": "empirical",
            "valid_from": "2025-01-01T00:00:00Z",
            "valid_to": vt,
            "access_count": "0",
            "summary": f"summary-{mid}",
        }
        for mid, _dist, vt in specs
    ]
    collection = MagicMock()
    collection.query.return_value = {
        "ids": [ids],
        "documents": [[f"doc {mid}" for mid in ids]],
        "distances": [[dist for _mid, dist, _vt in specs]],
        "metadatas": [metas],
    }
    client = MagicMock()
    client.get_or_create_collection.return_value = collection
    return client


@pytest.mark.asyncio
async def test_top_ranked_closed_candidate_is_dropped_before_ordering(monkeypatch) -> None:
    # Admission (Step 3.6) runs BEFORE the score sort (Step 4): the closed
    # candidate is the highest-scored, yet the filter removes it — ordering never
    # gets a chance to resurrect it.
    monkeypatch.setattr("config.features.ENABLE_FACT_INVALIDATION_FILTER", True)
    with patch("core.utils.nli.nli_score", return_value=_HIGH_ENTAILMENT):
        results = await recall_memories("q", _chroma_ranked(), None, top_k=10)
    ordered = [m["memory_id"] for m in results]
    assert "closed" not in ordered                 # dropped despite being #1 by score
    assert ordered == ["open_hi", "open_lo"]        # survivors ranked by score


@pytest.mark.asyncio
async def test_closed_candidate_ranks_first_when_filter_off() -> None:
    # Control: with the filter OFF the closed candidate is admitted and, being the
    # highest-scored, sorts to #1 — proving it really is the top rank the filter
    # overrides above (admissibility precedes ranking, not the reverse).
    with patch("core.utils.nli.nli_score", return_value=_HIGH_ENTAILMENT):
        results = await recall_memories("q", _chroma_ranked(), None, top_k=10)
    ordered = [m["memory_id"] for m in results]
    assert ordered[0] == "closed"                          # top-ranked when admitted
    assert set(ordered) == {"closed", "open_hi", "open_lo"}
