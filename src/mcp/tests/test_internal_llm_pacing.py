# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""bf-f3 pacing on the local internal-LLM path (qf-pacing).

Two client-side levers so background enrichment cannot saturate the local
backend:

1. Bounded concurrency (``INTERNAL_LLM_MAX_CONCURRENCY``, default 2) on
   non-streaming local calls.
2. A shared cooldown armed on timeout — subsequent calls wait it out
   instead of piling retries onto an already-saturated backend; doubling
   per consecutive timeout, reset on success.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import core.utils.internal_llm as mod


@pytest.fixture(autouse=True)
def _fresh_pacing_state():
    mod._reset_pacing_state()
    yield
    mod._reset_pacing_state()


class _PassThroughBreaker:
    async def call(self, fn):  # type: ignore[no-untyped-def]
        return await fn()


def _wire_backend(monkeypatch, post):
    fake_client = MagicMock()
    fake_client.post = post
    monkeypatch.setattr(mod, "_get_ollama_client", AsyncMock(return_value=fake_client))
    monkeypatch.setattr(mod, "get_breaker", lambda _name: _PassThroughBreaker())
    monkeypatch.setenv("OLLAMA_URL", "http://test-host:11434")
    monkeypatch.setattr(mod.config, "OLLAMA_DEFAULT_MODEL", "test-model", raising=False)
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MODEL", "", raising=False)
    monkeypatch.setenv("INTERNAL_LLM_RETRY_BACKOFF", "0.001")
    monkeypatch.setenv("INTERNAL_LLM_MAX_RETRIES", "3")


class _FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": "ok"}}


@pytest.mark.asyncio
async def test_concurrency_cap_enforced(monkeypatch):
    """N concurrent local calls never exceed INTERNAL_LLM_MAX_CONCURRENCY
    in-flight requests against the backend."""
    monkeypatch.setenv("INTERNAL_LLM_MAX_CONCURRENCY", "2")

    inflight = {"now": 0, "max": 0}

    async def _post(url, json=None):
        inflight["now"] += 1
        inflight["max"] = max(inflight["max"], inflight["now"])
        await asyncio.sleep(0.02)
        inflight["now"] -= 1
        return _FakeResponse()

    _wire_backend(monkeypatch, _post)

    await asyncio.gather(*[
        mod._call_ollama(
            [{"role": "user", "content": f"q{i}"}], temperature=0, max_tokens=5,
        )
        for i in range(8)
    ])
    assert inflight["max"] <= 2, (
        f"observed {inflight['max']} concurrent backend calls; cap is 2"
    )


@pytest.mark.asyncio
async def test_timeout_arms_shared_cooldown(monkeypatch):
    """A timed-out call arms the process-wide cooldown so the NEXT call
    waits before issuing (backoff-on-timeout, not immediate pile-on)."""
    monkeypatch.setenv("INTERNAL_LLM_TIMEOUT_COOLDOWN", "5.0")
    # Local exhaustion falls through to OpenRouter — stub the cloud leg.
    monkeypatch.setattr(
        "core.utils.llm_client.call_llm", AsyncMock(return_value="cloud"),
    )

    async def _post(url, json=None):
        raise httpx.TimeoutException("slow")

    _wire_backend(monkeypatch, _post)
    monkeypatch.setenv("INTERNAL_LLM_MAX_RETRIES", "1")  # after _wire_backend's 3

    result = await mod._call_ollama(
        [{"role": "user", "content": "q"}], temperature=0, max_tokens=5,
    )
    assert result == "cloud"
    with mod._pacing_guard:
        assert mod._pacing_cooldown_seconds == 5.0
        assert mod._pacing_cooldown_until > 0


@pytest.mark.asyncio
async def test_cooldown_doubles_and_caps(monkeypatch):
    monkeypatch.setenv("INTERNAL_LLM_TIMEOUT_COOLDOWN", "2.0")
    monkeypatch.setenv("INTERNAL_LLM_TIMEOUT_COOLDOWN_MAX", "6.0")
    mod._record_pacing_timeout()
    assert mod._pacing_cooldown_seconds == 2.0
    mod._record_pacing_timeout()
    assert mod._pacing_cooldown_seconds == 4.0
    mod._record_pacing_timeout()
    assert mod._pacing_cooldown_seconds == 6.0  # capped, not 8.0
    mod._record_pacing_timeout()
    assert mod._pacing_cooldown_seconds == 6.0


@pytest.mark.asyncio
async def test_success_resets_cooldown(monkeypatch):
    monkeypatch.setenv("INTERNAL_LLM_TIMEOUT_COOLDOWN", "2.0")
    mod._record_pacing_timeout()
    assert mod._pacing_cooldown_seconds > 0

    async def _post(url, json=None):
        return _FakeResponse()

    _wire_backend(monkeypatch, _post)
    monkeypatch.setenv("INTERNAL_LLM_TIMEOUT_COOLDOWN", "0.0")  # don't stall the test

    result = await mod._call_ollama(
        [{"role": "user", "content": "q"}], temperature=0, max_tokens=5,
    )
    assert result == "ok"
    assert mod._pacing_cooldown_seconds == 0.0
    assert mod._pacing_cooldown_until == 0.0


@pytest.mark.asyncio
async def test_cooldown_wait_delays_next_call(monkeypatch):
    """_wait_pacing_cooldown sleeps out the armed window."""
    import time

    monkeypatch.setenv("INTERNAL_LLM_TIMEOUT_COOLDOWN", "0.1")
    mod._record_pacing_timeout()
    start = time.monotonic()
    await mod._wait_pacing_cooldown()
    assert time.monotonic() - start >= 0.09


@pytest.mark.asyncio
async def test_semaphore_is_per_loop():
    """A semaphore created on one loop is replaced on the next loop
    (asyncio primitives bind to their creating loop)."""
    sem_a = mod._get_pacing_semaphore()

    async def _other_loop():
        return mod._get_pacing_semaphore()

    # Run on a fresh loop in a thread.
    import threading

    result: dict = {}

    def _runner():
        result["sem"] = asyncio.run(_other_loop())

    t = threading.Thread(target=_runner)
    t.start()
    t.join()
    assert result["sem"] is not sem_a
