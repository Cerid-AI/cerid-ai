# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Retrieval profile — per-chunk quality signals computed at ingest time.

Each chunk gets a ``retrieval_profile`` dict stored as JSON in ChromaDB
metadata.  At retrieval time the profile adjusts hybrid search weights and
reranking behavior so that noisy documents (OCR'd scans, dense tables) are
scored with the right strategy instead of a one-size-fits-all pipeline.

Profile dimensions
------------------
- **content_density** (0-1): ratio of meaningful prose tokens to total tokens.
  Low = mostly numbers/tables/boilerplate.  High = rich narrative.
- **keyword_richness** (0-1): distinct domain-relevant terms / total words.
  Low = repetitive forms.  High = diverse vocabulary.
- **table_ratio** (0-1): fraction of content that is tabular structure
  (detected by markdown pipe/dash patterns from pdfplumber output).
- **preferred_strategy**: ``"vector"`` | ``"keyword"`` | ``"balanced"``
  Recommendation for how this chunk should be scored at retrieval.
"""
from __future__ import annotations

import json
import re
from typing import Any

# Prose indicator patterns
_SENTENCE_RE = re.compile(r"[A-Z][^.!?]{10,}[.!?]")
_TABLE_RE = re.compile(r"(?:[\|\-\+]{3,}|(?:\d[\t ]+){3,})")
_NUMBER_HEAVY_RE = re.compile(r"\b\d[\d,.]+\b")


def compute_retrieval_profile(
    text: str,
    *,
    file_type: str = "",
    page_count: int | None = None,
    table_count: int | None = None,
) -> dict[str, Any]:
    """Compute a retrieval profile for a chunk or document.

    Parameters
    ----------
    text : str
        The chunk text (or full document text for artifact-level profile).
    file_type : str
        Source file type (``"pdf"``, ``"docx"``, ``"txt"``, etc.).
    page_count, table_count : int | None
        Metadata from the parser (PDF-specific).

    Returns
    -------
    dict with keys: content_density, keyword_richness, table_ratio,
    preferred_strategy, and a composite ``score`` (0-1).
    """
    words = text.split()
    word_count = len(words)
    if word_count == 0:
        return _empty_profile()

    # --- Content density: sentences vs total tokens ---
    sentences = _SENTENCE_RE.findall(text)
    prose_words = sum(len(s.split()) for s in sentences)
    content_density = min(1.0, prose_words / max(word_count, 1))

    # --- Keyword richness: unique words / total words ---
    unique_lower = {w.lower().strip(".,;:!?()[]{}\"'") for w in words if len(w) > 2}
    keyword_richness = min(1.0, len(unique_lower) / max(word_count, 1))

    # --- Table ratio: structural patterns ---
    table_lines = sum(1 for line in text.split("\n") if _TABLE_RE.search(line))
    total_lines = max(len(text.split("\n")), 1)
    table_ratio_text = table_lines / total_lines

    # Use parser table_count if available (more accurate than regex)
    if table_count is not None and page_count and page_count > 0:
        table_ratio = min(1.0, table_count / (page_count * 2))  # normalize: ~2 tables/page = high
    else:
        table_ratio = table_ratio_text

    # --- Number density ---
    number_matches = len(_NUMBER_HEAVY_RE.findall(text))
    number_density = min(1.0, number_matches / max(word_count / 5, 1))

    # --- Strategy recommendation ---
    if table_ratio > 0.3 or number_density > 0.4 or content_density < 0.2:
        preferred_strategy = "keyword"
    elif content_density > 0.6 and keyword_richness > 0.3:
        preferred_strategy = "vector"
    else:
        preferred_strategy = "balanced"

    # --- Composite score (higher = more suitable for vector retrieval) ---
    score = round(
        0.4 * content_density + 0.3 * keyword_richness + 0.3 * (1.0 - table_ratio),
        3,
    )

    return {
        "content_density": round(content_density, 3),
        "keyword_richness": round(keyword_richness, 3),
        "table_ratio": round(table_ratio, 3),
        "number_density": round(number_density, 3),
        "preferred_strategy": preferred_strategy,
        "score": score,
    }


def serialize_profile(profile: dict[str, Any]) -> str:
    """Serialize profile to JSON string for ChromaDB metadata storage."""
    return json.dumps(profile, separators=(",", ":"))


def deserialize_profile(profile_json: str | None) -> dict[str, Any] | None:
    """Deserialize profile from ChromaDB metadata. Returns None if absent."""
    if not profile_json:
        return None
    try:
        return json.loads(profile_json)
    except (json.JSONDecodeError, TypeError):
        return None


def get_hybrid_weights(
    profile: dict[str, Any] | None,
    default_vector: float = 0.5,
    default_keyword: float = 0.5,
) -> tuple[float, float]:
    """Return (vector_weight, keyword_weight) adjusted by retrieval profile.

    - ``"keyword"`` strategy: 30% vector / 70% keyword
    - ``"vector"`` strategy: 70% vector / 30% keyword
    - ``"balanced"`` or absent: use defaults
    """
    if not profile:
        return default_vector, default_keyword

    strategy = profile.get("preferred_strategy", "balanced")
    if strategy == "keyword":
        return 0.3, 0.7
    if strategy == "vector":
        return 0.7, 0.3
    return default_vector, default_keyword


def get_rerank_weights(
    profile: dict[str, Any] | None,
    default_ce: float = 0.4,
    default_original: float = 0.6,
) -> tuple[float, float]:
    """Return (ce_weight, original_weight) adjusted by retrieval profile.

    For keyword-strategy docs, reduce CE influence further since the
    cross-encoder is trained on prose, not structured/tabular text.
    """
    if not profile:
        return default_ce, default_original

    strategy = profile.get("preferred_strategy", "balanced")
    if strategy == "keyword":
        return 0.2, 0.8  # CE is unreliable on structured text
    if strategy == "vector":
        return 0.5, 0.5  # CE is useful for prose
    return default_ce, default_original


def _empty_profile() -> dict[str, Any]:
    return {
        "content_density": 0.0,
        "keyword_richness": 0.0,
        "table_ratio": 0.0,
        "number_density": 0.0,
        "preferred_strategy": "balanced",
        "score": 0.0,
    }
