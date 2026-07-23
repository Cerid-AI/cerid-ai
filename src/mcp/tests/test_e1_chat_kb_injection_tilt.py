# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 Phase-5 — KB-injection routing tilt is reachable (CR-051).

Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-051). The smart router's ``kb_injection_count >= 3`` SIMPLE->MODERATE tilt
was unreachable because chat counted system MESSAGES containing ``<document``
rather than the documents themselves — and the FE joins every ``<document>``
block into ONE system message, so the count was 0 or 1. RED-then-GREEN.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_cr051_kb_injection_counts_documents(monkeypatch):
    from app.routers import chat as chat_mod
    from core.routing.smart_router import RouteDecision

    captured: dict = {}

    async def _route(*_a, **k):
        captured.update(k)
        return RouteDecision(model="openrouter/x-ai/grok-4.3", provider="openrouter_paid",
                             reason="ok", estimated_cost_per_1k=0.0001)

    monkeypatch.setattr("utils.smart_router.route", _route, raising=False)
    monkeypatch.setattr("app.deps.get_redis", lambda: MagicMock(), raising=False)

    async def _fake_attempt(*_a, **_k):
        async def _g():
            return
            yield b""
        return _g()

    monkeypatch.setattr(chat_mod, "_attempt_stream", _fake_attempt)

    # Three <document> blocks in ONE system message — the real FE shape.
    sys_content = (
        "<document id='a'>alpha</document>\n"
        "<document id='b'>beta</document>\n"
        "<document id='c'>gamma</document>"
    )
    req = chat_mod.ChatRequest(
        model="auto",
        messages=[
            {"role": "system", "content": sys_content},
            {"role": "user", "content": "summarize"},
        ],
    )
    request = MagicMock()

    async def _disc():
        return False

    request.is_disconnected = _disc

    async for _ in chat_mod._proxy_stream(request, req, "req-1", "sk-test"):
        pass

    assert captured["kb_injection_count"] == 3
