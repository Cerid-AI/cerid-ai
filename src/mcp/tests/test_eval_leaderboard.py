# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the eval leaderboard module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from app.eval.leaderboard import (
    _LEADERBOARD_KEY,
    LeaderboardEntry,
    clear_leaderboard,
    get_leaderboard,
    update_leaderboard,
)


class TestLeaderboardEntryCompositeScore:
    """Tests for the composite_score property."""

    def test_composite_score_calculation(self) -> None:
        entry = LeaderboardEntry(
            pipeline="hybrid_reranked",
            avg_ndcg_5=1.0,
            avg_mrr=1.0,
            avg_faithfulness=1.0,
            avg_answer_relevancy=1.0,
        )
        # 0.4*1.0 + 0.2*1.0 + 0.2*1.0 + 0.2*1.0 = 1.0
        assert entry.composite_score == pytest.approx(1.0)

    def test_composite_score_weighted(self) -> None:
        entry = LeaderboardEntry(
            pipeline="test",
            avg_ndcg_5=0.5,
            avg_mrr=0.8,
            avg_faithfulness=0.6,
            avg_answer_relevancy=0.4,
        )
        expected = 0.4 * 0.5 + 0.2 * 0.8 + 0.2 * 0.6 + 0.2 * 0.4
        assert entry.composite_score == pytest.approx(expected)

    def test_composite_score_zeros(self) -> None:
        entry = LeaderboardEntry(pipeline="empty")
        assert entry.composite_score == pytest.approx(0.0)


class TestLeaderboardEntryDefaults:
    """Tests for default field values."""

    def test_defaults(self) -> None:
        entry = LeaderboardEntry(pipeline="test_pipeline")
        assert entry.pipeline == "test_pipeline"
        assert entry.avg_ndcg_5 == 0.0
        assert entry.avg_ndcg_10 == 0.0
        assert entry.avg_mrr == 0.0
        assert entry.avg_precision_5 == 0.0
        assert entry.avg_recall_10 == 0.0
        assert entry.avg_faithfulness == 0.0
        assert entry.avg_answer_relevancy == 0.0
        assert entry.avg_context_precision == 0.0
        assert entry.avg_context_recall == 0.0
        assert entry.n_queries == 0
        assert entry.benchmark == ""
        assert entry.timestamp == ""
        assert entry.metadata == {}


class TestUpdateAndGetLeaderboard:
    """Tests for update_leaderboard and get_leaderboard with mocked Redis."""

    def _make_redis(self) -> MagicMock:
        """Create a mock Redis client that simulates a sorted set."""
        store: dict[str, dict[str, float]] = {}

        redis = MagicMock()

        def zadd(key: str, mapping: dict[str, float]) -> None:
            if key not in store:
                store[key] = {}
            store[key].update(mapping)

        def zrevrange(key: str, start: int, stop: int) -> list[str]:
            if key not in store:
                return []
            sorted_items = sorted(store[key].items(), key=lambda x: x[1], reverse=True)
            # stop is inclusive in Redis
            return [item[0] for item in sorted_items[start : stop + 1]]

        def zremrangebyrank(key: str, start: int, stop: int) -> int:
            if key not in store:
                return 0
            sorted_items = sorted(store[key].items(), key=lambda x: x[1])
            to_remove = sorted_items[start:stop + 1] if stop >= 0 else sorted_items[start:max(0, len(sorted_items) + stop + 1)]
            for member, _ in to_remove:
                del store[key][member]
            return len(to_remove)

        def zcard(key: str) -> int:
            return len(store.get(key, {}))

        def delete(key: str) -> None:
            store.pop(key, None)

        redis.zadd = zadd
        redis.zrevrange = zrevrange
        redis.zremrangebyrank = zremrangebyrank
        redis.zcard = zcard
        redis.delete = delete
        redis._store = store

        return redis

    def test_update_and_get(self) -> None:
        redis = self._make_redis()
        entry = LeaderboardEntry(
            pipeline="hybrid_reranked",
            avg_ndcg_5=0.85,
            avg_mrr=0.9,
            avg_faithfulness=0.8,
            avg_answer_relevancy=0.75,
            n_queries=50,
            benchmark="beir_subset.jsonl",
        )

        update_leaderboard(entry, redis)
        results = get_leaderboard(redis, top_k=10)

        assert len(results) == 1
        assert results[0].pipeline == "hybrid_reranked"
        assert results[0].avg_ndcg_5 == 0.85
        assert results[0].n_queries == 50

    def test_leaderboard_sorted_by_score(self) -> None:
        redis = self._make_redis()

        low = LeaderboardEntry(pipeline="vector_only", avg_ndcg_5=0.3, avg_mrr=0.2)
        mid = LeaderboardEntry(pipeline="hybrid", avg_ndcg_5=0.6, avg_mrr=0.5)
        high = LeaderboardEntry(
            pipeline="hybrid_reranked",
            avg_ndcg_5=0.9,
            avg_mrr=0.85,
            avg_faithfulness=0.8,
            avg_answer_relevancy=0.75,
        )

        update_leaderboard(low, redis)
        update_leaderboard(mid, redis)
        update_leaderboard(high, redis)

        results = get_leaderboard(redis, top_k=10)
        assert len(results) == 3
        assert results[0].pipeline == "hybrid_reranked"
        assert results[1].pipeline == "hybrid"
        assert results[2].pipeline == "vector_only"
        # Verify descending score order
        scores = [r.composite_score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_clear_leaderboard(self) -> None:
        redis = self._make_redis()
        entry = LeaderboardEntry(pipeline="test", avg_ndcg_5=0.5)
        update_leaderboard(entry, redis)

        count = clear_leaderboard(redis)
        assert count == 1
        assert get_leaderboard(redis) == []

    def test_get_leaderboard_empty(self) -> None:
        redis = self._make_redis()
        results = get_leaderboard(redis, top_k=20)
        assert results == []

    def test_update_leaderboard_no_redis(self) -> None:
        """update_leaderboard with None redis should not raise."""
        entry = LeaderboardEntry(pipeline="test")
        update_leaderboard(entry, None)  # should just log warning

    def test_get_leaderboard_no_redis(self) -> None:
        """get_leaderboard with None redis returns empty list."""
        assert get_leaderboard(None) == []

    def test_clear_leaderboard_no_redis(self) -> None:
        """clear_leaderboard with None redis returns 0."""
        assert clear_leaderboard(None) == 0

    def test_get_leaderboard_handles_corrupt_data(self) -> None:
        """Corrupt JSON entries in Redis are skipped gracefully."""
        redis = self._make_redis()
        # Inject corrupt data directly
        redis._store[_LEADERBOARD_KEY] = {
            "not valid json": 1.0,
            json.dumps({"pipeline": "valid", "avg_ndcg_5": 0.5}): 0.5,
        }

        results = get_leaderboard(redis, top_k=10)
        # Only the valid entry should be returned
        assert len(results) == 1
        assert results[0].pipeline == "valid"
