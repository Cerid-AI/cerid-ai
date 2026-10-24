# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Smart mode's memory wall and KB-failure envelope (audit F24, F45).

F24: the guarded seams took the consumer's wall from the RequestContext for the
knowledge base only. Memory recall ran for a strict consumer whose scope leaves
out ``conversations`` (cerid-finance through ``/custom-agents/{id}/query``), in
smart mode and in manual mode alike.

F45: a failed KB search came back as an ordinary envelope: no degraded_reason
for the user, nothing the result cache recognises as degraded, and a cancelled
KB task was swallowed into the same empty result.

The guards and the orchestrator are real; only agent_query, the memory store
and the web fetch are faked.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.retrieval_orchestrator import guarded_orchestrated_query, orchestrated_query
from core.agents.guarded_retrieval import guarded_agent_query_full
from core.agents.request_context import RequestContext

FINANCE = RequestContext(client_id="cerid-finance", allowed_domains=("finance", "inbox"), strict_domains=True)
GUI = RequestContext(client_id="gui")

KB = {
    "results": [{"content": "a ledger row", "relevance": 0.9, "artifact_id": "kb-1", "domain": "finance"}],
    "context": "a ledger row",
    "confidence": 0.9,
}
MEMORY = [{"text": "the owner's private memory", "adjusted_score": 0.8, "memory_id": "m1"}]


class _Recall:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, **_kwargs):
        self.calls += 1
        return list(MEMORY)


async def _guarded_smart(ctx: RequestContext, recall: _Recall) -> dict:
    with (
        patch("core.agents.query_agent.agent_query", new=AsyncMock(return_value=dict(KB))),
        patch("app.agents.memory.recall_memories", new=recall),
        patch("app.agents.retrieval_orchestrator._query_external_sources", new=AsyncMock(return_value=[])),
    ):
        return await guarded_orchestrated_query(
            request_context=ctx, query="what did I decide about the budget", rag_mode="smart",
        )


@pytest.mark.asyncio
async def test_a_strict_consumer_gets_no_memories_in_smart_mode():
    recall = _Recall()
    result = await _guarded_smart(FINANCE, recall)

    assert recall.calls == 0
    assert result["source_breakdown"]["memory"] == []
    assert not any(r.get("source_type") == "memory" for r in result["results"])


@pytest.mark.asyncio
async def test_the_owner_still_gets_memories_in_smart_mode():
    recall = _Recall()
    result = await _guarded_smart(GUI, recall)

    assert recall.calls == 1
    assert [m["memory_id"] for m in result["source_breakdown"]["memory"]] == ["m1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("ctx", "expected"), [(FINANCE, False), (GUI, True)])
async def test_the_manual_guard_holds_the_memory_surface_to_the_wall(ctx, expected):
    seen: dict = {}

    async def _agent_query(**kwargs):
        seen.update(kwargs)
        return dict(KB)

    with patch("core.agents.query_agent.agent_query", new=_agent_query):
        await guarded_agent_query_full(
            request_context=ctx, query="what did I decide", external_augmentation=False,
        )

    assert seen["memory_enabled"] is expected


async def _smart_with_kb(side_effect: BaseException) -> dict:
    with (
        patch("core.agents.query_agent.agent_query", new=AsyncMock(side_effect=side_effect)),
        patch("app.agents.memory.recall_memories", new=_Recall()),
        patch("app.agents.retrieval_orchestrator._query_external_sources", new=AsyncMock(return_value=[])),
        patch("app.agents.retrieval_orchestrator.crag_wants_external", return_value=False),
    ):
        return await orchestrated_query(query="what did I decide", rag_mode="smart", domains=["finance"])


@pytest.mark.asyncio
async def test_a_failed_kb_search_is_reported_as_degraded():
    result = await _smart_with_kb(RuntimeError("chroma unreachable"))

    assert result["strategy"] == "error"
    assert result["retrieval_degraded"] is True
    assert "knowledge-base search failed" in result["degraded_reason"]
    # The /agent/query result cache skips an envelope with failed domains.
    assert result["domains_failed"] == ["finance"]
    assert result["domains_searched"] == []
    # Memory still answers.
    assert [m["memory_id"] for m in result["source_breakdown"]["memory"]] == ["m1"]


@pytest.mark.asyncio
async def test_a_failed_kb_search_is_counted_as_swallowed():
    import fakeredis

    from core.utils import swallowed

    fr = fakeredis.FakeRedis(decode_responses=True)
    with patch.object(swallowed, "_resolve_redis", return_value=fr):
        await _smart_with_kb(RuntimeError("chroma unreachable"))

    assert fr.zcard("cerid:swallowed:app.agents.retrieval_orchestrator.kb_query") == 1


@pytest.mark.asyncio
async def test_a_cancelled_kb_task_is_not_swallowed():
    with pytest.raises(asyncio.CancelledError):
        await _smart_with_kb(asyncio.CancelledError())
