# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for stage-aware routing + retry-on-502 in core.utils.internal_llm.

Two surfaces covered:

1. ``_resolve_stage_provider`` — env override beats pipeline mapping beats
   global default. Lets the LongMemEval scorer route to OpenRouter while
   memory_extract stays local.
2. ``_call_ollama`` retry loop — server-side 5xx, 429, and timeouts retry
   with exponential backoff before falling through to OpenRouter; 4xx
   classes and circuit-open are not retried.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from core.utils import internal_llm as mod

# ---------------------------------------------------------------------------
# _resolve_stage_provider
# ---------------------------------------------------------------------------


class TestResolveStageProvider:
    def test_no_stage_returns_default(self, monkeypatch):
        assert mod._resolve_stage_provider(None, "quenchforge") == "quenchforge"

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("PROVIDER_STAGE_LONGMEMEVAL_SCORE", "openrouter")
        assert (
            mod._resolve_stage_provider("longmemeval/score", "quenchforge")
            == "openrouter"
        )

    def test_dash_normalization(self, monkeypatch):
        monkeypatch.setenv("PROVIDER_STAGE_LONG_MEM_EVAL_SCORE", "openrouter")
        assert (
            mod._resolve_stage_provider("long-mem-eval/score", "quenchforge")
            == "openrouter"
        )

    def test_pipeline_providers_mapping(self, monkeypatch):
        monkeypatch.setattr(
            mod.config,
            "PIPELINE_PROVIDERS",
            {"claim_extraction": "openrouter"},
            raising=False,
        )
        # Make sure no env override is set for this stage
        monkeypatch.delenv("PROVIDER_STAGE_CLAIM_EXTRACTION", raising=False)
        assert (
            mod._resolve_stage_provider("claim_extraction", "quenchforge")
            == "openrouter"
        )

    def test_unknown_stage_falls_back_to_default(self, monkeypatch):
        monkeypatch.setattr(
            mod.config, "PIPELINE_PROVIDERS", {}, raising=False,
        )
        monkeypatch.delenv("PROVIDER_STAGE_LONGMEMEVAL_SCORE", raising=False)
        assert (
            mod._resolve_stage_provider("longmemeval/score", "quenchforge")
            == "quenchforge"
        )


# ---------------------------------------------------------------------------
# _call_ollama retry behaviour
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._body


def _wire_retry_harness(monkeypatch: pytest.MonkeyPatch, post_side_effects):
    """Wire a fake httpx client that returns/raises a scripted sequence.

    ``post_side_effects`` is a list of objects: either a dict (used as the
    successful JSON body) or an exception instance to raise on that call.
    """
    call_count = {"n": 0}

    async def _post(url: str, *, json: dict) -> _FakeResponse:  # noqa: ARG001
        idx = call_count["n"]
        call_count["n"] += 1
        if idx >= len(post_side_effects):
            raise AssertionError(f"unexpected call #{idx + 1}")
        effect = post_side_effects[idx]
        if isinstance(effect, Exception):
            raise effect
        return _FakeResponse(effect)

    fake_client = MagicMock()
    fake_client.post = _post
    monkeypatch.setattr(mod, "_get_ollama_client", AsyncMock(return_value=fake_client))

    class _PassThroughBreaker:
        async def call(self, fn):  # type: ignore[no-untyped-def]
            return await fn()

    monkeypatch.setattr(mod, "get_breaker", lambda _name: _PassThroughBreaker())
    monkeypatch.setenv("OLLAMA_URL", "http://test-host:11434")
    monkeypatch.setattr(mod.config, "OLLAMA_DEFAULT_MODEL", "llama3.2:3b", raising=False)
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MODEL", "", raising=False)
    # Tight backoff so the test stays fast.
    monkeypatch.setenv("INTERNAL_LLM_RETRY_BACKOFF", "0.001")
    monkeypatch.setenv("INTERNAL_LLM_MAX_RETRIES", "3")
    return call_count


def _make_http_status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://test-host:11434/api/chat")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status}", request=request, response=response,
    )


@pytest.mark.asyncio
async def test_retry_succeeds_after_transient_502(monkeypatch):
    """A single 502 should retry and succeed without falling through."""
    counter = _wire_retry_harness(
        monkeypatch,
        [
            _make_http_status_error(502),
            {"message": {"content": "ok"}},
        ],
    )
    result = await mod._call_ollama(
        [{"role": "user", "content": "hi"}], temperature=0, max_tokens=5,
    )
    assert result == "ok"
    assert counter["n"] == 2  # first call failed, second succeeded


@pytest.mark.asyncio
async def test_retry_succeeds_after_transient_timeout(monkeypatch):
    counter = _wire_retry_harness(
        monkeypatch,
        [
            httpx.TimeoutException("slow"),
            {"message": {"content": "ok"}},
        ],
    )
    result = await mod._call_ollama(
        [{"role": "user", "content": "hi"}], temperature=0, max_tokens=5,
    )
    assert result == "ok"
    assert counter["n"] == 2


@pytest.mark.asyncio
async def test_retry_succeeds_after_429(monkeypatch):
    """429 should be retried like 5xx — rate limit, not a bad request."""
    counter = _wire_retry_harness(
        monkeypatch,
        [
            _make_http_status_error(429),
            {"message": {"content": "ok"}},
        ],
    )
    result = await mod._call_ollama(
        [{"role": "user", "content": "hi"}], temperature=0, max_tokens=5,
    )
    assert result == "ok"
    assert counter["n"] == 2


@pytest.mark.asyncio
async def test_4xx_not_retried(monkeypatch):
    """400 is a bad request — retrying it just hits the same wall.

    Falls through to OpenRouter; we don't mock that path so we expect the
    fall-through to fail loudly when it tries to call OpenRouter with no
    key. The test only asserts the daemon-side wasn't retried.
    """
    counter = _wire_retry_harness(
        monkeypatch,
        [
            _make_http_status_error(400),
            # No second daemon call expected — the fall-through path runs
            # but doesn't go through _post.
        ],
    )
    # Block the OpenRouter fall-through so we can see exit cleanly.
    from core.utils import llm_client

    async def _stub_call_llm(*args, **kwargs):  # noqa: ARG001
        return "fallback-stub"

    monkeypatch.setattr(llm_client, "call_llm", _stub_call_llm)
    result = await mod._call_ollama(
        [{"role": "user", "content": "hi"}], temperature=0, max_tokens=5,
    )
    assert result == "fallback-stub"
    assert counter["n"] == 1  # only ONE daemon attempt


@pytest.mark.asyncio
async def test_retries_exhaust_then_falls_through(monkeypatch):
    """3 consecutive 502s should exhaust retries and fall through."""
    counter = _wire_retry_harness(
        monkeypatch,
        [
            _make_http_status_error(502),
            _make_http_status_error(502),
            _make_http_status_error(502),
        ],
    )
    from core.utils import llm_client

    async def _stub_call_llm(*args, **kwargs):  # noqa: ARG001
        return "openrouter-stub"

    monkeypatch.setattr(llm_client, "call_llm", _stub_call_llm)
    result = await mod._call_ollama(
        [{"role": "user", "content": "hi"}], temperature=0, max_tokens=5,
    )
    assert result == "openrouter-stub"
    assert counter["n"] == 3
