# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Local IR metrics — pure math, no external dependencies."""

from __future__ import annotations

import math


def ndcg_at_k(ranked_ids: list[str], relevant: set[str], k: int) -> float:
    """Normalized Discounted Cumulative Gain at K."""
    if not relevant:
        return 0.0
    dcg = sum(
        (1.0 / math.log2(i + 2)) for i, rid in enumerate(ranked_ids[:k]) if rid in relevant
    )
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(len(relevant), k)))
    return dcg / idcg if idcg > 0 else 0.0


def mrr(ranked_ids: list[str], relevant: set[str]) -> float:
    """Mean Reciprocal Rank — 1/rank of first relevant result."""
    for i, rid in enumerate(ranked_ids):
        if rid in relevant:
            return 1.0 / (i + 1)
    return 0.0


def precision_at_k(ranked_ids: list[str], relevant: set[str], k: int) -> float:
    """Precision at K — fraction of top-K that are relevant."""
    if not relevant or k == 0:
        return 0.0
    hits = sum(1 for rid in ranked_ids[:k] if rid in relevant)
    return hits / k


def recall_at_k(ranked_ids: list[str], relevant: set[str], k: int) -> float:
    """Recall at K — fraction of relevant found in top-K."""
    if not relevant:
        return 0.0
    hits = sum(1 for rid in ranked_ids[:k] if rid in relevant)
    return hits / len(relevant)


def _distribution(scores: list[float]) -> dict:
    """Mean with its spread and a flag for a number that carries no information.

    An empty list is "not measured", never a mean of zero.
    """
    if not scores:
        return {"mean": None, "min": None, "max": None, "spread": None, "stdev": None,
                "non_discriminating": False, "n": 0}
    lo, hi = min(scores), max(scores)
    mean = sum(scores) / len(scores)
    return {
        "mean": mean,
        "min": lo,
        "max": hi,
        "spread": hi - lo,
        "stdev": math.sqrt(sum((s - mean) ** 2 for s in scores) / len(scores)),
        "non_discriminating": len(scores) > 1 and lo == hi,
    }


def retrieval_summary(rankings: list[tuple[list[str], set[str]]], k: int = 5) -> dict:
    """NDCG@k and MRR over ``(ranked_ids, relevant_ids)`` per query.

    Returns ``{"ndcg": distribution, "mrr": distribution, "per_query": [...]}``
    where each per-query row carries the two scores and ``answer_rank`` (1-based
    position of the first relevant id, ``None`` when it was not returned) so a
    miss can be read as "ranked fourth behind its distractors" rather than as a
    bare number.
    """
    if not rankings:
        raise ValueError("retrieval_summary needs at least one ranking")
    per_query = []
    for ranked, relevant in rankings:
        rank = next((i + 1 for i, rid in enumerate(ranked) if rid in relevant), None)
        per_query.append({
            "ndcg": ndcg_at_k(ranked, relevant, k),
            "mrr": mrr(ranked, relevant),
            "answer_rank": rank,
        })
    return {
        "ndcg": _distribution([q["ndcg"] for q in per_query]),
        "mrr": _distribution([q["mrr"] for q in per_query]),
        "per_query": per_query,
    }
