# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Temporal awareness utilities for time-based search.

Provides:
- Temporal intent parsing (detect "last week", "recent", etc. in queries)
- Recency scoring (exponential decay factor for fresher content)

Canonical location as of Sprint D (2026-04-19 consolidation program).
Pre-Sprint D this module lived at ``src/mcp/utils/temporal.py`` and
was imported from ``core/agents/query_agent.py`` via the ``utils.*``
path — a reverse-layer import that blocked Sprint E's bridge
retirement. ``src/mcp/utils/temporal.py`` is now a thin re-export
bridge; the implementation lives here.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta

import config
from core.utils.time import utcnow

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
    """Detect temporal phrases in a query.

    Returns the lookback window in days, or ``None`` if no temporal
    intent is detected.
    """
    for pattern, days in _TEMPORAL_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            return days
    return None


def recency_score(ingested_at: str, half_life_days: float = 0) -> float:
    """Exponential decay score based on artifact age.

    ``score = 2^(-age_days / half_life)``. A document ingested today
    scores 1.0; ``half_life`` days ago scores 0.5; two half-lives ago
    scores 0.25. Returns 0.5 when the timestamp is unparseable —
    neutral rather than "very old" or "very new" for ambiguous data.
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
    """Check whether a timestamp falls within the last ``days`` days.

    Defaults to ``True`` on unparseable timestamps so a malformed
    record is included (err on the side of visibility) rather than
    silently dropped.
    """
    try:
        dt = datetime.fromisoformat(ingested_at)
        cutoff = utcnow().replace(tzinfo=None) - timedelta(days=days)
        return dt >= cutoff
    except (ValueError, TypeError):
        return True
