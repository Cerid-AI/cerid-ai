# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The smart-routed local path must report the model that served, and must go
through the same back-pressure machinery as ``call_internal_llm``.

F162: quenchforge answers ``{"model": "qwen2.5-7b-instruct-q4_k_m.gguf"}`` while
cerid asked for ``llama3.1-8b`` — the truth was on the wire and discarded.
F178: the path opened a fresh ``httpx.AsyncClient`` per call with no circuit
breaker and no pacing cooldown, so a dead chat slot failed slowly per request.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.utils import llm_client


def _decision(model="llama3.1-8b"):
    from core.routing.smart_router import RouteDecision

    return RouteDecision(
        model=model,
        provider="ollama",
        reason="local model (free, instant)",
        estimated_cost_per_1k=0.0,
        tier_p95_ms=5000,
    )


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


@pytest.fixture(autouse=True)
def _reset_breakers():
    from core.utils.circuit_breaker import get_breaker

    for name in ("quenchforge-chat", "ollama"):
        get_breaker(name).reset()
    yield
    for name in ("quenchforge-chat", "ollama"):
        get_breaker(name).reset()


@pytest.fixture
def local_client(monkeypatch):
    """A shared-client stub that records the POSTs it receives."""
    calls: list[dict] = []
    client = AsyncMock()

    async def _post(url, json=None, **kw):
        calls.append({"url": url, "body": json})
        return _Resp(
            {
                "model": "qwen2.5-7b-instruct-q4_k_m.gguf",
                "message": {"content": "42"},
            }
        )

    client.post = _post
    client.calls = calls
    monkeypatch.setattr(
        "core.utils.internal_llm._get_ollama_client",
        AsyncMock(return_value=client),
    )
    return client


@pytest.mark.asyncio
async def test_served_model_is_read_back_from_the_response(local_client, monkeypatch):
    """The daemon substitutes its loaded slot — report THAT, not the request."""
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "quenchforge")
    with patch(
        "core.routing.smart_router.route", new=AsyncMock(return_value=_decision())
    ):
        content, decision = await llm_client.route_and_call(
            [{"role": "user", "content": "6*7?"}], task_type="internal"
        )
    assert content == "42"
    assert decision.model == "qwen2.5-7b-instruct-q4_k_m.gguf"
    assert decision.provider == "ollama"


@pytest.mark.asyncio
async def test_local_call_uses_the_shared_client_not_a_fresh_one(
    local_client, monkeypatch
):
    """A per-call AsyncClient re-opens TCP against the single chat slot."""
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "quenchforge")
    monkeypatch.setenv("QUENCHFORGE_URL", "http://quenchforge:11434")
    with patch(
        "core.routing.smart_router.route", new=AsyncMock(return_value=_decision())
    ):
        await llm_client.route_and_call(
            [{"role": "user", "content": "hi"}], task_type="internal"
        )
    assert local_client.calls[0]["url"] == "http://quenchforge:11434/api/chat"


@pytest.mark.asyncio
async def test_local_failures_are_observed_by_the_chat_breaker(monkeypatch):
    """A dead chat slot must trip the quenchforge-chat breaker so subsequent
    SDK calls fail fast to OpenRouter instead of paying the timeout each time."""
    from core.utils.circuit_breaker import CircuitState, get_breaker

    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "quenchforge")
    breaker = get_breaker("quenchforge-chat")

    attempts: list[int] = []
    client = AsyncMock()

    async def _post(url, json=None, **kw):
        attempts.append(1)
        raise RuntimeError("chat slot dead")

    client.post = _post
    monkeypatch.setattr(
        "core.utils.internal_llm._get_ollama_client", AsyncMock(return_value=client)
    )

    with patch(
        "core.routing.smart_router.route", new=AsyncMock(return_value=_decision())
    ), patch(
        "core.utils.llm_client.call_llm", new=AsyncMock(return_value="cloud")
    ):
        for _ in range(8):
            content, decision = await llm_client.route_and_call(
                [{"role": "user", "content": "hi"}], task_type="internal"
            )

    assert breaker.state is CircuitState.OPEN
    # Once open, further SDK calls must fail fast to OpenRouter without
    # touching the dead slot at all.
    assert len(attempts) < 8
    assert content == "cloud"
    assert decision.provider == "openrouter_paid"


@pytest.mark.asyncio
async def test_local_call_honours_the_pacing_cooldown(local_client, monkeypatch):
    """The shared timeout cooldown protects the single slot from a retry
    pile-on; the smart-routed path bypassed it entirely."""
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "quenchforge")
    waited: list[bool] = []

    async def _wait(interactive: bool):
        waited.append(interactive)

    monkeypatch.setattr("core.utils.internal_llm._wait_pacing_cooldown", _wait)
    with patch(
        "core.routing.smart_router.route", new=AsyncMock(return_value=_decision())
    ):
        await llm_client.route_and_call(
            [{"role": "user", "content": "hi"}], task_type="internal"
        )
    assert waited, "pacing cooldown was never consulted"
    assert waited == [True], "a smart-routed query has a caller waiting — it is interactive"
