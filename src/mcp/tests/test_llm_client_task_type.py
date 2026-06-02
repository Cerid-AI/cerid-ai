# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""route_and_call maps unknown client task_type values to safe internal routing.

External clients use Cerid's LLM surface with domain-specific task types
(e.g. "gtm_creative", custom agent-phase labels). These must not 500 on the
TaskType enum — they map to INTERNAL routing. (External-client backend, GA P0.3.)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.utils.llm_client import route_and_call  # noqa: E402


def _decision(provider: str = "ollama"):
    from core.routing.smart_router import RouteDecision

    return RouteDecision(
        model="llama3.1-8b",
        provider=provider,
        reason="test",
        estimated_cost_per_1k=0.0,
        tier_p95_ms=5000,
    )


async def test_unknown_task_type_defaults_to_internal():
    from core.routing.smart_router import TaskType

    captured: dict = {}

    async def fake_route(query, *, task_type, **kw):
        captured["task_type"] = task_type
        return _decision()

    with (
        patch("core.routing.smart_router.route", new=fake_route),
        patch("core.utils.llm_client._call_ollama_direct", new=AsyncMock(return_value="ok")),
    ):
        content, _ = await route_and_call(
            [{"role": "user", "content": "hi"}], task_type="gtm_creative_custom"
        )

    assert content == "ok"
    assert captured["task_type"] == TaskType.INTERNAL


async def test_known_task_type_is_preserved():
    from core.routing.smart_router import TaskType

    captured: dict = {}

    async def fake_route(query, *, task_type, **kw):
        captured["task_type"] = task_type
        return _decision()

    with (
        patch("core.routing.smart_router.route", new=fake_route),
        patch("core.utils.llm_client._call_ollama_direct", new=AsyncMock(return_value="ok")),
    ):
        await route_and_call(
            [{"role": "user", "content": "hi"}], task_type="verification"
        )

    assert captured["task_type"] == TaskType.VERIFICATION
