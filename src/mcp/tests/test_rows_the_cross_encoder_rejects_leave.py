# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A row the cross-encoder rejects does not reach the answer.

Relevance after reranking is 0.4 x cross-encoder + 0.6 x retrieval, so a row
the cross-encoder scored 0.000 kept 0.38-0.50 and filled the top-k. Live audit
suite 2026-10-11: 136 of 272 top-8 slots were such rows (a dietary-fats note
for 'boiling point of ethanol', a Bloomingdale's email for 'AI news'), and none
of the 18 rows that answered a question scored below 0.44. Chroma, Neo4j and
the cross-encoder model are faked; agent_query runs for real.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.agents.query_agent import agent_query
from tests.test_rag_pipeline_integration import (
    _FEATURE_OVERRIDES,
    _chroma_collection,
    _mock_chroma_client,
    _mock_neo4j,
)

ANSWER = "Ethanol boils at 78.37 degrees Celsius at standard pressure."
NOISE = "Dietary fats and their impact on human wellness and heart health."
RELATED = "Methanol boils at 64.7 degrees Celsius, lower than ethanol."
CE = {ANSWER: 0.97, NOISE: 0.0, RELATED: 0.6}
NEUTRAL = {"contradiction": 0.1, "entailment": 0.1, "neutral": 0.8, "label": "neutral"}


def _meta(name: str) -> dict:
    return {"domain": "general", "filename": name, "artifact_id": name, "chunk_index": 0,
            "ingested_at": "", "sub_category": "", "tags_json": "[]", "keywords": "[]"}


async def _query(use_reranking: bool, docs: dict[str, tuple[str, float]] | None = None) -> list[str]:
    docs = docs or {"answer.md": (ANSWER, 0.4), "noise.md": (NOISE, 0.3)}
    coll = _chroma_collection(
        [text for text, _ in docs.values()], [_meta(name) for name in docs],
        [dist for _, dist in docs.values()], list(docs),
    )
    with (
        patch.multiple("config.features", **{k.split(".")[-1]: v for k, v in _FEATURE_OVERRIDES.items()}),
        patch("core.utils.nli.batch_nli_score", return_value=[NEUTRAL] * len(docs)),
        patch("core.retrieval.bm25.is_available", return_value=False),
        patch("core.retrieval.reranker._score_pairs", lambda _q, docs: [CE[d] for d in docs]),
        patch("config.RERANK_MODE", "cross_encoder"),
        patch("config.ENABLE_CASCADE_RERANK", False),
        patch("core.agents.query_agent._maybe_rerank_via_quenchforge", new=AsyncMock(return_value=None)),
        patch("core.agents.query_agent._maybe_rerank_via_sidecar", new=AsyncMock(return_value=None)),
    ):
        result = await agent_query(
            query="what is the boiling point of ethanol",
            domains=["general"],
            chroma_client=_mock_chroma_client({"domain_general": coll}),
            neo4j_driver=_mock_neo4j(),
            use_reranking=use_reranking,
            skip_cache=True,
        )
    _query.rejected = result.get("rerank_rejected")
    return [r["filename"] for r in result["results"]]


@pytest.mark.asyncio
async def test_a_row_the_cross_encoder_rejects_leaves_the_pool():
    assert await _query(use_reranking=True) == ["answer.md"]
    assert _query.rejected == 1


@pytest.mark.asyncio
async def test_a_row_past_the_cross_encoder_window_does_not_take_the_slot():
    docs = {"answer.md": (ANSWER, 0.3), "related.md": (RELATED, 0.35), "noise.md": (NOISE, 0.5)}
    with patch("config.QUERY_RERANK_CANDIDATES", 2):
        assert sorted(await _query(use_reranking=True, docs=docs)) == ["answer.md", "related.md"]


@pytest.mark.asyncio
async def test_without_a_cross_encoder_nothing_is_dropped():
    assert sorted(await _query(use_reranking=False)) == ["answer.md", "noise.md"]
