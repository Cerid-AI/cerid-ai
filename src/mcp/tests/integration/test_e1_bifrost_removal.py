# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 Phase-3e-1 verifiability harness — DEAD BIFROST-GEN REMOVAL probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 3.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-005, CR-052).

Bifrost was retired 2026-04-17, but ``models.py`` still regenerated a Bifrost
config into ``stacks/bifrost/`` (a directory that no longer exists) on every
assignment change and told the operator to "Restart Bifrost to apply changes" —
a false instruction (role assignments are read live via ``_current_assignments``).
The degradation manager's ``_LLM_BREAKERS`` also still listed the retired
``bifrost-verify`` / ``bifrost-claims`` breakers (which never open) instead of the
active ``quenchforge-chat`` breaker (CR-052).

3e-1 removes the dead Bifrost generation (keeping the LIVE role-assignment API +
the Settings→Models doctor that render it) and corrects the breaker list.
RED-then-GREEN; GREEN -> preservation gates.

NOTE (operator decision 2026-07-20): only the dead Bifrost *generation* is
removed; the 8-role assignment API + GET /doctor compat report stay — they feed a
live feature, contrary to the audit's "roles are dead" framing.
"""
from __future__ import annotations

import pytest

import app.routers.models as models_mod
import utils.degradation as degradation
from app.routers.models import ModelAssignments, update_assignments


def test_bifrost_config_generation_is_removed():
    """The dead Bifrost config generator + its stack-path constants must be gone.
    RED on HEAD (CR-005): generate_bifrost_config still renders to stacks/bifrost."""
    assert not hasattr(models_mod, "generate_bifrost_config"), (
        "generate_bifrost_config still present — dead Bifrost generation (CR-005)"
    )
    assert not hasattr(models_mod, "_BIFROST_CONFIG_PATH"), (
        "_BIFROST_CONFIG_PATH still present — dead Bifrost stack path (CR-005)"
    )


@pytest.mark.preservation
async def test_put_assignments_no_longer_demands_a_bifrost_restart(tmp_path, monkeypatch):
    """A live assignment change applies immediately (chat reads _current_assignments
    fresh); the response must NOT claim a Bifrost restart is required. RED on HEAD
    (CR-005): restart_required=True + a 'Restart Bifrost' message."""
    monkeypatch.setattr(models_mod, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(models_mod, "_MODEL_CONFIG_PATH", tmp_path / "model_config.json")

    result = await update_assignments(
        ModelAssignments(assignments={"general": "anthropic/claude-sonnet-4.6"})
    )

    assert result.success is True
    assert result.restart_required is False, (
        "PUT /assignments still says a restart is required — role changes apply live, "
        "and Bifrost is retired (CR-005)"
    )
    assert "bifrost" not in result.message.lower(), (
        f"response still references Bifrost: {result.message!r} (CR-005)"
    )


def test_llm_breaker_list_reflects_active_backends():
    """_LLM_BREAKERS must track the active LLM breakers — the quenchforge chat
    breaker included, the retired bifrost breakers dropped. RED on HEAD (CR-052)."""
    assert "quenchforge-chat" in degradation._LLM_BREAKERS, (
        "the active quenchforge-chat breaker is absent from _LLM_BREAKERS, so a "
        "quenchforge box's LLM degradation is invisible (CR-052)"
    )
    assert "bifrost-verify" not in degradation._LLM_BREAKERS, (
        "retired bifrost-verify breaker still listed — never opens, masks 'down' (CR-052)"
    )
    assert "bifrost-claims" not in degradation._LLM_BREAKERS
