# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Task 27 / audit C-9: ONNX cross-encoder failure must not crash queries.

After Bifrost was retired, the old ``_rerank_llm`` fallback path was broken
(it routed through Bifrost). A single ONNX load failure would therefore take
every query down. The graceful path now returns results in their input order
and tags each with ``reranker_status = 'onnx_failed_no_fallback'`` so the
caller can surface the degraded state.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


def _fixture_results() -> list[dict]:
    # Pre-sorted by relevance (descending) so the "input order" check is
    # meaningful — reranking would reorder them.
    return [
        {"content": "alpha", "domain": "docs", "filename": "a.md", "relevance": 0.9},
        {"content": "bravo", "domain": "docs", "filename": "b.md", "relevance": 0.6},
        {"content": "charlie", "domain": "docs", "filename": "c.md", "relevance": 0.3},
    ]


@pytest.mark.asyncio
async def test_onnx_failure_returns_original_order_and_status():
    """When the ONNX reranker raises, results come back in the input order
    with each item tagged ``reranker_status=onnx_failed_no_fallback`` — no
    LLM fallback, no crash."""
    from core.agents import query_agent

    results = _fixture_results()
    original_ids = [r["filename"] for r in results]

    with patch(
        "core.retrieval.reranker.rerank",
        side_effect=RuntimeError("ONNX session not initialised"),
    ):
        out = await query_agent._rerank_cross_encoder(results, query="anything")

    assert [r["filename"] for r in out] == original_ids, (
        "ONNX failure must preserve input order (no silent reshuffle)"
    )
    assert all(
        r.get("reranker_status") == "onnx_failed_no_fallback" for r in out
    ), "Every result must be tagged so the caller can surface the degraded state"


@pytest.mark.asyncio
async def test_onnx_success_does_not_tag_status():
    """Happy path: successful ONNX rerank does not set the degraded flag."""
    from core.agents import query_agent

    results = _fixture_results()

    def _fake_rerank(query, docs):  # signature of core.retrieval.reranker.rerank
        # Reverse to make the reorder observable.
        return list(reversed(docs))

    with patch("core.retrieval.reranker.rerank", side_effect=_fake_rerank):
        out = await query_agent._rerank_cross_encoder(results, query="anything")

    assert [r["filename"] for r in out] == ["c.md", "b.md", "a.md"]
    assert not any("reranker_status" in r for r in out)
