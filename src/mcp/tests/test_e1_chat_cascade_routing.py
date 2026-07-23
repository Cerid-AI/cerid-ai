# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 Phase-5 — ENABLE_MODEL_CASCADE must not 400 the chat stream (CR-053).

Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-053). The chat proxy took ``req.model = decision.model`` from the smart
router and dispatched it to OpenRouter, discarding ``decision.provider``. With
ENABLE_MODEL_CASCADE on, the router returns ``provider="ollama"`` with a bare
Ollama model id (e.g. ``"llama3.2"``); chat has no local Ollama streaming
transport, so shipping that id to OpenRouter is a guaranteed non-retryable 400.

The fix honors the provider: a local-only decision falls back to the general
cloud assignment (the same miss-path the routing except-branch uses) instead of
sending an undispatchable model id upstream. RED-then-GREEN.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _chat_redis(monkeypatch):
    """_record_chat_route_decision + _success_gen latency use get_redis."""
    monkeypatch.setattr("app.deps.get_redis", lambda: MagicMock(), raising=False)


async def _drive(monkeypatch, decision):
    """Run _proxy_stream with model='auto' and a stubbed router decision; return
    the bare_model _attempt_stream was ultimately asked to dispatch."""
    from app.routers import chat as chat_mod

    captured: dict = {}

    async def _fake_route(*_a, **_k):
        return decision

    monkeypatch.setattr("utils.smart_router.route", _fake_route, raising=False)
    monkeypatch.setattr(chat_mod, "_current_assignments", lambda: {})

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
async def test_cascade_ollama_decision_not_dispatched_to_openrouter(monkeypatch):
    """A provider='ollama' decision must NOT hand a bare Ollama id to OpenRouter.
    RED on HEAD: req.model = decision.model → 'llama3.2' → guaranteed 400."""
    from core.routing.smart_router import RouteDecision

    decision = RouteDecision(
        model="llama3.2", provider="ollama",
        reason="simple query — local cascade", estimated_cost_per_1k=0.0,
    )
    captured, chat_mod = await _drive(monkeypatch, decision)

    from app.routers.models import DEFAULT_ASSIGNMENTS

    assert captured["bare_model"] != "llama3.2", (
        "chat dispatched the bare Ollama model to OpenRouter — guaranteed 400 (CR-053)"
    )
    assert captured["bare_model"] == chat_mod._strip_prefix(DEFAULT_ASSIGNMENTS["general"])


@pytest.mark.asyncio
async def test_cascade_cloud_decision_still_honored(monkeypatch):
    """A dispatchable (cloud) decision must be taken verbatim — the guard is
    scoped to local-only providers, not a blanket override."""
    from core.routing.smart_router import RouteDecision

    model = "openrouter/meta-llama/llama-3.3-70b-instruct"
    decision = RouteDecision(
        model=model, provider="openrouter_paid",
        reason="simple query — cheap tier", estimated_cost_per_1k=0.0001,
    )
    captured, chat_mod = await _drive(monkeypatch, decision)

    assert captured["bare_model"] == chat_mod._strip_prefix(model)
