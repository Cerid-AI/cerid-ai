# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the observability metrics system.

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
        # 100 values from 1-100.
        #
        # These expectations changed on 2026-08-02. The previous ones (p95=96,
        # p99=100) pinned an off-by-one: the old code indexed ``values[int(n*p)]``
        # off ``n`` rather than ``n - 1``, biasing one rank high. p99=100 was the
        # tell — it is the *max*, and for n <= 100 that assertion could never have
        # read anything else. Nearest-rank over (n - 1) is what
        # ``app/processor/metrics.py::_percentile`` has always used.
        values = list(range(1, 101))
        points = self._make_points(values)
        agg = MetricsCollector._aggregate(points)

        assert agg["count"] == 100
        assert agg["avg"] == 50.5
        assert agg["p50"] == 51
        assert agg["p95"] == 95
        assert agg["p99"] == 99
        # A real percentile must be distinguishable from the max; the old
        # formula collapsed p95 to max for every n <= 20 and p99 for n <= 100.
        assert agg["p99"] < agg["max"]

    def test_p95_is_distinguishable_from_max_on_a_20_sample_window(self):
        # The regression that motivated the fix: the old formula returned the
        # max for p95 on every n <= 20, so a dashboard showing p95 and max
        # showed one number twice and could never distinguish a single slow
        # outlier from sustained slowness.
        #
        # Below ~11 samples p95 legitimately IS the top sample — that is the
        # percentile, not a bug — so 20 is the smallest honest witness.
        agg = MetricsCollector._aggregate(self._make_points(list(range(1, 21))))
        assert agg["max"] == 20
        assert agg["p95"] == 19

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

        # No data → score defaults to 0
        assert score == 0
        assert grade == "F"

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
            "cache_invalidation_count",
            "cache_stale_hit_count",
            "verification_accuracy",
            "queries_per_minute",
            "ragas_faithfulness",
            "ragas_answer_relevancy",
            "ragas_context_precision",
            "ragas_context_recall",
            "mcp_tool_call",
            "mcp_tool_call_duration_ms",
        }
        assert METRIC_NAMES == expected

    def test_is_frozenset(self):
        assert isinstance(METRIC_NAMES, frozenset)


# ---------------------------------------------------------------------------
# Tests: /observability/restarts (Workstream A Phase 1.3)
# ---------------------------------------------------------------------------


class TestRestartsEndpoint:
    @pytest.mark.asyncio
    async def test_returns_process_state_when_redis_unavailable(self, monkeypatch):
        """Endpoint must always return in-process state even if Redis is down."""
        from app.routers.observability import (
            _PROCESS_START_ISO,
            get_restart_info,
        )

        def _broken_get_redis():
            raise RuntimeError("redis pool not initialised")

        monkeypatch.setattr(
            "app.deps.get_redis", _broken_get_redis, raising=False
        )
        result = await get_restart_info()
        assert result["process_start_iso"] == _PROCESS_START_ISO
        assert isinstance(result["uptime_seconds"], float)
        assert result["uptime_seconds"] >= 0
        assert result["restart_count"] is None
        assert result["last_restart_iso"] is None

    @pytest.mark.asyncio
    async def test_returns_redis_counter_when_available(self, monkeypatch):
        from app.routers.observability import get_restart_info

        fake_redis = MagicMock()
        fake_redis.get.side_effect = lambda key: {
            "cerid:mcp:restart_count": b"42",
            "cerid:mcp:last_restart_iso": b"2026-05-07T12:00:00Z",
        }.get(key)
        monkeypatch.setattr(
            "app.deps.get_redis", lambda: fake_redis, raising=False
        )
        result = await get_restart_info()
        assert result["restart_count"] == 42
        assert result["last_restart_iso"] == "2026-05-07T12:00:00Z"

    def test_increment_swallows_redis_failure(self, monkeypatch):
        """increment_restart_counter must not raise on Redis errors."""
        from app.routers.observability import increment_restart_counter

        def _broken_get_redis():
            raise RuntimeError("redis down")

        monkeypatch.setattr(
            "app.deps.get_redis", _broken_get_redis, raising=False
        )
        assert increment_restart_counter() is None

    def test_increment_returns_redis_value(self, monkeypatch):
        from app.routers.observability import increment_restart_counter

        fake_redis = MagicMock()
        fake_redis.incr.return_value = 7
        monkeypatch.setattr(
            "app.deps.get_redis", lambda: fake_redis, raising=False
        )
        assert increment_restart_counter() == 7
        fake_redis.incr.assert_called_once_with("cerid:mcp:restart_count")
        fake_redis.set.assert_called_once()


# ---------------------------------------------------------------------------
# GET /observability/verification-rates (Phase 0.4a)
# ---------------------------------------------------------------------------


class TestVerificationRatesEndpoint:
    def test_returns_zeroed_shape_when_no_data(self, monkeypatch):
        import fakeredis

        from app.routers.observability import get_verification_rates_endpoint

        fake = fakeredis.FakeRedis(decode_responses=True)
        monkeypatch.setattr("app.deps.get_redis", lambda: fake, raising=False)

        result = get_verification_rates_endpoint()
        assert result["today"]["claims_total"] == 0
        assert result["today"]["timeout_rate"] is None
        assert result["last_7d"]["claims_total"] == 0
        assert "timestamp" in result

    def test_reflects_recorded_counters(self, monkeypatch):
        import fakeredis

        from app.observability.verification_metrics import record_verification_report
        from app.routers.observability import get_verification_rates_endpoint

        fake = fakeredis.FakeRedis(decode_responses=True)
        monkeypatch.setattr("app.deps.get_redis", lambda: fake, raising=False)

        record_verification_report(claims_total=20, uncertain_count=5, timeout_count=2)

        result = get_verification_rates_endpoint()
        assert result["today"]["claims_total"] == 20
        assert result["today"]["uncertain_rate"] == pytest.approx(0.25)
        assert result["today"]["timeout_rate"] == pytest.approx(0.1)

    def test_degrades_gracefully_when_redis_unavailable(self, monkeypatch):
        from app.routers.observability import get_verification_rates_endpoint

        def _broken():
            raise RuntimeError("redis down")

        monkeypatch.setattr("app.deps.get_redis", _broken, raising=False)

        result = get_verification_rates_endpoint()
        assert result["today"]["claims_total"] == 0
        assert result["today"]["timeout_rate"] is None
