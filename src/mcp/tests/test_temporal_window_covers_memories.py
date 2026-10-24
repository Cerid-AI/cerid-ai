# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A time-scoped question judges recalled memories by the same window as KB rows (audit F101).

The temporal filter read only ``ingested_at``. Memory rows carry their date as
``created_at`` and no ``ingested_at``, and an absent date counts as inside the
window, so a five-year-old memory answered "what did we decide this week".
Chroma, the memory store and the cross-encoder are faked; agent_query and the
memory adapter run for real.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from core.agents.query_agent import agent_query
from core.utils.time import utcnow
from tests.test_rag_pipeline_integration import (
    _FEATURE_OVERRIDES,
    _chroma_collection,
    _mock_chroma_client,
    _mock_neo4j,
)

NEUTRAL = {"contradiction": 0.1, "entailment": 0.1, "neutral": 0.8, "label": "neutral"}
OLD = "2020-01-01T00:00:00"


def _meta(name: str, ingested_at: str) -> dict:
    return {"domain": "general", "filename": name, "artifact_id": name, "chunk_index": 0,
            "ingested_at": ingested_at, "sub_category": "", "tags_json": "[]", "keywords": "[]"}


@pytest.mark.asyncio
async def test_an_old_memory_is_outside_this_weeks_window():
    recent = (utcnow().replace(tzinfo=None) - timedelta(days=1)).isoformat()
    coll = _chroma_collection(
        ["We chose Postgres for the database.", "We chose MySQL for the database."],
        [_meta("recent.md", recent), _meta("old.md", OLD)],
        [0.3, 0.3],
        ["recent.md", "old.md"],
    )
    memories = [{"text": "We decided on SQLite for the database.", "adjusted_score": 0.9,
                 "memory_id": "memory_old", "created_at": OLD, "memory_type": "decision"}]
    with (
        patch.multiple("config.features", **{k.split(".")[-1]: v for k, v in _FEATURE_OVERRIDES.items()}),
        patch("config.features.ENABLE_SURFACE_BIASED_RETRIEVAL", True),
        patch("core.agents.memory.recall_memories", new=AsyncMock(return_value=memories)),
        patch("core.utils.nli.batch_nli_score", side_effect=lambda pairs: [NEUTRAL] * len(pairs)),
        patch("core.retrieval.bm25.is_available", return_value=False),
        patch("core.retrieval.reranker._score_pairs", lambda _q, docs: [0.9 for _ in docs]),
        patch("config.RERANK_MODE", "cross_encoder"),
        patch("config.ENABLE_CASCADE_RERANK", False),
        patch("core.agents.query_agent._maybe_rerank_via_quenchforge", new=AsyncMock(return_value=None)),
        patch("core.agents.query_agent._maybe_rerank_via_sidecar", new=AsyncMock(return_value=None)),
    ):
        result = await agent_query(
            query="what did we decide this week about the database",
            domains=["general"],
            chroma_client=_mock_chroma_client({"domain_general": coll}),
            neo4j_driver=_mock_neo4j(),
            skip_cache=True,
        )

    assert result["surface_route"]["intent"] == "personal_context"
    assert [r["filename"] for r in result["results"]] == ["recent.md"]
