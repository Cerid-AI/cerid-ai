# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reciprocal Rank Fusion (RRF) for hybrid retrieval (Workstream E Phase 3).

RRF is the de-facto standard for combining multiple ranked retrieval
lists in 2026 production RAG (Elastic, OpenSearch, Azure AI Search,
neo4j-graphrag all default to it). It dominates weighted-sum fusion
when the retrievers' score distributions differ meaningfully — which
is always the case for cosine-similarity vector scores vs BM25 scores
(the two operate on different scales).

Paper: Cormack, Clarke, Buettcher (2009)
"Reciprocal Rank Fusion outperforms Condorcet and individual Rank
Learning Methods" — SIGIR 2009.

Implementation notes:

* ``k`` (default 60) is the documented default in every major
  implementation. It controls how steeply rank contributes to the
  fused score; lower k = top ranks dominate more.
* Per-retriever weights default to 1.0 (uniform). Use weights when one
  retriever is provably stronger on this corpus (typical: vector > BM25
  for paraphrased queries; BM25 > vector for jargon-heavy queries).
* The function is intentionally pure: takes pre-ranked lists, returns
  a fused ranked list. No I/O, no side effects, fully unit-testable.

This module is wired in by a follow-up commit; today it is dead code
behind a default-off flag. See ``HYBRID_FUSION_MODE`` in settings.py.
"""
from __future__ import annotations

from collections.abc import Sequence

# Standard RRF constant per Cormack/Clarke/Buettcher (SIGIR 2009).
# Same value used by Elastic, OpenSearch, Azure AI Search, neo4j-graphrag.
DEFAULT_K = 60


def rrf_fuse(
    rankings: Sequence[Sequence[tuple[str, float]]],
    *,
    k: int = DEFAULT_K,
    weights: Sequence[float] | None = None,
) -> list[tuple[str, float]]:
    """Fuse multiple ranked lists via Reciprocal Rank Fusion.

    Args:
        rankings: A sequence of ranked lists. Each ranked list is a
            sequence of ``(doc_id, score)`` tuples in **descending**
            score order. The original score is ignored — only rank
            position contributes to RRF. Example::

                rankings = [
                    [("a", 0.92), ("b", 0.85), ("c", 0.71)],   # vector
                    [("c", 12.4), ("a", 10.1), ("d", 8.0)],    # BM25
                ]

        k: RRF smoothing constant. Default 60 per the literature; do
            not change unless you've benchmarked. Smaller k = top ranks
            dominate; larger k = ranks blend more uniformly.

        weights: Optional per-ranking weights (same order as
            ``rankings``). When ``None``, all rankings are weighted 1.0.
            Use weights to express "vector is twice as informative as
            BM25 on this corpus" without resorting to score
            normalisation.

    Returns:
        A fused ranked list of ``(doc_id, fused_score)`` tuples in
        descending fused-score order. ``doc_id``s appearing in multiple
        input lists are deduped (their RRF contributions sum). Each
        score is the weighted sum ``Σ w_i / (k + rank_i)`` across the
        input lists where the doc appeared.

    Raises:
        ValueError: when ``weights`` is provided and its length doesn't
            match ``rankings``, or when ``k`` is non-positive.
    """
    if k <= 0:
        raise ValueError(f"RRF k must be positive, got {k}")

    n = len(rankings)
    if weights is None:
        ws = [1.0] * n
    else:
        if len(weights) != n:
            raise ValueError(
                f"weights length {len(weights)} != rankings length {n}",
            )
        ws = list(weights)

    fused: dict[str, float] = {}
    for ranking, weight in zip(rankings, ws, strict=True):
        for rank_idx, (doc_id, _score) in enumerate(ranking):
            # Ranks are 1-indexed in the canonical formula
            contribution = weight / (k + rank_idx + 1)
            fused[doc_id] = fused.get(doc_id, 0.0) + contribution

    # Sort by fused score descending; stable on insertion order for ties
    return sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
