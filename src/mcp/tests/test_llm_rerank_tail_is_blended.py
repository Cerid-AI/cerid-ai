# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The LLM rerank leg blends the rows it did not rank, as the other legs do (audit F56).

Candidates were blended RERANK_LLM_WEIGHT x rank score + RERANK_ORIGINAL_WEIGHT x
retrieval, while rows past QUERY_RERANK_CANDIDATES kept their raw retrieval
relevance. After the later sort an unranked row outranked candidates the LLM
placed low. The cross-encoder leg counts such rows as scored 0; the LLM leg now
does the same. The LLM call is faked; the leg runs for real.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.agents.query_agent import _rerank_llm


def _row(name: str, relevance: float) -> dict:
    return {"content": name, "relevance": relevance, "retrieval_relevance": relevance,
            "chunk_id": name, "domain": "general", "filename": name}


@pytest.mark.asyncio
async def test_a_row_the_llm_never_saw_does_not_outrank_one_it_ranked():
    rows = [_row(f"c{i}.md", 0.5) for i in range(4)] + [_row("tail.md", 0.49)]
    with (
        patch("config.QUERY_RERANK_CANDIDATES", 4),
        patch("core.utils.internal_llm.call_internal_llm", new=AsyncMock(return_value="[0, 1, 2, 3]")),
    ):
        out = await _rerank_llm(rows, "q")

    ordered = sorted(out, key=lambda r: r["relevance"], reverse=True)
    assert ordered[-1]["filename"] == "tail.md"
