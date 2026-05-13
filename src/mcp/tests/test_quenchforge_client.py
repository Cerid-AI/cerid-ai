# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Quenchforge GPU-routing client (v0.93.8).

Quenchforge is the Intel-Mac + AMD-GPU bridge that gives cerid GPU
acceleration for workloads ONNX runtime can't reach on that hardware
(no AMD-Mac ONNX execution provider exists; ROCm is Linux-only).

These tests verify:

1. The opt-in flags ``EMBEDDINGS_PROVIDER=quenchforge`` and
   ``RERANK_PROVIDER=quenchforge`` toggle the routing layer.
2. ``QUENCHFORGE_EMBED_MODEL`` and ``QUENCHFORGE_RERANK_MODEL`` are
   required when their respective provider is selected.
3. The embedding client validates output dimensions match
   ``EMBEDDING_DIMENSIONS`` (the ChromaDB contract).
4. The rerank client emits scores aligned with the input order even
   when Quenchforge's OpenAI-wire response sorts the items.
5. The health probe hits ``/health`` (the canonical endpoint per the
   upstream Quenchforge gateway, NOT ``/api/tags`` which is heavier).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _async_mock_response(json_data: dict, status: int = 200) -> AsyncMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json = MagicMock(return_value=json_data)
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Provider-flag helpers
# ---------------------------------------------------------------------------

def test_embeddings_provider_flag_off_by_default(monkeypatch):
    from utils.quenchforge_client import is_embeddings_provider_quenchforge
    monkeypatch.delenv("EMBEDDINGS_PROVIDER", raising=False)
    assert is_embeddings_provider_quenchforge() is False


def test_embeddings_provider_flag_picks_quenchforge(monkeypatch):
    from utils.quenchforge_client import is_embeddings_provider_quenchforge
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "quenchforge")
    assert is_embeddings_provider_quenchforge() is True


def test_embeddings_provider_flag_case_insensitive(monkeypatch):
    from utils.quenchforge_client import is_embeddings_provider_quenchforge
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "QuenchForge")
    assert is_embeddings_provider_quenchforge() is True


def test_embeddings_provider_flag_other_values(monkeypatch):
    from utils.quenchforge_client import is_embeddings_provider_quenchforge
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "sidecar")
    assert is_embeddings_provider_quenchforge() is False


def test_rerank_provider_flag(monkeypatch):
    from utils.quenchforge_client import is_rerank_provider_quenchforge
    monkeypatch.delenv("RERANK_PROVIDER", raising=False)
    assert is_rerank_provider_quenchforge() is False
    monkeypatch.setenv("RERANK_PROVIDER", "quenchforge")
    assert is_rerank_provider_quenchforge() is True


# ---------------------------------------------------------------------------
# embed — required-model guard + dimension validation + happy path
# ---------------------------------------------------------------------------

async def test_embed_requires_model_env(monkeypatch):
    from utils.quenchforge_client import quenchforge_embed
    monkeypatch.delenv("QUENCHFORGE_EMBED_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="QUENCHFORGE_EMBED_MODEL"):
        await quenchforge_embed(["hello"])


async def test_embed_validates_dimension(monkeypatch):
    """A model returning the wrong dimension must raise — not silently
    corrupt the ChromaDB index."""
    monkeypatch.setenv("QUENCHFORGE_EMBED_MODEL", "test-embed")
    import config.settings as settings_mod
    monkeypatch.setattr(settings_mod, "EMBEDDING_DIMENSIONS", 768)

    from utils import quenchforge_client

    # Quenchforge returned a 512-dim vector — must reject.
    fake_response = _async_mock_response({
        "data": [{"index": 0, "embedding": [0.0] * 512}],
    })
    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=fake_response)

    fake_breaker = MagicMock()
    async def _passthrough(coro_fn):
        return await coro_fn()
    fake_breaker.call = _passthrough

    with patch.object(quenchforge_client, "_get_client", AsyncMock(return_value=fake_client)):
        with patch.object(quenchforge_client, "get_breaker", return_value=fake_breaker):
            with pytest.raises(ValueError, match="dimension mismatch"):
                await quenchforge_client.quenchforge_embed(["hello"])


async def test_embed_sorts_by_index(monkeypatch):
    """OpenAI wire is allowed to return items in arbitrary order; we
    must align the output to the input order via the ``index`` field."""
    monkeypatch.setenv("QUENCHFORGE_EMBED_MODEL", "test-embed")
    import config.settings as settings_mod
    monkeypatch.setattr(settings_mod, "EMBEDDING_DIMENSIONS", 3)
    from utils import quenchforge_client

    fake_response = _async_mock_response({
        "data": [
            {"index": 2, "embedding": [3.0, 3.0, 3.0]},
            {"index": 0, "embedding": [1.0, 1.0, 1.0]},
            {"index": 1, "embedding": [2.0, 2.0, 2.0]},
        ],
    })
    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=fake_response)
    fake_breaker = MagicMock()
    async def _passthrough(coro_fn):
        return await coro_fn()
    fake_breaker.call = _passthrough

    with patch.object(quenchforge_client, "_get_client", AsyncMock(return_value=fake_client)):
        with patch.object(quenchforge_client, "get_breaker", return_value=fake_breaker):
            result = await quenchforge_client.quenchforge_embed(["a", "b", "c"])
    assert result == [[1.0, 1.0, 1.0], [2.0, 2.0, 2.0], [3.0, 3.0, 3.0]]


# ---------------------------------------------------------------------------
# rerank — required-model guard + alignment with input order
# ---------------------------------------------------------------------------

async def test_rerank_requires_model_env(monkeypatch):
    from utils.quenchforge_client import quenchforge_rerank
    monkeypatch.delenv("QUENCHFORGE_RERANK_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="QUENCHFORGE_RERANK_MODEL"):
        await quenchforge_rerank("q", ["d1", "d2"])


async def test_rerank_aligns_scores_to_input_order(monkeypatch):
    """Quenchforge's /v1/rerank may return results sorted by score; we
    must un-sort by ``index`` so the caller's score array maps 1:1 to
    the input documents."""
    monkeypatch.setenv("QUENCHFORGE_RERANK_MODEL", "test-rerank")
    from utils import quenchforge_client

    fake_response = _async_mock_response({
        "results": [
            {"index": 1, "relevance_score": 0.9},  # best
            {"index": 0, "relevance_score": 0.3},
            {"index": 2, "relevance_score": 0.5},
        ],
    })
    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=fake_response)
    fake_breaker = MagicMock()
    async def _passthrough(coro_fn):
        return await coro_fn()
    fake_breaker.call = _passthrough

    with patch.object(quenchforge_client, "_get_client", AsyncMock(return_value=fake_client)):
        with patch.object(quenchforge_client, "get_breaker", return_value=fake_breaker):
            scores = await quenchforge_client.quenchforge_rerank("q", ["d0", "d1", "d2"])
    assert scores == [0.3, 0.9, 0.5]


async def test_rerank_handles_missing_indices(monkeypatch):
    """Defense-in-depth: a partial response should not raise; missing
    indices default to 0.0 so the output length matches the input."""
    monkeypatch.setenv("QUENCHFORGE_RERANK_MODEL", "test-rerank")
    from utils import quenchforge_client

    # Only index 0 returned; we expect zeros for the rest.
    fake_response = _async_mock_response({
        "results": [{"index": 0, "relevance_score": 0.7}],
    })
    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=fake_response)
    fake_breaker = MagicMock()
    async def _passthrough(coro_fn):
        return await coro_fn()
    fake_breaker.call = _passthrough

    with patch.object(quenchforge_client, "_get_client", AsyncMock(return_value=fake_client)):
        with patch.object(quenchforge_client, "get_breaker", return_value=fake_breaker):
            scores = await quenchforge_client.quenchforge_rerank("q", ["d0", "d1", "d2"])
    assert scores == [0.7, 0.0, 0.0]


# ---------------------------------------------------------------------------
# health — verified canonical endpoint
# ---------------------------------------------------------------------------

async def test_health_probes_slash_health_not_api_tags(monkeypatch):
    """The Quenchforge gateway exposes /health as the lightweight liveness
    probe — verified directly against internal/gateway/gateway.go.
    /api/tags also works but does an FS walk; we probe the cheap path."""
    from utils import quenchforge_client

    captured = {}

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json = MagicMock(return_value={"status": "ok"})

    async def _fake_get(url, timeout=None):
        captured["url"] = url
        return fake_response

    fake_client = MagicMock()
    fake_client.get = _fake_get

    with patch.object(quenchforge_client, "_get_client", AsyncMock(return_value=fake_client)):
        result = await quenchforge_client.quenchforge_health()

    assert captured["url"].endswith("/health"), (
        f"health probe must hit /health, got {captured['url']!r}"
    )
    assert result == {"status": "ok"}


async def test_health_returns_none_on_failure(monkeypatch):
    from utils import quenchforge_client

    async def _failing_get(_url, timeout=None):
        raise RuntimeError("connection refused")

    fake_client = MagicMock()
    fake_client.get = _failing_get

    with patch.object(quenchforge_client, "_get_client", AsyncMock(return_value=fake_client)):
        result = await quenchforge_client.quenchforge_health()
    assert result is None


# ---------------------------------------------------------------------------
# URL resolution
# ---------------------------------------------------------------------------

def test_url_resolves_quenchforge_url_first(monkeypatch):
    from utils.quenchforge_client import _get_quenchforge_url
    monkeypatch.setenv("QUENCHFORGE_URL", "http://qf:11500")
    monkeypatch.setenv("OLLAMA_URL", "http://ol:11434")
    assert _get_quenchforge_url() == "http://qf:11500"


def test_url_falls_back_to_ollama_url(monkeypatch):
    from utils.quenchforge_client import _get_quenchforge_url
    monkeypatch.delenv("QUENCHFORGE_URL", raising=False)
    monkeypatch.setenv("OLLAMA_URL", "http://ol:11434")
    assert _get_quenchforge_url() == "http://ol:11434"


def test_url_final_fallback_to_localhost(monkeypatch):
    from utils.quenchforge_client import _get_quenchforge_url
    monkeypatch.delenv("QUENCHFORGE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    assert _get_quenchforge_url() == "http://localhost:11434"
