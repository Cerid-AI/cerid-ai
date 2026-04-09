# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Leaderboard module — stores eval run results and ranks pipelines by composite score.

Entries are persisted in a Redis sorted set keyed by composite score.
The leaderboard is trimmed to the top 50 entries on each update.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger("ai-companion.eval.leaderboard")

_LEADERBOARD_KEY = "cerid:eval:leaderboard"


@dataclass
class LeaderboardEntry:
    """A single leaderboard entry representing one eval run's aggregate scores."""

    pipeline: str
    avg_ndcg_5: float = 0.0
    avg_ndcg_10: float = 0.0
    avg_mrr: float = 0.0
    avg_precision_5: float = 0.0
    avg_recall_10: float = 0.0
    avg_faithfulness: float = 0.0
    avg_answer_relevancy: float = 0.0
    avg_context_precision: float = 0.0
    avg_context_recall: float = 0.0
    n_queries: int = 0
    benchmark: str = ""
    timestamp: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def composite_score(self) -> float:
        """Weighted composite: 40% NDCG@5, 20% MRR, 20% faithfulness, 20% answer_relevancy."""
        return (
            0.4 * self.avg_ndcg_5
            + 0.2 * self.avg_mrr
            + 0.2 * self.avg_faithfulness
            + 0.2 * self.avg_answer_relevancy
        )


def update_leaderboard(entry: LeaderboardEntry, redis_client: Any) -> None:
    """Add or update a leaderboard entry in Redis sorted set."""
    if redis_client is None:
        logger.warning("No Redis client — leaderboard update skipped")
        return
    member = json.dumps(asdict(entry))
    redis_client.zadd(_LEADERBOARD_KEY, {member: entry.composite_score})
    # Trim to top 50
    redis_client.zremrangebyrank(_LEADERBOARD_KEY, 0, -(51))


def get_leaderboard(redis_client: Any, top_k: int = 20) -> list[LeaderboardEntry]:
    """Get top leaderboard entries sorted by composite score (descending)."""
    if redis_client is None:
        return []
    raw = redis_client.zrevrange(_LEADERBOARD_KEY, 0, top_k - 1)
    entries = []
    for item in raw:
        try:
            data = json.loads(item)
            entries.append(LeaderboardEntry(**data))
        except (json.JSONDecodeError, TypeError):
            continue
    return entries


def clear_leaderboard(redis_client: Any) -> int:
    """Clear all leaderboard entries. Returns count of removed entries."""
    if redis_client is None:
        return 0
    count = redis_client.zcard(_LEADERBOARD_KEY)
    redis_client.delete(_LEADERBOARD_KEY)
    return count
