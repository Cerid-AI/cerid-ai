# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stage-profile dispatch — model resolution by (task_type, hardness).

Pins the contract documented in ``config/stage_profiles.py``:

- Every stage in ``STAGE_PROFILES`` resolves to a real tier in the
  registry's ``ACTIVE_MODELS["tiers"]`` map.
- ``PROVIDER_STAGE_<NAME>_MODEL`` env var beats the profile (operator pin).
- Unknown stages return empty / None so existing fallbacks take over.
- ``call_internal_llm`` threads the resolved model into ``call_llm``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from config.stage_profiles import (
    HARDNESS_TO_TIER,
    STAGE_PROFILES,
    Hardness,
    TaskType,
    env_pin_for,
    hardness_for,
    task_type_for,
    tier_for,
)
from core.utils.internal_llm import _resolve_stage_model
from utils.model_registry import ACTIVE_MODELS, get_model

# ---------------------------------------------------------------------------
# Profile registry sanity
# ---------------------------------------------------------------------------


def test_every_hardness_maps_to_a_real_registry_tier():
    """HARDNESS_TO_TIER must point at keys that exist in ACTIVE_MODELS['tiers'].

    Drift would silently fall back to the registry's gpt-4o-mini default —
    catch it here instead of in production.
    """
    tier_catalog = ACTIVE_MODELS["tiers"]
    for hardness, tier_key in HARDNESS_TO_TIER.items():
        assert tier_key in tier_catalog, (
            f"Hardness {hardness} maps to tier {tier_key!r} which is not in "
            f"ACTIVE_MODELS['tiers']: {sorted(tier_catalog)}"
        )


def test_every_stage_in_profiles_resolves_to_a_model():
    """STAGE_PROFILES entries resolve through HARDNESS_TO_TIER → registry → real model."""
    for stage in STAGE_PROFILES:
        model = _resolve_stage_model(stage)
        assert model, f"stage {stage!r} resolves to empty model id"
        assert "openrouter/" in model, (
            f"stage {stage!r} → {model!r} should be a registry-formatted id"
        )


def test_all_profiles_use_valid_enums():
    for stage, (task, hardness) in STAGE_PROFILES.items():
        assert isinstance(task, TaskType), f"{stage} task_type not a TaskType"
        assert isinstance(hardness, Hardness), f"{stage} hardness not a Hardness"


# ---------------------------------------------------------------------------
# Stage helper APIs
# ---------------------------------------------------------------------------


def test_helpers_for_known_stage():
    assert task_type_for("faithfulness/decompose") == TaskType.DECOMPOSITION
    assert hardness_for("faithfulness/decompose") == Hardness.MODERATE
    assert tier_for("faithfulness/decompose") == "research"


def test_ladder_is_documented():
    """HARDNESS_TO_TIER ladder matches the module docstring contract.

    Pins the policy decision that MODERATE → research (grok-4.x) and HARD →
    capable (sonnet), so a future edit that re-flips the ladder fails this
    test before silently changing dispatch behavior across ~25 stages.
    """
    assert HARDNESS_TO_TIER[Hardness.TRIVIAL] == "free"
    assert HARDNESS_TO_TIER[Hardness.SIMPLE] == "cheap"
    assert HARDNESS_TO_TIER[Hardness.MODERATE] == "research"
    assert HARDNESS_TO_TIER[Hardness.HARD] == "capable"
    assert HARDNESS_TO_TIER[Hardness.FRONTIER] == "expert"


def test_helpers_for_unknown_stage():
    assert task_type_for("does_not_exist") is None
    assert hardness_for("does_not_exist") is None
    assert tier_for("does_not_exist") is None


def test_env_pin_normalizes_stage_name(monkeypatch):
    monkeypatch.setenv(
        "PROVIDER_STAGE_FAITHFULNESS_DECOMPOSE_MODEL",
        "openrouter/google/gemini-2.5-flash",
    )
    assert env_pin_for("faithfulness/decompose") == "openrouter/google/gemini-2.5-flash"


def test_env_pin_unset_returns_none():
    assert env_pin_for("faithfulness/decompose") is None
    assert env_pin_for("") is None


# ---------------------------------------------------------------------------
# Resolver behavior — lookup order + fallbacks
# ---------------------------------------------------------------------------


def test_resolver_env_pin_beats_profile(monkeypatch):
    pin = "openrouter/meta-llama/llama-3.3-70b-instruct:free"
    monkeypatch.setenv("PROVIDER_STAGE_FAITHFULNESS_DECOMPOSE_MODEL", pin)
    assert _resolve_stage_model("faithfulness/decompose") == pin


def test_resolver_uses_profile_when_no_env_pin():
    """MODERATE-hardness stages resolve to the research tier (grok-4.x class)
    so dispatch is cheap + fast by default. Sonnet is reserved for HARD."""
    model = _resolve_stage_model("faithfulness/decompose")
    expected = get_model("tiers", "research")
    assert model == expected
    # Hard guard against ever silently routing judges through the expensive
    # opus tier or the cost-incident grok-4.20 model.
    assert "grok-4.20" not in model
    assert "opus" not in model


def test_resolver_returns_empty_for_unknown_stage():
    assert _resolve_stage_model("unmapped_stage") == ""


def test_resolver_returns_empty_for_no_stage():
    assert _resolve_stage_model(None) == ""
    assert _resolve_stage_model("") == ""


def test_judging_stages_dont_route_to_grok_4_20_or_opus():
    """Direct regression for the OpenRouter cost incident — RAGAS judges
    should NOT hit the grok-4.20 model (the original cost vector) and
    should NOT route to the opus expert tier (the bigger cost vector if
    we ever raise MODERATE).
    """
    judging_stages = [
        "faithfulness/decompose",
        "faithfulness/score",
        "context_precision",
        "context_recall",
        "answer_relevancy",
        "claim_extraction",
    ]
    for stage in judging_stages:
        model = _resolve_stage_model(stage)
        assert "grok-4.20" not in model, (
            f"Judging stage {stage!r} resolved to {model!r} — the grok-4.20 "
            "literal is the original cost incident. Update STAGE_PROFILES "
            "or HARDNESS_TO_TIER if this is intentional."
        )
        assert "opus" not in model, (
            f"Judging stage {stage!r} resolved to {model!r} — opus is the "
            "frontier expert tier, way over-spec for judging. Re-tag the "
            "stage's hardness if expert quality is genuinely required."
        )


def test_summarization_stages_land_in_cheap_tier():
    """Summary stages default to SIMPLE = cheap tier — cost-sensitive default."""
    summary_stages = [
        "curator_synopsis",
        "wiki_summary",
        "daily_digest",
        "mcp_summarize_artifact",
    ]
    cheap_model = get_model("tiers", "cheap")
    for stage in summary_stages:
        assert _resolve_stage_model(stage) == cheap_model


# ---------------------------------------------------------------------------
# call_internal_llm threads the resolved model into call_llm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_internal_llm_threads_resolved_model_into_call_llm():
    """The plumbing: stage='faithfulness/decompose' → call_llm gets model=<capable>."""
    from core.utils import internal_llm

    captured: dict = {}

    async def _fake_call_llm(messages, **kwargs):
        captured.update(kwargs)
        return "ok"

    with patch("core.utils.llm_client.call_llm", new=AsyncMock(side_effect=_fake_call_llm)):
        await internal_llm.call_internal_llm(
            [{"role": "user", "content": "hi"}],
            stage="faithfulness/decompose",
        )
    assert captured.get("model") == get_model("tiers", "research")


@pytest.mark.asyncio
async def test_call_internal_llm_env_pin_overrides_profile():
    from core.utils import internal_llm

    captured: dict = {}

    async def _fake_call_llm(messages, **kwargs):
        captured.update(kwargs)
        return "ok"

    pin = "openrouter/google/gemini-2.5-flash"
    with (
        patch.dict("os.environ", {"PROVIDER_STAGE_FAITHFULNESS_DECOMPOSE_MODEL": pin}),
        patch("core.utils.llm_client.call_llm", new=AsyncMock(side_effect=_fake_call_llm)),
    ):
        await internal_llm.call_internal_llm(
            [{"role": "user", "content": "hi"}],
            stage="faithfulness/decompose",
        )
    assert captured.get("model") == pin


@pytest.mark.asyncio
async def test_call_internal_llm_no_stage_passes_empty_model_so_caller_default_wins():
    """No stage → resolver returns '' → call_llm falls back to INTERNAL_LLM_MODEL."""
    from core.utils import internal_llm

    captured: dict = {}

    async def _fake_call_llm(messages, **kwargs):
        captured.update(kwargs)
        return "ok"

    with patch("core.utils.llm_client.call_llm", new=AsyncMock(side_effect=_fake_call_llm)):
        await internal_llm.call_internal_llm([{"role": "user", "content": "hi"}])
    assert captured.get("model") == ""
