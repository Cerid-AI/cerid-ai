# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for alias-aware entity canonicalization (entity_resolution module).

TDD suite for Task 2.2. Tests run cheapest-first (Tier A → B → C → fallback).
Tier C injects a fake embed callable — no quenchforge required.
"""
from __future__ import annotations

import math

import config.settings as settings_module
from core.agents.entity_resolution import resolve_canonical

# ---------------------------------------------------------------------------
# Tier A — deterministic alias table
# ---------------------------------------------------------------------------

class TestTierA:
    """Known alias clusters merge to the canonical slug."""

    def test_federal_reserve_full_name(self):
        assert resolve_canonical("Federal Reserve", "ORG") == "org:federal-reserve"

    def test_the_fed_alias(self):
        assert resolve_canonical("the Fed", "ORG") == "org:federal-reserve"

    def test_fomc_alias(self):
        assert resolve_canonical("FOMC", "ORG") == "org:federal-reserve"

    def test_case_insensitive_alias_lookup(self):
        # Alias table lookup must be case-insensitive after normalisation.
        assert resolve_canonical("fomc", "ORG") == "org:federal-reserve"
        assert resolve_canonical("THE FED", "ORG") == "org:federal-reserve"

    def test_alias_only_within_matching_type(self):
        # Even if "the Fed" were somehow listed under ASSET, ORG lookup is independent.
        # This also confirms cross-type isolation at the alias layer.
        fed_org = resolve_canonical("Federal Reserve", "ORG")
        assert fed_org == "org:federal-reserve"

    def test_us_treasury_alias(self):
        assert resolve_canonical("Treasury", "ORG") == "org:us-treasury"

    def test_sec_alias(self):
        assert resolve_canonical("SEC", "ORG") == "org:sec"
        assert resolve_canonical("Securities and Exchange Commission", "ORG") == "org:sec"

    def test_alias_not_applied_across_type(self):
        # "FED" as an ASSET is a ticker — must NOT resolve to the Federal Reserve.
        # The alias table only fires for ORG; ASSET has no "fed" entry.
        assert resolve_canonical("FED", "ASSET") == "asset:fed"
        # Confirm the ORG alias still works.
        assert resolve_canonical("FED", "ORG") == "org:federal-reserve"


# ---------------------------------------------------------------------------
# Tier B — string normalisation (honorifics, initials, legal suffixes)
# ---------------------------------------------------------------------------

class TestTierB:
    """Pure normalisation merges surface variants without an alias table hit."""

    def test_middle_initial_dropped(self):
        # "Elon R. Musk" → "Elon Musk" → person:elon-musk
        assert resolve_canonical("Elon R. Musk", "PERSON") == "person:elon-musk"

    def test_no_initial_unchanged(self):
        assert resolve_canonical("Elon Musk", "PERSON") == "person:elon-musk"

    def test_middle_initial_and_base_are_same(self):
        assert resolve_canonical("Elon R. Musk", "PERSON") == resolve_canonical("Elon Musk", "PERSON")

    def test_honorific_mr_stripped(self):
        assert resolve_canonical("Mr. John Smith", "PERSON") == "person:john-smith"

    def test_honorific_ms_stripped(self):
        assert resolve_canonical("Ms. Jane Doe", "PERSON") == "person:jane-doe"

    def test_honorific_dr_stripped(self):
        assert resolve_canonical("Dr. Alan Turing", "PERSON") == "person:alan-turing"

    def test_honorific_prof_stripped(self):
        assert resolve_canonical("Prof. Richard Feynman", "PERSON") == "person:richard-feynman"

    def test_legal_suffix_inc_stripped(self):
        # "Apple Inc." → "Apple" → org:apple
        assert resolve_canonical("Apple Inc.", "ORG") == "org:apple"

    def test_legal_suffix_llc_stripped(self):
        assert resolve_canonical("Acme LLC", "ORG") == "org:acme"

    def test_legal_suffix_corp_stripped(self):
        assert resolve_canonical("MegaCorp Corp", "ORG") == "org:megacorp"

    def test_legal_suffix_ltd_stripped(self):
        assert resolve_canonical("Widget Ltd.", "ORG") == "org:widget"

    def test_legal_suffix_plc_stripped(self):
        assert resolve_canonical("BritCo PLC", "ORG") == "org:britco"

    def test_inc_and_base_are_same(self):
        assert resolve_canonical("Apple Inc.", "ORG") == resolve_canonical("Apple", "ORG")

    def test_whitespace_folded(self):
        assert resolve_canonical("  Federal   Reserve  ", "ORG") == resolve_canonical("Federal Reserve", "ORG")

    def test_multi_initial_all_stripped(self):
        # "John A. B. Smith" → all middle initials stripped → "John Smith"
        assert resolve_canonical("John A. B. Smith", "PERSON") == "person:john-smith"

    def test_the_company_not_overstripped(self):
        # Stripping "Company" from "The Company" would leave only "The" — a stop-word.
        # The guard must keep the original name.
        result = resolve_canonical("The Company", "ORG")
        assert result != "org:the", f"Over-stripped to stop-word: {result}"
        assert result == "org:the-company"


# ---------------------------------------------------------------------------
# Tier C — opt-in embedding nearest-canonical (fake inject)
# ---------------------------------------------------------------------------

def _make_fake_embed(name_to_vec: dict[str, list[float]]):
    """Return a fake embed callable mapping names to fixed unit vectors."""
    def fake_embed(name: str) -> list[float]:
        vec = name_to_vec.get(name, [0.0, 0.0, 1.0])
        norm = math.sqrt(sum(x * x for x in vec))
        return [x / norm for x in vec] if norm > 0 else vec
    return fake_embed


class TestTierC:
    """Tier C merges via embedding when tiers A/B miss, skips below threshold."""

    def test_near_identical_names_merge(self, monkeypatch):
        monkeypatch.setattr(settings_module, "ENTITY_RESOLUTION_EMBED", True)
        embed = _make_fake_embed({
            "Jerome Powell": [1.0, 0.0, 0.0],
            "Jay Powell": [0.999, 0.001, 0.0],  # cosine ≈ 0.9999
        })
        id1 = resolve_canonical("Jerome Powell", "PERSON", embed=embed, existing={"PERSON": ["Jerome Powell"]})
        id2 = resolve_canonical("Jay Powell", "PERSON", embed=embed, existing={"PERSON": ["Jerome Powell"]})
        assert id1 == id2, "Near-identical embeddings must merge to same canonical"

    def test_orthogonal_names_stay_distinct(self, monkeypatch):
        monkeypatch.setattr(settings_module, "ENTITY_RESOLUTION_EMBED", True)
        embed = _make_fake_embed({
            "Jerome Powell": [1.0, 0.0, 0.0],
            "Janet Yellen": [0.0, 1.0, 0.0],  # cosine = 0
        })
        id1 = resolve_canonical("Jerome Powell", "PERSON", embed=embed, existing={"PERSON": ["Jerome Powell"]})
        id2 = resolve_canonical("Janet Yellen", "PERSON", embed=embed, existing={"PERSON": ["Jerome Powell"]})
        assert id1 != id2, "Orthogonal embeddings must remain distinct"

    def test_no_embed_skips_tier_c(self):
        # embed=None → tiers A/B only; novel name falls back to slug.
        result = resolve_canonical("Completely Novel Name XYZ", "PERSON", embed=None)
        assert result == "person:completely-novel-name-xyz"

    def test_cross_type_not_merged_by_embedding(self, monkeypatch):
        monkeypatch.setattr(settings_module, "ENTITY_RESOLUTION_EMBED", True)
        # Same embedding for "Apple" but different entity_type → must stay distinct.
        embed = _make_fake_embed({
            "Apple": [1.0, 0.0, 0.0],
        })
        id_org = resolve_canonical("Apple", "ORG", embed=embed, existing={"ORG": ["Apple"]})
        id_asset = resolve_canonical("Apple", "ASSET", embed=embed, existing={"ASSET": ["Apple"]})
        # They're the same name so same slug, but different type prefixes.
        assert id_org.startswith("org:"), f"Expected org: prefix, got {id_org}"
        assert id_asset.startswith("asset:"), f"Expected asset: prefix, got {id_asset}"
        assert id_org != id_asset

    def test_embed_flag_false_skips_tier_c(self, monkeypatch):
        # With ENTITY_RESOLUTION_EMBED=False, Tier C must NOT run even when
        # an embed callable is passed. Result must be the slug fallback.
        monkeypatch.setattr(settings_module, "ENTITY_RESOLUTION_EMBED", False)
        embed = _make_fake_embed({
            "Jerome Powell": [1.0, 0.0, 0.0],
            "Jay Powell": [0.999, 0.001, 0.0],
        })
        # Jay Powell has no alias or normalization hit, so without Tier C it
        # falls back to the raw slug regardless of the near-identical embedding.
        result = resolve_canonical(
            "Jay Powell", "PERSON",
            embed=embed,
            existing={"PERSON": ["Jerome Powell"]},
        )
        assert result == "person:jay-powell", (
            f"Tier C ran despite ENTITY_RESOLUTION_EMBED=False: {result}"
        )

    def test_embed_flag_true_enables_tier_c(self, monkeypatch):
        # With ENTITY_RESOLUTION_EMBED=True and a near-identical embed, Tier C
        # fires and merges Jay Powell → Jerome Powell's canonical.
        monkeypatch.setattr(settings_module, "ENTITY_RESOLUTION_EMBED", True)
        embed = _make_fake_embed({
            "Jerome Powell": [1.0, 0.0, 0.0],
            "Jay Powell": [0.999, 0.001, 0.0],
        })
        result = resolve_canonical(
            "Jay Powell", "PERSON",
            embed=embed,
            existing={"PERSON": ["Jerome Powell"]},
        )
        assert result == "person:jerome-powell", (
            f"Tier C did not merge with ENTITY_RESOLUTION_EMBED=True: {result}"
        )


# ---------------------------------------------------------------------------
# Cross-type isolation (all tiers)
# ---------------------------------------------------------------------------

class TestCrossTypeIsolation:
    """Entity type must never be crossed during merging."""

    def test_apple_org_vs_apple_asset_distinct(self):
        org_id = resolve_canonical("Apple", "ORG")
        asset_id = resolve_canonical("Apple", "ASSET")
        assert org_id != asset_id

    def test_apple_inc_org_vs_apple_asset_distinct(self):
        # Even after stripping "Inc." from ORG, different type from ASSET.
        org_id = resolve_canonical("Apple Inc.", "ORG")
        asset_id = resolve_canonical("Apple", "ASSET")
        assert org_id != asset_id


# ---------------------------------------------------------------------------
# Fallback — novel single name → unchanged slug
# ---------------------------------------------------------------------------

class TestFallback:
    """A novel name with no alias/normalization/embedding match falls back to slug."""

    def test_novel_org(self):
        assert resolve_canonical("Zephyr Technologies", "ORG") == "org:zephyr-technologies"

    def test_novel_person(self):
        assert resolve_canonical("Alice Wonderland", "PERSON") == "person:alice-wonderland"

    def test_novel_asset(self):
        assert resolve_canonical("BTC/USD", "ASSET") == "asset:btc-usd"

    def test_no_regression_on_existing_canonical_ids(self):
        # Ensure existing single-name canonical_id outputs are unchanged.
        # These replicate the test_entity_extraction.py::TestCanonicalId assertions
        # via the new resolve_canonical path.
        assert resolve_canonical("Elon Musk", "PERSON") == "person:elon-musk"
        assert resolve_canonical("BTC/USD", "ASSET") == "asset:btc-usd"
