# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Smart mode sends a question to external sources only when CRAG says so (audit F09).

Live probe 2026-10-10: /agent/query in smart mode returned external rows for
14 of 14 questions the knowledge base answers and 6 of 6 greetings, where the
manual path, gated by CRAG, fired on 1 of 14. The orchestrator ran the external
fetch in parallel with the KB, unconditionally. Only the KB and the external
fetch are faked; the gate and the orchestrator are real.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.retrieval_orchestrator import orchestrated_query

EXTERNAL = [{"content": "from the web", "relevance": 0.8, "source_url": "https://example.com", "source_name": "web"}]


def _kb(relevance: float | None, **extra: Any) -> dict[str, Any]:
    rows = [] if relevance is None else [{"content": "kb row", "relevance": relevance, "artifact_id": "a1"}]
    return {"results": rows, "sources": rows, "context": "", "confidence": relevance or 0.0, **extra}


async def _ask(kb: dict[str, Any], query: str = "how does the reranker blend scores") -> tuple[dict, AsyncMock]:
    fetch = AsyncMock(return_value=EXTERNAL)
    with (
        patch("core.agents.query_agent.agent_query", new=AsyncMock(return_value=kb)),
        patch("app.agents.retrieval_orchestrator._query_external_sources", new=fetch),
        patch("config.RETRIEVAL_QUALITY_THRESHOLD", 0.6),
    ):
        result = await orchestrated_query(
            query=query, rag_mode="smart", chroma_client=None, redis_client=None, neo4j_driver=None,
            context_sources={"kb": True, "memory": False, "external": True},
        )
    return result, fetch


def _external(result: dict) -> list[dict]:
    return result["source_breakdown"]["external"]


@pytest.mark.asyncio
async def test_a_strong_kb_answer_does_not_go_to_the_web():
    result, fetch = await _ask(_kb(0.85))

    fetch.assert_not_awaited()
    assert _external(result) == []


@pytest.mark.asyncio
async def test_a_weak_kb_answer_does():
    result, fetch = await _ask(_kb(0.2))

    fetch.assert_awaited_once()
    assert [r["source_url"] for r in _external(result)] == ["https://example.com"]


@pytest.mark.asyncio
async def test_a_skipped_retrieval_does_not_go_to_the_web():
    result, fetch = await _ask(_kb(None, retrieval_skipped=True), query="good morning")

    fetch.assert_not_awaited()
    assert _external(result) == []


@pytest.mark.asyncio
async def test_external_off_is_still_off():
    fetch = AsyncMock(return_value=EXTERNAL)
    with (
        patch("core.agents.query_agent.agent_query", new=AsyncMock(return_value=_kb(0.1))),
        patch("app.agents.retrieval_orchestrator._query_external_sources", new=fetch),
    ):
        await orchestrated_query(
            query="q", rag_mode="smart", chroma_client=None, redis_client=None, neo4j_driver=None,
            context_sources={"kb": True, "memory": False, "external": False},
        )

    fetch.assert_not_awaited()
