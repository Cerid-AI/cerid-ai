# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 Phase-3e-4 verifiability harness — FREE-tier cost honesty.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 3.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-027).

CR-027 (CONFIRMED): the smart-router FREE tier carries the *bare paid* id
``openrouter/meta-llama/llama-3.3-70b-instruct`` (the ``:free`` slug is built
only as a probe id for the dead failover wrappers), yet every FREE-tier
RouteDecision — INTERNAL without a local backend, SIMPLE chat, and MODERATE +
high cost sensitivity — stamped ``provider="openrouter_free"`` and
``estimated_cost_per_1k=0.0`` while dispatching the paid slug, and
``/sdk/v1/llm/complete`` forwards that 0.0 "for budget tracking". A paid
dispatch reported as free/zero is the same dishonest-telemetry class as CR-013.

Operator decision (2026-07-20): FREE tier → **honest paid cost**. Keep
dispatching the reliable paid llama-3.3 slug, but stamp ``provider=
"openrouter_paid"`` + the real per-1K rate (``_LLAMA_33_PAID_COST_PER_1K``,
matching the CR-013 fallback rate). No free-to-user tier. The ``:free``
failover wrappers stay dead (dead-code removal is deferred to 3e-6).

RED-then-GREEN; synthetic (route() internals monkeypatched, no live stack, no
network) so the assertions exercise the decision table directly.
"""
from __future__ import annotations

import pytest

import config
from core.routing import smart_router
from core.routing.smart_router import Complexity, TaskType, route


async def _route_internal_no_ollama(monkeypatch: pytest.MonkeyPatch):
    """INTERNAL task with no reachable local backend → FREE-tier fallthrough."""

    async def _no_ollama() -> bool:
        return False

    monkeypatch.setattr(smart_router, "_check_ollama", _no_ollama)
    return await route("classify this", task_type=TaskType.INTERNAL)


async def _route_simple_chat(monkeypatch: pytest.MonkeyPatch):
    """SIMPLE chat with cascade off → FREE-tier."""

    async def _fake_classify(_query: str) -> Complexity:
        return Complexity.SIMPLE

    monkeypatch.setattr(smart_router, "_classify_with_best_available", _fake_classify)
    monkeypatch.setattr(config, "ENABLE_MODEL_CASCADE", False, raising=False)
    return await route("hi", task_type=TaskType.CHAT, total_chars=100)


async def _route_moderate_high(monkeypatch: pytest.MonkeyPatch):
    """MODERATE + high cost sensitivity → FREE-tier (decision table)."""

    async def _fake_classify(_query: str) -> Complexity:
        return Complexity.MODERATE

    monkeypatch.setattr(smart_router, "_classify_with_best_available", _fake_classify)
    return await route(
        "hmm", task_type=TaskType.CHAT, cost_sensitivity="high", total_chars=100
    )


def _assert_honest_paid(decision) -> None:
    """The FREE tier dispatches the paid llama-3.3 slug — so it must report
    a paid provider + the real cost, never openrouter_free/0.0 (CR-027)."""
    assert "llama-3.3" in decision.model.lower(), "still the llama-3.3 slug"
    assert decision.provider == "openrouter_paid", (
        f"paid dispatch must report a paid provider, got {decision.provider!r}"
    )
    assert decision.provider != "openrouter_free", "the CR-027 lie"
    assert decision.estimated_cost_per_1k == smart_router._LLAMA_33_PAID_COST_PER_1K
    assert decision.estimated_cost_per_1k > 0.0, "paid dispatch is not free"


# ---------------------------------------------------------------------------
# CR-027 — all three live FREE-tier stamps report honest paid cost
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_internal_no_local_backend_reports_paid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_honest_paid(await _route_internal_no_ollama(monkeypatch))


@pytest.mark.asyncio
async def test_simple_chat_reports_paid(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_honest_paid(await _route_simple_chat(monkeypatch))


@pytest.mark.asyncio
async def test_moderate_high_cost_sensitivity_reports_paid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_honest_paid(await _route_moderate_high(monkeypatch))


def test_free_tier_cost_constant_matches_fallback_rate() -> None:
    """The FREE-tier paid rate is the same paid llama-3.3 per-1K rate the CR-013
    local->cloud fallback stamps — the two must not drift."""
    from core.utils.llm_client import _FALLBACK_COST_PER_1K

    assert smart_router._LLAMA_33_PAID_COST_PER_1K == _FALLBACK_COST_PER_1K
