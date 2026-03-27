# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the observability metrics system (Phase 47).

Covers:
- MetricsCollector: recording, retrieval, aggregation, cost breakdown
- Health score computation
- Cost estimation utility
- Edge cases (empty data, unknown models, expired entries)
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest

from utils.metrics import (
    _KEY_PREFIX,
    _METRIC_TTL_SECONDS,
    METRIC_NAMES,
    MetricPoint,
    MetricsCollector,
    estimate_cost,
)

# ---------------------------------------------------------------------------
# Mock Redis helper
# ---------------------------------------------------------------------------

def _mock_redis() -> MagicMock:
    """Create a mock Redis client backed by an in-memory dict."""
    sorted_sets: dict[str, list[tuple[str, float]]] = {}
    expiry: dict[str, int] = {}

    mock = MagicMock()

    def _zadd(key: str, mapping: dict[str, float]):
        if key not in sorted_sets:
            sorted_sets[key] = []
        for member, score in mapping.items():
            sorted_sets[key].append((member, score))
        # Keep sorted by score
        sorted_sets[key].sort(key=lambda x: x[1])

    def _zrangebyscore(key: str, min_score: float, max_score: float):
        if key not in sorted_sets:
            return []
        return [m for m, s in sorted_sets[key] if min_score <= s <= max_score]

    def _zremrangebyscore(key: str, min_score: float, max_score: float):
        if key not in sorted_sets:
            return 0
        before = len(sorted_sets[key])
        sorted_sets[key] = [(m, s) for m, s in sorted_sets[key] if not (min_score <= s <= max_score)]
        return before - len(sorted_sets[key])

    def _expire(key: str, ttl: int):
        expiry[key] = ttl

    mock.zadd = MagicMock(side_effect=_zadd)
    mock.zrangebyscore = MagicMock(side_effect=_zrangebyscore)
    mock.zremrangebyscore = MagicMock(side_effect=_zremrangebyscore)
    mock.expire = MagicMock(side_effect=_expire)
    mock._sorted_sets = sorted_sets
    mock._expiry = expiry

    return mock


# ---------------------------------------------------------------------------
# Tests: MetricsCollector
# ---------------------------------------------------------------------------


class TestMetricsCollector:
    def test_record_and_retrieve(self):
        redis = _mock_redis()
        mc = MetricsCollector(redis)

        mc.record_metric("query_latency_ms", 150.0)
        mc.record_metric("query_latency_ms", 250.0)

        points = mc.get_metrics("query_latency_ms", window_minutes=5)
        assert len(points) == 2
        assert points[0].value == 150.0
        assert points[1].value == 250.0

    def test_record_with_tags(self):
        redis = _mock_redis()
        mc = MetricsCollector(redis)

        mc.record_metric("llm_cost_usd", 0.003, tags={"model": "gpt-4o-mini"})

        points = mc.get_metrics("llm_cost_usd", window_minutes=5)
        assert len(points) == 1
        assert points[0].value == 0.003
        assert points[0].tags == {"model": "gpt-4o-mini"}

    def test_get_metrics_empty(self):
        redis = _mock_redis()
        mc = MetricsCollector(redis)

        points = mc.get_metrics("nonexistent_metric", window_minutes=60)
        assert points == []

    def test_redis_key_format(self):
        redis = _mock_redis()
        mc = MetricsCollector(redis)

        mc.record_metric("cache_hit_rate", 0.85)

        redis.zadd.assert_called_once()
        call_args = redis.zadd.call_args
        assert call_args[0][0] == f"{_KEY_PREFIX}:cache_hit_rate"

    def test_expire_called(self):
        redis = _mock_redis()
        mc = MetricsCollector(redis)

        mc.record_metric("query_latency_ms", 100.0)

        redis.expire.assert_called_once()
        key, ttl = redis.expire.call_args[0]
        assert key == f"{_KEY_PREFIX}:query_latency_ms"
        assert ttl == _METRIC_TTL_SECONDS

    def test_record_metric_redis_error(self):
        """Recording should not raise when Redis fails."""
        redis = MagicMock()
        redis.zadd.side_effect = ConnectionError("Redis down")
        mc = MetricsCollector(redis)

        # Should not raise
        mc.record_metric("query_latency_ms", 100.0)

    def test_get_metrics_redis_error(self):
        """Retrieval should return empty list when Redis fails."""
        redis = MagicMock()
        redis.zrangebyscore.side_effect = ConnectionError("Redis down")
        mc = MetricsCollector(redis)

        points = mc.get_metrics("query_latency_ms", window_minutes=60)
        assert points == []


class TestAggregation:
    def _make_points(self, values: list[float]) -> list[MetricPoint]:
        now = time.time()
        return [MetricPoint(timestamp=now + i, value=v) for i, v in enumerate(values)]

    def test_aggregate_basic(self):
        points = self._make_points([10, 20, 30, 40, 50])
        agg = MetricsCollector._aggregate(points)

        assert agg["count"] == 5
        assert agg["avg"] == 30.0
        assert agg["min"] == 10
        assert agg["max"] == 50
        assert agg["p50"] == 30  # middle of sorted [10, 20, 30, 40, 50]

    def test_aggregate_empty(self):
        agg = MetricsCollector._aggregate([])

        assert agg["count"] == 0
        assert agg["avg"] is None
        assert agg["p50"] is None
        assert agg["p95"] is None
        assert agg["p99"] is None

    def test_aggregate_single_value(self):
        points = self._make_points([42.0])
        agg = MetricsCollector._aggregate(points)

        assert agg["count"] == 1
        assert agg["avg"] == 42.0
        assert agg["p50"] == 42.0
        assert agg["p95"] == 42.0
        assert agg["p99"] == 42.0

    def test_aggregate_percentiles(self):
        # 100 values from 1-100
        values = list(range(1, 101))
        points = self._make_points(values)
        agg = MetricsCollector._aggregate(points)

        assert agg["count"] == 100
        assert agg["avg"] == 50.5
        assert agg["p50"] == 51  # index 50 of sorted 1-100
        assert agg["p95"] == 96  # index 95
        assert agg["p99"] == 100  # index 99

    def test_get_aggregated_metrics_covers_all(self):
        redis = _mock_redis()
        mc = MetricsCollector(redis)

        # Record a value for one metric
        mc.record_metric("query_latency_ms", 100.0)

        result = mc.get_aggregated_metrics(window_minutes=5)

        # All known metrics should be present
        for name in METRIC_NAMES:
            assert name in result

        # The one we recorded should have data
        assert result["query_latency_ms"]["count"] == 1
        assert result["query_latency_ms"]["avg"] == 100.0

        # Others should be empty
        assert result["cache_hit_rate"]["count"] == 0


class TestCostBreakdown:
    def test_cost_breakdown_by_model(self):
        redis = _mock_redis()
        mc = MetricsCollector(redis)

        mc.record_metric("llm_cost_usd", 0.003, tags={"model": "gpt-4o-mini"})
        mc.record_metric("llm_cost_usd", 0.015, tags={"model": "claude-sonnet-4.6"})
        mc.record_metric("llm_cost_usd", 0.002, tags={"model": "gpt-4o-mini"})

        breakdown = mc.get_cost_breakdown(window_minutes=5)

        assert breakdown["gpt-4o-mini"] == pytest.approx(0.005)
        assert breakdown["claude-sonnet-4.6"] == pytest.approx(0.015)

    def test_cost_breakdown_empty(self):
        redis = _mock_redis()
        mc = MetricsCollector(redis)

        breakdown = mc.get_cost_breakdown(window_minutes=5)
        assert breakdown == {}

    def test_cost_breakdown_no_model_tag(self):
        redis = _mock_redis()
        mc = MetricsCollector(redis)

        mc.record_metric("llm_cost_usd", 0.01)

        breakdown = mc.get_cost_breakdown(window_minutes=5)
        assert breakdown["unknown"] == pytest.approx(0.01)


class TestCleanupExpired:
    def test_cleanup_removes_old_entries(self):
        redis = _mock_redis()
        mc = MetricsCollector(redis)

        # Manually add an old entry
        old_ts = time.time() - _METRIC_TTL_SECONDS - 100
        key = mc._key("query_latency_ms")
        member = json.dumps({"v": 100.0, "t": {}, "ts": old_ts})
        redis._sorted_sets[key] = [(member, old_ts)]

        # Add a fresh entry
        mc.record_metric("query_latency_ms", 200.0)

        removed = mc.cleanup_expired("query_latency_ms")
        assert removed == 1

        # Fresh entry should remain
        points = mc.get_metrics("query_latency_ms", window_minutes=5)
        assert len(points) == 1
        assert points[0].value == 200.0


# ---------------------------------------------------------------------------
# Tests: estimate_cost
# ---------------------------------------------------------------------------


class TestEstimateCost:
    def test_known_model(self):
        cost = estimate_cost("openrouter/openai/gpt-4o-mini", 1000, 500)
        # input: 0.15/1M * 1000 = 0.00015
        # output: 0.60/1M * 500 = 0.0003
        assert cost == pytest.approx(0.00045)

    def test_strips_openrouter_prefix(self):
        cost1 = estimate_cost("openrouter/openai/gpt-4o-mini", 1000, 500)
        cost2 = estimate_cost("openai/gpt-4o-mini", 1000, 500)
        assert cost1 == cost2

    def test_unknown_model(self):
        cost = estimate_cost("unknown/model-xyz", 1000, 500)
        assert cost == 0.0

    def test_zero_tokens(self):
        cost = estimate_cost("openrouter/openai/gpt-4o-mini", 0, 0)
        assert cost == 0.0

    def test_claude_opus(self):
        cost = estimate_cost("openrouter/anthropic/claude-opus-4.6", 10000, 2000)
        # input: 5.0/1M * 10000 = 0.05
        # output: 25.0/1M * 2000 = 0.05
        assert cost == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# Tests: Health score computation
# ---------------------------------------------------------------------------


class TestHealthScore:
    def test_perfect_metrics(self):
        from app.routers.observability import _compute_health_score

        metrics = {
            "query_latency_ms": {"p95": 200.0},
            "cache_hit_rate": {"avg": 0.9},
            "verification_accuracy": {"avg": 0.95},
            "queries_per_minute": {"count": 100},
        }
        score, grade, factors = _compute_health_score(metrics)

        assert score >= 80
        assert grade in ("A", "B")
        assert "latency" in factors
        assert "cache" in factors
        assert "verification" in factors
        assert "throughput" in factors

    def test_no_data(self):
        from app.routers.observability import _compute_health_score

        metrics = {}
        score, grade, factors = _compute_health_score(metrics)

        # Should get a reasonable default score, not zero
        assert 30 <= score <= 70
        assert grade in ("C", "D")

    def test_high_latency_penalized(self):
        from app.routers.observability import _compute_health_score

        fast = {"query_latency_ms": {"p95": 100.0}}
        slow = {"query_latency_ms": {"p95": 8000.0}}

        fast_score, _, _ = _compute_health_score(fast)
        slow_score, _, _ = _compute_health_score(slow)

        assert fast_score > slow_score

    def test_grades(self):
        from app.routers.observability import _compute_health_score

        # Excellent metrics
        excellent = {
            "query_latency_ms": {"p95": 50.0},
            "cache_hit_rate": {"avg": 0.95},
            "verification_accuracy": {"avg": 0.98},
            "queries_per_minute": {"count": 200},
        }
        score, grade, _ = _compute_health_score(excellent)
        assert grade == "A"

    def test_grade_boundaries(self):
        from app.routers.observability import _compute_health_score

        # Very poor metrics
        poor = {
            "query_latency_ms": {"p95": 50000.0},
            "cache_hit_rate": {"avg": 0.0},
            "verification_accuracy": {"avg": 0.0},
            "queries_per_minute": {"count": 0},
        }
        score, grade, _ = _compute_health_score(poor)
        assert grade in ("D", "F")


# ---------------------------------------------------------------------------
# Tests: MetricPoint dataclass
# ---------------------------------------------------------------------------


class TestMetricPoint:
    def test_defaults(self):
        pt = MetricPoint(timestamp=1.0, value=42.0)
        assert pt.tags == {}

    def test_with_tags(self):
        pt = MetricPoint(timestamp=1.0, value=42.0, tags={"model": "test"})
        assert pt.tags["model"] == "test"


# ---------------------------------------------------------------------------
# Tests: METRIC_NAMES constant
# ---------------------------------------------------------------------------


class TestMetricNames:
    def test_expected_metrics_present(self):
        expected = {
            "query_latency_ms",
            "retrieval_latency_ms",
            "llm_latency_ms",
            "llm_cost_usd",
            "retrieval_ndcg",
            "cache_hit_rate",
            "verification_accuracy",
            "queries_per_minute",
            "ragas_faithfulness",
            "ragas_answer_relevancy",
            "ragas_context_precision",
            "ragas_context_recall",
        }
        assert METRIC_NAMES == expected

    def test_is_frozenset(self):
        assert isinstance(METRIC_NAMES, frozenset)
