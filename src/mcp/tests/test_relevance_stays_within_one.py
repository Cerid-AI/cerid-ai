# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Relevance stays on its 0-1 scale after the post-rerank multipliers (audit F11).

The quality boost multiplies relevance by up to 1.2 and the endorsement weight
by up to 10, both after reranking, and neither had a ceiling: the audit
baseline saw an answerable row at 1.044. Every additive boost already caps at
1.0, and confidence, CRAG and the source list all read relevance as 0-1.
Chroma, the graph store and the cross-encoder model are faked; agent_query and
the quality step run for real.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.agents.query_agent import _apply_quality_and_summaries, agent_query
from tests.helpers.fake_neo4j import _FakeNeo4jDriver
from tests.test_rag_pipeline_integration import (
    _FEATURE_OVERRIDES,
    _chroma_collection,
    _mock_chroma_client,
)

ANSWER = "Ethanol boils at 78.37 degrees Celsius at standard pressure."
NEUTRAL = {"contradiction": 0.1, "entailment": 0.1, "neutral": 0.8, "label": "neutral"}


class _GraphStore:
    def __init__(self, quality: dict[str, float]) -> None:
        self._quality = quality

    async def get_quality_and_summaries(self, artifact_ids):
        return {a: self._quality[a] for a in artifact_ids if a in self._quality}, {}


@pytest.mark.asyncio
async def test_the_quality_boost_does_not_lift_relevance_past_one():
    rows = [
        {"artifact_id": "curated", "relevance": 0.87},
        {"artifact_id": "neutral", "relevance": 0.7},
    ]

    out = await _apply_quality_and_summaries(rows, graph_store=_GraphStore({"curated": 1.0}))
    by_id = {r["artifact_id"]: r["relevance"] for r in out}

    assert by_id["curated"] == 1.0
    assert by_id["neutral"] == 0.7  # neutral 0.5 quality leaves it alone


@pytest.mark.asyncio
async def test_an_endorsed_row_stays_within_one():
    coll = _chroma_collection(
        [ANSWER],
        [{"domain": "general", "filename": "answer.md", "artifact_id": "answer.md", "chunk_index": 0,
          "ingested_at": "", "sub_category": "", "tags_json": "[]", "keywords": "[]"}],
        [0.3],
        ["answer.md"],
    )
    driver = _FakeNeo4jDriver().add_artifact("answer.md", chunk_ids=["answer.md"], endorsement_weight=10.0)
    with (
        patch.multiple("config.features", **{k.split(".")[-1]: v for k, v in _FEATURE_OVERRIDES.items()}),
        patch("core.utils.nli.batch_nli_score", return_value=[NEUTRAL]),
        patch("core.retrieval.bm25.is_available", return_value=False),
        patch("core.retrieval.reranker._score_pairs", lambda _q, docs: [0.9 for _ in docs]),
        patch("config.RERANK_MODE", "cross_encoder"),
        patch("config.ENABLE_CASCADE_RERANK", False),
        patch("core.agents.query_agent._maybe_rerank_via_quenchforge", new=AsyncMock(return_value=None)),
        patch("core.agents.query_agent._maybe_rerank_via_sidecar", new=AsyncMock(return_value=None)),
    ):
        result = await agent_query(
            query="what is the boiling point of ethanol",
            domains=["general"],
            chroma_client=_mock_chroma_client({"domain_general": coll}),
            neo4j_driver=driver,
            skip_cache=True,
        )

    assert [r["filename"] for r in result["results"]] == ["answer.md"]
    assert result["results"][0]["relevance"] == 1.0
