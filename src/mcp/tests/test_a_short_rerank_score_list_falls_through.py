# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A remote rerank leg that returns fewer scores than documents is a failure (audit F55).

``zip(..., strict=False)`` scored as many rows as there were scores and left
the tail on its retrieval relevance with no ``rerank_score``. The vetting step
then treats an unscored row as rejected, so a misbehaving sidecar silently cost
the answer every row it left unscored. The leg now falls through to the in-process
cross-encoder, which scores every row. The sidecar's HTTP client and the
cross-encoder model are faked; the rerank chain and the vetting run for real.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.agents import query_agent


def _row(name: str, relevance: float) -> dict:
    return {"content": name, "relevance": relevance, "retrieval_relevance": relevance,
            "chunk_id": name, "domain": "general", "filename": name, "pack_id": ""}


@pytest.mark.asyncio
async def test_a_short_score_list_does_not_drop_the_unscored_row():
    rows = [_row("a.md", 0.9), _row("b.md", 0.8), _row("c.md", 0.7)]
    sidecar = SimpleNamespace(provider="fastembed-sidecar", sidecar_available=True)
    with (
        patch("config.RERANK_MODE", "cross_encoder"),
        patch("config.ENABLE_CASCADE_RERANK", False),
        patch("core.agents.query_agent._maybe_rerank_via_quenchforge", new=AsyncMock(return_value=None)),
        patch("utils.inference_config.get_inference_config", return_value=sidecar),
        patch("utils.inference_sidecar_client.sidecar_rerank", new=AsyncMock(return_value=[0.9, 0.9])),
        patch("core.retrieval.reranker._score_pairs", lambda _q, docs: [0.8 for _ in docs]),
    ):
        out = query_agent.vetted_by_cross_encoder(await query_agent.rerank_results(rows, "q"))

    assert sorted(r["filename"] for r in out) == ["a.md", "b.md", "c.md"]
    assert {r["rerank_score"] for r in out} == {0.8}  # every row scored by one leg
