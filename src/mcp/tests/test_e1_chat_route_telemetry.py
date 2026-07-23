# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 Phase-5 — chat route-decision paths are observable + tested (CR-049/050).

Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-049, CR-050). The chat proxy's cross-family fallback retry (CR-049) and its
model=='auto' smart-routing branch (CR-050) had zero tests, and a failed
auto-route degraded to the default assignment while still recording route_source
"auto" — indistinguishable in telemetry from a working smart router. The fix adds
an "auto_failed" telemetry bucket; these probes cover both branches. RED-then-GREEN.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


async def _drive(monkeypatch, *, model, route=None, attempt_results, pick_fallback=None):
    """Run _proxy_stream, capturing the recorded route_source and the sequence of
    bare_models handed to _attempt_stream. attempt_results queue: an int is
    returned as an upstream status (triggers fallback); "OK" yields an empty
    stream."""
    from app.routers import chat as chat_mod

    recorded: dict = {"route_source": None, "attempts": []}
    monkeypatch.setattr("app.deps.get_redis", lambda: MagicMock(), raising=False)
    monkeypatch.setattr(chat_mod, "_record_chat_route_decision",
                        lambda rs: recorded.__setitem__("route_source", rs))
    monkeypatch.setattr(chat_mod, "_current_assignments", lambda: {})
    if route is not None:
        monkeypatch.setattr("utils.smart_router.route", route, raising=False)
    if pick_fallback is not None:
        monkeypatch.setattr(chat_mod, "_pick_fallback", pick_fallback)

    queue = list(attempt_results)

    async def _empty_gen():
        return
        yield b""

    async def _fake_attempt(request, req, bare_model, request_id, api_key):  # noqa: ARG001
        recorded["attempts"].append(bare_model)
        nxt = queue.pop(0)
        return _empty_gen() if nxt == "OK" else nxt

    monkeypatch.setattr(chat_mod, "_attempt_stream", _fake_attempt)

    req = chat_mod.ChatRequest(model=model, messages=[{"role": "user", "content": "hi"}])
    request = MagicMock()

    async def _disc():
        return False

    request.is_disconnected = _disc

    async for _ in chat_mod._proxy_stream(request, req, "req-1", "sk-test"):
        pass
    return recorded, chat_mod


@pytest.mark.asyncio
async def test_cr050_auto_route_success_records_auto(monkeypatch):
    from core.routing.smart_router import RouteDecision

    async def _route(*_a, **_k):
        return RouteDecision(model="openrouter/x-ai/grok-4.3", provider="openrouter_paid",
                             reason="ok", estimated_cost_per_1k=0.0001)

    recorded, chat_mod = await _drive(
        monkeypatch, model="auto", route=_route, attempt_results=["OK"],
    )
    assert recorded["route_source"] == "auto"
    assert recorded["attempts"][0] == chat_mod._strip_prefix("openrouter/x-ai/grok-4.3")


@pytest.mark.asyncio
async def test_cr050_auto_route_failure_records_auto_failed(monkeypatch):
    """A raising smart router must degrade to the default assignment AND record
    'auto_failed' so the degradation is observable. RED on HEAD: it recorded a
    plain 'auto', hiding the failure."""
    from app.routers.models import DEFAULT_ASSIGNMENTS

    async def _boom(*_a, **_k):
        raise RuntimeError("router exploded")

    recorded, chat_mod = await _drive(
        monkeypatch, model="auto", route=_boom, attempt_results=["OK"],
    )
    assert recorded["route_source"] == "auto_failed"
    assert recorded["attempts"][0] == chat_mod._strip_prefix(DEFAULT_ASSIGNMENTS["general"])


@pytest.mark.asyncio
async def test_cr049_cross_family_fallback_retry(monkeypatch):
    """An upstream failure on the first model must retry the picked fallback and
    record route_source 'fallback'."""
    recorded, chat_mod = await _drive(
        monkeypatch,
        model="openrouter/openai/gpt-4o-mini",
        attempt_results=[503, "OK"],
        # _pick_fallback's return is dispatched verbatim as the retry model.
        pick_fallback=lambda _bare: "anthropic/claude-haiku-3.5",
    )
    assert recorded["route_source"] == "fallback"
    assert recorded["attempts"] == [
        chat_mod._strip_prefix("openrouter/openai/gpt-4o-mini"),
        "anthropic/claude-haiku-3.5",
    ]
