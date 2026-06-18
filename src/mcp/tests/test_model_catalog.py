# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the in-family latest-model resolver (core/routing/model_catalog)."""
from __future__ import annotations

from core.routing.model_catalog import (
    catalog_ids,
    diff_assignments,
    resolve_assignments,
    resolve_latest,
)

# A representative OpenRouter-style catalog (bare provider/model ids).
CATALOG = [
    "anthropic/claude-sonnet-4.6",
    "anthropic/claude-sonnet-4.7",
    "anthropic/claude-opus-4.6",
    "x-ai/grok-4.1-fast",
    "x-ai/grok-4.2-fast",
    "x-ai/grok-4.2",  # bare variant — must NOT capture the -fast family
    "meta-llama/llama-3.3-70b-instruct:free",
    "meta-llama/llama-3.4-70b-instruct:free",
    "meta-llama/llama-3.4-405b-instruct:free",  # different size — must stay out
    "openai/gpt-4o-mini",
    "google/gemini-3.1-flash-lite",
    "google/gemini-3.5-flash-lite",
]


class TestResolveLatest:
    def test_picks_higher_minor_in_family(self):
        assert resolve_latest("anthropic/claude-sonnet-4.6", CATALOG) == "anthropic/claude-sonnet-4.7"

    def test_preserves_variant_suffix(self):
        # grok-4.1-fast must go to grok-4.2-fast, not the bare grok-4.2
        assert resolve_latest("x-ai/grok-4.1-fast", CATALOG) == "x-ai/grok-4.2-fast"

    def test_preserves_size_and_free_suffix(self):
        # 70b + :free held fixed; the 405b sibling must not be chosen
        assert (
            resolve_latest("meta-llama/llama-3.3-70b-instruct:free", CATALOG)
            == "meta-llama/llama-3.4-70b-instruct:free"
        )

    def test_no_dotted_version_stays_pinned(self):
        # gpt-4o-mini has no dotted version token → never auto-upgraded
        assert resolve_latest("openai/gpt-4o-mini", CATALOG) == "openai/gpt-4o-mini"

    def test_already_latest_unchanged(self):
        assert resolve_latest("anthropic/claude-sonnet-4.7", CATALOG) == "anthropic/claude-sonnet-4.7"

    def test_empty_catalog_unchanged(self):
        assert resolve_latest("anthropic/claude-sonnet-4.6", []) == "anthropic/claude-sonnet-4.6"

    def test_routing_prefix_preserved(self):
        # Frontend-style id with openrouter/ prefix; prefix re-applied on output.
        assert (
            resolve_latest("openrouter/anthropic/claude-sonnet-4.6", CATALOG)
            == "openrouter/anthropic/claude-sonnet-4.7"
        )

    def test_version_tuple_ordering_not_lexical(self):
        # 4.20 > 4.3 numerically (tuple (4,20) > (4,3)), not as strings.
        cat = ["x-ai/grok-4.3", "x-ai/grok-4.20"]
        assert resolve_latest("x-ai/grok-4.3", cat) == "x-ai/grok-4.20"

    def test_does_not_cross_family(self):
        # opus must not resolve to a sonnet even though both bump 4.6→4.7.
        assert resolve_latest("anthropic/claude-opus-4.6", CATALOG) == "anthropic/claude-opus-4.6"


class TestResolveAssignments:
    def test_resolves_per_role_and_diffs(self):
        current = {
            "coding": "anthropic/claude-sonnet-4.6",
            "research": "x-ai/grok-4.1-fast",
            "general": "openai/gpt-4o-mini",  # pinned (no dotted version)
        }
        resolved = resolve_assignments(current, CATALOG)
        assert resolved == {
            "coding": "anthropic/claude-sonnet-4.7",
            "research": "x-ai/grok-4.2-fast",
            "general": "openai/gpt-4o-mini",
        }
        diff = diff_assignments(current, resolved)
        assert {d["role"] for d in diff} == {"coding", "research"}
        assert all({"role", "from", "to"} == set(d) for d in diff)


def test_catalog_ids_extraction():
    payload = [{"id": "a/b-1.0"}, {"id": "c/d-2.0"}, {"created": 1}, {}]
    assert catalog_ids(payload) == ["a/b-1.0", "c/d-2.0"]


# --- hardware-compatibility guard (auto-update must never adopt a crash model) ---

def test_resolve_assignments_skips_hardware_incompatible_successor():
    """A newer in-family model that is incompatible with the active hardware
    must NOT be adopted — the resolver filters the catalog by profile first."""
    current = {"chat": "meta-llama/llama-3.1-3b-instruct"}
    catalog = ["meta-llama/llama-3.2-3b-instruct"]  # incompatible on amd-mac
    # Without a profile, the resolver bumps to the newer in-family model.
    assert resolve_assignments(current, catalog)["chat"] == "meta-llama/llama-3.2-3b-instruct"
    # On amd-mac, llama-3.2-3b is denylisted → stays pinned to the safe current.
    guarded = resolve_assignments(current, catalog, hardware_profile="amd-mac")
    assert guarded["chat"] == "meta-llama/llama-3.1-3b-instruct"
