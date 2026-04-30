# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for Reciprocal Rank Fusion (Workstream E Phase 3)."""

from __future__ import annotations

import pytest

from core.retrieval.rrf import DEFAULT_K, rrf_fuse


def test_rrf_default_k_is_60():
    """The literature standard. Don't drift."""
    assert DEFAULT_K == 60


def test_rrf_single_ranking_is_identity_order():
    """One input list → output preserves rank order, scores by formula."""
    ranking = [("a", 0.9), ("b", 0.5), ("c", 0.1)]
    fused = rrf_fuse([ranking])
    ids = [doc_id for doc_id, _ in fused]
    assert ids == ["a", "b", "c"]


def test_rrf_two_rankings_dedup_and_combine():
    """A doc that ranks high in both lists scores higher than one that ranks high in one."""
    vector = [("a", 0.92), ("b", 0.85), ("c", 0.71)]
    bm25 = [("c", 12.4), ("a", 10.1), ("d", 8.0)]

    fused = rrf_fuse([vector, bm25])
    fused_dict = dict(fused)

    # All four unique ids appear
    assert set(fused_dict) == {"a", "b", "c", "d"}

    # 'a' is rank 1 in vector and rank 2 in bm25 — should outscore 'd' (rank 3 in bm25 only)
    assert fused_dict["a"] > fused_dict["d"]

    # 'c' is rank 3 in vector and rank 1 in bm25 — should beat 'b' (rank 2 in vector only)
    assert fused_dict["c"] > fused_dict["b"]


def test_rrf_weights_skew_results():
    """Up-weighting one retriever lifts its top hit's fused score."""
    a_first = [("a", 1.0), ("b", 0.5)]
    b_first = [("b", 1.0), ("a", 0.5)]

    # Equal weights: 'a' and 'b' tie (symmetric)
    fused_equal = dict(rrf_fuse([a_first, b_first]))
    assert fused_equal["a"] == pytest.approx(fused_equal["b"])

    # Up-weight a_first: 'a' should win
    fused_skewed = dict(rrf_fuse([a_first, b_first], weights=[2.0, 1.0]))
    assert fused_skewed["a"] > fused_skewed["b"]


def test_rrf_score_matches_formula():
    """Fused score equals Σ weight / (k + rank) per the canonical formula."""
    ranking = [("a", 999), ("b", 500), ("c", 1)]
    k = 60
    fused = dict(rrf_fuse([ranking], k=k))
    assert fused["a"] == pytest.approx(1 / (k + 1))
    assert fused["b"] == pytest.approx(1 / (k + 2))
    assert fused["c"] == pytest.approx(1 / (k + 3))


def test_rrf_k_must_be_positive():
    with pytest.raises(ValueError, match="positive"):
        rrf_fuse([[("a", 1)]], k=0)
    with pytest.raises(ValueError, match="positive"):
        rrf_fuse([[("a", 1)]], k=-5)


def test_rrf_weights_length_must_match():
    with pytest.raises(ValueError, match="length"):
        rrf_fuse([[("a", 1)], [("b", 1)]], weights=[1.0])


def test_rrf_empty_input_returns_empty():
    assert rrf_fuse([]) == []
    assert rrf_fuse([[]]) == []


def test_rrf_smaller_k_makes_top_ranks_dominate_more():
    """Lower k → larger gap between rank 1 and rank 10."""
    ranking = [(f"d{i}", 100 - i) for i in range(10)]

    low_k = dict(rrf_fuse([ranking], k=1))
    high_k = dict(rrf_fuse([ranking], k=600))

    low_gap = low_k["d0"] - low_k["d9"]
    high_gap = high_k["d0"] - high_k["d9"]

    assert low_gap > high_gap


def test_rrf_three_rankings_full_dedup():
    """Three input lists with overlap — all unique ids surface, top wins."""
    r1 = [("a", 1), ("b", 1), ("c", 1)]
    r2 = [("b", 1), ("c", 1), ("d", 1)]
    r3 = [("c", 1), ("d", 1), ("e", 1)]

    fused = rrf_fuse([r1, r2, r3])
    fused_dict = dict(fused)

    assert set(fused_dict) == {"a", "b", "c", "d", "e"}
    # 'c' appears in all three at high ranks — should be top
    assert fused[0][0] == "c"
