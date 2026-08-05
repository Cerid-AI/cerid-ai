# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-3d verifiability harness — ROUTE-DECISION HONESTY probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 3.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-013).

``route_and_call`` stamps the pre-dispatch ``RouteDecision`` into the SDK response,
but the local dispatch path silently fell back to OpenRouter on failure and never
updated the decision — so ``provider="ollama", estimated_cost_per_1k=0.0`` was
returned even though the bytes came from paid OpenRouter cloud. The SDK response
therefore under-reported cloud usage as local/free (a privacy + cost-tracking lie),
and no ``inference_health`` fallback was recorded.

3d moves the fallback into ``route_and_call`` so it updates the returned decision to
reflect the ACTUAL serve (provider/model/cost) and records the degradation.
RED-then-GREEN; GREEN -> preservation gates.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import core.utils.llm_client as llm_client
from core.routing.smart_router import RouteDecision


def _ollama_plan() -> RouteDecision:
    return RouteDecision(
        model="llama3.1-8b",
        provider="ollama",
        reason="local internal routing",
        estimated_cost_per_1k=0.0,
    )


@pytest.mark.preservation
async def test_local_failure_updates_decision_to_reflect_openrouter_serve(monkeypatch):
    """When the local backend is unavailable and OpenRouter serves the bytes, the
    returned decision must reflect the ACTUAL serve — not the pre-fallback local
    plan. RED on HEAD (CR-013): the decision is returned unchanged (or the failure
    propagates), so provider='ollama'/cost=0 ships for cloud bytes."""
    monkeypatch.setattr(
        "core.routing.smart_router.route", AsyncMock(return_value=_ollama_plan())
    )
    monkeypatch.setattr(
        llm_client, "_call_ollama_direct", AsyncMock(side_effect=RuntimeError("daemon down"))
    )
    monkeypatch.setattr(
        llm_client, "call_llm", AsyncMock(return_value="cloud-served content")
    )

    content, decision = await llm_client.route_and_call(
        [{"role": "user", "content": "hi"}], task_type="internal"
    )

    assert content == "cloud-served content"
    assert decision.provider != "ollama", (
        "local backend failed and OpenRouter served the answer, but the decision "
        f"still reports provider={decision.provider!r} — the SDK stamps local/free "
        "for cloud bytes (CR-013)"
    )
    assert "openrouter" in decision.provider, (
        f"decision provider should reflect the OpenRouter serve, got {decision.provider!r}"
    )
    assert decision.estimated_cost_per_1k > 0.0, (
        "OpenRouter served the answer but estimated_cost_per_1k is still 0.0 — "
        "cloud usage under-reported as free (CR-013)"
    )


@pytest.mark.preservation
async def test_local_to_cloud_fallback_is_recorded(monkeypatch):
    """The local->cloud fallback must be recorded to inference_health so /health
    reflects the degradation. RED on HEAD: no fallback is recorded (the failure is
    swallowed inside _call_ollama_direct)."""
    monkeypatch.setattr(
        "core.routing.smart_router.route", AsyncMock(return_value=_ollama_plan())
    )
    monkeypatch.setattr(
        llm_client, "_call_ollama_direct", AsyncMock(side_effect=RuntimeError("daemon down"))
    )
    monkeypatch.setattr(
        llm_client, "call_llm", AsyncMock(return_value="cloud-served content")
    )

    calls: list[dict] = []
    monkeypatch.setattr(
        "core.utils.inference_health.record_fallback",
        lambda workload, **kw: calls.append({"workload": workload, **kw}),
    )

    await llm_client.route_and_call([{"role": "user", "content": "hi"}], task_type="internal")

    assert calls, "local->cloud fallback was not recorded to inference_health (CR-013)"
    assert calls[-1]["workload"] == "llm"
    assert calls[-1]["configured"] == "ollama"
    assert calls[-1]["served_by"] == "openrouter"
