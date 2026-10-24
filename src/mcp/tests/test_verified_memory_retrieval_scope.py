# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Verified memories stay out of general retrieval (ruling D3, finding F113).

Verification promotes the claims it confirms ("Mount Everest stands at
8,848.86 m") into the conversations collection. They are general knowledge,
not something the person said, and they answered unrelated questions at
0.7-0.9. They are kept for verification and returned only to a caller that
names the conversations domain.

Only the stores are faked: an in-memory Chroma whose every vector is the same,
so each row is an equally strong match and only the filtering decides what
comes back.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config
from core.retrieval.artifact_rows import verified_memory_id, verified_memory_row_id
from tests.helpers.fake_chroma import FakeChromaClient, FakeChromaCollection

_HIGH_ENTAILMENT = {"entailment": 0.9, "neutral": 0.05, "contradiction": 0.05}

FACT = "Mount Everest stands at 8,848.86 m above sea level"
FACT_ID = verified_memory_id(FACT)
FACT_ROW = verified_memory_row_id(FACT_ID, FACT)
LEGACY_FACT = "The 401(k) employee contribution limit for 2025 is 23,500 dollars"
LEGACY_ROW = f"verified_memory_{verified_memory_id(LEGACY_FACT)}"
MEMORY = "We decided to keep the retirement savings in the index fund"
MEMORY_ROW = "mem-decision_chunk-one"
FINANCE_ROW = "irs-pub_chunk-one"


def _same_vector(texts: list[str]) -> list[list[float]]:
    return [[1.0, 0.0, 0.0] for _ in texts]


def _verified_meta(memory_id: str) -> dict[str, Any]:
    """What verified_memory.promote_verified_facts writes."""
    return {
        "artifact_id": memory_id, "memory_type": "empirical", "memory_source_type": "verification",
        "domain": "conversations", "filename": f"verified_fact_{memory_id[:8]}",
        "ingested_at": "2026-10-01T00:00:00+00:00", "decay_anchor": "2026-10-01T00:00:00+00:00",
    }


def _chroma() -> FakeChromaClient:
    conversations = FakeChromaCollection(config.collection_name("conversations"), _same_vector)
    conversations.upsert(
        ids=[FACT_ROW, LEGACY_ROW, MEMORY_ROW],
        documents=[FACT, LEGACY_FACT, MEMORY],
        metadatas=[
            _verified_meta(FACT_ID),
            _verified_meta(verified_memory_id(LEGACY_FACT)),
            {"artifact_id": "mem-decision", "memory_type": "decision", "domain": "conversations",
             "filename": "memory_decision_abc_20261001_0", "ingested_at": "2026-10-01T00:00:00+00:00"},
        ],
    )
    finance = FakeChromaCollection(config.collection_name("finance"), _same_vector)
    finance.upsert(
        ids=[FINANCE_ROW],
        documents=["IRS: the 2025 401(k) elective deferral limit is $23,500."],
        metadatas=[{"artifact_id": "irs-pub", "domain": "finance", "filename": "irs-pub-560.pdf"}],
    )
    return FakeChromaClient([conversations, finance])


@pytest.fixture(autouse=True)
def _nli_entails():
    with patch("core.utils.nli.nli_score", return_value=_HIGH_ENTAILMENT):
        yield


# --- memory recall: the chat's per-send recall, the SDK, MCP and A2A --------


@pytest.mark.asyncio
async def test_recall_leaves_verified_memories_out_and_keeps_the_persons_own():
    from core.agents.memory import recall_memories

    got = await recall_memories("401(k) contribution limit for 2025", _chroma(), None, top_k=10)
    assert [m["chunk_id"] for m in got] == [MEMORY_ROW]


@pytest.mark.asyncio
async def test_recall_returns_them_when_asked():
    from core.agents.memory import recall_memories

    got = await recall_memories("401(k) contribution limit", _chroma(), None, top_k=10, include_verified=True)
    assert {m["chunk_id"] for m in got} == {FACT_ROW, LEGACY_ROW, MEMORY_ROW}


@pytest.mark.asyncio
async def test_the_guarded_recall_the_chat_calls_leaves_them_out():
    from app.services.request_policy import build_request_context
    from core.agents.guarded_retrieval import guarded_recall_memories

    got = await guarded_recall_memories(
        request_context=build_request_context(client_id="gui", private_level=0),
        query="401(k) contribution limit for 2025", chroma_client=_chroma(), top_k=10,
    )
    assert [m["chunk_id"] for m in got] == [MEMORY_ROW]


# --- the KB scan, through the real per-domain arm ---------------------------


@pytest.fixture
def _plain_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.agents import query_agent
    from core.retrieval import bm25 as bm25_mod

    monkeypatch.setenv("CERID_FILTER_PENDING_CHUNKS", "false")
    monkeypatch.setattr(bm25_mod, "is_available", lambda: False)
    monkeypatch.setattr(query_agent, "_parent_child_enabled", lambda: False)
    monkeypatch.setattr(query_agent, "_unsearchable_folder_ids", lambda: ())


async def _scan(**kw: Any) -> set[str]:
    from core.agents import query_agent

    rows = await query_agent.multi_domain_query(
        query="401(k) contribution limit for 2025", domains=["conversations", "finance"],
        top_k=10, chroma_client=_chroma(), **kw,
    )
    return {r["chunk_id"] for r in rows}


@pytest.mark.asyncio
@pytest.mark.usefixtures("_plain_retrieval")
async def test_the_scan_leaves_verified_memories_out_when_told():
    assert await _scan(exclude_verified_memories=True) == {MEMORY_ROW, FINANCE_ROW}
    assert await _scan() == {FACT_ROW, LEGACY_ROW, MEMORY_ROW, FINANCE_ROW}


@pytest.mark.asyncio
@pytest.mark.usefixtures("_plain_retrieval")
async def test_a_keyword_only_hit_is_left_out_too(monkeypatch: pytest.MonkeyPatch):
    """A BM25 hit skips Chroma's where; the same clause is applied to it.

    The vector arm already left the fact out, so the keyword index hands it
    over as a keyword-only hit, the path that re-reads rows by id."""
    from core.retrieval import bm25 as bm25_mod

    monkeypatch.setattr(bm25_mod, "is_available", lambda: True)
    monkeypatch.setattr(
        bm25_mod, "search_bm25",
        lambda domain, _q, _k: [(FACT_ROW, 1.0), (LEGACY_ROW, 1.0)] if domain == "conversations" else [],
    )
    assert await _scan(exclude_verified_memories=True) == {MEMORY_ROW, FINANCE_ROW}


# --- the smart-mode memory lane ---------------------------------------------


async def _memory_lane(domains: list[str] | None) -> set[str]:
    from app.agents.retrieval_orchestrator import orchestrated_query

    empty = {"results": [], "context": "", "sources": [], "confidence": 0.0}
    with patch("core.agents.query_agent.agent_query", new=AsyncMock(return_value=empty)):
        out = await orchestrated_query(
            query="401(k) contribution limit for 2025", rag_mode="smart", domains=domains,
            chroma_client=_chroma(), context_sources={"external": False},
        )
    return {m["memory_id"] for m in out["source_breakdown"]["memory"]}


@pytest.mark.asyncio
async def test_the_memory_lane_leaves_them_out_of_a_general_question():
    assert await _memory_lane(None) == {"mem-decision"}
    assert await _memory_lane(["finance"]) == {"mem-decision"}


@pytest.mark.asyncio
async def test_the_memory_lane_returns_them_when_the_caller_names_conversations():
    assert await _memory_lane(["conversations"]) == {
        "mem-decision", FACT_ID, verified_memory_id(LEGACY_FACT),
    }


# --- verification's own lookup still reads them -----------------------------


@pytest.mark.asyncio
async def test_promotion_still_finds_the_fact_it_already_holds():
    """The dedup check before promoting reads the verified memories; were it to
    lose sight of them, every re-verification would write the claim again."""
    from core.agents.verified_memory import promote_verified_facts

    create = MagicMock()
    counts = await promote_verified_facts(
        {"conversation_id": "conv-1", "claims": [{
            "claim": FACT, "status": "verified", "similarity": 0.95, "claim_type": "factual",
            "nli_entailment": 0.93, "source_urls": ["https://example.org/everest"],
        }]},
        _chroma(), None, create_memory_fn=create,
    )
    assert counts["skipped_duplicate"] == 1 and counts["promoted"] == 0
    create.assert_not_called()
