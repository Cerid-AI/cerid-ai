# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared text processing constants and helpers.

Canonical definitions for stopwords, word tokenization, and basic text
normalization used across the retrieval pipeline.
"""
from __future__ import annotations

import re

# English stopwords — superset used across query enrichment, MMR diversity,
# and context assembly. Individual modules may use subsets via set intersection.
STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "each",
    "every", "both", "few", "more", "most", "other", "some", "such", "no",
    "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "just", "because", "but", "and", "or", "if", "while", "about", "up",
    "that", "this", "these", "those", "am", "what", "which", "who", "whom",
    "its", "it", "he", "she", "they", "them", "his", "her", "my", "your",
    "our", "their", "me", "him", "us", "i", "you", "we", "also", "like",
    "get", "got", "make", "made", "know", "think", "want", "see", "look",
    "find", "give", "tell", "say", "said", "going", "come", "take",
})

# Word tokenization pattern — matches alphanumeric runs with optional
# hyphenated or apostrophe-joined segments (e.g. "well-known", "don't").
WORD_RE: re.Pattern[str] = re.compile(r"[a-zA-Z0-9_]+(?:[-'][a-zA-Z0-9_]+)*")


def tokenize_lower(text: str) -> list[str]:
    """Tokenize text to lowercase words, excluding stopwords.

    Returns a list (preserving order) of lowercase non-stopword tokens.
    """
    return [w for w in (m.lower() for m in WORD_RE.findall(text)) if w not in STOPWORDS]


def extract_keywords_simple(text: str, max_keywords: int = 10) -> list[str]:
    """Frequency-based keyword extraction — pure-regex fallback.

    Counts word frequencies after stopword removal and returns the top
    ``max_keywords`` terms. Used by the authoritative-verification path
    to reduce a claim to its searchable terms without invoking an LLM.

    Canonical location as of Sprint D (2026-04-19). Previously lived as
    ``utils.metadata._extract_keywords_simple``; that name remains as a
    thin pass-through in metadata.py for legacy import-path compat.
    """
    from collections import Counter

    # Scan at most 5k chars to cap worst-case cost on very long inputs.
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text[:5000].lower())
    filtered = [w for w in words if w not in STOPWORDS]
    return [word for word, _ in Counter(filtered).most_common(max_keywords)]
