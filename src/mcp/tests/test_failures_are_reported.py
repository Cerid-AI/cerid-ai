# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A retrieval stage that fails says so instead of handing back an ordinary
result (audit F57, F103)."""
from __future__ import annotations

import pytest

from core.agents import query_agent as qa
from core.retrieval.query_decomposer import parallel_retrieve


@pytest.mark.asyncio
async def test_every_sub_query_failing_is_an_error_not_an_empty_result():
    async def down(q, **_k):
        raise ConnectionError("vector store unreachable")

    with pytest.raises(ConnectionError):
        await parallel_retrieve(["first half", "second half"], down)


@pytest.mark.asyncio
async def test_one_sub_query_failing_keeps_the_others():
    async def half(q, **_k):
        if q == "second half":
            raise ConnectionError("vector store unreachable")
        return [{"content": "kept", "relevance": 0.5}]

    out = await parallel_retrieve(["first half", "second half"], half)

    assert [r["content"] for r in out] == ["kept"]


@pytest.mark.asyncio
async def test_a_failed_llm_rerank_is_reported_as_degraded(monkeypatch):
    async def unparseable(*_a, **_k):
        return "not a ranking"

    monkeypatch.setattr("core.utils.internal_llm.call_internal_llm", unparseable)
    monkeypatch.setattr(qa.config, "RERANK_MODE", "llm")
    monkeypatch.setattr(qa.config, "RERANK_PREFER_LOCAL", False, raising=False)
    rows = [
        {"content": f"row {i}", "relevance": 0.9 - i / 10, "domain": "coding", "filename": f"f{i}.md"}
        for i in range(3)
    ]

    await qa.rerank_results(rows, "a question", use_reranking=True)

    assert "unavailable" in qa.rerank_degraded_reason()
