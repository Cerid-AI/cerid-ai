# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared quality scoring utility.

Provides a single ``compute_quality_score`` function used by both the
ingestion pipeline (at ingest time) and the curator agent (for batch
rescore).  This ensures identical scoring logic across both paths.

Four weighted dimensions:
    * **Summary** (30 %) – length-based quality
    * **Keywords** (25 %) – count vs optimal target
    * **Freshness** (20 %) – exponential decay
    * **Completeness** (25 %) – presence of summary, keywords, tags, sub-category
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import config

UTC = timezone.utc

# ---------------------------------------------------------------------------
# Helpers (portable — no Neo4j or ChromaDB dependency)
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """UTC-aware now (matches curator.utcnow)."""
    return datetime.now(UTC)


def _score_summary(summary: str) -> float:
    """Score summary quality based on length.  Returns [0, 1]."""
    if not summary or not summary.strip():
        return 0.0
    length = len(summary.strip())
    if length < config.QUALITY_SUMMARY_MIN_CHARS:
        return length / config.QUALITY_SUMMARY_MIN_CHARS
    if length <= config.QUALITY_SUMMARY_MAX_CHARS:
        return 1.0
    overshoot = length - config.QUALITY_SUMMARY_MAX_CHARS
    return max(0.3, 1.0 - (overshoot / 1000))


def _score_keywords(keywords: list[str]) -> float:
    """Score keyword quality based on count.  Returns [0, 1]."""
    if not keywords:
        return 0.0
    count = len(keywords)
    if count >= config.QUALITY_KEYWORDS_OPTIMAL:
        return 1.0
    return count / config.QUALITY_KEYWORDS_OPTIMAL


def _score_freshness(ingested_at: str | None) -> float:
    """Score freshness using exponential decay.  Returns [0, 1]."""
    if not ingested_at:
        return 0.5
    try:
        dt = datetime.fromisoformat(ingested_at)
        now = _utcnow()
        if dt.tzinfo is None:
            now = now.replace(tzinfo=None)
        age_days = max(0, (now - dt).total_seconds() / 86400.0)
        return math.pow(2, -age_days / config.TEMPORAL_HALF_LIFE_DAYS)
    except (ValueError, TypeError):
        return 0.5


def _score_completeness(
    summary: str,
    keywords: list[str],
    tags: list[str],
    sub_category: str,
    default_sub_category: str,
) -> float:
    """Score metadata completeness.  Returns [0, 1]."""
    checks = 0
    total = 4
    if summary and len(summary.strip()) >= 20:
        checks += 1
    if len(keywords) >= 2:
        checks += 1
    if tags:
        checks += 1
    if sub_category and sub_category != default_sub_category:
        checks += 1
    return checks / total


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
) -> float:
    """Compute a weighted quality score in [0, 1].

    Parameters accept both parsed lists and JSON strings (for convenience
    at ingestion time where metadata values are still JSON-encoded).
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

    s_summary = _score_summary(summary)
    s_keywords = _score_keywords(keywords)
    s_freshness = _score_freshness(ingested_at)
    s_completeness = _score_completeness(
        summary, keywords, tags, sub_category, default_sub_category,
    )

    total = (
        config.QUALITY_WEIGHT_SUMMARY * s_summary
        + config.QUALITY_WEIGHT_KEYWORDS * s_keywords
        + config.QUALITY_WEIGHT_FRESHNESS * s_freshness
        + config.QUALITY_WEIGHT_COMPLETENESS * s_completeness
    )
    return round(min(total, 1.0), 4)
