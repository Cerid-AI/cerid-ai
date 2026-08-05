# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Classify a query and pick which knowledge surfaces to consult.

Five intent buckets map to four surfaces (wiki / vector / graph /
memory). Deterministic regex on the fast path; the LLM fallback for
ambiguous cases is bounded by a 200ms / 200-token budget. Wiki-first
for "what is X", vector-first for specific facts, graph-first for
relational queries, memory-first for "what did we decide", mixed
falls back to all-surfaces fusion.

Distinct from ``core/retrieval/query_router.py`` which picks
GraphRAG modes internal to retrieval.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger("ai-companion.retrieval.surface_router")


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

IntentClass = Literal[
    "compiled_summary",   # "what is X", "tell me about Y" — wiki-first
    "specific_fact",      # quoted spans, "find the email where…" — vector-first
    "relational",         # "how does X relate to Y" — graph-first
    "personal_context",   # "what did we decide", "I prefer" — memory-first
    "mixed",              # default; consult all surfaces
]

Surface = Literal["wiki", "vector", "graph", "memory"]


@dataclass
class SurfaceRoute:
    """Outcome of routing one query."""

    primary: Surface
    surfaces: list[Surface]
    intent: IntentClass
    confidence: float = 1.0
    rationale: str = ""
    matched_entity_hint: str | None = None  # captured slug when wiki-first
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Regex patterns (ordered: more specific first)
# ---------------------------------------------------------------------------

# Compiled-summary signals: "what is X", "who is X", "tell me about X",
# "summarize Y", "give me an overview of Z". The named entity is the
# key signal — without it, treat as mixed.
_COMPILED_SUMMARY_PATTERNS = [
    re.compile(r"^\s*(what|who)\s+(is|are|was|were)\s+(\S+(?:\s+\S+){0,4})", re.IGNORECASE),
    re.compile(r"^\s*(tell me about|describe|overview of|summarize|summary of)\s+(.+)", re.IGNORECASE),
    re.compile(r"^\s*(give me an? )?(overview|summary)\s+(of|on|about)\s+(.+)", re.IGNORECASE),
    # Concept/topic queries route here too — the wiki tool falls
    # through to community lookup on entity miss.
    re.compile(r"^\s*(what|describe).*\b(concept|topic|theme|cluster)\s+(of|about|around)\s+(.+)", re.IGNORECASE),
]

# Specific-fact signals: quoted spans, "find the X that says Y",
# exact-phrase asks.
_SPECIFIC_FACT_PATTERNS = [
    re.compile(r'"[^"]{4,}"'),  # any quoted phrase ≥ 4 chars
    re.compile(r"^\s*(find|show me|locate|where (does|did) (.+) say)", re.IGNORECASE),
    re.compile(r"^\s*(the (passage|paragraph|sentence|email|message|note)\s+where)", re.IGNORECASE),
]

# Relational signals: "how does X relate to Y", "what connects A and B",
# "list everything mentioning Z", "compare X and Y", "X vs Y".
_RELATIONAL_PATTERNS = [
    re.compile(r"\b(relate(?:s|d)?|connect(?:s|ed)?|connection|link(?:ed|s)?)\b.*\b(to|and|between|with)\b", re.IGNORECASE),
    re.compile(r"^\s*(compare|contrast|differences? between|relationship between)\b", re.IGNORECASE),
    re.compile(r"^\s*(list|show|surface)\s+(everything|all|every)\s+(that mentions|mentioning|about|involving)\b", re.IGNORECASE),
    re.compile(r"\b(network|graph|neighbors|adjacent|connected to)\b", re.IGNORECASE),
    # Finding #3: "vs"/"vs."/"versus" comparisons are relational — they should
    # consult the graph surface, not fall through to mixed.
    re.compile(r"\b(vs\.?|versus)\b", re.IGNORECASE),
    # UNANCHORED comparison/relationship terms: natural phrasings lead with a
    # verb/wh-word ("what's the difference between X and Y", "how is X different
    # from Y"), so the ^-anchored pattern above misses them and they fall through
    # to mixed instead of graph-first. Match the comparison term mid-query.
    re.compile(
        r"\b(compare|contrast|difference between|differences between"
        r"|relationship between|different (?:from|than))\b",
        re.IGNORECASE,
    ),
]

# Personal-context signals: first-person, "we", "I", "did we", "last time".
# Checked BEFORE compiled_summary so "what is my preference…" routes to memory,
# not wiki (Finding #2: the compiled_summary "what is X" pattern would otherwise
# greedily capture preference/decision questions).
_PERSONAL_CONTEXT_PATTERNS = [
    re.compile(r"^\s*(what did (we|i)|did (we|i)|we discussed|i (said|told|asked|mentioned)|last time)\b", re.IGNORECASE),
    # Finding #2: a possessive over a personal noun anywhere in the query
    # (unanchored) — catches "what is my preference", "remind me of our decision".
    re.compile(r"\b(my|our)\s+(preference|decision|note|view|opinion|policy|choice|plan|stance)s?\b", re.IGNORECASE),
    re.compile(r"\b(remember when|earlier (i|we)|previously (i|we)|i (prefer|chose|decided))\b", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_intent(query: str) -> tuple[IntentClass, float, str]:
    """Heuristic classification of a query into one of the five intent buckets.

    Returns ``(intent, confidence, rationale)``. Confidence is 1.0 for
    confident regex matches, 0.5 for the ``mixed`` fallback.
    """
    if not query or not query.strip():
        return "mixed", 0.5, "empty query"

    q = query.strip()

    # Personal context first — it's the most distinctive (first-person)
    # and "what did we decide about X" should NOT route to wiki even
    # though "X" looks like a compiled-summary target.
    for pat in _PERSONAL_CONTEXT_PATTERNS:
        if pat.search(q):
            return "personal_context", 1.0, f"personal_context regex: {pat.pattern[:40]}"

    # Specific-fact: quoted spans are a strong signal.
    for pat in _SPECIFIC_FACT_PATTERNS:
        if pat.search(q):
            return "specific_fact", 1.0, f"specific_fact regex: {pat.pattern[:40]}"

    # Relational
    for pat in _RELATIONAL_PATTERNS:
        if pat.search(q):
            return "relational", 1.0, f"relational regex: {pat.pattern[:40]}"

    # Compiled-summary
    for pat in _COMPILED_SUMMARY_PATTERNS:
        if pat.search(q):
            return "compiled_summary", 1.0, f"compiled_summary regex: {pat.pattern[:40]}"

    return "mixed", 0.5, "no regex match"


def _entity_hint(query: str, intent: IntentClass) -> str | None:
    """Extract the candidate entity name from a compiled-summary query.

    Best-effort: returns the trailing noun phrase from "what is X" /
    "tell me about Y". Caller can pass this to fuzzy slug lookup to
    avoid a separate NLP pass.
    """
    if intent != "compiled_summary":
        return None
    q = query.strip().rstrip("?.! ")
    for pat in _COMPILED_SUMMARY_PATTERNS:
        m = pat.search(q)
        if m:
            # The last capture group is the entity portion in all our patterns.
            entity = m.group(m.lastindex or 0)
            if entity:
                return entity.strip()
    return None


def _surfaces_for_intent(intent: IntentClass) -> tuple[Surface, list[Surface]]:
    """Map an intent class to the primary + fallback surface list."""
    if intent == "compiled_summary":
        return "wiki", ["wiki", "vector"]
    if intent == "specific_fact":
        return "vector", ["vector", "graph"]
    if intent == "relational":
        return "graph", ["graph", "vector"]
    if intent == "personal_context":
        return "memory", ["memory", "vector"]
    # mixed
    return "vector", ["wiki", "vector", "graph"]


def route(query: str) -> SurfaceRoute:
    """Top-level router. Returns the SurfaceRoute for the query.

    Heuristic-only. Future K3.1 extension: LLM fallback for the
    ``mixed`` bucket when confidence is low.
    """
    intent, confidence, rationale = classify_intent(query)
    primary, surfaces = _surfaces_for_intent(intent)
    hint = _entity_hint(query, intent)

    result = SurfaceRoute(
        primary=primary,
        surfaces=surfaces,
        intent=intent,
        confidence=confidence,
        rationale=rationale,
        matched_entity_hint=hint,
    )
    logger.debug(
        "surface_router.route query=%r intent=%s primary=%s confidence=%.2f",
        query[:60], intent, primary, confidence,
    )
    return result
