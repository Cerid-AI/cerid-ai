# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Quenchforge circuit-breaker mitigation tests.

Regression cover for the 2026-06-06 edge case: a healthy Quenchforge
backend was locked out by its circuit breaker because

  1. the chat path's retry loop wrapped ``breaker.call`` *inside* the
     per-attempt loop, so one transiently-loading slot (502 × N retries)
     counted as N breaker failures and opened the breaker by itself; and
  2. chat / embed / rerank all shared a single ``"quenchforge"`` breaker,
     so one workload's transient 5xx starved the other two.

The fix: one logical request = one breaker outcome (retry *inside*
``breaker.call``), and a per-workload breaker key (``quenchforge-chat`` /
``quenchforge-embed`` / ``quenchforge-rerank``) tuned for transient
tolerance.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import core.utils.internal_llm as mod
from core.utils.circuit_breaker import AsyncCircuitBreaker, get_breaker


class _FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._body


def _make_http_status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://test-host:11434/api/chat")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


def _wire(monkeypatch, post_side_effects, breaker: AsyncCircuitBreaker):
    """Scripted httpx client + a REAL breaker injected at every get_breaker."""
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
    monkeypatch.setattr(mod, "get_breaker", lambda _name: breaker)
    monkeypatch.setenv("OLLAMA_URL", "http://test-host:11434")
    monkeypatch.setattr(mod.config, "OLLAMA_DEFAULT_MODEL", "llama3.1-8b", raising=False)
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MODEL", "", raising=False)
    monkeypatch.setenv("INTERNAL_LLM_RETRY_BACKOFF", "0.001")
    monkeypatch.setenv("INTERNAL_LLM_MAX_RETRIES", "3")
    return call_count


@pytest.mark.asyncio
async def test_one_failing_request_counts_one_breaker_failure(monkeypatch):
    """3 retries of a single transiently-failing request must increment the
    breaker exactly ONCE — not once per retry (the pre-fix bug that opened a
    healthy backend's breaker on a single loading slot)."""
    breaker = AsyncCircuitBreaker("quenchforge-chat", failure_threshold=3, recovery_timeout=20)
    counter = _wire(
        monkeypatch,
        [_make_http_status_error(502)] * 3,  # one request, exhausts its 3 retries
        breaker,
    )
    from core.utils import llm_client

    async def _stub_call_llm(*a, **k):  # noqa: ARG001
        return "fallback-stub"

    monkeypatch.setattr(llm_client, "call_llm", _stub_call_llm)

    result = await mod._call_ollama(
        [{"role": "user", "content": "hi"}], temperature=0, max_tokens=5,
    )
    assert result == "fallback-stub"
    assert counter["n"] == 3  # retried 3× at the HTTP layer
    assert breaker._failure_count == 1  # but only ONE breaker failure
    assert breaker.state.value == "closed"  # 1 < threshold(3) → stays closed


@pytest.mark.asyncio
async def test_transient_then_success_leaves_breaker_closed(monkeypatch):
    """A 502 then a success = a recovered request; breaker stays closed at 0."""
    breaker = AsyncCircuitBreaker("quenchforge-chat", failure_threshold=3, recovery_timeout=20)
    counter = _wire(
        monkeypatch,
        [_make_http_status_error(502), {"message": {"content": "ok"}}],
        breaker,
    )
    result = await mod._call_ollama(
        [{"role": "user", "content": "hi"}], temperature=0, max_tokens=5,
    )
    assert result == "ok"
    assert counter["n"] == 2
    assert breaker._failure_count == 0
    assert breaker.state.value == "closed"


def test_quenchforge_workload_breakers_are_distinct_and_tuned():
    """chat / embed / rerank must be SEPARATE breakers so one workload's
    transient 5xx can't starve the others, and registered (not auto-created)
    with transient-tolerant thresholds."""
    chat = get_breaker("quenchforge-chat")
    embed = get_breaker("quenchforge-embed")
    rerank = get_breaker("quenchforge-rerank")
    assert chat is not embed
    assert embed is not rerank
    assert chat is not rerank
    # Transient-tolerant tuning (registered, not the threshold=3 auto-default).
    for b in (chat, embed, rerank):
        assert b.failure_threshold >= 5
        assert b.recovery_timeout <= 30
