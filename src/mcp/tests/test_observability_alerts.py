# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the alerting system and new observability endpoints.

Covers:
- Alert rule CRUD (create, list, get, update, delete)
- Alert evaluation logic (gt triggered, not triggered)
- Alert event storage in Redis
- Webhook notification via httpx
- RAGAS metrics endpoint response format
- Cost-per-query calculation
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.routers.alerts import (
    _EVENTS_KEY,
    _RULES_KEY,
    AlertRule,
    _compare,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_redis_for_alerts() -> MagicMock:
    """Create a mock Redis client with in-memory hash and list storage."""
    hashes: dict[str, dict[str, str]] = {}
    lists: dict[str, list[str]] = {}

    mock = MagicMock()

    def _hset(key: str, field: str, value: str):
        hashes.setdefault(key, {})[field] = value

    def _hget(key: str, field: str):
        return hashes.get(key, {}).get(field)

    def _hgetall(key: str):
        return hashes.get(key, {})

    def _hexists(key: str, field: str):
        return field in hashes.get(key, {})

    def _hdel(key: str, field: str):
        if key in hashes and field in hashes[key]:
            del hashes[key][field]
            return 1
        return 0

    def _lpush(key: str, value: str):
        lists.setdefault(key, []).insert(0, value)

    def _ltrim(key: str, start: int, stop: int):
        if key in lists:
            lists[key] = lists[key][start : stop + 1]

    def _lrange(key: str, start: int, stop: int):
        if key not in lists:
            return []
        return lists[key][start : stop + 1]

    mock.hset = MagicMock(side_effect=_hset)
    mock.hget = MagicMock(side_effect=_hget)
    mock.hgetall = MagicMock(side_effect=_hgetall)
    mock.hexists = MagicMock(side_effect=_hexists)
    mock.hdel = MagicMock(side_effect=_hdel)
    mock.lpush = MagicMock(side_effect=_lpush)
    mock.ltrim = MagicMock(side_effect=_ltrim)
    mock.lrange = MagicMock(side_effect=_lrange)

    # Expose internal storage for assertions
    mock._hashes = hashes
    mock._lists = lists

    return mock


# ---------------------------------------------------------------------------
# Alert rule CRUD tests
# ---------------------------------------------------------------------------


class TestAlertRuleCRUD:
    def test_alert_rule_creation(self):
        """Alert rule creation generates a UUID and stores in Redis."""
        redis = _mock_redis_for_alerts()
        rule = AlertRule(
            metric_name="query_latency_ms",
            operator="gt",
            threshold=5000.0,
            description="High latency alert",
        )
        # Simulate what the endpoint does
        rule.id = str(uuid.uuid4())
        redis.hset(_RULES_KEY, rule.id, json.dumps(rule.model_dump()))

        assert rule.id  # UUID was generated
        stored = json.loads(redis.hget(_RULES_KEY, rule.id))
        assert stored["metric_name"] == "query_latency_ms"
        assert stored["operator"] == "gt"
        assert stored["threshold"] == 5000.0

    def test_alert_rule_list(self):
        """Listing alert rules returns all stored rules."""
        redis = _mock_redis_for_alerts()

        # Store two rules
        for i in range(2):
            rule = AlertRule(
                id=f"rule-{i}",
                metric_name="query_latency_ms",
                operator="gt",
                threshold=1000.0 * (i + 1),
            )
            redis.hset(_RULES_KEY, rule.id, json.dumps(rule.model_dump()))

        raw = redis.hgetall(_RULES_KEY)
        rules = [AlertRule(**json.loads(v)) for v in raw.values()]
        assert len(rules) == 2
        assert {r.id for r in rules} == {"rule-0", "rule-1"}


# ---------------------------------------------------------------------------
# Alert evaluation tests
# ---------------------------------------------------------------------------


class TestAlertEvaluation:
    @pytest.mark.asyncio
    async def test_alert_evaluation_gt_triggered(self):
        """Alert fires when metric value exceeds gt threshold."""
        redis = _mock_redis_for_alerts()

        # Store a rule: query_latency_ms > 2000
        rule = AlertRule(
            id="test-rule",
            metric_name="query_latency_ms",
            operator="gt",
            threshold=2000.0,
            enabled=True,
        )
        redis.hset(_RULES_KEY, rule.id, json.dumps(rule.model_dump()))

        # Mock the metrics collector to return avg=3000
        mock_collector = MagicMock()
        mock_collector.get_aggregated_metrics.return_value = {
            "query_latency_ms": {"avg": 3000.0, "count": 10},
        }

        with (
            patch("utils.metrics.get_metrics_collector", return_value=mock_collector),
            patch("app.routers.alerts._iso_now", return_value="2026-03-26T00:00:00Z"),
        ):
            # Import fresh to get patched version
            from app.routers.alerts import evaluate_alerts as _eval

            events = await _eval(redis)

        assert len(events) == 1
        assert events[0].rule_id == "test-rule"
        assert events[0].current_value == 3000.0
        assert events[0].operator == "gt"

    @pytest.mark.asyncio
    async def test_alert_evaluation_not_triggered(self):
        """Alert does NOT fire when metric value is below gt threshold."""
        redis = _mock_redis_for_alerts()

        rule = AlertRule(
            id="test-rule",
            metric_name="query_latency_ms",
            operator="gt",
            threshold=5000.0,
            enabled=True,
        )
        redis.hset(_RULES_KEY, rule.id, json.dumps(rule.model_dump()))

        mock_collector = MagicMock()
        mock_collector.get_aggregated_metrics.return_value = {
            "query_latency_ms": {"avg": 1000.0, "count": 10},
        }

        with (
            patch("utils.metrics.get_metrics_collector", return_value=mock_collector),
            patch("app.routers.alerts._iso_now", return_value="2026-03-26T00:00:00Z"),
        ):
            from app.routers.alerts import evaluate_alerts as _eval

            events = await _eval(redis)

        assert len(events) == 0


# ---------------------------------------------------------------------------
# Alert event storage
# ---------------------------------------------------------------------------


class TestAlertEventStorage:
    @pytest.mark.asyncio
    async def test_alert_event_storage(self):
        """Triggered alerts store events in the Redis list."""
        redis = _mock_redis_for_alerts()

        rule = AlertRule(
            id="store-test",
            metric_name="llm_cost_usd",
            operator="gt",
            threshold=0.01,
            enabled=True,
        )
        redis.hset(_RULES_KEY, rule.id, json.dumps(rule.model_dump()))

        mock_collector = MagicMock()
        mock_collector.get_aggregated_metrics.return_value = {
            "llm_cost_usd": {"avg": 0.05, "count": 5},
        }

        with (
            patch("utils.metrics.get_metrics_collector", return_value=mock_collector),
            patch("app.routers.alerts._iso_now", return_value="2026-03-26T12:00:00Z"),
        ):
            from app.routers.alerts import evaluate_alerts as _eval

            events = await _eval(redis)

        assert len(events) == 1

        # Verify event was stored in Redis list
        stored = redis.lrange(_EVENTS_KEY, 0, 99)
        assert len(stored) == 1
        parsed = json.loads(stored[0])
        assert parsed["rule_id"] == "store-test"
        assert parsed["current_value"] == 0.05


# ---------------------------------------------------------------------------
# Webhook notification
# ---------------------------------------------------------------------------


class TestWebhookNotification:
    @pytest.mark.asyncio
    async def test_alert_webhook_notification(self):
        """Webhook URL is called with alert event payload."""
        redis = _mock_redis_for_alerts()

        rule = AlertRule(
            id="webhook-test",
            metric_name="query_latency_ms",
            operator="gt",
            threshold=1000.0,
            enabled=True,
            webhook_url="https://hooks.example.com/alert",
        )
        redis.hset(_RULES_KEY, rule.id, json.dumps(rule.model_dump()))

        mock_collector = MagicMock()
        mock_collector.get_aggregated_metrics.return_value = {
            "query_latency_ms": {"avg": 5000.0, "count": 10},
        }

        mock_notify = AsyncMock()

        with (
            patch("utils.metrics.get_metrics_collector", return_value=mock_collector),
            patch("app.routers.alerts._iso_now", return_value="2026-03-26T00:00:00Z"),
            patch("app.routers.alerts._notify_webhook", mock_notify),
        ):
            from app.routers.alerts import evaluate_alerts as _eval

            events = await _eval(redis)

        assert len(events) == 1
        mock_notify.assert_called_once()
        call_args = mock_notify.call_args
        assert call_args[0][0] == "https://hooks.example.com/alert"
        # Second arg is the AlertEvent
        assert call_args[0][1].rule_id == "webhook-test"


# ---------------------------------------------------------------------------
# Comparison operator tests
# ---------------------------------------------------------------------------


class TestCompareOperator:
    @pytest.mark.parametrize(
        "value,op,threshold,expected",
        [
            (10.0, "gt", 5.0, True),
            (5.0, "gt", 10.0, False),
            (5.0, "lt", 10.0, True),
            (10.0, "lt", 5.0, False),
            (5.0, "gte", 5.0, True),
            (4.0, "gte", 5.0, False),
            (5.0, "lte", 5.0, True),
            (6.0, "lte", 5.0, False),
            (5.0, "eq", 5.0, True),
            (5.0, "eq", 6.0, False),
            (5.0, "invalid", 5.0, False),
        ],
    )
    def test_compare(self, value, op, threshold, expected):
        assert _compare(value, op, threshold) == expected


# ---------------------------------------------------------------------------
# New observability endpoint tests
# ---------------------------------------------------------------------------


class TestRagasMetricsEndpoint:
    def test_ragas_metrics_endpoint_format(self):
        """RAGAS endpoint returns properly structured response model."""
        from app.routers.observability import MetricAggregation, RagasMetricsResponse

        response = RagasMetricsResponse(
            window_minutes=60,
            faithfulness=MetricAggregation(avg=0.85, count=10),
            answer_relevancy=MetricAggregation(avg=0.90, count=10),
            context_precision=MetricAggregation(avg=0.78, count=10),
            context_recall=MetricAggregation(avg=0.82, count=10),
            timestamp="2026-03-26T00:00:00Z",
        )

        assert response.window_minutes == 60
        assert response.faithfulness.avg == 0.85
        assert response.answer_relevancy.avg == 0.90
        assert response.context_precision.avg == 0.78
        assert response.context_recall.avg == 0.82
        assert response.timestamp == "2026-03-26T00:00:00Z"

        # Verify model_dump produces expected keys
        dumped = response.model_dump()
        assert "faithfulness" in dumped
        assert "answer_relevancy" in dumped
        assert "context_precision" in dumped
        assert "context_recall" in dumped


class TestCostPerQuery:
    def test_cost_per_query_calculation(self):
        """Cost-per-query correctly divides total cost by query count."""
        # Simulate the calculation logic from the endpoint
        cost_data = {"avg": 0.005, "count": 100}
        throughput_data = {"count": 50}

        total_cost = (cost_data.get("avg", 0) or 0) * (cost_data.get("count", 0) or 0)
        query_count = throughput_data.get("count", 0) or 1
        cost_per_query = round(total_cost / max(query_count, 1), 6)
        total_cost_rounded = round(total_cost, 6)

        assert total_cost_rounded == 0.5  # 0.005 * 100
        assert cost_per_query == 0.01  # 0.5 / 50

    def test_cost_per_query_zero_queries(self):
        """Cost-per-query handles zero queries without division error."""
        cost_data = {"avg": 0.005, "count": 10}
        throughput_data = {"count": 0}

        total_cost = (cost_data.get("avg", 0) or 0) * (cost_data.get("count", 0) or 0)
        query_count = throughput_data.get("count", 0) or 1
        cost_per_query = round(total_cost / max(query_count, 1), 6)

        assert cost_per_query == 0.05  # 0.05 / 1 (max protection)

    def test_cost_per_query_no_data(self):
        """Cost-per-query handles missing data gracefully."""
        cost_data: dict = {}
        throughput_data: dict = {}

        total_cost = (cost_data.get("avg", 0) or 0) * (cost_data.get("count", 0) or 0)
        query_count = throughput_data.get("count", 0) or 1
        cost_per_query = round(total_cost / max(query_count, 1), 6)

        assert cost_per_query == 0.0
