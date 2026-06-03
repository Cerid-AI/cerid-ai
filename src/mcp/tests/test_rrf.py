# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for Reciprocal Rank Fusion (Workstream E Phase 3)."""

from __future__ import annotations

import pytest

from core.retrieval.rrf import DEFAULT_K, rrf_fuse, rrf_fuse_by_artifact


def _art(cid: str) -> str:
    """chunk id 'a#0' → artifact id 'a'."""
    return cid.split("#", 1)[0]


class TestArtifactLevelRRF:
    """GA P0.5 B1 — artifact-level RRF stops multi-chunk artifacts from
    consuming rank slots and demoting competitors."""

    def test_multichunk_artifact_counted_once_and_does_not_demote(self):
        # Artifact 'a' has 3 chunks at ranks 0,1,2; artifact 'b' one chunk after.
        ranking = [("a#0", 0.9), ("a#1", 0.8), ("a#2", 0.7), ("b#0", 0.6)]
        fused = rrf_fuse_by_artifact([ranking], _art, k=60)
        # 'a' counted once at the best (0th) rank; 'b' is the 2nd DISTINCT
        # artifact → rank 1, NOT rank 3. Its three chunks didn't push 'b' down.
        assert fused["a"] == pytest.approx(1 / (60 + 1))
        assert fused["b"] == pytest.approx(1 / (60 + 2))

    def test_chunk_level_would_demote_b(self):
        # Contrast: chunk-level RRF puts 'b#0' at rank index 3 → a weaker score.
        # This is the regression artifact-level fusion removes.
        ranking = [("a#0", 0.9), ("a#1", 0.8), ("a#2", 0.7), ("b#0", 0.6)]
        chunk_fused = dict(rrf_fuse([ranking], k=60))
        art_fused = rrf_fuse_by_artifact([ranking], _art, k=60)
        assert chunk_fused["b#0"] == pytest.approx(1 / (60 + 4))  # 0-indexed rank 3 → +1
        assert art_fused["b"] > chunk_fused["b#0"]  # artifact-level keeps b higher

    def test_consensus_across_retrievers_still_sums(self):
        vec = [("a#0", 0.9), ("b#0", 0.5)]
        bm25 = [("b#1", 9.0), ("a#1", 8.0)]  # same artifacts, different chunks
        fused = rrf_fuse_by_artifact([vec, bm25], _art, k=60)
        # 'a': rank0 in vec + rank1 in bm25; 'b': rank1 in vec + rank0 in bm25 — equal.
        assert fused["a"] == pytest.approx(1 / 61 + 1 / 62)
        assert fused["b"] == pytest.approx(1 / 62 + 1 / 61)

    def test_unknown_artifact_falls_back_to_singleton(self):
        ranking = [("x", 0.9), ("y", 0.5)]  # _art('x')='x' (no '#')
        fused = rrf_fuse_by_artifact([ranking], _art, k=60)
        assert set(fused) == {"x", "y"}

    def test_bad_k_and_weights_raise(self):
        with pytest.raises(ValueError):
            rrf_fuse_by_artifact([[("a", 1.0)]], _art, k=0)
        with pytest.raises(ValueError):
            rrf_fuse_by_artifact([[("a", 1.0)]], _art, weights=[1.0, 2.0])


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
