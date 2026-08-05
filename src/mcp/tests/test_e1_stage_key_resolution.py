# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 CR-006/014 — per-stage provider routing + model-tiering now resolve.

The config stage keys didn't match the live call_internal_llm ``stage=`` literals,
so PIPELINE_PROVIDERS overrides were inert and briefs/HyPE lost their model tier
(fell to the INTERNAL_LLM_MODEL default). Fix: PIPELINE_PROVIDERS keys renamed to
the real stage names + STAGE_PROFILES lookup falls back to the pre-slash prefix
for sub-stages. Synthetic, no stack."""
from __future__ import annotations

import config
from config.settings import PIPELINE_PROVIDERS
from config.stage_profiles import Hardness, hardness_for, tier_for
from core.utils.internal_llm import _resolve_stage_provider

# --- CR-006: PIPELINE_PROVIDERS keys match live stage names ---

def test_pipeline_providers_keys_are_live_stages() -> None:
    assert set(PIPELINE_PROVIDERS) == {
        "claim_extraction",
        "query_decompose",
        "topic_extraction",
        "memory_conflict_resolve",
        "rerank_llm",
    }
    # the old mismatched / dead keys are gone
    for dead in ("query_decomposition", "memory_resolution", "reranking",
                 "verification_simple", "verification_complex", "chat_generation"):
        assert dead not in PIPELINE_PROVIDERS


def test_resolve_stage_provider_matches_live_stage(monkeypatch) -> None:  # noqa: ANN001
    """A live stage name now hits PIPELINE_PROVIDERS (exact-match lookup)."""
    monkeypatch.setattr(config, "PIPELINE_PROVIDERS", {"query_decompose": "myprovider"}, raising=False)
    monkeypatch.delenv("PROVIDER_STAGE_QUERY_DECOMPOSE", raising=False)
    assert _resolve_stage_provider("query_decompose", "default") == "myprovider"
    # the old wrong key would have fallen through to the default
    assert _resolve_stage_provider("query_decomposition", "default") == "default"


# --- CR-014: slashed sub-stages resolve to their profile's tier ---

def test_slashed_substage_resolves_to_profile_tier() -> None:
    assert hardness_for("brief/daily") is Hardness.FRONTIER
    assert tier_for("brief/daily") == "expert"       # FRONTIER -> expert
    assert tier_for("brief/weekly") == "expert"
    assert tier_for("hype_index/generate") == "research"  # MODERATE -> research


def test_exact_substage_profile_is_preserved() -> None:
    """An explicit slashed key (faithfulness/decompose) still matches exactly,
    not via the 'faithfulness' prefix (which has no profile)."""
    assert hardness_for("faithfulness/decompose") is Hardness.MODERATE


def test_unknown_stage_still_none() -> None:
    assert hardness_for("nonexistent/substage") is None
    assert tier_for("nonexistent") is None
