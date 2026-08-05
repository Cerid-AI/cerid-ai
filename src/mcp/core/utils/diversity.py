# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Maximal Marginal Relevance (MMR) diversity reordering.

Reduces redundancy in retrieval results by penalizing documents too
similar to already-selected documents. Prefers cosine similarity over
each result's pre-computed embedding (upstream retrieval attaches it under
the ``"embedding"`` key) when BOTH documents in a comparison carry one;
falls back to Jaccard similarity on stemmed term sets otherwise. This
module never computes a new embedding — it is a pure re-ranker over
whatever similarity signal retrieval already put in the result dict, so a
paraphrase pair with low token overlap but high semantic similarity (which
Jaccard is blind to) is still recognized as redundant when embeddings are
present, while callers that never attach embeddings get the original
Jaccard-only behavior unchanged.

Canonical location as of Sprint D. Previously at ``src/mcp/utils/diversity.py``;
a thin bridge stays there until Sprint E retires the utils/ bridge dir.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from typing import Any

from config.features import MMR_LAMBDA
from core.utils.text import STOPWORDS as _STOPWORDS
from core.utils.text import WORD_RE as _WORD_RE

logger = logging.getLogger("ai-companion.diversity")

# MMR's diversity penalty assumes a similarity scale of [0, 1], matching
# Jaccard's natural range. Raw cosine similarity can go negative for
# anti-correlated embeddings; an anti-correlated pair is not "more diverse
# than orthogonal", so it is floored here rather than allowed to invert the
# penalty term.
_MMR_MIN_EMBEDDING_SIMILARITY = 0.0


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


def cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """Cosine similarity between two dense vectors, floored at 0 (see module docstring).

    Empty, mismatched-length, or zero-norm input yields 0 — neutral rather
    than an error, since the caller (``mmr_reorder``) treats "no similarity
    signal" as "fall back to Jaccard", not as a crash.
    """
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return max(_MMR_MIN_EMBEDDING_SIMILARITY, dot / (norm_a * norm_b))


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

    The ``d_selected`` similarity term uses cosine similarity over each
    result's ``"embedding"`` field when BOTH documents in a pairwise
    comparison carry one (no embedding is computed here — see module
    docstring); it falls back to Jaccard on stemmed term sets per-pair
    otherwise, so partial embedding coverage degrades gracefully instead
    of an all-or-nothing switch.
    """
    if len(results) <= 1:
        return results

    lam = lambda_param if lambda_param is not None else MMR_LAMBDA
    n = top_n or len(results)

    doc_terms = [_extract_terms(r.get("content", "")) for r in results]
    doc_embeddings: list[Sequence[float] | None] = [r.get("embedding") for r in results]

    # Use calibrated relevance scores directly — already boosted/reranked upstream
    query_sims = [r.get("relevance", 0.0) for r in results]

    def _pair_similarity(idx: int, sel_idx: int) -> float:
        emb_a, emb_b = doc_embeddings[idx], doc_embeddings[sel_idx]
        if emb_a and emb_b:
            return cosine_similarity(emb_a, emb_b)
        return jaccard_similarity(doc_terms[idx], doc_terms[sel_idx])

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
                sim = _pair_similarity(idx, sel_idx)
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
