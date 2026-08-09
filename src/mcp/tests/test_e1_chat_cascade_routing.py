# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Chat cascade routing — local stream when enabled, cloud fallback when not.

Originally CR-053 (no local stream → never send bare Ollama id to OpenRouter).
Tier A local-chat: when the local daemon is enabled, honor the ollama decision
on the OpenAI-compatible local stream instead of remapping to cloud.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _chat_redis(monkeypatch):
    """_record_chat_route_decision + _success_gen latency use get_redis."""
    monkeypatch.setattr("app.deps.get_redis", lambda: MagicMock(), raising=False)


async def _drive(monkeypatch, decision, *, env: dict | None = None):
    """Run _proxy_stream with model='auto' and a stubbed router decision; return
    the bare_model _attempt_stream was ultimately asked to dispatch."""
    from app.routers import chat as chat_mod

    captured: dict = {}

    async def _fake_route(*_a, **_k):
        return decision

    monkeypatch.setattr("utils.smart_router.route", _fake_route, raising=False)
    monkeypatch.setattr(chat_mod, "_current_assignments", lambda: {})

    if env:
        for k, v in env.items():
            monkeypatch.setenv(k, v)

    async def _empty_gen():
        return
        yield b""  # pragma: no cover — makes this an async generator

    async def _fake_attempt(request, req, bare_model, request_id, api_key):  # noqa: ARG001
        captured["bare_model"] = bare_model
        return _empty_gen()

    monkeypatch.setattr(chat_mod, "_attempt_stream", _fake_attempt)

    req = chat_mod.ChatRequest(model="auto", messages=[{"role": "user", "content": "hi"}])
    request = MagicMock()

    async def _disc():
        return False

    request.is_disconnected = _disc

    async for _ in chat_mod._proxy_stream(request, req, "req-1", "sk-test"):
        pass
    return captured, chat_mod


@pytest.mark.asyncio
async def test_cascade_ollama_uses_local_when_enabled(monkeypatch):
    """With OLLAMA_ENABLED / local provider, cascade keeps the bare local model."""
    from core.routing.smart_router import RouteDecision

    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_ENABLED", "true")

    decision = RouteDecision(
        model="llama3.2", provider="ollama",
        reason="simple query — local cascade", estimated_cost_per_1k=0.0,
    )
    captured, _chat_mod = await _drive(monkeypatch, decision)

    assert captured["bare_model"] == "llama3.2"


@pytest.mark.asyncio
async def test_cascade_ollama_falls_back_cloud_when_local_disabled(monkeypatch):
    """Without local daemon enabled, bare Ollama id must not reach OpenRouter."""
    from core.routing.smart_router import RouteDecision

    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OLLAMA_ENABLED", "false")
    monkeypatch.delenv("OLLAMA_ENABLED", raising=False)
    # Force openrouter + no ollama flag
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "openrouter")

    decision = RouteDecision(
        model="llama3.2", provider="ollama",
        reason="simple query — local cascade", estimated_cost_per_1k=0.0,
    )
    captured, chat_mod = await _drive(monkeypatch, decision)

    from app.routers.models import DEFAULT_ASSIGNMENTS

    assert captured["bare_model"] != "llama3.2", (
        "chat dispatched the bare Ollama model without local enabled"
    )
    assert captured["bare_model"] == chat_mod._strip_prefix(DEFAULT_ASSIGNMENTS["general"])


@pytest.mark.asyncio
async def test_cascade_cloud_decision_still_honored(monkeypatch):
    """A dispatchable (cloud) decision must be taken verbatim."""
    from core.routing.smart_router import RouteDecision

    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "openrouter")

    model = "openrouter/meta-llama/llama-3.3-70b-instruct"
    decision = RouteDecision(
        model=model, provider="openrouter_paid",
        reason="simple query — cheap tier", estimated_cost_per_1k=0.0001,
    )
    captured, chat_mod = await _drive(monkeypatch, decision)

    assert captured["bare_model"] == chat_mod._strip_prefix(model)
