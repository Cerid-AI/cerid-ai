# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Structural consistency: every chat-routable model maps to a known tier.

Phase 0.4a gap: ``core.routing.smart_router.TIER_P95_MS`` is hand-maintained
with no observed-latency reconciliation, and nothing guarded it against
drifting out of sync with the model ids actually configured in
``config.CHAT_FALLBACK_POOL`` / ``app.routers.models.DEFAULT_ASSIGNMENTS``.
This test does NOT edit ``core/routing/smart_router.py`` — it only reads
``TIER_P95_MS`` and asserts every configured chat model can be bucketed
into one of its tier keys via a family-name heuristic mirroring the model
tables (FREE_MODELS/CHEAP_MODELS/CAPABLE_MODELS/RESEARCH_MODELS/
EXPERT_MODELS) already defined there. A model that fails to classify (new
family added to the pool without a matching tier bucket) or that
classifies into a tier key TIER_P95_MS no longer has is exactly the drift
class this test exists to catch.

See app/processor/router.py's ``/processor/status`` (job_type_latency,
chat_route_counts_today, chat_model_latency) and
app/routers/chat.py's ``get_chat_model_latency_stats`` for the observed
per-model latency an integrator should reconcile TIER_P95_MS against.
"""
from __future__ import annotations

import config
from app.routers.models import DEFAULT_ASSIGNMENTS
from core.routing.smart_router import TIER_P95_MS


def _classify_model_tier(model_id: str) -> str | None:
    """Bucket a bare model id into an approximate TIER_P95_MS key.

    Mirrors the family groupings in ``core.routing.smart_router``'s
    FREE_MODELS/CHEAP_MODELS/CAPABLE_MODELS/RESEARCH_MODELS/EXPERT_MODELS
    tables. Not an exact-id lookup (the tables' ids drift from
    CHAT_FALLBACK_POOL/DEFAULT_ASSIGNMENTS independently via catalog
    refreshes — that drift is exactly what Phase 0.4a flags) — this is a
    coarser family-name heuristic good enough to prove every configured
    model has SOME eligible tier.
    """
    m = model_id.removeprefix("openrouter/").lower()

    if "claude-sonnet" in m or ("gpt-4o" in m and "mini" not in m):
        return "openrouter_capable"
    if "gpt-4o-mini" in m:
        return "openrouter_cheap"
    if "gemini" in m and "flash" in m:
        return "openrouter_cheap"
    if "grok" in m and "4.20" in m:
        return "openrouter_expert"
    if "grok" in m:
        return "openrouter_research"
    if "llama-3.3" in m:
        return "openrouter_free"
    return None


def _all_configured_chat_models() -> set[str]:
    return set(config.CHAT_FALLBACK_POOL) | set(DEFAULT_ASSIGNMENTS.values())


def test_every_configured_chat_model_classifies_to_a_known_tier():
    """Every model in CHAT_FALLBACK_POOL + DEFAULT_ASSIGNMENTS has a tier
    bucket, and that bucket is present in TIER_P95_MS."""
    models = _all_configured_chat_models()
    assert models, "expected at least one configured chat model"

    unclassified: list[str] = []
    missing_tier: list[tuple[str, str]] = []
    for model in sorted(models):
        tier = _classify_model_tier(model)
        if tier is None:
            unclassified.append(model)
        elif tier not in TIER_P95_MS:
            missing_tier.append((model, tier))

    assert not unclassified, (
        f"model(s) with no known tier family: {unclassified} — "
        "add a bucket rule to _classify_model_tier or investigate whether "
        "TIER_P95_MS needs a new tier"
    )
    assert not missing_tier, (
        f"model(s) classified into a tier missing from TIER_P95_MS: {missing_tier}"
    )


def test_chat_fallback_pool_is_nonempty():
    """Sanity guard: an empty pool would make the structural test vacuous."""
    assert len(config.CHAT_FALLBACK_POOL) > 0


def test_default_assignments_is_nonempty():
    """Sanity guard: an empty map would make the structural test vacuous."""
    assert len(DEFAULT_ASSIGNMENTS) > 0
