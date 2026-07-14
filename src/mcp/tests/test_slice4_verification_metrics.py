# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Slice 4 — verification trust + observability (RAG Quality Program 2026-06-12).

Pins three contracts:

- **Phase 4.2** — a temporal (current/recency) claim supported only by KB
  evidence older than the verification staleness window, with live
  verification inconclusive, returns ``uncertain`` / ``stale_evidence`` —
  NOT ``verified`` on stale data. Fresh evidence and non-temporal claims
  keep the verified verdict.
- **Phase 4.3** — the three quality metrics declared in ``METRIC_NAMES``
  (``verification_accuracy``, ``cache_hit_rate``, ``retrieval_ndcg``) are
  actually recorded into the time-series collector ``/observability/quality``
  reads.
- **§7.1** — ``/health`` surfaces the knowledge-pack registry path + count
  so a path-resolution regression is visible in the health probe.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agents.hallucination.verification import (
    _evidence_is_stale,
    _stale_evidence_verdict,
    verify_claim,
)
from core.utils.time import utcnow


def _iso_days_ago(days: int) -> str:
    return (utcnow() - timedelta(days=days)).replace(tzinfo=None).isoformat()


# ---------------------------------------------------------------------------
# Phase 4.2 — staleness helper
# ---------------------------------------------------------------------------


class TestEvidenceStaleness:
    def test_old_created_at_is_stale(self):
        assert _evidence_is_stale({"created_at": _iso_days_ago(60)}, window_days=7) is True

    def test_recent_created_at_is_fresh(self):
        assert _evidence_is_stale({"created_at": _iso_days_ago(2)}, window_days=7) is False

    def test_falls_back_to_ingested_at(self):
        assert _evidence_is_stale({"ingested_at": _iso_days_ago(60)}, window_days=7) is True

    def test_missing_date_is_not_stale(self):
        # Conservative: absence of a date never manufactures doubt.
        assert _evidence_is_stale({"relevance": 0.9}, window_days=7) is False

    def test_unparseable_date_is_not_stale(self):
        assert _evidence_is_stale({"created_at": "not-a-date"}, window_days=7) is False

    def test_zulu_suffix_parses(self):
        assert _evidence_is_stale(
            {"created_at": "2024-01-01T00:00:00Z"}, window_days=7
        ) is True


class TestStaleEvidenceVerdict:
    def test_verdict_shape(self):
        v = _stale_evidence_verdict(
            "ETH is $2184 now",
            {"created_at": "2024-05-17T00:00:00", "artifact_id": "a1", "content": "x"},
            similarity=0.9,
        )
        assert v["status"] == "uncertain"
        assert v["stale_evidence"] is True
        assert "stale_evidence" in v["reason"]
        assert v["evidence_date"] == "2024-05-17"
        assert v["verification_method"] == "kb_stale_evidence"
        # Uncertain confidence must not cross into verified territory.
        assert v["similarity"] <= 0.64


# ---------------------------------------------------------------------------
# Phase 4.2 — verify_claim integration
# ---------------------------------------------------------------------------


_TEMPORAL_CLAIM = "The current price of Ethereum is $2,184.14"
_TIMELESS_CLAIM = "The Eiffel Tower is 330 meters tall"


def _kb_result(
    created_days_ago: int,
    *,
    relevance: float = 0.85,
    content: str = "ETH order filled at $2,184.14",
) -> list[dict]:
    # No artifact_id → skips the graph-boost branch, keeping the path clean.
    # ``content`` should support the claim under test so the numeric-
    # contradiction detector doesn't escalate (that's a separate path).
    return [{
        "relevance": relevance,
        "filename": "trades.csv",
        "domain": "finance",
        "content": content,
        "created_at": _iso_days_ago(created_days_ago),
    }]


@pytest.mark.asyncio
@patch("core.agents.hallucination.verification.cache_verdict", new_callable=AsyncMock)
@patch("core.agents.hallucination.verification.get_cached_verdict", new_callable=AsyncMock, return_value=None)
@patch("core.utils.nli.nli_score_async", new_callable=AsyncMock)
@patch("core.agents.hallucination.verification._verify_claim_externally", new_callable=AsyncMock)
@patch("core.agents.hallucination.verification._query_memories", new_callable=AsyncMock, return_value=[])
@patch("core.agents.query_agent.lightweight_kb_query", new_callable=AsyncMock)
async def test_temporal_claim_stale_evidence_inconclusive_external_is_uncertain(
    mock_kb, _mem, mock_ext, mock_nli, _getcache, _setcache,
    mock_chroma, mock_neo4j, mock_redis,
):
    """F1 failure mode: temporal claim, stale KB hit, web search inconclusive
    → uncertain/stale_evidence rather than verified-on-stale-data."""
    mock_kb.return_value = _kb_result(created_days_ago=60)
    mock_nli.return_value = {"entailment": 0.95, "contradiction": 0.0, "neutral": 0.05, "label": "entailment"}
    # Web verification inconclusive (uncertain → not in verified/unverified).
    mock_ext.return_value = {"status": "uncertain", "confidence": 0.3, "reason": "inconclusive"}

    result = await verify_claim(_TEMPORAL_CLAIM, mock_chroma[0], None, mock_redis)

    assert result["status"] == "uncertain"
    assert result.get("stale_evidence") is True
    assert "stale_evidence" in result["reason"]


@pytest.mark.asyncio
@patch("core.agents.hallucination.verification.cache_verdict", new_callable=AsyncMock)
@patch("core.agents.hallucination.verification.get_cached_verdict", new_callable=AsyncMock, return_value=None)
@patch("core.utils.nli.nli_score_async", new_callable=AsyncMock)
@patch("core.agents.hallucination.verification._verify_claim_externally", new_callable=AsyncMock)
@patch("core.agents.hallucination.verification._query_memories", new_callable=AsyncMock, return_value=[])
@patch("core.agents.query_agent.lightweight_kb_query", new_callable=AsyncMock)
async def test_temporal_claim_fresh_evidence_stays_verified(
    mock_kb, _mem, mock_ext, mock_nli, _getcache, _setcache,
    mock_chroma, mock_neo4j, mock_redis,
):
    """Fresh KB evidence for a temporal claim still verifies — the gate only
    fires on KNOWN-old evidence."""
    mock_kb.return_value = _kb_result(created_days_ago=1)
    mock_nli.return_value = {"entailment": 0.95, "contradiction": 0.0, "neutral": 0.05, "label": "entailment"}
    mock_ext.return_value = {"status": "uncertain", "confidence": 0.3, "reason": "inconclusive"}

    result = await verify_claim(_TEMPORAL_CLAIM, mock_chroma[0], None, mock_redis)

    assert result["status"] == "verified"
    assert not result.get("stale_evidence")


@pytest.mark.asyncio
@patch("core.agents.hallucination.verification.cache_verdict", new_callable=AsyncMock)
@patch("core.agents.hallucination.verification.get_cached_verdict", new_callable=AsyncMock, return_value=None)
@patch("core.utils.nli.nli_score_async", new_callable=AsyncMock)
@patch("core.agents.hallucination.verification._verify_claim_externally", new_callable=AsyncMock)
@patch("core.agents.hallucination.verification._query_memories", new_callable=AsyncMock, return_value=[])
@patch("core.agents.query_agent.lightweight_kb_query", new_callable=AsyncMock)
async def test_non_temporal_claim_stale_evidence_stays_verified(
    mock_kb, _mem, mock_ext, mock_nli, _getcache, _setcache,
    mock_chroma, mock_neo4j, mock_redis,
):
    """A timeless claim (Eiffel Tower height) on old evidence is NOT downgraded
    — staleness only applies to current/recency-scoped claims."""
    mock_kb.return_value = _kb_result(
        created_days_ago=400,
        content="The Eiffel Tower stands 330 meters tall.",
    )
    mock_nli.return_value = {"entailment": 0.95, "contradiction": 0.0, "neutral": 0.05, "label": "entailment"}
    mock_ext.return_value = {"status": "uncertain", "confidence": 0.3, "reason": "inconclusive"}

    result = await verify_claim(_TIMELESS_CLAIM, mock_chroma[0], None, mock_redis)

    assert result["status"] == "verified"
    assert not result.get("stale_evidence")


# ---------------------------------------------------------------------------
# Phase 4.3 — quality metrics recording
# ---------------------------------------------------------------------------


class TestVerificationAccuracyRecording:
    def test_log_verification_metrics_records_collector_metric(self):
        from core.utils import cache as cache_mod

        recorded: dict = {}

        class _FakeCollector:
            def __init__(self, _redis):
                pass

            def record_metric(self, name, value, tags=None):
                recorded["name"] = name
                recorded["value"] = value
                recorded["tags"] = tags

        with patch("utils.metrics.MetricsCollector", _FakeCollector):
            cache_mod.log_verification_metrics(
                MagicMock(), "conv-1", model="grok-4.3",
                verified=8, unverified=1, uncertain=1, total=10,
            )

        assert recorded["name"] == "verification_accuracy"
        assert recorded["value"] == 0.8  # 8/10
        assert recorded["tags"] == {"model": "grok-4.3"}

    def test_no_record_when_total_zero(self):
        from core.utils import cache as cache_mod

        called = {"n": 0}

        class _FakeCollector:
            def __init__(self, _redis):
                pass

            def record_metric(self, *a, **k):
                called["n"] += 1

        with patch("utils.metrics.MetricsCollector", _FakeCollector):
            cache_mod.log_verification_metrics(MagicMock(), "conv-2", total=0)
        assert called["n"] == 0


class TestCacheHitRateRecording:
    def test_records_hit_on_cache_hit(self):
        import json as _json

        from core.retrieval import semantic_cache as sc

        recorded: list = []

        class _FakeCollector:
            def __init__(self, _redis):
                pass

            def record_metric(self, name, value, tags=None):
                recorded.append((name, value))

        backend = MagicMock()
        backend.count.return_value = 5
        backend.query.return_value = {"ids": [["e1"]], "distances": [[0.01]]}
        redis_client = MagicMock()
        # Scope-tagged wrapper payload (legacy scope-less entries never match).
        redis_client.get.return_value = _json.dumps(
            {"domain_scope": "__all__", "result": {"answer": "cached", "sources": [{"id": "s1"}]}}
        )

        with (
            patch.object(sc, "_get_backend", return_value=backend),
            patch.object(sc, "SEMANTIC_CACHE_THRESHOLD", 0.5),
            patch("utils.metrics.MetricsCollector", _FakeCollector),
        ):
            import numpy as np
            out = sc.cache_lookup(np.zeros(8, dtype=np.float32), redis_client)

        assert out == {"answer": "cached", "sources": [{"id": "s1"}]}
        assert ("cache_hit_rate", 1.0) in recorded

    def test_records_miss_on_below_threshold(self):
        from core.retrieval import semantic_cache as sc

        recorded: list = []

        class _FakeCollector:
            def __init__(self, _redis):
                pass

            def record_metric(self, name, value, tags=None):
                recorded.append((name, value))

        backend = MagicMock()
        backend.count.return_value = 5
        backend.query.return_value = {"ids": [["e1"]], "distances": [[0.9]]}  # sim=0.1 < thresh

        with (
            patch.object(sc, "_get_backend", return_value=backend),
            patch.object(sc, "SEMANTIC_CACHE_THRESHOLD", 0.5),
            patch("utils.metrics.MetricsCollector", _FakeCollector),
        ):
            import numpy as np
            out = sc.cache_lookup(np.zeros(8, dtype=np.float32), MagicMock())

        assert out is None
        assert ("cache_hit_rate", 0.0) in recorded

    def test_no_record_when_backend_disabled(self):
        from core.retrieval import semantic_cache as sc

        recorded: list = []

        class _FakeCollector:
            def __init__(self, _redis):
                pass

            def record_metric(self, name, value, tags=None):
                recorded.append((name, value))

        with (
            patch.object(sc, "_get_backend", return_value=None),
            patch("utils.metrics.MetricsCollector", _FakeCollector),
        ):
            import numpy as np
            out = sc.cache_lookup(np.zeros(8, dtype=np.float32), MagicMock())

        assert out is None
        assert recorded == []  # disabled is not a "miss"


# ---------------------------------------------------------------------------
# §7.1 — knowledge-pack registry health guard
# ---------------------------------------------------------------------------


class TestKnowledgePacksHealth:
    def test_snapshot_reports_live_count(self):
        from app.routers.health import _knowledge_packs_snapshot

        snap = _knowledge_packs_snapshot()
        assert "registry_path" in snap
        assert snap["registry_exists"] is True
        assert snap["pack_count"] > 0
        assert snap["ok"] is True
