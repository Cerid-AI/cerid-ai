# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Information retrieval evaluation metrics.

Pure functions — no external dependencies. Operates on ranked ID lists
and relevant ID sets to compute standard IR metrics.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def precision_at_k(
    ranked_ids: Sequence[str], relevant_ids: set[str], k: int
) -> float:
    """Proportion of top-k results that are relevant."""
    if k <= 0:
        return 0.0
    top_k = ranked_ids[:k]
    if not top_k:
        return 0.0
    hits = sum(1 for rid in top_k if rid in relevant_ids)
    return hits / len(top_k)


def recall_at_k(
    ranked_ids: Sequence[str], relevant_ids: set[str], k: int
) -> float:
    """Proportion of relevant items found in top-k results."""
    if not relevant_ids or k <= 0:
        return 0.0
    top_k = ranked_ids[:k]
    hits = sum(1 for rid in top_k if rid in relevant_ids)
    return hits / len(relevant_ids)


def mrr(ranked_ids: Sequence[str], relevant_ids: set[str]) -> float:
    """
    Mean Reciprocal Rank — reciprocal of the rank of the first relevant result.
    Returns 0.0 if no relevant results are found.
    """
    for i, rid in enumerate(ranked_ids):
        if rid in relevant_ids:
            return 1.0 / (i + 1)
    return 0.0


def ndcg_at_k(
    ranked_ids: Sequence[str], relevant_ids: set[str], k: int
) -> float:
    """
    Normalized Discounted Cumulative Gain at k.

    Uses binary relevance (1 if relevant, 0 otherwise).
    Returns 0.0 if there are no relevant items or k <= 0.
    """
    if k <= 0 or not relevant_ids:
        return 0.0

    top_k = ranked_ids[:k]

    # DCG: sum of (rel_i / log2(i + 2)) for i in 0..k-1
    dcg = 0.0
    for i, rid in enumerate(top_k):
        if rid in relevant_ids:
            dcg += 1.0 / math.log2(i + 2)

    # Ideal DCG: all relevant items at top positions
    ideal_count = min(len(relevant_ids), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_count))

    if idcg == 0:
        return 0.0
    return dcg / idcg


def average_precision(
    ranked_ids: Sequence[str], relevant_ids: set[str]
) -> float:
    """
    Average Precision — mean of precision values at each relevant result position.
    Returns 0.0 if there are no relevant items.
    """
    if not relevant_ids:
        return 0.0

    hits = 0
    sum_precision = 0.0
    for i, rid in enumerate(ranked_ids):
        if rid in relevant_ids:
            hits += 1
            sum_precision += hits / (i + 1)

    return sum_precision / len(relevant_ids) if relevant_ids else 0.0
