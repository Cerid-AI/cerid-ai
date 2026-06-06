# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the chunks-per-answer soak metric: the emit side
(``core.utils.cache.record_chunks_per_answer``) and the collector side
(``scripts/k_program_metrics.metric_chunks_per_answer``).

The two must agree on the Redis key contract — the collector is dark
until the answer path emits, so an end-to-end round trip is the test
that actually protects the soak metric.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone

import pytest

from ._helpers import scripts_dir


def _make_fakeredis():
    """Real fakeredis client, or skip — the round trip needs real list ops."""
    try:
        import fakeredis

        return fakeredis.FakeRedis()
    except ImportError:  # pragma: no cover - CI always has fakeredis
        pytest.skip("fakeredis not installed")


def _load_collector():
    sd = scripts_dir()
    if sd is None:
        pytest.skip("scripts/ dir not reachable from test env")
    spec = importlib.util.spec_from_file_location(
        "k_program_metrics", sd / "k_program_metrics.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BUCKET = "%Y-%m-%d"


# --------------------------------------------------------------------------
# Emit side
# --------------------------------------------------------------------------


def test_compiled_summary_intent_routes_to_compiled_summary_stream():
    from core.utils.cache import record_chunks_per_answer

    redis = _make_fakeredis()
    now = datetime(2026, 6, 5, tzinfo=timezone.utc)
    record_chunks_per_answer(redis, intent="compiled_summary", chunk_count=3, now=now)

    key = f"cerid:metrics:chunks_per_answer:samples:compiled_summary:{now.strftime(_BUCKET)}"
    assert [int(v) for v in redis.lrange(key, 0, -1)] == [3]


def test_non_compiled_intent_routes_to_baseline_stream():
    from core.utils.cache import record_chunks_per_answer

    redis = _make_fakeredis()
    now = datetime(2026, 6, 5, tzinfo=timezone.utc)
    record_chunks_per_answer(redis, intent="specific_fact", chunk_count=7, now=now)

    baseline_key = f"cerid:metrics:chunks_per_answer:samples:baseline:{now.strftime(_BUCKET)}"
    compiled_key = f"cerid:metrics:chunks_per_answer:samples:compiled_summary:{now.strftime(_BUCKET)}"
    assert [int(v) for v in redis.lrange(baseline_key, 0, -1)] == [7]
    assert redis.lrange(compiled_key, 0, -1) == []


def test_emit_sets_a_ttl_on_the_daily_key():
    from core.utils.cache import record_chunks_per_answer

    redis = _make_fakeredis()
    now = datetime(2026, 6, 5, tzinfo=timezone.utc)
    record_chunks_per_answer(redis, intent="compiled_summary", chunk_count=2, now=now)

    key = f"cerid:metrics:chunks_per_answer:samples:compiled_summary:{now.strftime(_BUCKET)}"
    ttl = redis.ttl(key)
    assert ttl > 0


def test_emit_is_a_noop_when_redis_is_none():
    from core.utils.cache import record_chunks_per_answer

    # Must not raise — a metric write may never fail a user query.
    record_chunks_per_answer(None, intent="compiled_summary", chunk_count=4)


def test_emit_never_raises_when_redis_errors():
    from core.utils.cache import record_chunks_per_answer

    class _BoomRedis:
        def rpush(self, *_a, **_k):
            raise RuntimeError("redis down")

    record_chunks_per_answer(_BoomRedis(), intent="compiled_summary", chunk_count=4)


# --------------------------------------------------------------------------
# Collector side + end-to-end contract
# --------------------------------------------------------------------------


def test_loading_collector_does_not_leak_dotenv_into_environ():
    """Importing the collector must have NO env side effects — else it pollutes
    order-dependent tests (e.g. test_ollama_models reads OLLAMA_DEFAULT_MODEL).
    `.env` must load on CLI invocation only."""
    import os

    os.environ.pop("OLLAMA_DEFAULT_MODEL", None)
    _load_collector()
    assert "OLLAMA_DEFAULT_MODEL" not in os.environ, (
        "importing the collector leaked .env into os.environ"
    )


def test_collector_reports_none_until_a_week_of_data_exists():
    collector = _load_collector()
    redis = _make_fakeredis()
    result = collector.metric_chunks_per_answer(redis)
    assert result["available"] is True
    assert result["actual_reduction_pct"] is None


def test_end_to_end_emit_then_collect_computes_reduction():
    from core.utils.cache import record_chunks_per_answer

    collector = _load_collector()
    redis = _make_fakeredis()
    now = datetime(2026, 6, 12, tzinfo=timezone.utc)
    week_ago = now - timedelta(days=7)

    # Baseline arm a week ago: median 10 chunks/answer.
    for count in (8, 10, 12):
        record_chunks_per_answer(redis, intent="specific_fact", chunk_count=count, now=week_ago)
    # Compiled-summary arm today: median 6 chunks/answer → 40% reduction.
    for count in (5, 6, 7):
        record_chunks_per_answer(redis, intent="compiled_summary", chunk_count=count, now=now)

    result = collector.metric_chunks_per_answer(redis, now=now)
    assert result["baseline_median"] == 10.0
    assert result["current_median"] == 6.0
    assert result["actual_reduction_pct"] == 40.0
    assert result["meets_target"] is True


def test_collector_median_is_even_length_average():
    from core.utils.cache import record_chunks_per_answer

    collector = _load_collector()
    redis = _make_fakeredis()
    now = datetime(2026, 6, 12, tzinfo=timezone.utc)
    week_ago = now - timedelta(days=7)

    for count in (10, 10):
        record_chunks_per_answer(redis, intent="baseline_arm", chunk_count=count, now=week_ago)
    # Even number of samples → median is the average of the two middle values.
    for count in (4, 8):
        record_chunks_per_answer(redis, intent="compiled_summary", chunk_count=count, now=now)

    result = collector.metric_chunks_per_answer(redis, now=now)
    assert result["current_median"] == 6.0  # (4 + 8) / 2
    assert result["baseline_median"] == 10.0


# --------------------------------------------------------------------------
# Wiring: the answer path must actually emit
# --------------------------------------------------------------------------


async def test_answer_path_emits_intent_and_chunk_count(monkeypatch):
    """pkb_answer_with_citations records one sample with the route's intent
    and the retrieved-chunk count."""
    from unittest.mock import AsyncMock, MagicMock

    import app.mcp_tools.retrieval as retrieval_mod
    from core.retrieval.surface_router import SurfaceRoute

    # Deterministic route; no entity hint → skips the optional wiki lookup.
    route = SurfaceRoute(
        primary="wiki",
        surfaces=["wiki", "vector"],
        intent="compiled_summary",
        confidence=1.0,
        matched_entity_hint=None,
    )
    monkeypatch.setattr("core.retrieval.surface_router.route", lambda _q: route)

    results = [
        {"text": "alpha", "artifact_id": "a", "chunk_id": "c1"},
        {"text": "beta", "artifact_id": "b", "chunk_id": "c2"},
        {"text": "gamma", "artifact_id": "g", "chunk_id": "c3"},
    ]
    monkeypatch.setattr(
        "core.agents.query_agent.agent_query",
        AsyncMock(return_value={"results": results, "context": "ctx", "total_results": 3}),
    )
    monkeypatch.setattr(
        "core.utils.internal_llm.call_internal_llm",
        AsyncMock(return_value="an answer"),
    )
    monkeypatch.setattr(
        "core.agents.hallucination.extraction.extract_claims",
        AsyncMock(return_value=([], "noop")),
    )
    monkeypatch.setattr(retrieval_mod, "get_redis", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(retrieval_mod, "get_chroma", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(retrieval_mod, "get_neo4j", MagicMock(return_value=MagicMock()))

    spy = MagicMock()
    monkeypatch.setattr("core.utils.cache.record_chunks_per_answer", spy)

    await retrieval_mod.pkb_answer_with_citations("tell me about X")

    spy.assert_called_once()
    assert spy.call_args.kwargs["intent"] == "compiled_summary"
    assert spy.call_args.kwargs["chunk_count"] == 3
