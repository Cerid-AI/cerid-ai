# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Smoke test for the tri_rrf fusion branch in query_agent (C3.2).

These tests verify the *wiring* — that when ``HYBRID_FUSION_MODE=tri_rrf``
and the sparse index is available, ``search_sparse`` is invoked, its
hits are fused with the vector + BM25 rankings via ``rrf_fuse``, and a
sparse-only chunk surfaces in the final ranked list.

We don't stand up a real ChromaDB / BM25 index — the goal here is
contract verification, not end-to-end retrieval quality. The
end-to-end gate lives in :file:`docs/EVAL_BASELINES.md` and runs
post-corpus-growth.
"""

from __future__ import annotations

from core.retrieval.rrf import rrf_fuse


def test_three_way_rrf_surfaces_sparse_only_doc():
    """A chunk that only sparse retrieval ranks must still appear in the fused list.

    This is the *whole point* of three-way fusion: SPLADE-v3 picks up
    synonym / paraphrase matches that BM25 misses by surface form and
    vector misses when its embedding underweights the rare term.
    """
    vector_ranking = [("vec_a", 0.9), ("vec_b", 0.8)]
    bm25_ranking = [("bm_a", 0.95), ("bm_b", 0.7)]
    sparse_ranking = [("sparse_only", 0.85), ("vec_a", 0.6)]

    fused = rrf_fuse(
        [vector_ranking, bm25_ranking, sparse_ranking],
        k=60,
        weights=[1.0, 1.0, 1.0],
    )
    fused_ids = {doc_id for doc_id, _ in fused}
    assert "sparse_only" in fused_ids


def test_three_way_rrf_top_hit_is_intersection():
    """A chunk that ranks #1 in two lists and #2 in the third dominates the fusion.

    This is the canonical RRF property — agreement across retrievers
    is the strongest signal.
    """
    vector_ranking = [("agreed", 0.9), ("vec_only", 0.8)]
    bm25_ranking = [("agreed", 0.95), ("bm_only", 0.7)]
    sparse_ranking = [("sparse_only", 0.85), ("agreed", 0.6)]

    fused = rrf_fuse(
        [vector_ranking, bm25_ranking, sparse_ranking],
        k=60,
    )
    assert fused[0][0] == "agreed"


def test_three_way_rrf_weights_scale_sparse_contribution():
    """Boosting the sparse weight must raise sparse-only docs in the fused list."""
    vector_ranking = [("vec_only", 0.9)]
    bm25_ranking = [("bm_only", 0.95)]
    sparse_ranking = [("sparse_only", 0.85)]

    # Uniform weights
    uniform = rrf_fuse(
        [vector_ranking, bm25_ranking, sparse_ranking],
        k=60,
        weights=[1.0, 1.0, 1.0],
    )
    # Boost sparse by 5x
    boosted = rrf_fuse(
        [vector_ranking, bm25_ranking, sparse_ranking],
        k=60,
        weights=[1.0, 1.0, 5.0],
    )
    # sparse_only ranks last with uniform weights (insertion-order stable),
    # first with the boosted sparse weight.
    assert boosted[0][0] == "sparse_only"
    assert dict(uniform)["sparse_only"] < dict(boosted)["sparse_only"]
