# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Temporal awareness utilities for time-based search.

Provides:
- Temporal intent parsing (detect "last week", "recent", etc. in queries)
- Recency scoring (exponential decay factor for fresher content)
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta

import config
from utils.time import utcnow

# Patterns mapped to lookback window in days (most specific first)
_TEMPORAL_PATTERNS = [
    (r"\btoday\b", 1),
    (r"\byesterday\b", 2),
    (r"\blast\s+(?:few\s+)?days?\b", 3),
    (r"\bthis\s+week\b", 7),
    (r"\blast\s+week\b", 14),
    (r"\brecent(?:ly)?\b", 7),
    (r"\bthis\s+month\b", 30),
    (r"\blast\s+month\b", 60),
    (r"\blast\s+(?:few\s+)?months?\b", 90),
    (r"\bthis\s+year\b", 365),
]


def parse_temporal_intent(query: str) -> int | None:
    """
    Detect temporal phrases in a query.

    Returns:
        Number of days to look back, or None if no temporal intent detected.
    """
    for pattern, days in _TEMPORAL_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            return days
    return None


def recency_score(ingested_at: str, half_life_days: float = 0) -> float:
    """
    Exponential decay score based on artifact age.

    score = 2^(-age_days / half_life)

    A document ingested today scores 1.0. A document ingested `half_life`
    days ago scores 0.5. Two half-lives ago scores 0.25, etc.

    Args:
        ingested_at: ISO 8601 datetime string
        half_life_days: Decay half-life (0 = use config default)

    Returns:
        Score in (0, 1] range. Returns 0.5 if date is unparseable.
    """
    half_life = half_life_days or config.TEMPORAL_HALF_LIFE_DAYS
    try:
        dt = datetime.fromisoformat(ingested_at)
        # Strip tzinfo for backward-compat with naive timestamps in DB
        age_days = (utcnow().replace(tzinfo=None) - dt).total_seconds() / 86400.0
        if age_days < 0:
            age_days = 0
        return math.pow(2, -age_days / half_life)
    except (ValueError, TypeError):
        return 0.5


def is_within_window(ingested_at: str, days: int) -> bool:
    """Check if a timestamp falls within the last N days."""
    try:
        dt = datetime.fromisoformat(ingested_at)
        cutoff = utcnow().replace(tzinfo=None) - timedelta(days=days)
        return dt >= cutoff
    except (ValueError, TypeError):
        return True  # include if we can't parse the date
