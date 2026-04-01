# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Query decomposition — breaks multi-part questions into sub-queries.

Heuristic detection for common multi-part patterns (conjunctions,
multiple question marks, comparisons). Optionally uses LLM
decomposition via Bifrost for ambiguous cases.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from config.features import QUERY_DECOMPOSITION_MAX_SUBQUERIES
from errors import RetrievalError

logger = logging.getLogger("ai-companion.query_decomposer")

# Heuristic patterns for multi-part queries
_CONJUNCTION_SPLIT = re.compile(
    r"\b(?:and also|and\s+(?:what|how|why|when|where|who|which))\b",
    re.IGNORECASE,
)
_COMPARISON_PATTERN = re.compile(
    r"\b(\w+(?:\s+\w+){0,2}?)\s+(?:vs\.?|versus|compared?\s+(?:to|with)|difference\s+between)\s+(\w+(?:\s+\w+){0,2})",
    re.IGNORECASE,
)
_MULTI_QUESTION = re.compile(r"\?\s*(?:and|also|additionally|plus|what|how|why|when)", re.IGNORECASE)
_LIST_PATTERN = re.compile(r"\b(?:first|second|third|1\)|2\)|3\)|\d+\.)\s", re.IGNORECASE)


def needs_decomposition(query: str) -> bool:
    """Check if a query would benefit from decomposition."""
    if not query or len(query.strip()) < 20:
        return False

    q = query.strip()

    # Multiple question marks
    if q.count("?") >= 2:
        return True

    # Conjunction patterns
    if _CONJUNCTION_SPLIT.search(q):
        return True

    # Comparison patterns
    if _COMPARISON_PATTERN.search(q):
        return True

    # Multi-question patterns
    if _MULTI_QUESTION.search(q):
        return True

    # Numbered list patterns
    if _LIST_PATTERN.search(q):
        return True

    return False


def decompose_heuristic(query: str) -> list[str]:
    """Split a multi-part query into sub-queries using heuristics.

    Returns a list of 1-4 sub-queries. Returns [query] if no split needed.
    """
    q = query.strip()

    # Try comparison decomposition first
    match = _COMPARISON_PATTERN.search(q)
    if match:
        term_a, term_b = match.group(1), match.group(2)
        return [
            f"What is {term_a}?",
            f"What is {term_b}?",
            q,  # Keep the original comparison question too
        ][:QUERY_DECOMPOSITION_MAX_SUBQUERIES]

    # Try splitting on conjunction patterns
    parts = _CONJUNCTION_SPLIT.split(q)
    if len(parts) >= 2:
        sub_queries = [p.strip().rstrip("?.,") + "?" for p in parts if len(p.strip()) >= 10]
        return sub_queries[:QUERY_DECOMPOSITION_MAX_SUBQUERIES] if sub_queries else [q]

    # Try splitting on multiple question marks
    if q.count("?") >= 2:
        sentences = re.split(r"\?\s*", q)
        sub_queries = [s.strip() + "?" for s in sentences if len(s.strip()) >= 10]
        return sub_queries[:QUERY_DECOMPOSITION_MAX_SUBQUERIES] if sub_queries else [q]

    return [q]


async def decompose_query(
    query: str,
    use_llm: bool = False,
) -> list[str]:
    """Decompose a query into sub-queries.

    Tries heuristic decomposition first. Falls back to LLM if
    use_llm=True and heuristics don't produce a split.

    Returns list of sub-queries (always at least 1 — the original).
    """
    if not needs_decomposition(query):
        return [query]

    sub_queries = decompose_heuristic(query)
    if len(sub_queries) > 1:
        from utils.agent_events import emit_agent_event
        emit_agent_event("decomposer", f"Breaking this down into {len(sub_queries)} sub-questions...")
        logger.info("Decomposed query into %d sub-queries (heuristic)", len(sub_queries))
        return sub_queries

    # LLM fallback (optional, rarely needed)
    if use_llm:
        try:
            from utils.internal_llm import call_internal_llm

            prompt = (
                "Break this question into 2-4 independent sub-questions that can be answered separately. "
                "Return ONLY a JSON array of strings. No explanation.\n\n"
                f"Question: {query}"
            )
            content = (await call_internal_llm(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=200,
            )).strip()
            # Parse JSON array
            if content.startswith("["):
                from utils.llm_parsing import parse_llm_json
                parsed = parse_llm_json(content)
                if isinstance(parsed, list) and all(isinstance(s, str) for s in parsed):
                    result = [s for s in parsed if len(s.strip()) >= 10][:QUERY_DECOMPOSITION_MAX_SUBQUERIES]
                    if result:
                        logger.info("Decomposed query into %d sub-queries (LLM)", len(result))
                        return result
        except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
            logger.warning("LLM decomposition failed: %s", e)

    return [query]


async def parallel_retrieve(
    sub_queries: list[str],
    retrieve_fn,
    **kwargs,
) -> list[dict[str, Any]]:
    """Execute retrieval for multiple sub-queries in parallel.

    Args:
        sub_queries: List of sub-queries to retrieve for.
        retrieve_fn: Async function(query, **kwargs) -> list[dict].
        **kwargs: Additional arguments passed to retrieve_fn.

    Returns merged results with sub_query_source provenance tagging.
    """
    if len(sub_queries) <= 1:
        return await retrieve_fn(sub_queries[0], **kwargs)

    tasks = [retrieve_fn(sq, **kwargs) for sq in sub_queries]
    all_results = await asyncio.gather(*tasks, return_exceptions=True)

    merged: list[dict[str, Any]] = []
    for i, result in enumerate(all_results):
        if isinstance(result, BaseException):
            logger.warning("Sub-query %d failed: %s", i, result)
            continue
        for r in result:
            r["sub_query_source"] = sub_queries[i]
        merged.extend(result)

    return merged
