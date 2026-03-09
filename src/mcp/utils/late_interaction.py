# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""ColBERT-inspired late interaction scoring.

Uses sliding-window embeddings from the existing ONNX model to compute
MaxSim scores between query windows and document windows. Applied only
to the top N candidates after cross-encoder reranking.

No new model needed — reuses OnnxEmbeddingFunction from embeddings.py.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import numpy as np

logger = logging.getLogger("ai-companion.late_interaction")

ENABLE_LATE_INTERACTION = os.getenv("ENABLE_LATE_INTERACTION", "false").lower() == "true"
LATE_INTERACTION_TOP_N = int(os.getenv("LATE_INTERACTION_TOP_N", "8"))
LATE_INTERACTION_BLEND_WEIGHT = float(os.getenv("LATE_INTERACTION_BLEND_WEIGHT", "0.15"))

# Sliding window parameters
_WINDOW_SIZE = 3  # words per window
_WINDOW_STRIDE = 2  # overlap


def _sliding_windows(text: str, window_size: int = _WINDOW_SIZE, stride: int = _WINDOW_STRIDE) -> list[str]:
    """Generate sliding window text segments."""
    words = text.split()
    if len(words) <= window_size:
        return [text] if text.strip() else []

    windows: list[str] = []
    for i in range(0, len(words) - window_size + 1, stride):
        window = " ".join(words[i:i + window_size])
        windows.append(window)
    return windows


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors."""
    dot = float(np.dot(a, b))
    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def compute_maxsim(
    query_embeddings: list[np.ndarray],
    doc_embeddings: list[np.ndarray],
) -> float:
    """Compute MaxSim score between query and document window embeddings.

    MaxSim(Q, D) = (1/|Q|) * sum over q in Q of max over d in D of cos(q, d)
    """
    if not query_embeddings or not doc_embeddings:
        return 0.0

    total = 0.0
    for q_emb in query_embeddings:
        max_sim = max(
            _cosine_similarity(q_emb, d_emb)
            for d_emb in doc_embeddings
        )
        total += max_sim

    return total / len(query_embeddings)


def late_interaction_rerank(
    results: list[dict[str, Any]],
    query: str,
    embed_fn,
    top_n: int | None = None,
    blend_weight: float | None = None,
) -> list[dict[str, Any]]:
    """Apply late interaction scoring to top N results.

    Args:
        results: Ranked results from previous stages.
        query: Original query string.
        embed_fn: Callable that embeds a list of strings -> list of np.ndarray.
        top_n: Number of top results to re-score (rest pass through).
        blend_weight: How much late interaction score influences final score.

    Returns:
        Results with blended scores, re-sorted.
    """
    n = top_n if top_n is not None else LATE_INTERACTION_TOP_N
    weight = blend_weight if blend_weight is not None else LATE_INTERACTION_BLEND_WEIGHT

    if len(results) <= 1 or n <= 0:
        return results

    # Only process top N candidates
    candidates = results[:n]
    rest = results[n:]

    # Generate query windows and embeddings
    query_windows = _sliding_windows(query)
    if not query_windows:
        return results

    try:
        query_embeddings = embed_fn(query_windows)
    except Exception as e:
        logger.warning("Failed to embed query windows: %s", e)
        return results

    # Score each candidate
    for r in candidates:
        content = r.get("content", "")
        doc_windows = _sliding_windows(content)
        if not doc_windows:
            continue

        try:
            doc_embeddings = embed_fn(doc_windows[:20])  # Cap windows for performance
        except Exception:
            continue

        maxsim = compute_maxsim(query_embeddings, doc_embeddings)
        original_score = r.get("relevance", 0.0)
        blended = (1.0 - weight) * original_score + weight * maxsim
        r["relevance"] = round(blended, 4)
        r["late_interaction_score"] = round(maxsim, 4)

    # Re-sort candidates by blended score
    candidates.sort(key=lambda x: x.get("relevance", 0), reverse=True)

    return candidates + rest
