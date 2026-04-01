# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for agents/audit.py — operation tracking and cost analytics."""

import asyncio
import json
from unittest.mock import MagicMock, patch

from agents.audit import (
    AVG_TOKENS,
    COST_PER_1K_TOKENS,
    MODEL_COST_RATES,
    audit,
    estimate_costs,
    get_activity_summary,
    get_conversation_analytics,
    get_ingestion_stats,
    get_query_patterns,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(**overrides):
    """Create a minimal audit log entry."""
    entry = {
        "timestamp": "2026-02-28T12:00:00",
        "event": "ingest",
        "domain": "coding",
        "filename": "test.py",
    }
    entry.update(overrides)
    return entry


# ---------------------------------------------------------------------------
# Tests: Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_cost_tiers_defined(self):
        assert "smart" in COST_PER_1K_TOKENS
        assert "pro" in COST_PER_1K_TOKENS
        assert "rerank" in COST_PER_1K_TOKENS

    def test_smart_tier_is_free(self):
        assert COST_PER_1K_TOKENS["smart"] == 0.0

    def test_avg_tokens_defined(self):
        assert "categorize_smart" in AVG_TOKENS
        assert "categorize_pro" in AVG_TOKENS

    def test_model_cost_rates_has_models(self):
        assert len(MODEL_COST_RATES) > 0
        for model, rates in MODEL_COST_RATES.items():
            assert "input" in rates
            assert "output" in rates


# ---------------------------------------------------------------------------
# Tests: get_activity_summary
# ---------------------------------------------------------------------------

class TestGetActivitySummary:
    @patch("agents.audit.get_log")
    @patch("agents.audit.utcnow")
    def test_empty_log(self, mock_utcnow, mock_get_log):
        from datetime import datetime
        mock_utcnow.return_value = datetime(2026, 2, 28, 12, 0, 0)
        mock_get_log.return_value = []

        result = get_activity_summary(MagicMock(), hours=24)
        assert result["total_events"] == 0
        assert result["event_breakdown"] == {}
        assert result["domain_breakdown"] == {}

    @patch("agents.audit.get_log")
    @patch("agents.audit.utcnow")
    def test_counts_recent_events(self, mock_utcnow, mock_get_log):
        from datetime import datetime
        mock_utcnow.return_value = datetime(2026, 2, 28, 12, 0, 0)
        mock_get_log.return_value = [
            _make_entry(event="ingest", domain="coding"),
            _make_entry(event="ingest", domain="finance"),
            _make_entry(event="query", domain="coding"),
        ]

        result = get_activity_summary(MagicMock(), hours=24)
        assert result["total_events"] == 3
        assert result["event_breakdown"]["ingest"] == 2
        assert result["event_breakdown"]["query"] == 1
        assert result["domain_breakdown"]["coding"] == 2

    @patch("agents.audit.get_log")
    @patch("agents.audit.utcnow")
    def test_filters_by_time_window(self, mock_utcnow, mock_get_log):
        from datetime import datetime
        mock_utcnow.return_value = datetime(2026, 2, 28, 12, 0, 0)
        mock_get_log.return_value = [
            _make_entry(timestamp="2026-02-28T11:00:00"),  # Within 24h
            _make_entry(timestamp="2026-02-27T13:00:00"),  # Within 24h (after 12:00 cutoff)
            _make_entry(timestamp="2026-02-20T00:00:00"),  # Outside 24h
        ]

        result = get_activity_summary(MagicMock(), hours=24)
        assert result["total_events"] == 2

    @patch("agents.audit.get_log")
    @patch("agents.audit.utcnow")
    def test_hourly_timeline(self, mock_utcnow, mock_get_log):
        from datetime import datetime
        mock_utcnow.return_value = datetime(2026, 2, 28, 12, 0, 0)
        mock_get_log.return_value = [
            _make_entry(timestamp="2026-02-28T10:15:00"),
            _make_entry(timestamp="2026-02-28T10:45:00"),
            _make_entry(timestamp="2026-02-28T11:00:00"),
        ]

        result = get_activity_summary(MagicMock(), hours=24)
        assert result["hourly_timeline"]["2026-02-28T10"] == 2
        assert result["hourly_timeline"]["2026-02-28T11"] == 1

    @patch("agents.audit.get_log")
    @patch("agents.audit.utcnow")
    def test_captures_failures(self, mock_utcnow, mock_get_log):
        from datetime import datetime
        mock_utcnow.return_value = datetime(2026, 2, 28, 12, 0, 0)
        mock_get_log.return_value = [
            _make_entry(event="ingest"),
            _make_entry(event="ingest_error"),
            _make_entry(event="parse_error"),
        ]

        result = get_activity_summary(MagicMock(), hours=24)
        assert len(result["recent_failures"]) == 2


# ---------------------------------------------------------------------------
# Tests: get_ingestion_stats
# ---------------------------------------------------------------------------

class TestGetIngestionStats:
    @patch("agents.audit.get_log")
    def test_basic_stats(self, mock_get_log):
        mock_get_log.return_value = [
            _make_entry(event="ingest", filename="a.py", chunks=5),
            _make_entry(event="ingest", filename="b.txt", chunks=3),
            _make_entry(event="duplicate", domain="coding"),
        ]

        result = get_ingestion_stats(MagicMock())
        assert result["total_ingests"] == 2
        assert result["total_duplicates"] == 1
        assert result["duplicate_rate"] == round(1 / 3, 3)

    @patch("agents.audit.get_log")
    def test_file_type_distribution(self, mock_get_log):
        mock_get_log.return_value = [
            _make_entry(event="ingest", filename="a.py"),
            _make_entry(event="ingest", filename="b.py"),
            _make_entry(event="ingest", filename="c.md"),
        ]

        result = get_ingestion_stats(MagicMock())
        assert result["file_type_distribution"]["py"] == 2
        assert result["file_type_distribution"]["md"] == 1

    @patch("agents.audit.get_log")
    def test_avg_chunks(self, mock_get_log):
        mock_get_log.return_value = [
            _make_entry(event="ingest", chunks=4),
            _make_entry(event="ingest", chunks=6),
        ]

        result = get_ingestion_stats(MagicMock())
        assert result["avg_chunks_per_file"] == 5.0

    @patch("agents.audit.get_log")
    def test_no_ingests(self, mock_get_log):
        mock_get_log.return_value = []

        result = get_ingestion_stats(MagicMock())
        assert result["total_ingests"] == 0
        assert result["duplicate_rate"] == 0.0
        assert result["avg_chunks_per_file"] == 0

    @patch("agents.audit.get_log")
    def test_duplicate_status_counted(self, mock_get_log):
        """Entries with status='duplicate' should also count as duplicates."""
        mock_get_log.return_value = [
            _make_entry(event="ingest"),
            _make_entry(event="other", status="duplicate"),
        ]

        result = get_ingestion_stats(MagicMock())
        assert result["total_duplicates"] == 1


# ---------------------------------------------------------------------------
# Tests: estimate_costs
# ---------------------------------------------------------------------------

class TestEstimateCosts:
    @patch("agents.audit.get_log")
    @patch("agents.audit.utcnow")
    def test_cost_calculation(self, mock_utcnow, mock_get_log):
        from datetime import datetime
        mock_utcnow.return_value = datetime(2026, 2, 28, 12, 0, 0)

        mock_get_log.return_value = [
            _make_entry(event="ingest", categorize_mode="smart"),
            _make_entry(event="ingest", categorize_mode="pro"),
            _make_entry(event="query"),
        ]

        result = estimate_costs(MagicMock(), hours=720)
        assert result["operations"]["categorize_smart"] == 1
        assert result["operations"]["categorize_pro"] == 1
        assert result["operations"]["rerank"] == 1

        # Verify token math
        assert result["estimated_tokens"]["smart"] == AVG_TOKENS["categorize_smart"]
        assert result["estimated_tokens"]["pro"] == AVG_TOKENS["categorize_pro"]
        assert result["estimated_tokens"]["rerank"] == AVG_TOKENS["rerank"]

    @patch("agents.audit.get_log")
    @patch("agents.audit.utcnow")
    def test_smart_tier_zero_cost(self, mock_utcnow, mock_get_log):
        from datetime import datetime
        mock_utcnow.return_value = datetime(2026, 2, 28, 12, 0, 0)

        mock_get_log.return_value = [
            _make_entry(event="ingest", categorize_mode="smart"),
        ]

        result = estimate_costs(MagicMock())
        assert result["estimated_cost_usd"]["smart"] == 0.0

    @patch("agents.audit.get_log")
    @patch("agents.audit.utcnow")
    def test_no_operations(self, mock_utcnow, mock_get_log):
        from datetime import datetime
        mock_utcnow.return_value = datetime(2026, 2, 28, 12, 0, 0)
        mock_get_log.return_value = []

        result = estimate_costs(MagicMock())
        assert result["estimated_cost_usd"]["total"] == 0.0
        assert result["estimated_tokens"]["total"] == 0


# ---------------------------------------------------------------------------
# Tests: get_query_patterns
# ---------------------------------------------------------------------------

class TestGetQueryPatterns:
    @patch("agents.audit.get_log")
    def test_basic_patterns(self, mock_get_log):
        mock_get_log.return_value = [
            _make_entry(event="query", domain="coding", results=5),
            _make_entry(event="agent_query", domain="coding,finance", results=10),
        ]

        result = get_query_patterns(MagicMock())
        assert result["total_queries"] == 2
        assert result["domain_frequency"]["coding"] == 2
        assert result["domain_frequency"]["finance"] == 1

    @patch("agents.audit.get_log")
    def test_avg_results(self, mock_get_log):
        mock_get_log.return_value = [
            _make_entry(event="query", results=4),
            _make_entry(event="query", results=8),
        ]

        result = get_query_patterns(MagicMock())
        assert result["avg_results_per_query"] == 6.0

    @patch("agents.audit.get_log")
    def test_no_queries(self, mock_get_log):
        mock_get_log.return_value = [
            _make_entry(event="ingest"),  # Not a query
        ]

        result = get_query_patterns(MagicMock())
        assert result["total_queries"] == 0
        assert result["domain_frequency"] == {}
        assert result["avg_results_per_query"] == 0


# ---------------------------------------------------------------------------
# Tests: get_conversation_analytics
# ---------------------------------------------------------------------------

class TestGetConversationAnalytics:
    def test_redis_scan_error_returns_empty(self):
        redis = MagicMock()
        redis.scan_iter.side_effect = Exception("Connection refused")

        result = get_conversation_analytics(redis)
        assert result["total_conversations"] == 0
        assert result["total_turns"] == 0
        assert result["total_cost_usd"] == 0.0

    def test_no_conversations(self):
        redis = MagicMock()
        redis.scan_iter.return_value = iter([])

        result = get_conversation_analytics(redis)
        assert result["total_conversations"] == 0
        assert result["total_turns"] == 0

    def test_aggregates_model_stats(self):
        redis = MagicMock()
        redis.scan_iter.return_value = iter(["conv:abc:metrics"])

        entries = [
            json.dumps({
                "model": "anthropic/claude-sonnet-4",
                "input_tokens": 1000,
                "output_tokens": 500,
                "latency_ms": 200,
            }),
            json.dumps({
                "model": "anthropic/claude-sonnet-4",
                "input_tokens": 2000,
                "output_tokens": 1000,
                "latency_ms": 300,
            }),
        ]
        redis.lrange.return_value = entries

        result = get_conversation_analytics(redis)
        assert result["total_conversations"] == 1
        assert result["total_turns"] == 2

        model_stats = result["models"]["anthropic/claude-sonnet-4"]
        assert model_stats["turns"] == 2
        assert model_stats["input_tokens"] == 3000
        assert model_stats["output_tokens"] == 1500
        assert model_stats["avg_latency_ms"] == 250

    def test_unknown_model_fallback_rate(self):
        redis = MagicMock()
        redis.scan_iter.return_value = iter(["conv:abc:metrics"])

        entries = [
            json.dumps({
                "model": "unknown/new-model",
                "input_tokens": 1000,
                "output_tokens": 500,
                "latency_ms": 100,
            }),
        ]
        redis.lrange.return_value = entries

        result = get_conversation_analytics(redis)
        model_stats = result["models"]["unknown/new-model"]
        # Fallback rate: input=0.001, output=0.005
        expected_cost = (1000 / 1000) * 0.001 + (500 / 1000) * 0.005
        assert model_stats["cost_usd"] == round(expected_cost, 4)

    def test_strips_openrouter_prefix(self):
        redis = MagicMock()
        redis.scan_iter.return_value = iter(["conv:abc:metrics"])

        entries = [
            json.dumps({
                "model": "openrouter/anthropic/claude-sonnet-4",
                "input_tokens": 100,
                "output_tokens": 50,
                "latency_ms": 100,
            }),
        ]
        redis.lrange.return_value = entries

        result = get_conversation_analytics(redis)
        assert "anthropic/claude-sonnet-4" in result["models"]


# ---------------------------------------------------------------------------
# Tests: audit (main function)
# ---------------------------------------------------------------------------

class TestAudit:
    @patch("agents.audit.get_conversation_analytics")
    @patch("agents.audit.get_query_patterns")
    @patch("agents.audit.estimate_costs")
    @patch("agents.audit.get_ingestion_stats")
    @patch("agents.audit.get_activity_summary")
    def test_all_reports(self, mock_activity, mock_ingest, mock_costs, mock_queries, mock_conv):
        mock_activity.return_value = {"total_events": 0}
        mock_ingest.return_value = {"total_ingests": 0}
        mock_costs.return_value = {"estimated_cost_usd": {"total": 0}}
        mock_queries.return_value = {"total_queries": 0}
        mock_conv.return_value = {"total_conversations": 0}

        result = asyncio.get_event_loop().run_until_complete(
            audit(MagicMock())
        )
        assert "timestamp" in result
        assert "activity" in result
        assert "ingestion" in result
        assert "costs" in result
        assert "queries" in result
        assert "conversations" in result

    @patch("agents.audit.get_activity_summary")
    def test_single_report(self, mock_activity):
        mock_activity.return_value = {"total_events": 5}

        result = asyncio.get_event_loop().run_until_complete(
            audit(MagicMock(), reports=["activity"])
        )
        assert "activity" in result
        assert "ingestion" not in result
        assert "costs" not in result
