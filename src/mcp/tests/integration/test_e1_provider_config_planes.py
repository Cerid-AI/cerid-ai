# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 Phase-0 verifiability harness — the PROVIDER-CONFIG-PLANE probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 3
(the cluster is surfaced in Phase 0 so the fix has a gate to turn green).
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``.

Runtime provider configuration lives in four disjoint planes with no single
authority, and different writers/readers touch different planes:
  A. ``os.environ`` (read by ``smart_router._check_ollama`` and
     ``inference_routing.get_routing_snapshot``)
  B. ``config`` module attrs (read by ``internal_llm`` dispatch and
     ``health.degradation_status``; written by ``PUT /providers/internal``)
  C. Redis (``cerid:model_providers:config``)
  D. the ``.env`` file (boot-time only)

Because ``PUT /providers/internal`` writes plane B while the smart-router /
``/health`` inference-routing surfaces read plane A, a runtime provider switch
is invisible to those readers (split-brain), and ``/health/status`` fabricates a
per-stage provider table that is wrong for quenchforge deployments.

Synthetic, offline. The RED probes are ``xfail(strict=True)`` keyed to their CR;
they flip to a live gate when the Phase-3 provider-state authority lands and a
runtime write reaches every reader. Do NOT tag ``preservation`` while xfailed.
"""
from __future__ import annotations

import os

import pytest

_CFG_ATTRS = ("INTERNAL_LLM_PROVIDER", "INTERNAL_LLM_MODEL")
_ENV_KEYS = (
    "INTERNAL_LLM_PROVIDER", "INTERNAL_LLM_MODEL", "OLLAMA_ENABLED", "OLLAMA_URL",
    "QUENCHFORGE_URL", "EMBEDDINGS_PROVIDER", "RERANK_PROVIDER",
    "QUENCHFORGE_DEFAULT_MODEL",
)


@pytest.fixture
def clean_provider_state(monkeypatch):
    """Snapshot + restore the config-attr plane and the env plane so a probe
    that mutates provider config never leaks into another test. Establishes an
    ``openrouter`` baseline on both planes."""
    import config

    saved_cfg = {a: getattr(config, a, "__unset__") for a in _CFG_ATTRS}
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)  # monkeypatch auto-restores env
    monkeypatch.setattr(config, "INTERNAL_LLM_PROVIDER", "openrouter", raising=False)
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "openrouter")
    try:
        yield config
    finally:
        for attr, val in saved_cfg.items():
            if val == "__unset__":
                if hasattr(config, attr):
                    delattr(config, attr)
            else:
                setattr(config, attr, val)


# ---------------------------------------------------------------------------
# CR-007 / CR-100 — a runtime provider switch must reach the dispatch plane
# ---------------------------------------------------------------------------

# E1 Phase 3a CLOSED CR-007/100: set_internal_provider now writes the canonical
# (env) plane via provider_state.set_active_provider, and get_routing_snapshot
# reads it through provider_state.active_provider — the switch reaches dispatch.
@pytest.mark.preservation
async def test_runtime_provider_switch_reaches_dispatch_plane(clean_provider_state):
    """After a runtime ``PUT /providers/internal`` switch to ``ollama``, the
    dispatch-side view of the provider (what smart_router + ``/health``
    inference-routing report) must reflect it."""
    from app.routers.providers import set_internal_provider
    from core.utils.inference_routing import get_routing_snapshot

    await set_internal_provider({"provider": "ollama", "model": "llama3.1-8b"})

    snapshot = get_routing_snapshot()
    assert snapshot["llm"]["provider"] == "ollama", (
        "runtime provider switch set config.INTERNAL_LLM_PROVIDER='ollama' but the "
        "dispatch plane (os.environ, read by smart_router._check_ollama and "
        "/health inference_routing) still reports "
        f"'{snapshot['llm']['provider']}' — split-brain (CR-007/CR-100)"
    )


# ---------------------------------------------------------------------------
# CR-024 — /health/status must report local dispatch truthfully for quenchforge
# ---------------------------------------------------------------------------

# E1 Phase 3a CLOSED CR-024: degradation_status resolves is_local via
# provider_state.is_local_provider (ollama OR quenchforge), so a quenchforge box
# reports its stages as local instead of fabricating openrouter.
@pytest.mark.preservation
async def test_health_status_reports_quenchforge_dispatch_truthfully(
    clean_provider_state, monkeypatch
):
    """On a quenchforge deployment every pipeline stage dispatches locally
    (``internal_llm`` treats quenchforge as local), and ``/health`` inference
    routing agrees — but ``/health/status`` ``pipeline_providers`` must not
    fabricate ``openrouter`` for every stage."""
    import config

    monkeypatch.setattr(config, "INTERNAL_LLM_PROVIDER", "quenchforge", raising=False)
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "quenchforge")
    # health_check() touches live services; stub it to a base WITHOUT an "ollama"
    # block (quenchforge deployments never set OLLAMA_ENABLED, so the real base
    # has none either).
    monkeypatch.setattr("app.routers.health.health_check", lambda: {"status": "ok"})

    from app.routers.health import degradation_status
    from core.utils.inference_routing import get_routing_snapshot

    # /health inference_routing tells the truth:
    assert get_routing_snapshot()["llm"]["provider"] == "quenchforge"

    # /health/status pipeline_providers must agree that stages run locally.
    pipeline = degradation_status()["pipeline_providers"]
    assert pipeline["chat_generation"] == "quenchforge", (
        "quenchforge deployment dispatches every stage locally and /health "
        f"inference_routing reports quenchforge, but /health/status says "
        f"'{pipeline['chat_generation']}' for chat_generation — fabricated "
        "pipeline_providers table (CR-024)"
    )


# ---------------------------------------------------------------------------
# GREEN anchor — the planes agree at a clean openrouter baseline
# ---------------------------------------------------------------------------

def test_green_anchor_planes_agree_at_openrouter_baseline(clean_provider_state):
    """With no runtime override applied, the config-attr plane and the env plane
    agree (both openrouter). Guards against a probe/fixture that desyncs the
    planes spuriously; holds now and after the Phase-3 authority lands."""
    import config
    from core.utils.inference_routing import get_routing_snapshot

    assert getattr(config, "INTERNAL_LLM_PROVIDER") == "openrouter"
    assert get_routing_snapshot()["llm"]["provider"] == "openrouter"
    assert os.getenv("INTERNAL_LLM_PROVIDER") == "openrouter"
