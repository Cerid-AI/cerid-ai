# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Intelligent context assembly — three-pass assembly maximizing query coverage.

1. Facet extraction from query (split on conjunctions/commas)
2. Greedy set-cover weighted by relevance
3. Coherence padding with complementary chunks

``max_chars`` bounds the whole returned context: each block is charged with its
source header, history note and separator, not just its content. A block that
does not fit the remaining budget is skipped and smaller ones are still
considered, the same packing rule ``assemble_context`` follows.

Returns coverage metadata (facets_total, facets_covered, coverage_ratio).
"""
from __future__ import annotations

import logging
import re
from typing import Any

from core.lineage.history import history_note
from core.utils.text import STOPWORDS as _STOPWORDS
from core.utils.text import WORD_RE as _WORD_RE

logger = logging.getLogger("ai-companion.context_assembler")

# Minimum meaningful facet length
_MIN_FACET_LENGTH = 8

_BLOCK_SEPARATOR = "\n\n---\n\n"


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


def _render_block(r: dict[str, Any]) -> str:
    """The text one result contributes to the context."""
    header = f"[Source: {r.get('filename', 'unknown')} | Domain: {r.get('domain', 'unknown')}]"
    note = history_note(r)
    return f"{header}\n{r.get('content', '')}" + (f"\n{note}" if note else "")


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

    # Charge each block as it will appear in the context: header, content,
    # history note, and the separator joining it to the previous block.
    blocks = [_render_block(r) for r in results]

    def _cost(idx: int) -> int:
        return len(blocks[idx]) + (len(_BLOCK_SEPARATOR) if selected_indices else 0)

    # Pass 1: Greedy set-cover — pick docs that cover uncovered facets
    for _ in range(len(results)):
        if not remaining or char_count >= max_chars:
            break

        best_idx = -1
        best_score = -1.0

        for idx in remaining:
            if char_count + _cost(idx) > max_chars:
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

        char_count += _cost(best_idx)
        selected_indices.append(best_idx)
        remaining.discard(best_idx)

        # Update facet coverage
        for fi, ft in enumerate(facet_term_sets):
            if not facets_covered[fi]:
                cov = _facet_coverage(doc_term_sets[best_idx], ft)
                if cov >= 0.3:  # Threshold: 30% term overlap
                    facets_covered[fi] = True

    # Pass 2: Coherence padding — fill remaining budget with highest-relevance docs
    for idx in sorted(remaining, key=lambda i: results[i].get("relevance", 0), reverse=True):
        cost = _cost(idx)
        if char_count + cost > max_chars:
            continue
        selected_indices.append(idx)
        char_count += cost
        # Update facet coverage
        for fi, ft in enumerate(facet_term_sets):
            if not facets_covered[fi]:
                cov = _facet_coverage(doc_term_sets[idx], ft)
                if cov >= 0.3:
                    facets_covered[fi] = True

    sources = [results[idx] for idx in selected_indices]

    covered_count = sum(1 for c in facets_covered if c)
    coverage_metadata = {
        "facets_total": len(facets),
        "facets_covered": covered_count,
        "coverage_ratio": round(covered_count / len(facets), 4) if facets else 1.0,
        "facets": facets,
    }

    context = _BLOCK_SEPARATOR.join(blocks[idx] for idx in selected_indices)
    return context, sources, coverage_metadata
