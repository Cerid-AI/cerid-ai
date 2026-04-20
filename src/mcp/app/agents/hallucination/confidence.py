# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Confidence calibration and verification details — extracted from verification.py.

Dependencies: patterns.py.
Error types: none.
"""

from __future__ import annotations

from typing import Any

from config.constants import NUMERIC_MATCH_RATIO_THRESHOLD, QUALITY_TIER_EXCELLENT
from core.agents.hallucination.patterns import PERCENT_RE, YEAR_RE

__all__ = [
    "_check_numeric_alignment",
    "_compute_adjusted_confidence",
    "_build_verification_details",
]


# ---------------------------------------------------------------------------
# Numeric contradiction detection (zero LLM cost)
# ---------------------------------------------------------------------------

def _check_numeric_alignment(
    claim: str,
    top_result: dict[str, Any],
) -> float:
    """Check if specific numbers/dates/percentages in the claim match the source.

    Returns a small positive adjustment if numbers match, negative if they conflict.
    Returns 0.0 if no numbers to compare.

    This is the key defense against inverted-fact hallucinations where embedding
    similarity is high but the actual data is wrong (e.g. "released in 2021"
    vs source saying "released in 1991").
    """
    source_text = top_result.get("content", "")
    if not source_text:
        return 0.0

    claim_years = set(YEAR_RE.findall(claim))
    claim_pcts = set(PERCENT_RE.findall(claim))

    # No verifiable numbers in claim = nothing to check
    if not claim_years and not claim_pcts:
        return 0.0

    source_years = set(YEAR_RE.findall(source_text))
    source_pcts = set(PERCENT_RE.findall(source_text))

    matches = 0
    total_checks = 0

    # Check years
    for year in claim_years:
        total_checks += 1
        if year in source_years:
            matches += 1

    # Check percentages
    for pct in claim_pcts:
        total_checks += 1
        if pct in source_pcts:
            matches += 1

    if total_checks == 0:
        return 0.0

    match_ratio = matches / total_checks

    if match_ratio >= QUALITY_TIER_EXCELLENT:
        return 0.03   # Numbers align well
    elif match_ratio <= NUMERIC_MATCH_RATIO_THRESHOLD and total_checks >= 2:
        return -0.05  # Numbers conflict — likely inverted fact
    return 0.0


# ---------------------------------------------------------------------------
# Multi-result confidence calibration (zero LLM cost)
# ---------------------------------------------------------------------------

def _compute_adjusted_confidence(
    claim: str,
    top_results: list[dict[str, Any]],
    raw_similarity: float,
) -> float:
    """Adjust confidence based on multi-result triangulation and snippet analysis.

    Factors:
    1. Score spread: tight spread across top results = corroborating evidence.
       Large drop-off from #1 to #2 = isolated match (weaker).
    2. Domain diversity: results from multiple domains = stronger evidence.
    3. Numeric alignment: do the hard facts (years, percentages) match?
    4. Result count: fewer results = less confident.
    """
    adjustment = 0.0

    # Factor 1: Score spread analysis
    if len(top_results) >= 2:
        scores = [r.get("relevance", 0.0) for r in top_results]
        spread = scores[0] - scores[-1]
        if spread < 0.15:
            # Multiple results at similar scores = corroborating evidence
            adjustment += 0.03
        elif spread > 0.4:
            # Only one strong match, others are distant = weaker evidence
            adjustment -= 0.03

    # Factor 2: Domain diversity
    if len(top_results) >= 2:
        domains = {r.get("domain") for r in top_results if r.get("relevance", 0) > 0.3}
        if len(domains) > 1:
            adjustment += 0.02

    # Factor 3: Snippet-based number/date verification
    adjustment += _check_numeric_alignment(claim, top_results[0])

    # Factor 4: Result count penalty
    if len(top_results) == 1:
        adjustment -= 0.02

    return max(0.0, min(1.0, raw_similarity + adjustment))


def _build_verification_details(
    claim: str,
    top_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build verification detail metadata for transparency and analytics."""
    scores = [r.get("relevance", 0.0) for r in top_results]
    domains = [r.get("domain", "") for r in top_results]

    details: dict[str, Any] = {
        "result_count": len(top_results),
        "top_scores": [round(s, 3) for s in scores],
        "domains_found": list(set(d for d in domains if d)),
        "score_spread": round(scores[0] - scores[-1], 3) if len(scores) > 1 else 0.0,
    }

    # Check for numeric alignment
    snippet_adj = _check_numeric_alignment(claim, top_results[0]) if top_results else 0.0
    if snippet_adj != 0.0:
        details["numeric_alignment"] = "match" if snippet_adj > 0 else "conflict"

    # Generate reason string based on analysis
    reasons = []
    if details["score_spread"] > 0.4:
        reasons.append("isolated match (large score drop-off)")
    if len(set(domains)) > 1 and all(s > 0.3 for s in scores[:2]):
        reasons.append("cross-domain corroboration")
    if snippet_adj < 0:
        reasons.append("numeric values conflict with source")
    if any(r.get("memory_source") for r in top_results[:1]):
        reasons.append("verified against user memory")
    if len(top_results) == 1:
        reasons.append("single result only")

    if reasons:
        details["reason"] = "; ".join(reasons)

    return details
