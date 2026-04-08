# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Adaptive retrieval gate — classifies whether a query needs KB retrieval.

Heuristic-based classification: pattern-match for conversational queries,
single-concept lookups, and complex questions. Returns skip/light/full decision
to reduce unnecessary retrieval overhead.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from config.features import ADAPTIVE_RETRIEVAL_LIGHT_TOP_K

logger = logging.getLogger("ai-companion.retrieval_gate")


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

# Technical *concepts* that upgrade a "light" query to "full" retrieval.
# These are generic CS/engineering concepts (not specific product names like
# "kubernetes" or "docker") where the user likely needs deeper KB context
# beyond a simple one-line definition.
_TECHNICAL_TERMS = {
    "algorithm", "architecture", "authentication", "authorization",
    "binary", "blockchain", "buffer", "cache", "callback",
    "cipher", "compiler", "concurrency", "cryptography",
    "database", "deadlock", "dependency", "deployment",
    "encryption", "endpoint", "entropy",
    "function", "gateway", "graph", "hash", "heap",
    "index", "inference", "injection", "interface", "interpreter",
    "kernel", "lambda", "latency",
    "linker", "malloc", "microservice", "middleware",
    "mutex", "namespace", "neural",
    "orm", "parser", "pipeline", "pointer", "protocol", "proxy",
    "query", "queue", "recursion", "regex",
    "replication", "runtime", "schema", "semaphore",
    "serialization", "sharding", "socket",
    "stack", "state machine", "stream", "subnet",
    "tensor", "thread", "token", "topology", "transformer",
    "vector", "virtualization", "zero-copy",
}


# Patterns that indicate complex retrieval needed
_FULL_PATTERNS = [
    re.compile(r"\b(compare|contrast|difference|versus|vs\.?)\b", re.IGNORECASE),
    re.compile(r"\b(how|why)\s+.{15,}", re.IGNORECASE),
    re.compile(r"\?.*\?", re.IGNORECASE),  # multiple question marks
    re.compile(r"\b(and|also|additionally|furthermore|moreover)\b.*\?", re.IGNORECASE),
    re.compile(r"\b(analyze|evaluate|assess|review)\b", re.IGNORECASE),
]


def classify_retrieval_need(query: str) -> RetrievalDecision:
    """Classify whether a query needs KB retrieval."""
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

    # Light patterns: simple lookups — but upgrade to full if technical terms present
    for pattern in _LIGHT_PATTERNS:
        if pattern.search(q):
            # Technical term upgrade: "What is the algorithm" → full
            q_lower = q.lower()
            for term in _TECHNICAL_TERMS:
                if term in q_lower:
                    return RetrievalDecision(
                        action="full",
                        top_k=10,
                        reason="technical_term_upgrade",
                    )
            return RetrievalDecision(
                action="light",
                top_k=ADAPTIVE_RETRIEVAL_LIGHT_TOP_K,
                reason="simple_lookup",
            )

    # Default: full retrieval for anything non-trivial
    if word_count >= 3 or "?" in q:
        return RetrievalDecision(action="full", top_k=10, reason="default_full")

    return RetrievalDecision(action="light", top_k=ADAPTIVE_RETRIEVAL_LIGHT_TOP_K, reason="default_light")
