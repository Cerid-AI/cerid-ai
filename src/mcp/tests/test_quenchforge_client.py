# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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

import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


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

async def test_rerank_defaults_model_when_env_unset(monkeypatch):
    """rerank carries a safe default (a cross-encoder score needs no stored
    vectors), so an unset env falls back to bge-reranker-v2-m3 instead of
    raising — unlike embed, whose model must match the corpus's vector space."""
    monkeypatch.delenv("QUENCHFORGE_RERANK_MODEL", raising=False)
    import config.settings as settings_mod
    monkeypatch.setattr(settings_mod, "QUENCHFORGE_RERANK_MODEL", "bge-reranker-v2-m3")
    from utils import quenchforge_client

    fake_response = _async_mock_response({"results": [{"index": 0, "relevance_score": 0.4}]})
    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=fake_response)
    fake_breaker = MagicMock()
    async def _passthrough(coro_fn):
        return await coro_fn()
    fake_breaker.call = _passthrough

    with patch.object(quenchforge_client, "_get_client", AsyncMock(return_value=fake_client)):
        with patch.object(quenchforge_client, "get_breaker", return_value=fake_breaker):
            scores = await quenchforge_client.quenchforge_rerank("q", ["d0"])

    assert scores == [pytest.approx(_sigmoid(0.4))]
    assert fake_client.post.call_args.kwargs["json"]["model"] == "bge-reranker-v2-m3"


async def test_rerank_aligns_scores_to_input_order(monkeypatch):
    """Quenchforge's /v1/rerank may return results sorted by score; we
    must un-sort by ``index`` so the caller's score array maps 1:1 to
    the input documents. Scores are RAW LOGITS and must come back
    sigmoid-mapped into [0, 1]."""
    monkeypatch.setenv("QUENCHFORGE_RERANK_MODEL", "test-rerank")
    from utils import quenchforge_client

    fake_response = _async_mock_response({
        "results": [
            {"index": 1, "relevance_score": 4.1},   # best (strong positive logit)
            {"index": 0, "relevance_score": -5.2},  # typical adjacent-doc logit
            {"index": 2, "relevance_score": -1.2},
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
    assert scores == [
        pytest.approx(_sigmoid(-5.2)),
        pytest.approx(_sigmoid(4.1)),
        pytest.approx(_sigmoid(-1.2)),
    ]
    # Order-preserving map: relative ranking must survive the activation.
    assert scores[1] > scores[2] > scores[0]


async def test_rerank_sigmoid_maps_raw_logits_into_unit_interval(monkeypatch):
    """Regression (live 2026-07-14): llama.cpp's /v1/rerank emits raw
    bge-reranker logits (roughly [-11, +11]); passing them through as
    relevance made every negative-logit candidate floor to zero downstream,
    so /query returned EMPTY for any query lacking a strongly-positive
    match. The client owns the sigmoid so all three rerank legs
    (quenchforge / sidecar / local ONNX) honor the same [0, 1] contract."""
    monkeypatch.setenv("QUENCHFORGE_RERANK_MODEL", "test-rerank")
    from utils import quenchforge_client

    fake_response = _async_mock_response({
        "results": [
            {"index": 0, "relevance_score": -10.6},
            {"index": 1, "relevance_score": 11.0},
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
            scores = await quenchforge_client.quenchforge_rerank("q", ["d0", "d1"])
    assert 0.0 < scores[0] < 0.5 < scores[1] < 1.0


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
    # Scored docs are sigmoid-mapped; MISSING docs stay 0.0 (post-sigmoid
    # floor), never sigmoid(0)=0.5 — an absent doc must not outrank a
    # scored-irrelevant one.
    assert scores == [pytest.approx(_sigmoid(0.7)), 0.0, 0.0]


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


# ---------------------------------------------------------------------------
# 503 / Retry-After handling — back-pressure must NOT trip the breaker
# ---------------------------------------------------------------------------


def _resp_with_status(status: int, *, retry_after: str | None = None, json_data: dict | None = None):
    """Build a fake httpx Response with the given status + optional headers."""
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"Retry-After": retry_after} if retry_after is not None else {}
    if json_data is not None:
        resp.json = MagicMock(return_value=json_data)
    else:
        resp.json = MagicMock(return_value={})
    if 400 <= status < 600:
        import httpx
        # Build a real HTTPStatusError so raise_for_status() round-trips
        # through the breaker the way httpx does in production.
        err = httpx.HTTPStatusError(
            f"HTTP {status}",
            request=MagicMock(),
            response=MagicMock(status_code=status),
        )
        resp.raise_for_status = MagicMock(side_effect=err)
    else:
        resp.raise_for_status = MagicMock()
    return resp


async def test_503_retries_then_succeeds(monkeypatch):
    """A 503 should trigger a Retry-After sleep + retry, not bubble up.

    The breaker must NOT see the 503 — it's back-pressure, not a slot
    failure. After the slot recovers (returns 200), the call succeeds
    normally.
    """
    monkeypatch.setenv("QUENCHFORGE_EMBED_MODEL", "test-embed")
    import config.settings as settings_mod
    monkeypatch.setattr(settings_mod, "EMBEDDING_DIMENSIONS", 3)
    from utils import quenchforge_client

    # First call: 503 with Retry-After: 0; second call: 200 OK.
    fake_client = MagicMock()
    fake_client.post = AsyncMock(side_effect=[
        _resp_with_status(503, retry_after="0"),
        _resp_with_status(200, json_data={
            "data": [{"index": 0, "embedding": [1.0, 1.0, 1.0]}],
        }),
    ])
    fake_breaker = MagicMock()
    async def _passthrough(coro_fn):
        return await coro_fn()
    fake_breaker.call = _passthrough

    with patch.object(quenchforge_client, "_get_client", AsyncMock(return_value=fake_client)):
        with patch.object(quenchforge_client, "get_breaker", return_value=fake_breaker):
            result = await quenchforge_client.quenchforge_embed(["hello"])
    assert result == [[1.0, 1.0, 1.0]]
    # Two POSTs total — initial + 1 retry.
    assert fake_client.post.await_count == 2


async def test_503_exhausts_retries_raises(monkeypatch):
    """If the slot keeps returning 503, the helper raises after
    _MAX_503_RETRIES so the breaker eventually sees the failure."""
    monkeypatch.setenv("QUENCHFORGE_EMBED_MODEL", "test-embed")
    import config.settings as settings_mod
    monkeypatch.setattr(settings_mod, "EMBEDDING_DIMENSIONS", 3)
    from utils import quenchforge_client

    fake_client = MagicMock()
    # Always 503.
    fake_client.post = AsyncMock(
        return_value=_resp_with_status(503, retry_after="0"),
    )
    fake_breaker = MagicMock()
    async def _passthrough(coro_fn):
        return await coro_fn()
    fake_breaker.call = _passthrough

    with patch.object(quenchforge_client, "_get_client", AsyncMock(return_value=fake_client)):
        with patch.object(quenchforge_client, "get_breaker", return_value=fake_breaker):
            with pytest.raises(Exception):
                await quenchforge_client.quenchforge_embed(["hello"])
    # _MAX_503_RETRIES = 3 → 4 total attempts (initial + 3 retries).
    assert fake_client.post.await_count == 4


async def test_502_propagates_to_breaker(monkeypatch):
    """502 Bad Gateway = slot dead. Must propagate to the breaker so
    the breaker can open + the embedding chain falls through. The
    Retry-After path must NOT swallow 502s."""
    monkeypatch.setenv("QUENCHFORGE_EMBED_MODEL", "test-embed")
    import config.settings as settings_mod
    monkeypatch.setattr(settings_mod, "EMBEDDING_DIMENSIONS", 3)
    from utils import quenchforge_client

    fake_client = MagicMock()
    fake_client.post = AsyncMock(
        return_value=_resp_with_status(502),
    )
    fake_breaker = MagicMock()
    async def _passthrough(coro_fn):
        return await coro_fn()
    fake_breaker.call = _passthrough

    with patch.object(quenchforge_client, "_get_client", AsyncMock(return_value=fake_client)):
        with patch.object(quenchforge_client, "get_breaker", return_value=fake_breaker):
            with pytest.raises(Exception):
                await quenchforge_client.quenchforge_embed(["hello"])
    # 502 should NOT trigger the retry loop — exactly one POST.
    assert fake_client.post.await_count == 1


async def test_retry_after_clamped_to_max(monkeypatch):
    """A misconfigured server claiming Retry-After: 600 must not pin
    the calling thread for 10 minutes. We clamp to 5s."""
    monkeypatch.setenv("QUENCHFORGE_EMBED_MODEL", "test-embed")
    import config.settings as settings_mod
    monkeypatch.setattr(settings_mod, "EMBEDDING_DIMENSIONS", 3)
    from utils import quenchforge_client

    sleep_calls: list[float] = []
    async def _spy_sleep(seconds):
        sleep_calls.append(seconds)

    fake_client = MagicMock()
    fake_client.post = AsyncMock(side_effect=[
        _resp_with_status(503, retry_after="600"),
        _resp_with_status(200, json_data={
            "data": [{"index": 0, "embedding": [1.0, 1.0, 1.0]}],
        }),
    ])
    fake_breaker = MagicMock()
    async def _passthrough(coro_fn):
        return await coro_fn()
    fake_breaker.call = _passthrough

    with patch.object(quenchforge_client, "_get_client", AsyncMock(return_value=fake_client)):
        with patch.object(quenchforge_client, "get_breaker", return_value=fake_breaker):
            with patch.object(quenchforge_client.asyncio, "sleep", new=_spy_sleep):
                await quenchforge_client.quenchforge_embed(["hello"])

    # Should have slept exactly once before the retry, clamped to 5s.
    assert sleep_calls == [5.0]


def test_parse_retry_after_handles_invalid_values():
    from utils.quenchforge_client import _parse_retry_after
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("") is None
    assert _parse_retry_after("not-a-number") is None
    # HTTP-date form is intentionally unsupported (would parse as None).
    assert _parse_retry_after("Fri, 31 Dec 1999 23:59:59 GMT") is None
    # Valid integer seconds.
    assert _parse_retry_after("2") == 2.0
    assert _parse_retry_after("  3  ") == 3.0
