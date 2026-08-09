# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-3e-3 verifiability harness — EXPERT TIER ESCALATION + WEB-MODEL knob.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 3.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-029, CR-030).

CR-029 (CONFIRMED): the EXPERT tier is maintained (``EXPERT_MODELS``),
weekly-refreshed (``tier_source_ids`` feeds the overlay job), SDK-advertised
("FREE / CHEAP / CAPABLE / RESEARCH / EXPERT"), and displayed — but ``route()``
has NO branch that can ever select it. The COMPLEX/low path ends at CAPABLE with
the comment "Escalation to EXPERT is kept behind a separate flag" — a flag that
never existed. An SDK consumer sending a complex query with
``cost_sensitivity=low`` always gets CAPABLE while the catalog job keeps resolving
an expert id nothing can dispatch.

CR-030 (CONFIRMED-CONTESTED): ``VERIFICATION_EXPERT_WEB_MODEL`` is honored only by
the ``route(task_type=VERIFICATION_EXPERT)`` branch (which no shipped code
invokes); the real expert-verification paths hardcode
``VERIFICATION_EXPERT_MODEL + ":online"`` at the streaming batch sites and in
verification.py, never consulting the operator-overridable knob.

3e-3 (operator decision 2026-07-20: EXPERT tier → WIRE an escalation flag):
- adds ``config.ENABLE_EXPERT_ESCALATION`` (default off — "low cost sensitivity"
  must not silently 10x spend) and a COMPLEX/low → EXPERT branch gated on it;
- points the three hardcoded expert-web-model sites at
  ``config.VERIFICATION_EXPERT_WEB_MODEL`` (behavior-neutral at its default, which
  equals the old concatenation) so the knob is actually read.

RED-then-GREEN; synthetic (no live stack) so it runs in ci-local.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import config
from core.routing import smart_router
from core.routing.smart_router import Complexity, TaskType, route


async def _route_complex(monkeypatch: pytest.MonkeyPatch, cost_sensitivity: str):
    """Route a query forced to COMPLEX (large context overrides classification)."""

    async def _fake_classify(_query: str) -> Complexity:
        return Complexity.SIMPLE  # total_chars > 40k forces COMPLEX regardless

    monkeypatch.setattr(smart_router, "_classify_with_best_available", _fake_classify)
    return await route(
        "analyze this",
        task_type=TaskType.CHAT,
        cost_sensitivity=cost_sensitivity,
        total_chars=50_000,
    )


# ---------------------------------------------------------------------------
# CR-029 — EXPERT tier is now reachable, gated on ENABLE_EXPERT_ESCALATION
# ---------------------------------------------------------------------------


def test_expert_escalation_off_by_default() -> None:
    """The flag exists and defaults OFF — escalation must be opt-in so low cost
    sensitivity does not silently 10x spend."""
    assert config.ENABLE_EXPERT_ESCALATION is False


@pytest.mark.asyncio
async def test_route_complex_low_stays_capable_without_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With escalation off, COMPLEX + low resolves to CAPABLE (unchanged default)."""
    monkeypatch.setattr(config, "ENABLE_EXPERT_ESCALATION", False, raising=False)

    decision = await _route_complex(monkeypatch, "low")

    assert decision.estimated_cost_per_1k == 0.003, "capable tier (claude-sonnet)"
    assert "claude" in decision.model.lower()


@pytest.mark.asyncio
async def test_route_complex_low_escalates_to_expert_with_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With escalation on, COMPLEX + low selects the EXPERT tier — the branch that
    never existed (CR-029)."""
    monkeypatch.setattr(config, "ENABLE_EXPERT_ESCALATION", True, raising=False)

    decision = await _route_complex(monkeypatch, "low")

    assert decision.estimated_cost_per_1k == 0.00125, "expert tier cost, not capable (0.003)"
    assert "grok" in decision.model.lower(), "EXPERT_MODELS grok-4.20 id"
    assert decision.provider == "openrouter_paid"
    assert decision.tier_p95_ms == 75000, "expert p95 budget (openrouter_expert)"


@pytest.mark.asyncio
async def test_route_complex_medium_unaffected_by_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Escalation only applies to low cost sensitivity — COMPLEX + medium stays
    CAPABLE even with the flag on (no silent spend bump for medium)."""
    monkeypatch.setattr(config, "ENABLE_EXPERT_ESCALATION", True, raising=False)

    decision = await _route_complex(monkeypatch, "medium")

    assert decision.estimated_cost_per_1k == 0.003, "medium stays capable"
    assert "claude" in decision.model.lower()


# ---------------------------------------------------------------------------
# CR-030 — the expert-web-model knob is actually consulted
# ---------------------------------------------------------------------------


def test_verification_expert_web_model_default_is_online_variant() -> None:
    """The knob's default equals the old hardcoded concatenation, so wiring it in
    is behavior-neutral until an operator overrides it."""
    assert config.VERIFICATION_EXPERT_WEB_MODEL == config.VERIFICATION_EXPERT_MODEL + ":online"


def test_expert_web_model_sites_read_the_knob() -> None:
    """The streaming batch sites and verification.py must resolve the expert web
    model from VERIFICATION_EXPERT_WEB_MODEL, not by hardcoding
    ``VERIFICATION_EXPERT_MODEL + ":online"`` (CR-030)."""
    import core.agents.hallucination.streaming as streaming_mod
    import core.agents.hallucination.verification as verification_mod

    for mod in (streaming_mod, verification_mod):
        src = Path(mod.__file__).read_text()
        assert 'VERIFICATION_EXPERT_MODEL + ":online"' not in src, (
            f"{mod.__name__} still hardcodes the :online concat — the "
            "VERIFICATION_EXPERT_WEB_MODEL knob is unread (CR-030)"
        )
        assert "VERIFICATION_EXPERT_WEB_MODEL" in src, (
            f"{mod.__name__} does not reference the expert-web-model knob"
        )
