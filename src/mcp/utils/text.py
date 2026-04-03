# Copyright (c) 2026 Cerid AI. All rights reserved.
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


