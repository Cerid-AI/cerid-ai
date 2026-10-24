# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Time phrases scope agent_query by the ingest stamps ingestion actually writes (audit F128).

Ingestion stamps rows with an aware UTC timestamp and the temporal helpers
compared it with a naive cutoff. The TypeError was swallowed, so "this week"
filtered nothing and every row took the same flat recency boost, with or
without a time phrase in the query. Chroma, the memory store and the
cross-encoder are faked; agent_query and the temporal code run for real.
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


def _aware(days_ago: float) -> str:
    return (utcnow() - timedelta(days=days_ago)).isoformat()


def _naive(days_ago: float) -> str:
    return (utcnow().replace(tzinfo=None) - timedelta(days=days_ago)).isoformat()


def _meta(name: str, ingested_at: str) -> dict:
    return {"domain": "general", "filename": name, "artifact_id": name, "chunk_index": 0,
            "ingested_at": ingested_at, "sub_category": "", "tags_json": "[]", "keywords": "[]"}


async def _query(query: str, rows: dict[str, str]) -> dict[str, float]:
    """Run agent_query over equally-similar rows; returns filename -> final relevance."""
    names = list(rows)
    coll = _chroma_collection(
        [f"Notes on the database choice, part {i}." for i in range(len(names))],
        [_meta(name, rows[name]) for name in names],
        [0.3] * len(names),
        names,
    )
    with (
        patch.multiple("config.features", **{k.split(".")[-1]: v for k, v in _FEATURE_OVERRIDES.items()}),
        patch("core.agents.memory.recall_memories", new=AsyncMock(return_value=[])),
        patch("core.utils.nli.batch_nli_score", side_effect=lambda pairs: [NEUTRAL] * len(pairs)),
        patch("core.retrieval.bm25.is_available", return_value=False),
        patch("core.retrieval.reranker._score_pairs", lambda _q, docs: [0.9 for _ in docs]),
        patch("config.RERANK_MODE", "cross_encoder"),
        patch("config.ENABLE_CASCADE_RERANK", False),
        patch("core.agents.query_agent._maybe_rerank_via_quenchforge", new=AsyncMock(return_value=None)),
        patch("core.agents.query_agent._maybe_rerank_via_sidecar", new=AsyncMock(return_value=None)),
    ):
        result = await agent_query(
            query=query,
            domains=["general"],
            chroma_client=_mock_chroma_client({"domain_general": coll}),
            neo4j_driver=_mock_neo4j(),
            skip_cache=True,
        )
    return {r["filename"]: r["relevance"] for r in result["results"]}


@pytest.mark.asyncio
async def test_this_week_drops_a_ten_day_old_row():
    got = await _query("what was decided this week about the database",
                       {"fresh.md": _aware(2), "stale.md": _aware(10)})
    assert list(got) == ["fresh.md"]


@pytest.mark.asyncio
async def test_recent_drops_nothing_and_ranks_the_newer_row_higher():
    got = await _query("recent notes about the database",
                       {"old.md": _aware(200), "new.md": _aware(1)})
    assert set(got) == {"old.md", "new.md"}
    assert got["new.md"] > got["old.md"]


@pytest.mark.asyncio
async def test_no_time_phrase_means_no_recency_boost():
    # Naive stamps, which the old code could date: it boosted the newer row
    # even though nothing in the question asks about time.
    got = await _query("notes about the database",
                       {"old.md": _naive(400), "new.md": _naive(0)})
    assert set(got) == {"old.md", "new.md"}
    assert got["new.md"] == got["old.md"]
