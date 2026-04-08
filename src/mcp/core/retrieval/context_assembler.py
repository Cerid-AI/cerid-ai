# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Intelligent context assembly — three-pass assembly maximizing query coverage.

1. Facet extraction from query (split on conjunctions/commas)
2. Greedy set-cover weighted by relevance
3. Coherence padding with complementary chunks

Returns coverage metadata (facets_total, facets_covered, coverage_ratio).
"""
from __future__ import annotations

import logging
import re
from typing import Any

from core.utils.text import STOPWORDS as _STOPWORDS
from core.utils.text import WORD_RE as _WORD_RE

logger = logging.getLogger("ai-companion.context_assembler")

# Minimum meaningful facet length
_MIN_FACET_LENGTH = 8


def extract_facets(query: str) -> list[str]:
    """Extract distinct facets (sub-topics) from a query.

    Splits on conjunctions, commas, and question boundaries.
    Returns a list of facet strings.
    """
    q = query.strip()
    if not q:
        return []

    # Split on common boundaries
    parts = re.split(r"\b(?:and|also|additionally|plus|as well as)\b|[,;]|\?\s*", q, flags=re.IGNORECASE)

    facets: list[str] = []
    for part in parts:
        cleaned = part.strip().strip("?.!,;")
        if len(cleaned) >= _MIN_FACET_LENGTH:
            facets.append(cleaned)

    # If no split happened, use the full query as a single facet
    if not facets and len(q) >= _MIN_FACET_LENGTH:
        facets = [q]

    return facets


def _facet_terms(facet: str) -> frozenset[str]:
    """Extract significant terms from a facet."""
    words = _WORD_RE.findall(facet.lower())
    return frozenset(w for w in words if w not in _STOPWORDS and len(w) >= 3)


def _doc_terms(content: str) -> frozenset[str]:
    """Extract significant terms from document content."""
    words = _WORD_RE.findall(content.lower())
    return frozenset(w for w in words if w not in _STOPWORDS and len(w) >= 3)


def _facet_coverage(doc_terms_set: frozenset[str], facet_terms_set: frozenset[str]) -> float:
    """Compute how well a document covers a facet (0-1)."""
    if not facet_terms_set:
        return 0.0
    overlap = len(doc_terms_set & facet_terms_set)
    return overlap / len(facet_terms_set)


def intelligent_assemble(
    results: list[dict[str, Any]],
    query: str,
    max_chars: int = 12000,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Assemble context from results using facet-aware greedy set cover.

    Returns:
        (context_text, sources, coverage_metadata)
    """
    if not results:
        return "", [], {"facets_total": 0, "facets_covered": 0, "coverage_ratio": 0.0}

    facets = extract_facets(query)
    facet_term_sets = [_facet_terms(f) for f in facets]

    # Pre-compute doc terms
    doc_term_sets = [_doc_terms(r.get("content", "")) for r in results]

    # Track which facets are covered
    facets_covered = [False] * len(facets)
    selected_indices: list[int] = []
    char_count = 0
    remaining = set(range(len(results)))

    # Pass 1: Greedy set-cover — pick docs that cover uncovered facets
    for _ in range(len(results)):
        if not remaining or char_count >= max_chars:
            break

        best_idx = -1
        best_score = -1.0

        for idx in remaining:
            content_len = len(results[idx].get("content", ""))
            if char_count + content_len > max_chars:
                continue

            # Score: weighted sum of uncovered facet coverage * relevance
            relevance = results[idx].get("relevance", 0.0)
            coverage_bonus = 0.0
            for fi, covered in enumerate(facets_covered):
                if not covered:
                    cov = _facet_coverage(doc_term_sets[idx], facet_term_sets[fi])
                    coverage_bonus += cov

            score = relevance + coverage_bonus * 0.5

            if score > best_score:
                best_score = score
                best_idx = idx

        if best_idx < 0:
            break

        selected_indices.append(best_idx)
        remaining.discard(best_idx)
        char_count += len(results[best_idx].get("content", ""))

        # Update facet coverage
        for fi, ft in enumerate(facet_term_sets):
            if not facets_covered[fi]:
                cov = _facet_coverage(doc_term_sets[best_idx], ft)
                if cov >= 0.3:  # Threshold: 30% term overlap
                    facets_covered[fi] = True

    # Pass 2: Coherence padding — fill remaining budget with highest-relevance docs
    for idx in sorted(remaining, key=lambda i: results[i].get("relevance", 0), reverse=True):
        content_len = len(results[idx].get("content", ""))
        if char_count + content_len > max_chars:
            continue
        selected_indices.append(idx)
        char_count += content_len
        # Update facet coverage
        for fi, ft in enumerate(facet_term_sets):
            if not facets_covered[fi]:
                cov = _facet_coverage(doc_term_sets[idx], ft)
                if cov >= 0.3:
                    facets_covered[fi] = True

    # Build context
    parts: list[str] = []
    sources: list[dict[str, Any]] = []
    for idx in selected_indices:
        r = results[idx]
        header = f"[Source: {r.get('filename', 'unknown')} | Domain: {r.get('domain', 'unknown')}]"
        parts.append(f"{header}\n{r.get('content', '')}")
        sources.append(r)

    covered_count = sum(1 for c in facets_covered if c)
    coverage_metadata = {
        "facets_total": len(facets),
        "facets_covered": covered_count,
        "coverage_ratio": round(covered_count / len(facets), 4) if facets else 1.0,
        "facets": facets,
    }

    context = "\n\n---\n\n".join(parts)
    return context, sources, coverage_metadata
