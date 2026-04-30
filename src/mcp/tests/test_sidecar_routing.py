# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for Workstream E Phase E.6.4 — sidecar routing for embeddings + rerank.

The sidecar HTTP client + auto-detection already had unit tests; this
suite verifies the wire-in: when ``inference_config`` says
``provider == "fastembed-sidecar"`` and the sidecar is reachable,
embeddings.OnnxEmbeddingFunction.__call__ and
query_agent._rerank_cross_encoder route through it; on any failure
they fall through to the local ONNX path silently.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _fake_cfg(provider: str, sidecar_available: bool):
    """Build a duck-typed inference-config replacement for the patches."""
    cfg = MagicMock()
    cfg.provider = provider
    cfg.sidecar_available = sidecar_available
    return cfg


# ---------------------------------------------------------------------------
# Embeddings — _maybe_embed_via_sidecar
# ---------------------------------------------------------------------------


def _make_ef():
    """Build an OnnxEmbeddingFunction without triggering ChromaDB init."""
    from core.utils.embeddings import OnnxEmbeddingFunction
    # Don't pass a model_id — only use the helper, never call __call__ end-to-end
    return OnnxEmbeddingFunction(model_id="dummy/model")


def test_maybe_embed_via_sidecar_returns_none_when_provider_is_not_sidecar():
    """ONNX-CPU mode: sidecar branch is dormant — return None to fall through."""
    ef = _make_ef()
    with patch("utils.inference_config.get_inference_config",
               return_value=_fake_cfg("onnx-cpu", sidecar_available=False)):
        result = ef._maybe_embed_via_sidecar(["hello"])
    assert result is None


def test_maybe_embed_via_sidecar_returns_none_when_sidecar_unreachable():
    """Provider says sidecar but auto-detection says it's down — fall through."""
    ef = _make_ef()
    with patch("utils.inference_config.get_inference_config",
               return_value=_fake_cfg("fastembed-sidecar", sidecar_available=False)):
        result = ef._maybe_embed_via_sidecar(["hello"])
    assert result is None


def test_maybe_embed_via_sidecar_uses_sidecar_when_active():
    """Provider says sidecar AND it's reachable → return embeddings from
    the sidecar without touching local ONNX."""
    ef = _make_ef()
    fake_embeddings = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    fake_sidecar = AsyncMock(return_value=fake_embeddings)

    with patch("utils.inference_config.get_inference_config",
               return_value=_fake_cfg("fastembed-sidecar", sidecar_available=True)), \
         patch("utils.inference_sidecar_client.sidecar_embed", fake_sidecar):
        result = ef._maybe_embed_via_sidecar(["hello", "world"])

    assert result == fake_embeddings
    fake_sidecar.assert_awaited_once_with(["hello", "world"])


def test_maybe_embed_via_sidecar_returns_none_on_inference_config_error():
    """If the config helper itself raises (e.g. import failure), fall
    through to local ONNX rather than crash the embed path."""
    ef = _make_ef()
    with patch("utils.inference_config.get_inference_config",
               side_effect=ImportError("simulated module load failure")):
        result = ef._maybe_embed_via_sidecar(["hello"])
    assert result is None


def test_maybe_embed_via_sidecar_returns_none_on_sidecar_failure():
    """A sidecar HTTP failure must not crash the embed path."""
    ef = _make_ef()
    with patch("utils.inference_config.get_inference_config",
               return_value=_fake_cfg("fastembed-sidecar", sidecar_available=True)), \
         patch("utils.inference_sidecar_client.sidecar_embed",
               side_effect=ConnectionError("simulated sidecar outage")):
        result = ef._maybe_embed_via_sidecar(["hello"])
    assert result is None


# ---------------------------------------------------------------------------
# Rerank — _maybe_rerank_via_sidecar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maybe_rerank_returns_none_when_provider_not_sidecar():
    from core.agents.query_agent import _maybe_rerank_via_sidecar

    results = [{"content": "doc a", "relevance": 0.5}]
    with patch("utils.inference_config.get_inference_config",
               return_value=_fake_cfg("onnx-cpu", sidecar_available=False)):
        out = await _maybe_rerank_via_sidecar(results, "query")
    assert out is None


@pytest.mark.asyncio
async def test_maybe_rerank_uses_sidecar_when_active_and_sorts_by_score():
    from core.agents.query_agent import _maybe_rerank_via_sidecar

    results = [
        {"content": "doc a", "relevance": 0.5},
        {"content": "doc b", "relevance": 0.5},
        {"content": "doc c", "relevance": 0.5},
    ]
    # Sidecar returns scores in input order — wire-in must apply + re-sort
    fake_scores = [0.30, 0.95, 0.60]
    fake_rerank = AsyncMock(return_value=fake_scores)

    with patch("utils.inference_config.get_inference_config",
               return_value=_fake_cfg("fastembed-sidecar", sidecar_available=True)), \
         patch("utils.inference_sidecar_client.sidecar_rerank", fake_rerank):
        out = await _maybe_rerank_via_sidecar(results, "query")

    assert out is not None
    assert len(out) == 3
    # Sorted by relevance descending: b (0.95) > c (0.60) > a (0.30)
    assert [r["content"] for r in out] == ["doc b", "doc c", "doc a"]
    # Reranker status tagged so downstream observability can attribute the path
    for r in out:
        assert r["reranker_status"] == "sidecar"
    # Sidecar was called with the documents in input order
    fake_rerank.assert_awaited_once()
    args, _ = fake_rerank.call_args
    assert args[0] == "query"
    assert args[1] == ["doc a", "doc b", "doc c"]


@pytest.mark.asyncio
async def test_maybe_rerank_returns_none_when_sidecar_call_fails():
    from core.agents.query_agent import _maybe_rerank_via_sidecar

    results = [{"content": "doc a", "relevance": 0.5}]
    with patch("utils.inference_config.get_inference_config",
               return_value=_fake_cfg("fastembed-sidecar", sidecar_available=True)), \
         patch("utils.inference_sidecar_client.sidecar_rerank",
               side_effect=ConnectionError("simulated sidecar outage")):
        out = await _maybe_rerank_via_sidecar(results, "query")
    assert out is None


@pytest.mark.asyncio
async def test_rerank_cross_encoder_falls_back_to_local_when_sidecar_skips():
    """End-to-end: sidecar returns None (provider not active) → local ONNX path
    runs. We mock the local ce_rerank to verify it gets called."""
    from core.agents import query_agent

    results = [{"content": "doc a", "relevance": 0.5}]
    fake_local = MagicMock(return_value=[{"content": "doc a", "relevance": 0.9}])
    with patch("core.retrieval.reranker.rerank", fake_local), \
         patch("utils.inference_config.get_inference_config",
               return_value=_fake_cfg("onnx-cpu", sidecar_available=False)):
        out = await query_agent._rerank_cross_encoder(results, "query")
    assert out[0]["relevance"] == 0.9
    fake_local.assert_called_once()
