# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-5 — cost-sensitivity resolves end-to-end (CR-026/028).

Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-026, CR-028). cost_sensitivity was half-wired: /agent/query's field promised
a consumer-registry resolution that did not exist, and the persisted
COST_SENSITIVITY setting was consulted by no router. resolve_cost_sensitivity now
implements the request -> consumer-registry -> persisted-setting chain, and the
chat smart-router consumes the resolved value. RED-then-GREEN.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_cr028_resolution_precedence(monkeypatch):
    import config
    from app.services.request_policy import resolve_cost_sensitivity
    from config.settings import CONSUMER_REGISTRY

    # 1. An explicit request value always wins.
    assert resolve_cost_sensitivity("high", "gui") == "high"

    # 2. Else the consumer's registry default.
    monkeypatch.setitem(CONSUMER_REGISTRY, "cost-consumer", {"cost_sensitivity": "low"})
    assert resolve_cost_sensitivity(None, "cost-consumer") == "low"

    # 3. Else the persisted global setting (gui has no per-consumer cost default).
    monkeypatch.setattr(config, "COST_SENSITIVITY", "high", raising=False)
    assert resolve_cost_sensitivity(None, "gui") == "high"

    # 4. Else "medium".
    monkeypatch.setattr(config, "COST_SENSITIVITY", "", raising=False)
    assert resolve_cost_sensitivity(None, "gui") == "medium"


@pytest.mark.asyncio
async def test_cr026_chat_router_uses_resolved_cost_sensitivity(monkeypatch):
    """With no per-request value, the chat smart-router must receive the persisted
    COST_SENSITIVITY — so the GUI's stored preference actually steers routing."""
    import config
    from app.routers import chat as chat_mod
    from core.routing.smart_router import RouteDecision

    monkeypatch.setattr(config, "COST_SENSITIVITY", "low", raising=False)

    captured: dict = {}

    async def _route(*_a, **k):
        captured.update(k)
        return RouteDecision(model="openrouter/x-ai/grok-4.3", provider="openrouter_paid",
                             reason="ok", estimated_cost_per_1k=0.0001)

    monkeypatch.setattr("utils.smart_router.route", _route, raising=False)
    monkeypatch.setattr("app.deps.get_redis", lambda: MagicMock(), raising=False)
    monkeypatch.setattr(chat_mod, "_current_assignments", lambda: {})

    async def _fake_attempt(*_a, **_k):
        async def _g():
            return
            yield b""
        return _g()

    monkeypatch.setattr(chat_mod, "_attempt_stream", _fake_attempt)

    # model="auto" triggers routing; cost_sensitivity omitted (None default).
    req = chat_mod.ChatRequest(model="auto", messages=[{"role": "user", "content": "hi"}])
    assert req.cost_sensitivity is None  # unset resolves server-side, not "medium"

    request = MagicMock()
    request.headers = {"x-client-id": "gui"}

    async def _disc():
        return False

    request.is_disconnected = _disc

    async for _ in chat_mod._proxy_stream(request, req, "req-1", "sk-test"):
        pass

    assert captured["cost_sensitivity"] == "low"
