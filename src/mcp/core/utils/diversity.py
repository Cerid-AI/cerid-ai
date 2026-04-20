# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Maximal Marginal Relevance (MMR) diversity reordering.

Reduces redundancy in retrieval results by penalizing documents
too similar to already-selected documents. Uses Jaccard similarity
on stemmed term sets — no additional model required.

Canonical location as of Sprint D. Previously at ``src/mcp/utils/diversity.py``;
a thin bridge stays there until Sprint E retires the utils/ bridge dir.
"""
from __future__ import annotations

import logging
from typing import Any

from config.features import MMR_LAMBDA
from core.utils.text import STOPWORDS as _STOPWORDS
from core.utils.text import WORD_RE as _WORD_RE

logger = logging.getLogger("ai-companion.diversity")


def _extract_terms(text: str) -> frozenset[str]:
    """Extract lowercase stemmed terms from text, minus stopwords."""
    words = _WORD_RE.findall(text.lower())
    # Simple suffix stripping (Porter-lite). Order matters — longer
    # suffixes tried first so "ness"/"ment" don't collapse to "s".
    terms: set[str] = set()
    for w in words:
        if w in _STOPWORDS or len(w) < 3:
            continue
        if w.endswith("ing") and len(w) > 5:
            w = w[:-3]
        elif w.endswith("tion") and len(w) > 6:
            w = w[:-4]
        elif w.endswith("ed") and len(w) > 4:
            w = w[:-2]
        elif w.endswith("ly") and len(w) > 4:
            w = w[:-2]
        elif w.endswith("ness") and len(w) > 6:
            w = w[:-4]
        elif w.endswith("ment") and len(w) > 6:
            w = w[:-4]
        elif w.endswith("es") and len(w) > 4:
            w = w[:-2]
        elif w.endswith("s") and len(w) > 3:
            w = w[:-1]
        terms.add(w)
    return frozenset(terms)


def jaccard_similarity(terms_a: frozenset[str], terms_b: frozenset[str]) -> float:
    """Jaccard similarity between two term sets — empty input yields 0."""
    if not terms_a or not terms_b:
        return 0.0
    intersection = len(terms_a & terms_b)
    union = len(terms_a | terms_b)
    return intersection / union if union > 0 else 0.0


def mmr_reorder(
    results: list[dict[str, Any]],
    query: str,
    lambda_param: float | None = None,
    top_n: int | None = None,
) -> list[dict[str, Any]]:
    """Reorder results using Maximal Marginal Relevance.

    ``MMR(d) = λ · Sim(d, query) − (1 − λ) · max Sim(d, d_selected)``

    ``lambda_param=1`` reduces to pure relevance ranking; ``=0`` is
    pure diversity. Default comes from ``config.features.MMR_LAMBDA``.
    """
    if len(results) <= 1:
        return results

    lam = lambda_param if lambda_param is not None else MMR_LAMBDA
    n = top_n or len(results)

    doc_terms = [_extract_terms(r.get("content", "")) for r in results]

    # Use calibrated relevance scores directly — already boosted/reranked upstream
    query_sims = [r.get("relevance", 0.0) for r in results]

    selected_indices: list[int] = []
    remaining = set(range(len(results)))

    for _ in range(min(n, len(results))):
        best_idx = -1
        best_score = -float("inf")

        for idx in remaining:
            relevance_score = query_sims[idx]

            # Max similarity to already selected docs
            max_sim_to_selected = 0.0
            for sel_idx in selected_indices:
                sim = jaccard_similarity(doc_terms[idx], doc_terms[sel_idx])
                if sim > max_sim_to_selected:
                    max_sim_to_selected = sim

            mmr_score = lam * relevance_score - (1.0 - lam) * max_sim_to_selected

            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = idx

        if best_idx < 0:
            break

        selected_indices.append(best_idx)
        remaining.discard(best_idx)

    return [results[i] for i in selected_indices]
