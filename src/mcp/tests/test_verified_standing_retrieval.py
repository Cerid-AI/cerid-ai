# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Retrieval defects verified standing on 2026-10-11 (sprint lane B).

- F116: a domain whose search raised was still listed in ``domains_searched``,
  and the result was cached like a healthy one.
- F107: the adjacent-domain bleed searched at ``top_k // 2`` after the adaptive
  gate had lowered the request's ``top_k``.
- F117: smart mode's confidence, a mean over the whole pool, was not held to
  [0, 1]; a memory's adjusted score can exceed 1.
- F50: the Settings model-router toggle wrote ``ENABLE_MODEL_ROUTER`` while
  chat routing reads ``SMART_ROUTING_ENABLED``.
Chroma, Neo4j and the stores are faked; the code under test runs for real.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agents.query_agent import agent_query
from tests.test_rag_pipeline_integration import (
    _FEATURE_OVERRIDES,
    _chroma_collection,
    _mock_chroma_client,
    _mock_neo4j,
)

_FEATURES = {k.split(".")[-1]: v for k, v in _FEATURE_OVERRIDES.items()}


def _meta(domain: str, name: str) -> dict:
    return {"domain": domain, "filename": name, "artifact_id": name, "chunk_index": 0,
            "ingested_at": "", "sub_category": "", "tags_json": "[]", "keywords": "[]"}


async def _query(features: dict | None = None, **kwargs) -> dict:
    ok = _chroma_collection(["Spark caches a DataFrame with cache()."], [_meta("coding", "spark.md")], [0.3], ["s"])
    broken = MagicMock()
    broken.count.return_value = 5
    broken.query.side_effect = RuntimeError("chroma: collection unavailable")
    client = _mock_chroma_client({"domain_coding": ok, "domain_finance": broken})
    with (
        patch.multiple("config.features", **{**_FEATURES, **(features or {})}),
        patch("core.retrieval.bm25.is_available", return_value=False),
        patch("core.utils.nli.batch_nli_score", return_value=[]),
    ):
        return await agent_query(query="how do I cache a DataFrame", chroma_client=client,
                                 neo4j_driver=_mock_neo4j(), use_reranking=False, skip_cache=True, **kwargs)


@pytest.mark.asyncio
async def test_a_failed_domain_is_reported_failed_not_searched():
    result = await _query(domains=["coding", "finance"], strict_domains=True)
    assert result["domains_failed"] == ["finance"]
    assert "finance" not in result["domains_searched"]
    assert "coding" in result["domains_searched"]
    assert result["retrieval_degraded"] is True


@pytest.mark.asyncio
async def test_the_adjacent_domains_follow_the_lowered_top_k():
    calls = []

    async def capture(**kw):
        calls.append(kw)
        return []

    from core.retrieval.retrieval_gate import RetrievalDecision

    light = RetrievalDecision(action="light", top_k=8, reason="test")
    with (
        patch("core.retrieval.retrieval_gate.classify_retrieval_need", return_value=light),
        patch("core.agents.query_agent._get_adjacent_domains", return_value={"finance": 0.5}),
        patch("core.agents.query_agent.multi_domain_query", side_effect=capture),
    ):
        await _query(features={"ENABLE_ADAPTIVE_RETRIEVAL": True}, domains=["coding"], top_k=20)
    adjacent = [c for c in calls if c.get("domains") == ["finance"]]
    assert adjacent and adjacent[0]["top_k"] == 4, "max(3, 8 // 2), not max(3, 20 // 2)"


@pytest.mark.asyncio
async def test_smart_mode_confidence_stays_inside_zero_and_one():
    from app.agents.retrieval_orchestrator import orchestrated_query

    kb = {"results": [], "sources": [], "context": "", "confidence": 0.0, "total_results": 0}
    memory = [{"text": "the user prefers Python", "adjusted_score": 1.4, "memory_type": "empirical"}]
    with (
        patch("core.agents.query_agent.agent_query", new=AsyncMock(return_value=kb)),
        patch("app.agents.retrieval_orchestrator._recall_with_timeout", new=AsyncMock(return_value=memory)),
        patch("app.agents.retrieval_orchestrator.crag_wants_external", return_value=False),
    ):
        result = await orchestrated_query("what language do I prefer", rag_mode="smart")
    assert result["confidence"] == 1.0


def test_the_model_router_toggle_changes_what_routing_reads():
    import config
    import config.settings as settings
    from utils.features import set_toggle

    before = settings.SMART_ROUTING_ENABLED
    try:
        set_toggle("enable_model_router", not before)
        assert settings.SMART_ROUTING_ENABLED is (not before)
        assert config.SMART_ROUTING_ENABLED is (not before)
    finally:
        set_toggle("enable_model_router", before)
