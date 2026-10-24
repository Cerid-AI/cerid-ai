# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import config
from core.utils.time import utcnow


@dataclass(frozen=True)
class TemporalIntent:
    """A query's time scope: a lookback in days, and whether rows outside it are dropped.

    Only a calendar phrase ("this week", "last month") is a hard window. A
    vague one ("recent", "last few months") only favours newer rows.
    """

    days: int
    hard: bool


# Calendar phrases first: "recent notes from last month" is scoped to last month.
_TEMPORAL_PATTERNS = [
    (r"\btoday\b", 1, True),
    (r"\byesterday\b", 2, True),
    (r"\blast\s+(?:few\s+)?days?\b", 3, True),
    (r"\bthis\s+week\b", 7, True),
    (r"\blast\s+week\b", 14, True),
    (r"\bthis\s+month\b", 30, True),
    (r"\blast\s+month\b", 60, True),
    (r"\bthis\s+year\b", 365, True),
    (r"\brecent(?:ly)?\b", 7, False),
    (r"\blast\s+(?:few\s+)?months?\b", 90, False),
]


def parse_temporal_intent(query: str) -> TemporalIntent | None:
    """Detect temporal phrases in a query; ``None`` when it has no time scope."""
    for pattern, days, hard in _TEMPORAL_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            return TemporalIntent(days=days, hard=hard)
    return None


def _parse_utc(stamp: str) -> datetime | None:
    """An ISO timestamp as an aware UTC datetime; a naive one is taken as UTC."""
    try:
        dt = datetime.fromisoformat(stamp)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def recency_score(ingested_at: str, half_life_days: float = 0) -> float | None:
    """Exponential decay score based on artifact age.

    ``score = 2^(-age_days / half_life)``. A document ingested today
    scores 1.0; ``half_life`` days ago scores 0.5; two half-lives ago
    scores 0.25. Returns ``None`` when the timestamp is missing or
    unparseable: an undated row earns no recency boost.
    """
    dt = _parse_utc(ingested_at)
    if dt is None:
        return None
    half_life = half_life_days or config.TEMPORAL_HALF_LIFE_DAYS
    age_days = max(0.0, (utcnow() - dt).total_seconds() / 86400.0)
    return math.pow(2, -age_days / half_life)


def is_within_window(ingested_at: str, days: int) -> bool:
    """Check whether a timestamp falls within the last ``days`` days.

    Defaults to ``True`` on unparseable timestamps so a malformed
    record is included (err on the side of visibility) rather than
    silently dropped.
    """
    dt = _parse_utc(ingested_at)
    if dt is None:
        return True
    return dt >= utcnow() - timedelta(days=days)


def _row_date(row: dict[str, Any]) -> str:
    # Memory and wiki rows carry their date as created_at, not ingested_at.
    return row.get("ingested_at") or row.get("created_at") or ""


def apply_temporal_intent(results: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Scope *results* to the query's time phrase: drop rows outside a hard window, boost newer rows.

    Only a question about time favours newer rows: without one, a knowledge
    pack's ingest date says nothing about how current its content is. An
    undated row stays and earns no boost.
    """
    intent = parse_temporal_intent(query)
    if intent is None:
        return results
    if intent.hard:
        results = [r for r in results if is_within_window(_row_date(r), intent.days)]
    for r in results:
        recency = recency_score(_row_date(r))
        if recency is not None:
            boost = recency * config.TEMPORAL_RECENCY_WEIGHT
            r["relevance"] = round(min(1.0, r["relevance"] + boost), 4)
    return results
