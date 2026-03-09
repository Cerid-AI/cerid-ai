# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Adaptive retrieval gate — classifies whether a query needs KB retrieval.

Heuristic-based classification: pattern-match for conversational queries,
single-concept lookups, and complex questions. Returns skip/light/full decision
to reduce unnecessary retrieval overhead.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

logger = logging.getLogger("ai-companion.retrieval_gate")

ENABLE_ADAPTIVE_RETRIEVAL = os.getenv("ENABLE_ADAPTIVE_RETRIEVAL", "false").lower() == "true"
ADAPTIVE_RETRIEVAL_LIGHT_TOP_K = int(os.getenv("ADAPTIVE_RETRIEVAL_LIGHT_TOP_K", "3"))


@dataclass
class RetrievalDecision:
    """Decision on how to handle retrieval for a query."""
    action: str  # "skip" | "light" | "full"
    top_k: int
    reason: str


# Patterns that indicate no KB retrieval needed
_SKIP_PATTERNS = [
    re.compile(r"^(hi|hello|hey|thanks|thank you|bye|goodbye|ok|okay)\b", re.IGNORECASE),
    re.compile(r"^(can you|could you|please)\s+(rephrase|rewrite|summarize|explain)\s+(that|this|it|the above)", re.IGNORECASE),
    re.compile(r"^(what|who) are you\b", re.IGNORECASE),
    re.compile(r"^(yes|no|sure|nope|yep|nah)\s*[.!?]*$", re.IGNORECASE),
    re.compile(r"^(good|great|nice|cool|awesome|perfect|excellent)\s*[.!?]*$", re.IGNORECASE),
]

# Patterns that indicate a simple/light retrieval
_LIGHT_PATTERNS = [
    re.compile(r"^(what is|define|explain)\s+\w+(\s+\w+){0,3}[.?]*$", re.IGNORECASE),
    re.compile(r"^(show|list|find)\s+(me\s+)?(the\s+)?\w+(\s+\w+){0,2}[.?]*$", re.IGNORECASE),
]

# Patterns that indicate complex retrieval needed
_FULL_PATTERNS = [
    re.compile(r"\b(compare|contrast|difference|versus|vs\.?)\b", re.IGNORECASE),
    re.compile(r"\b(how|why)\s+.{15,}", re.IGNORECASE),
    re.compile(r"\?.*\?", re.IGNORECASE),  # multiple question marks
    re.compile(r"\b(and|also|additionally|furthermore|moreover)\b.*\?", re.IGNORECASE),
    re.compile(r"\b(analyze|evaluate|assess|review)\b", re.IGNORECASE),
]


def classify_retrieval_need(
    query: str,
    conversation_messages: list[dict[str, str]] | None = None,
) -> RetrievalDecision:
    """Classify whether a query needs KB retrieval.

    Returns a RetrievalDecision with action, top_k, and reason.
    """
    if not query or not query.strip():
        return RetrievalDecision(action="skip", top_k=0, reason="empty_query")

    q = query.strip()

    # Skip patterns: conversational, meta, acknowledgments
    for pattern in _SKIP_PATTERNS:
        if pattern.search(q):
            return RetrievalDecision(action="skip", top_k=0, reason="conversational")

    # Very short queries (< 3 words, no question mark) likely conversational
    word_count = len(q.split())
    if word_count <= 2 and "?" not in q:
        return RetrievalDecision(action="skip", top_k=0, reason="too_short")

    # Full retrieval patterns: complex, multi-part, analytical
    for pattern in _FULL_PATTERNS:
        if pattern.search(q):
            return RetrievalDecision(action="full", top_k=10, reason="complex_query")

    # Light patterns: simple lookups
    for pattern in _LIGHT_PATTERNS:
        if pattern.search(q):
            return RetrievalDecision(
                action="light",
                top_k=ADAPTIVE_RETRIEVAL_LIGHT_TOP_K,
                reason="simple_lookup",
            )

    # Default: full retrieval for anything non-trivial
    if word_count >= 3 or "?" in q:
        return RetrievalDecision(action="full", top_k=10, reason="default_full")

    return RetrievalDecision(action="light", top_k=ADAPTIVE_RETRIEVAL_LIGHT_TOP_K, reason="default_light")
