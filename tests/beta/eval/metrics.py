# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

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
