# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Smart mode returns at most top_k rows, best first across sources (audit F25).

Live 2026-10-10: a gui smart-mode query with top_k 10 returned 71 rows. The
manual path caps in agent_query_full; smart mode flattened agent_query's
uncapped KB rows with every memory and web row. Only the KB, memory and
external fetches are faked; the orchestrator is real.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.retrieval_orchestrator import orchestrated_query


def _kb(*rels: float) -> dict:
    rows = [{"content": f"kb {r}", "relevance": r, "artifact_id": f"kb-{r}"} for r in rels]
    return {"results": rows, "sources": rows, "context": "", "confidence": 0.5}


async def _smart(top_k: int, kb: dict, memories: list[dict], external: list[dict]) -> dict:
    with (
        patch("core.agents.query_agent.agent_query", new=AsyncMock(return_value=kb)),
        patch("app.agents.retrieval_orchestrator._recall_with_timeout", new=AsyncMock(return_value=memories)),
        patch("app.agents.retrieval_orchestrator._query_external_sources", new=AsyncMock(return_value=external)),
        patch("app.agents.retrieval_orchestrator.crag_wants_external", return_value=True),
    ):
        return await orchestrated_query(
            query="what did we decide", rag_mode="smart", top_k=top_k,
            chroma_client=None, redis_client=None, neo4j_driver=None,
            context_sources={"kb": True, "memory": True, "external": True},
        )


MEMORIES = [
    {"text": "a strong memory", "adjusted_score": 0.95, "memory_id": "m1"},
    {"text": "a weak memory", "adjusted_score": 0.1, "memory_id": "m2"},
]
EXTERNAL = [{"content": "from the web", "relevance": 1.0, "source_url": "https://example.com", "source_name": "web"}]


@pytest.mark.asyncio
async def test_the_pool_is_capped_at_top_k_best_first():
    # The web row is discounted to 0.6, which beats every KB row but the first.
    result = await _smart(3, _kb(0.9, 0.5, 0.4, 0.3, 0.2), MEMORIES, EXTERNAL)

    assert result["total_results"] == len(result["results"]) == 3
    kept = {r.get("memory_id") or r.get("artifact_id") or r.get("source_url") for r in result["results"]}
    assert "m1" in kept and "kb-0.9" in kept and "https://example.com" in kept
    assert "m2" not in kept and "kb-0.5" not in kept


@pytest.mark.asyncio
async def test_the_breakdown_is_the_capped_pool():
    result = await _smart(3, _kb(0.9, 0.8, 0.7, 0.6, 0.5), MEMORIES, EXTERNAL)

    sb = result["source_breakdown"]
    assert result["results"] == [*sb["kb"], *sb["memory"], *sb["external"]]
    assert result["sources"] == result["results"]


@pytest.mark.asyncio
async def test_a_pool_within_top_k_is_unchanged():
    result = await _smart(10, _kb(0.9, 0.8), MEMORIES, [])

    assert result["total_results"] == 4
