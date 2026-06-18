# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Slice 3 regressions — retrieval spine (RAG Quality Program 2026-06-12).

Pins four contracts the eval found broken end-to-end:

- **3.1** smart path threads ``graph_store`` to ``agent_query`` so the baseline
  fall-through graph expansion runs (the verified root cause of
  ``graph_results=0`` across all 31 Wave-1 queries).
- **3.2** CRAG gate fires external on current-intent queries when the freshest
  KB hit is stale, even if relevance is above threshold.
- **3.3** every rerank failure path tags ``reranker_status`` and returns a
  vector-ordered envelope (never-empty invariant) so degradation is observable.
- **4.1** non-streaming hallucination summary reports ``overall_confidence``
  with the same formula as the streaming path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Phase 3.1 — graph_store threaded through smart / manual path
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    from app.routers import agents

    app = FastAPI()
    app.include_router(agents.router)

    with (
        patch.object(agents, "get_chroma", return_value=MagicMock()),
        patch.object(agents, "get_neo4j", return_value=MagicMock()),
        patch.object(agents, "get_graph_store", return_value=MagicMock(name="GraphStore")),
        patch.object(agents, "get_redis", return_value=MagicMock()),
    ):
        yield TestClient(app, raise_server_exceptions=False)


def test_smart_path_threads_graph_store_to_orchestrated_query(client):
    captured: dict = {}

    async def _spy(**kwargs):
        captured.update(kwargs)
        return {
            "context": "", "sources": [], "confidence": 0.0,
            "results": [], "total_results": 0,
        }

    with patch(
        "app.agents.retrieval_orchestrator.orchestrated_query",
        new=AsyncMock(side_effect=_spy),
    ):
        res = client.post(
            "/agent/query",
            json={"query": "q", "rag_mode": "smart", "skip_cache": True},
        )
    assert res.status_code == 200
    assert captured.get("graph_store") is not None, (
        "smart path must thread graph_store to orchestrated_query — "
        "without it graph_expand_results silently no-ops"
    )


def test_manual_path_threads_graph_store_to_agent_query(client):
    captured: dict = {}

    async def _spy(**kwargs):
        captured.update(kwargs)
        return {
            "context": "", "sources": [], "confidence": 0.0,
            "results": [], "total_results": 0,
        }

    with patch(
        "core.agents.query_agent.agent_query",
        new=AsyncMock(side_effect=_spy),
    ):
        res = client.post(
            "/agent/query",
            json={"query": "q", "rag_mode": "manual", "skip_cache": True},
        )
    assert res.status_code == 200
    assert captured.get("graph_store") is not None, (
        "manual path must thread graph_store to agent_query"
    )


# ---------------------------------------------------------------------------
# Phase 3.2 — CRAG answerability/recency
# ---------------------------------------------------------------------------


def test_crag_gate_fires_on_below_threshold():
    from app.routers.agents import should_fire_external_crag

    weak = {"results": [{"relevance": 0.2}]}
    assert should_fire_external_crag(ext_on=True, kb_result=weak, threshold=0.5) is True


def test_crag_gate_skips_strong_kb_when_no_temporal_intent():
    from app.routers.agents import should_fire_external_crag

    strong = {"results": [{"relevance": 0.9}]}
    assert should_fire_external_crag(ext_on=True, kb_result=strong, threshold=0.5) is False


def test_crag_gate_fires_on_stale_kb_with_temporal_intent():
    """High relevance + temporal intent + stale KB → fire external."""
    from app.routers.agents import should_fire_external_crag

    strong_stale = {"results": [{"relevance": 0.9}]}
    assert should_fire_external_crag(
        ext_on=True,
        kb_result=strong_stale,
        threshold=0.5,
        temporal_intent_days=7,
        freshest_kb_age_days=30.0,  # well outside 7d window
        staleness_window_days=7,
    ) is True


def test_crag_gate_skips_fresh_kb_with_temporal_intent():
    from app.routers.agents import should_fire_external_crag

    strong_fresh = {"results": [{"relevance": 0.9}]}
    assert should_fire_external_crag(
        ext_on=True,
        kb_result=strong_fresh,
        threshold=0.5,
        temporal_intent_days=7,
        freshest_kb_age_days=2.0,
        staleness_window_days=7,
    ) is False


def test_crag_gate_returns_false_when_external_disabled():
    from app.routers.agents import should_fire_external_crag

    weak = {"results": [{"relevance": 0.1}]}
    assert should_fire_external_crag(ext_on=False, kb_result=weak, threshold=0.5) is False


def test_freshest_kb_age_parses_created_at():
    from datetime import timedelta

    from app.routers.agents import _freshest_kb_age_days
    from core.utils.time import utcnow

    recent = (utcnow() - timedelta(days=2)).replace(tzinfo=None).isoformat()
    old = (utcnow() - timedelta(days=30)).replace(tzinfo=None).isoformat()

    age = _freshest_kb_age_days({"results": [
        {"created_at": old},
        {"created_at": recent},
    ]})
    assert age is not None
    assert 1.5 < age < 3.0  # the recent one wins


def test_freshest_kb_age_returns_none_when_no_dates():
    from app.routers.agents import _freshest_kb_age_days

    assert _freshest_kb_age_days({"results": [{"relevance": 0.5}]}) is None
    assert _freshest_kb_age_days({}) is None


# ---------------------------------------------------------------------------
# Phase 3.3 — rerank resilience: never-empty + reranker_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerank_disabled_preserves_order_without_status():
    """Healthy "no rerank" paths (disabled, mode=none) leave reranker_status
    absent — only degradation tags. Mirrors the contract pinned by
    test_reranker_fallback.test_onnx_success_does_not_tag_status."""
    from core.agents.query_agent import rerank_results

    results = [{"relevance": 0.3, "content": "a"}, {"relevance": 0.7, "content": "b"}]
    out = await rerank_results(results, "q", use_reranking=False)
    assert len(out) == 2
    assert out[0]["relevance"] == 0.7  # vector-order sort applied
    assert not any("reranker_status" in r for r in out)


@pytest.mark.asyncio
async def test_rerank_cross_encoder_failure_returns_full_vector_envelope():
    """When every rerank provider raises, results survive in vector order and
    every item carries a degradation status."""
    import config
    from core.agents.query_agent import rerank_results

    results = [
        {"relevance": 0.3, "content": "a"},
        {"relevance": 0.7, "content": "b"},
        {"relevance": 0.5, "content": "c"},
    ]

    def _boom(*_a, **_k):
        raise RuntimeError("ONNX boom")

    with (
        patch.object(config, "RERANK_MODE", "cross_encoder"),
        patch("core.agents.query_agent._maybe_rerank_via_quenchforge",
              new=AsyncMock(return_value=None)),
        patch("core.agents.query_agent._maybe_rerank_via_sidecar",
              new=AsyncMock(return_value=None)),
        patch("core.retrieval.reranker.rerank", side_effect=_boom),
    ):
        out = await rerank_results(results, "q", use_reranking=True)

    # Never-empty invariant
    assert len(out) == 3
    # Vector order preserved (sorted at entry to rerank_results)
    assert [r["content"] for r in out] == ["b", "c", "a"]
    # Every result tagged with the ONNX degradation marker
    assert all(r.get("reranker_status") == "onnx_failed_no_fallback" for r in out)


# ---------------------------------------------------------------------------
# Phase 4.1 — overall_confidence on the non-streaming verification path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_streaming_summary_includes_overall_confidence():
    """Mirrors the streaming summary aggregate (verified+unverified only)."""
    from core.agents.hallucination import streaming

    fake_redis = MagicMock()
    fake_redis.setex = MagicMock()

    async def _fake_extract(*_a, **_k):
        return ["c1", "c2", "c3"], "test"

    async def _fake_verify(claim_text, *_a, **_k):
        # Two verified at 0.9 and 0.8, one uncertain (excluded from aggregate)
        return {
            "c1": {"status": "verified", "similarity": 0.9, "text": "c1"},
            "c2": {"status": "verified", "similarity": 0.8, "text": "c2"},
            "c3": {"status": "uncertain", "similarity": 0.5, "text": "c3"},
        }[claim_text]

    with (
        patch.object(streaming, "extract_claims", new=AsyncMock(side_effect=_fake_extract)),
        patch.object(streaming, "verify_claim", new=AsyncMock(side_effect=_fake_verify)),
    ):
        report = await streaming.check_hallucinations(
            response_text="some response that is long enough for the verifier "
                          "to attempt extraction without bailing early.",
            conversation_id="cid-test",
            chroma_client=MagicMock(),
            neo4j_driver=MagicMock(),
            redis_client=fake_redis,
            user_query="what is x?",
        )

    summary = report["summary"]
    assert summary["assessed"] == 2  # uncertain excluded
    # (0.9 + 0.8) / 2 = 0.85
    assert summary["overall_confidence"] == pytest.approx(0.85, abs=1e-3)
