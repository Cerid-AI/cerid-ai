# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the hardware model-compatibility guard + config audit.

The guard ensures the auto-update / config never selects a model known to be
incompatible with the active hardware (e.g. `llama3.2:3b` crashes the Vega II
Metal stack), keeps a curated known-good local set per hardware profile, and
audits the live config for incompatible pins, dead OpenRouter pins, and
local-model currency.
"""
from __future__ import annotations

from core.routing import model_compat as mc


class TestIncompatibility:
    def test_known_crash_model_flagged_on_amd_mac(self):
        assert mc.is_incompatible("llama3.2:3b", "amd-mac") is True
        assert mc.incompatible_reason("llama3.2:3b", "amd-mac")  # non-empty reason

    def test_openrouter_form_of_crash_model_flagged(self):
        # The same model in OpenRouter id form must also be caught.
        assert mc.is_incompatible("meta-llama/llama-3.2-3b-instruct", "amd-mac") is True

    def test_known_good_model_not_flagged(self):
        assert mc.is_incompatible("llama3.1-8b", "amd-mac") is False
        assert mc.incompatible_reason("llama3.1-8b", "amd-mac") is None

    def test_unknown_or_empty_profile_is_permissive(self):
        # No profile configured → don't block (fail-open on the *guard*, since
        # we can't prove incompatibility; the documented crash is amd-mac only).
        assert mc.is_incompatible("llama3.2:3b", "") is False
        assert mc.is_incompatible("llama3.2:3b", "nvidia") is False

    def test_compatible_catalog_ids_filters_incompatible(self):
        catalog = ["meta-llama/llama-3.2-3b-instruct", "x-ai/grok-4.3", "qwen/qwen3-8b"]
        out = mc.compatible_catalog_ids(catalog, "amd-mac")
        assert "meta-llama/llama-3.2-3b-instruct" not in out
        assert "x-ai/grok-4.3" in out and "qwen/qwen3-8b" in out

    def test_compatible_catalog_ids_noop_without_profile(self):
        catalog = ["meta-llama/llama-3.2-3b-instruct", "x-ai/grok-4.3"]
        assert mc.compatible_catalog_ids(catalog, "") == catalog


class TestKnownGood:
    def test_known_good_local_for_amd_mac(self):
        kg = mc.known_good_local("amd-mac")
        assert kg["chat"] == "llama3.1-8b"
        assert "embed" in kg and "rerank" in kg

    def test_candidate_upgrades_present_with_validation_note(self):
        cands = mc.candidate_local_upgrades("chat")
        assert cands, "expected at least one chat upgrade candidate"
        # every candidate must carry a validate-on-device note (Metal compat
        # can only be proven by loading — never auto-adopt).
        assert all(c.get("validate") for c in cands)


class TestAudit:
    def test_audit_flags_incompatible_pin(self):
        findings = mc.audit_model_config(
            configured={"internal_llm": "llama3.2:3b"},
            hardware_profile="amd-mac",
            catalog_ids=["x-ai/grok-4.3"],
        )
        sev = {f["severity"] for f in findings if f["kind"] == "incompatible"}
        assert "error" in sev
        assert any("internal_llm" in f["role"] for f in findings if f["kind"] == "incompatible")

    def test_audit_flags_dead_openrouter_pin(self):
        # grok-4.1-fast no longer in the catalog → dead pin.
        findings = mc.audit_model_config(
            configured={"verification": "x-ai/grok-4.1-fast"},
            hardware_profile="amd-mac",
            catalog_ids=["x-ai/grok-4.3", "x-ai/grok-4.20"],
        )
        assert any(f["kind"] == "dead_pin" and f["role"] == "verification" for f in findings)

    def test_audit_clean_config_has_no_error(self):
        findings = mc.audit_model_config(
            configured={"internal_llm": "llama3.1-8b", "general": "openai/gpt-4o-mini"},
            hardware_profile="amd-mac",
            catalog_ids=["openai/gpt-4o-mini"],
        )
        assert not [f for f in findings if f["severity"] == "error"]

    def test_audit_local_model_not_known_good_is_info(self):
        findings = mc.audit_model_config(
            configured={"chat_local": "some-other-7b"},
            hardware_profile="amd-mac",
            catalog_ids=[],
            local_roles={"chat_local": "chat"},
        )
        assert any(f["kind"] == "local_currency" for f in findings)


class TestReport:
    def test_report_not_ok_on_incompatible(self):
        r = mc.build_compat_report(
            configured={"internal_llm": "llama3.2:3b"},
            hardware_profile="amd-mac",
            catalog_ids=["x-ai/grok-4.3"],
        )
        assert r["ok"] is False
        assert r["hardware_profile"] == "amd-mac"
        assert r["known_good_local"]["chat"] == "llama3.1-8b"
        assert "chat" in r["candidate_upgrades"]

    def test_report_ok_when_clean(self):
        r = mc.build_compat_report(
            configured={"general": "openai/gpt-4o-mini"},
            hardware_profile="amd-mac",
            catalog_ids=["openai/gpt-4o-mini"],
        )
        assert r["ok"] is True
        assert r["findings"] == []
