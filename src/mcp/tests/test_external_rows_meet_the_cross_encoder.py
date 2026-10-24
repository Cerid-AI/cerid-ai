# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""External rows are scored by the reranker the KB rows went through (F27, F77, F111).

A source's confidence is a constant: every Wikipedia page 0.85, a web hit by its
rank. Live probe 2026-10-11, 'What is the boiling point of ethanol in Celsius?':
Wikipedia's 'Celsius' page sat at 0.54 and DuckDuckGo's 'Anders Celsius' at
0.42, above the web hit that gave 78.3 degrees (0.33). The cross-encoder gives
the off-topic pages 0.00 and the answer 1.00, and a row it rejects leaves, as a
KB row does. The cross-encoder model is faked; the blend, both merge paths and
the sort are real.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.agents import crag

QUESTION = "What is the boiling point of ethanol in Celsius?"
ANSWER = "The boiling point of ethanol is 78.3 degrees Celsius."
OFF_TOPIC = "The degree Celsius is the unit of temperature on the Celsius scale."
CE = {ANSWER: 1.0, OFF_TOPIC: 0.0}

FETCHED = [
    {"title": "Celsius", "content": OFF_TOPIC, "source_url": "https://en.wikipedia.org/wiki/Celsius",
     "source_name": "Wikipedia", "confidence": 0.9},
    {"title": "Boiling point", "content": ANSWER, "source_url": "https://example.org/ethanol",
     "source_name": "Web Search (searxng)", "confidence": 0.55},
]


def _cross_encoder():
    return (
        patch("core.retrieval.reranker._score_pairs", lambda _q, docs: [CE[d] for d in docs]),
        patch("config.RERANK_MODE", "cross_encoder"),
        patch("config.ENABLE_CASCADE_RERANK", False),
        patch("core.agents.query_agent._maybe_rerank_via_quenchforge", new=AsyncMock(return_value=None)),
        patch("core.agents.query_agent._maybe_rerank_via_sidecar", new=AsyncMock(return_value=None)),
    )


def _by_content(rows):
    return {r["content"]: r["relevance"] for r in rows}


@pytest.mark.asyncio
async def test_crag_keeps_the_answer_and_drops_a_page_that_only_shares_a_word():
    registry = AsyncMock()
    registry.query_all = AsyncMock(return_value=[dict(r) for r in FETCHED])
    a, b, c, d, e = _cross_encoder()
    with a, b, c, d, e, patch.object(crag, "_external_registry", registry), \
            patch.object(crag, "_search_term_extractor", lambda q: q):
        result = await crag.augment_external_crag({"results": []}, QUESTION, None, 0.6)

    assert list(_by_content(result["results"])) == [ANSWER], "the page that only shares a word leaves"
    assert result["rerank_rejected"] == 1, "an answer emptied by rejection is an answer, and is cached"


@pytest.mark.asyncio
async def test_smart_mode_keeps_the_answer_and_drops_a_page_that_only_shares_a_word():
    from app.agents.retrieval_orchestrator import orchestrated_query

    kb = {"results": [], "sources": [], "context": "", "confidence": 0.0, "total_results": 0}
    a, b, c, d, e = _cross_encoder()
    with a, b, c, d, e, \
            patch("app.agents.retrieval_orchestrator._query_external_sources",
                  new=AsyncMock(return_value=[dict(r) for r in FETCHED])), \
            patch("core.agents.query_agent.agent_query", new=AsyncMock(return_value=kb)):
        result = await orchestrated_query(
            QUESTION, rag_mode="smart", context_sources={"kb": True, "memory": False, "external": True},
        )

    assert list(_by_content(result["source_breakdown"]["external"])) == [ANSWER]
    assert result["rerank_rejected"] == 1
