# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""HyDE (Hypothetical Document Embeddings) — fallback retrieval for low-confidence queries.

When initial retrieval confidence is low (top score < HYDE_TRIGGER_THRESHOLD), HyDE
generates a hypothetical answer via LLM, embeds it, and re-searches the KB with that
embedding. Results are merged with original results via Reciprocal Rank Fusion (RRF).

Algorithm:
  1. Check trigger condition: top retrieval score < threshold AND not already attempted
  2. Generate hypothetical document via LLM (Ollama preferred — small model task)
  3. Embed the hypothetical document using the same embedding function
  4. Search KB with hypothetical embedding
  5. Merge results via RRF (Reciprocal Rank Fusion)

Dependencies: utils/internal_llm.py, config/constants.py
Error types: none (HyDE failure silently falls back to original results)
"""

from __future__ import annotations

import logging
from collections import defaultdict

from config.constants import HYDE_TRIGGER_THRESHOLD
from errors import RetrievalError

logger = logging.getLogger("ai-companion.hyde")

_HYDE_PROMPT = (
    "Write a short factual paragraph that would directly answer this question: {query}\n"
    "Focus on the domain: {domain}\n"
    "Be specific and include key terms that would appear in a real document."
)


def should_trigger_hyde(top_score: float, already_attempted: bool = False) -> bool:
    """Return True if HyDE should be attempted based on retrieval confidence."""
    return top_score < HYDE_TRIGGER_THRESHOLD and not already_attempted


async def generate_hypothetical_document(
    query: str, domain: str | None = None,
) -> str | None:
    """Generate a hypothetical answer to use as a retrieval embedding.

    Tries internal LLM (Ollama when configured, else OpenRouter).
    Returns None on any failure — HyDE must never block retrieval.
    """
    try:
        from core.utils.internal_llm import call_internal_llm

        prompt = _HYDE_PROMPT.format(
            query=query,
            domain=domain if domain else "general knowledge",
        )
        result = await call_internal_llm(
            [
                {"role": "system", "content": "You are a knowledge base assistant."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=200,
        )
        if result and result.strip():
            logger.debug("HyDE generated hypothetical doc (%d chars)", len(result))
            return result.strip()
        return None
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        logger.debug("HyDE generation failed, falling back to original results", exc_info=True)
        return None


def reciprocal_rank_fusion(
    original_results: list[dict],
    hyde_results: list[dict],
    k: int = 60,
) -> list[dict]:
    """Merge two result lists via Reciprocal Rank Fusion.

    Each result dict must have an ``id`` field (or ``chunk_id`` as fallback).
    Returns a new list sorted by combined RRF score, highest first.
    """
    try:
        scores: dict[str, float] = defaultdict(float)
        result_map: dict[str, dict] = {}

        for rank, item in enumerate(original_results):
            item_id = item.get("id") or item.get("chunk_id", "")
            if not item_id:
                continue
            scores[item_id] += 1.0 / (k + rank + 1)
            result_map[item_id] = item

        for rank, item in enumerate(hyde_results):
            item_id = item.get("id") or item.get("chunk_id", "")
            if not item_id:
                continue
            scores[item_id] += 1.0 / (k + rank + 1)
            if item_id not in result_map:
                result_map[item_id] = item

        sorted_ids = sorted(scores, key=lambda x: scores[x], reverse=True)
        merged = [result_map[item_id] for item_id in sorted_ids]
        logger.debug(
            "RRF merged %d + %d → %d results",
            len(original_results), len(hyde_results), len(merged),
        )
        return merged
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        logger.debug("RRF merge failed, returning original results", exc_info=True)
        return list(original_results)


__all__ = [
    "should_trigger_hyde",
    "generate_hypothetical_document",
    "reciprocal_rank_fusion",
]
