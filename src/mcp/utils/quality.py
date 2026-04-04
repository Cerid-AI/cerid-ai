# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Quality scoring v2 — domain-adaptive, 6-dimension weighted scoring.

Six weighted dimensions:
    * **Content richness** (25 %) – word count, structural elements, info density
    * **Metadata completeness** (20 %) – summary, keywords, tags, sub-category
    * **Freshness** (15 %) – domain-adaptive exponential decay
    * **Source authority** (15 %) – upload > webhook > clipboard > external
    * **Retrieval utility** (15 %) – how often the document is retrieved
    * **Embedding coherence** (10 %) – placeholder (default 0.7)

Replaces the v1 4-dimension algorithm that penalised rich documents
(long summaries, evergreen content) resulting in scores like Q20 for resumes.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone

from config.constants import (
    QUALITY_MIN_FLOOR,
    QUALITY_WEIGHT_RICHNESS,
    QUALITY_WEIGHT_METADATA,
    QUALITY_WEIGHT_FRESHNESS,
    QUALITY_WEIGHT_AUTHORITY,
    QUALITY_WEIGHT_UTILITY,
    QUALITY_WEIGHT_COHERENCE,
    QUALITY_EVERGREEN_DOMAINS,
    QUALITY_EVERGREEN_HALF_LIFE_DAYS,
    QUALITY_TEMPORAL_HALF_LIFE_DAYS,
)
import config

UTC = timezone.utc

_WORD_RE = re.compile(r"\w+")
_HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)
_LIST_RE = re.compile(r"^[\-\*\d]+[\.\)]\s", re.MULTILINE)
_CODE_BLOCK_RE = re.compile(r"```")

_SOURCE_AUTHORITY: dict[str, float] = {
    "upload": 1.0,
    "webhook": 0.9,
    "clipboard": 0.8,
    "external": 0.7,
}


# ---------------------------------------------------------------------------
# Helpers (portable — no Neo4j or ChromaDB dependency)
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _score_richness(content: str) -> float:
    """Score content richness: word count, structure, info density."""
    if not content or not content.strip():
        return 0.0

    words = _WORD_RE.findall(content)
    word_count = len(words)

    # Word count score — optimal 100-5000
    if word_count < 20:
        wc_score = 0.1
    elif word_count < 100:
        wc_score = 0.3 + 0.7 * (word_count / 100)
    elif word_count <= 5000:
        wc_score = 1.0
    else:
        wc_score = max(0.7, 1.0 - (word_count - 5000) / 20000)

    # Structural elements (headings, lists, code blocks)
    headings = len(_HEADING_RE.findall(content))
    lists = len(_LIST_RE.findall(content))
    code_blocks = len(_CODE_BLOCK_RE.findall(content)) // 2
    struct_score = min(1.0, (headings + lists + code_blocks) / 5)

    # Info density — unique words / total words
    unique_count = len(set(w.lower() for w in words))
    density = unique_count / word_count if word_count > 0 else 0.0
    density_score = min(1.0, density / 0.5)  # 50%+ unique → full marks

    return 0.5 * wc_score + 0.3 * struct_score + 0.2 * density_score


def _score_metadata(
    summary: str,
    keywords: list[str],
    tags: list[str],
    sub_category: str,
    default_sub_category: str,
) -> float:
    """Graduated metadata completeness: 0.25 per field present."""
    score = 0.0
    if summary and len(summary.strip()) >= 20:
        score += 0.25
    if len(keywords) >= 2:
        score += 0.25
    if tags:
        score += 0.25
    if sub_category and sub_category != default_sub_category:
        score += 0.25
    return score


def _score_freshness(
    ingested_at: str | None,
    domain: str = "general",
    evergreen: bool = False,
) -> float:
    """Domain-adaptive freshness with evergreen support."""
    if not ingested_at:
        return 0.5

    # Evergreen items don't decay
    if evergreen:
        return 1.0

    # Pick half-life based on domain
    if domain in QUALITY_EVERGREEN_DOMAINS:
        half_life = QUALITY_EVERGREEN_HALF_LIFE_DAYS
    else:
        half_life = QUALITY_TEMPORAL_HALF_LIFE_DAYS

    try:
        dt = datetime.fromisoformat(ingested_at)
        now = _utcnow()
        if dt.tzinfo is None:
            now = now.replace(tzinfo=None)
        age_days = max(0, (now - dt).total_seconds() / 86400.0)
        return math.pow(2, -age_days / half_life)
    except (ValueError, TypeError):
        return 0.5


def _score_authority(source_type: str) -> float:
    """Source authority: upload=1.0, webhook=0.9, clipboard=0.8, external=0.7."""
    return _SOURCE_AUTHORITY.get(source_type, 0.7)


def _score_utility(retrieval_count: int) -> float:
    """Retrieval utility: 10+ retrievals = full marks."""
    return min(1.0, retrieval_count / 10) if retrieval_count > 0 else 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_quality_score(
    summary: str,
    keywords: list[str] | str,
    tags: list[str] | str,
    sub_category: str,
    default_sub_category: str,
    ingested_at: str | None = None,
    content: str = "",
    domain: str = "general",
    source_type: str = "upload",
    retrieval_count: int = 0,
    starred: bool = False,
    evergreen: bool = False,
) -> float:
    """Compute a weighted quality score in [QUALITY_MIN_FLOOR, 1.0].

    Backward-compatible: new parameters have defaults matching v1 behavior.
    """
    # Normalise JSON strings → lists
    if isinstance(keywords, str):
        try:
            keywords = json.loads(keywords) if keywords else []
        except (json.JSONDecodeError, TypeError):
            keywords = []
    if isinstance(tags, str):
        try:
            tags = json.loads(tags) if tags else []
        except (json.JSONDecodeError, TypeError):
            tags = []

    # Use content if available, else fall back to summary for richness
    richness_text = content if content else summary

    s_richness = _score_richness(richness_text)
    s_metadata = _score_metadata(summary, keywords, tags, sub_category, default_sub_category)
    s_freshness = _score_freshness(ingested_at, domain, evergreen)
    s_authority = _score_authority(source_type)
    s_utility = _score_utility(retrieval_count)
    s_coherence = 0.7  # Placeholder — real implementation deferred to P2

    total = (
        QUALITY_WEIGHT_RICHNESS * s_richness
        + QUALITY_WEIGHT_METADATA * s_metadata
        + QUALITY_WEIGHT_FRESHNESS * s_freshness
        + QUALITY_WEIGHT_AUTHORITY * s_authority
        + QUALITY_WEIGHT_UTILITY * s_utility
        + QUALITY_WEIGHT_COHERENCE * s_coherence
    )

    return round(max(QUALITY_MIN_FLOOR, min(total, 1.0)), 4)
