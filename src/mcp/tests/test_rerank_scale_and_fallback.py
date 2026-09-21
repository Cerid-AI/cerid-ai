# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""F108 / F109 — rerank score scale and fallback visibility.

F108: the quenchforge and sidecar legs REPLACED ``relevance`` with the raw
cross-encoder sigmoid while the local ONNX leg blended it with the retrieval
score. Two contracts are thresholded against that number — the user-facing
``confidence`` and the CRAG gate at RETRIEVAL_QUALITY_THRESHOLD=0.4 — so
flipping RERANK_PROVIDER silently redefined both: bge-reranker-v2-m3 puts a
correct top answer near sigmoid(-4) ≈ 0.02.

F109: when the configured leg fails, the chain serves from a weaker one and
says nothing in the response. ``reranker_status`` is per-result and has zero
readers.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import config
from core.agents import query_agent


def _hit(relevance: float, content: str = "body") -> dict[str, Any]:
    return {
        "content": content,
        "relevance": relevance,
        "retrieval_relevance": relevance,
        "chunk_id": "c1",
        "domain": "general",
        "filename": "f.md",
        "pack_id": "",
    }


def _blend(model_score: float, original: float) -> float:
    return round(
        config.RERANK_CE_WEIGHT * model_score
        + config.RERANK_ORIGINAL_WEIGHT * original,
        4,
    )


@pytest.mark.asyncio
async def test_quenchforge_scores_are_blended_not_substituted() -> None:
    with (
        patch("utils.quenchforge_client.is_rerank_provider_quenchforge", return_value=True),
        patch("utils.quenchforge_client.quenchforge_rerank", new=AsyncMock(return_value=[0.02])),
    ):
        out = await query_agent._maybe_rerank_via_quenchforge([_hit(0.72)], "q")

    assert out is not None
    assert out[0]["relevance"] == _blend(0.02, 0.72), out[0]


@pytest.mark.asyncio
async def test_sidecar_scores_are_blended_not_substituted() -> None:
    cfg = SimpleNamespace(provider="fastembed-sidecar", sidecar_available=True)
    with (
        patch("utils.inference_config.get_inference_config", return_value=cfg),
        patch("utils.inference_sidecar_client.sidecar_rerank", new=AsyncMock(return_value=[0.02])),
    ):
        out = await query_agent._maybe_rerank_via_sidecar([_hit(0.72)], "q")

    assert out is not None
    assert out[0]["relevance"] == _blend(0.02, 0.72), out[0]


@pytest.mark.asyncio
async def test_sidecar_failure_is_recorded_as_a_fallback() -> None:
    cfg = SimpleNamespace(provider="fastembed-sidecar", sidecar_available=True)
    with (
        patch("utils.inference_config.get_inference_config", return_value=cfg),
        patch(
            "utils.inference_sidecar_client.sidecar_rerank",
            new=AsyncMock(side_effect=RuntimeError("sidecar 503")),
        ),
        patch("core.utils.inference_health.record_fallback") as rec,
    ):
        out = await query_agent._maybe_rerank_via_sidecar([_hit(0.72)], "q")

    assert out is None
    rec.assert_called_once()
    assert rec.call_args.kwargs["configured"] == "sidecar"
    assert rec.call_args.kwargs["served_by"] == "onnx"


@pytest.mark.asyncio
async def test_rerank_fallback_is_visible_in_the_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A query served on the weaker leg must say so in its own envelope."""
    monkeypatch.setattr(config, "RERANK_MODE", "cross_encoder")

    def _ce_rerank(query: str, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return results

    with (
        patch("utils.quenchforge_client.is_rerank_provider_quenchforge", return_value=True),
        patch(
            "utils.quenchforge_client.quenchforge_rerank",
            new=AsyncMock(side_effect=RuntimeError("quenchforge 503")),
        ),
        patch("core.retrieval.reranker.rerank", new=_ce_rerank),
    ):
        await query_agent.rerank_results([_hit(0.72)], "q")
        reason = query_agent.rerank_degraded_reason()

    assert reason, "rerank fallback left no signal for the response envelope"
    assert "quenchforge" in reason


@pytest.mark.asyncio
async def test_no_degraded_reason_when_configured_leg_serves() -> None:
    with (
        patch("utils.quenchforge_client.is_rerank_provider_quenchforge", return_value=True),
        patch("utils.quenchforge_client.quenchforge_rerank", new=AsyncMock(return_value=[0.5])),
    ):
        await query_agent.rerank_results([_hit(0.72)], "q")
        assert query_agent.rerank_degraded_reason() == ""


class _RecordingChroma:
    """Minimal Chroma double returning one chunk for the general domain."""

    def list_collections(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name=config.collection_name("general"))]

    def get_collection(self, name: str) -> Any:
        return SimpleNamespace(query=lambda **kw: {
            "ids": [["c1"]],
            "documents": [["body"]],
            "metadatas": [[{"filename": "f.md", "artifact_id": "a1"}]],
            "distances": [[0.2]],
        })


@pytest.mark.asyncio
async def test_envelope_carries_the_rerank_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: the answer itself must disclose the weaker leg."""
    from core.retrieval import bm25 as bm25_mod

    monkeypatch.setattr(bm25_mod, "is_available", lambda: False)
    monkeypatch.setattr(config, "RERANK_MODE", "cross_encoder")

    with (
        patch("utils.quenchforge_client.is_rerank_provider_quenchforge", return_value=True),
        patch(
            "utils.quenchforge_client.quenchforge_rerank",
            new=AsyncMock(side_effect=RuntimeError("quenchforge 503")),
        ),
        patch("core.retrieval.reranker.rerank", new=lambda q, r: r),
    ):
        env = await query_agent.agent_query(
            query="anything",
            domains=["general"],
            top_k=3,
            chroma_client=_RecordingChroma(),
        )

    assert env.get("retrieval_degraded") is True, env.keys()
    assert "quenchforge" in env.get("degraded_reason", "")
