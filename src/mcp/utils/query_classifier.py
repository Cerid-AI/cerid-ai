# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Query intent classifier — determines RAG routing per message.

Pure pattern-based (zero LLM calls, must be instant). Classifies queries into
intent categories that determine how much RAG context to inject.

Intent → RAG behavior:
  factual       → Full RAG (all retrieval strategies active)
  code          → Full RAG (coding domain prioritized)
  analytical    → Full RAG (multi-domain, decomposition enabled)
  creative      → Minimal RAG (top 3 only, skip if no strong match)
  conversational → Skip RAG entirely

Dependencies: none (pure regex, no external calls)
Error types: none (always returns a valid intent)
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["classify_query_intent", "get_rag_config"]

# Compiled patterns — evaluated once at module load
_CONVERSATIONAL_RE = re.compile(
    r"^(hi|hello|hey|thanks|thank you|ok|okay|sure|yes|no|got it|cool|great)\b",
    re.IGNORECASE,
)
_CODE_RE = re.compile(
    r"\b(function|class|def |import |require|error|bug|debug|implement|refactor"
    r"|code|script|api|endpoint|database|query|sql|regex|algorithm)\b",
    re.IGNORECASE,
)
_CREATIVE_RE = re.compile(
    r"^(write|create|compose|draft|imagine|brainstorm|generate|design|invent"
    r"|story|poem|essay|letter|email)\b",
    re.IGNORECASE,
)
_ANALYTICAL_RE = re.compile(
    r"\b(compare|analyze|evaluate|assess|review|summarize"
    r"|explain the difference|pros and cons|trade.?offs?)\b",
    re.IGNORECASE,
)
_FACTUAL_RE = re.compile(
    r"(\?|^(who|what|when|where|how|why)\b)", re.IGNORECASE
)


def classify_query_intent(query: str) -> str:
    """Classify a user query into an intent category for RAG routing."""
    stripped = query.strip()

    # 1. Conversational — greetings, acknowledgments, short affirmations
    if _CONVERSATIONAL_RE.search(stripped):
        if len(stripped) <= 20 and "?" not in stripped:
            return "conversational"

    # 2. Code — programming keywords anywhere
    if _CODE_RE.search(stripped):
        return "code"

    # 3. Creative — generative verbs at start
    if _CREATIVE_RE.search(stripped):
        return "creative"

    # 4. Analytical — comparison/analysis keywords
    if _ANALYTICAL_RE.search(stripped):
        return "analytical"

    # 5. Factual — default
    return "factual"


def get_rag_config(intent: str) -> dict[str, Any]:
    """Return RAG retrieval configuration based on intent."""
    configs: dict[str, dict[str, Any]] = {
        "factual": {"inject": True, "top_k": 10, "decompose": True, "rerank": True},
        "code": {"inject": True, "top_k": 10, "decompose": True, "rerank": True, "domains": ["coding"]},
        "analytical": {"inject": True, "top_k": 15, "decompose": True, "rerank": True},
        "creative": {"inject": False, "top_k": 3, "decompose": False, "rerank": False},
        "conversational": {"inject": False, "top_k": 0, "decompose": False, "rerank": False},
    }
    if intent in configs:
        return configs[intent]
    return configs["factual"]
