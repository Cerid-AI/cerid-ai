# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-3b verifiability harness — PROVIDER WRITE-AUTHORITY probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 3.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-040, CR-088, CR-089).

A provider switch has a co-dependent env key the writers forgot: the ollama proxy
gate (``ollama_proxy._ollama_enabled``) and the smart-router local gate
(``smart_router._check_ollama``) both require ``OLLAMA_ENABLED=true`` for the
``ollama`` provider. Pre-3b, no runtime writer set it:

- **CR-088** — ``POST /providers/ollama/enable`` set the provider but not
  ``OLLAMA_ENABLED``, so every /ollama proxy surface 503'd immediately after the
  enable endpoint returned ``status=enabled``.
- **CR-089** — ``PATCH /settings`` set the provider (env+attr) but not
  ``OLLAMA_ENABLED``, so choosing ``ollama`` split dispatch: pipeline stages went
  local while route()/CLASSIFICATION traffic stayed on openrouter.
- **CR-040** — ``PUT /providers/internal`` 400'd on ``quenchforge`` even though
  it is a first-class provider everywhere else.

3b makes ``provider_state.set_active_provider`` the coherent env-write authority
(``OLLAMA_ENABLED`` tracks the active provider), routes ``PATCH /settings`` through
it, and accepts quenchforge on ``PUT /providers/internal``. RED-then-GREEN;
GREEN -> preservation gates.
"""
from __future__ import annotations

import pytest

_CFG_ATTRS = ("INTERNAL_LLM_PROVIDER", "INTERNAL_LLM_MODEL")
_ENV_KEYS = (
    "INTERNAL_LLM_PROVIDER", "INTERNAL_LLM_MODEL", "OLLAMA_ENABLED", "OLLAMA_URL",
    "QUENCHFORGE_URL", "EMBEDDINGS_PROVIDER", "RERANK_PROVIDER",
)


@pytest.fixture
def clean_provider_state(monkeypatch):
    """Snapshot + restore the config-attr + env planes; openrouter baseline."""
    import config

    saved_cfg = {a: getattr(config, a, "__unset__") for a in _CFG_ATTRS}
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
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


@pytest.mark.preservation
def test_switch_to_ollama_enables_the_proxy_gate(clean_provider_state):
    """set_active_provider('ollama') must set OLLAMA_ENABLED so the ollama proxy
    gate + smart-router local gate see the switch. RED on HEAD (CR-088/089): the
    writer sets the provider but never OLLAMA_ENABLED."""
    from app.routers.ollama_proxy import _ollama_enabled
    from core.routing.provider_state import set_active_provider

    set_active_provider("ollama", "llama3.1-8b")

    assert _ollama_enabled(), (
        "switched to ollama but the proxy gate (_ollama_enabled) is still off — "
        "OLLAMA_ENABLED was not written, so /ollama proxy surfaces 503 and "
        "smart_router keeps routing local-selected traffic to openrouter (CR-088/089)"
    )


@pytest.mark.preservation
def test_switch_away_from_ollama_preserves_hybrid_gate(clean_provider_state, monkeypatch):
    """E1 R6 supersedes the CR-088/089 force-false: switching the primary
    provider away from ollama must NOT clear an explicitly-enabled local hybrid
    (cloud global + local classification). Explicit local disable is the
    ollama_enabled settings plane, not a side effect of set_active_provider."""
    from app.routers.ollama_proxy import _ollama_enabled
    from core.routing.provider_state import set_active_provider

    monkeypatch.setenv("OLLAMA_ENABLED", "true")  # hybrid: local stages still wanted
    set_active_provider("openrouter")

    assert _ollama_enabled(), (
        "switched to openrouter but OLLAMA_ENABLED was force-cleared — hybrid "
        "local classification/internal stages would go dark (E1 R6)"
    )


@pytest.mark.preservation
async def test_put_providers_internal_accepts_quenchforge(clean_provider_state):
    """PUT /providers/internal must accept quenchforge (a first-class provider
    everywhere else) and the switch must reach the dispatch plane. RED on HEAD
    (CR-040): the handler 400s on anything but openrouter/ollama."""
    from app.routers.providers import set_internal_provider
    from core.utils.inference_routing import get_routing_snapshot

    result = await set_internal_provider(
        {"provider": "quenchforge", "model": "llama3.1-8b"}
    )
    assert result["provider"] == "quenchforge"
    assert get_routing_snapshot()["llm"]["provider"] == "quenchforge", (
        "quenchforge accepted but the switch did not reach the dispatch plane"
    )


@pytest.mark.preservation
async def test_patch_settings_ollama_enables_the_gate(clean_provider_state):
    """PATCH /settings switching to ollama must route through the write authority
    so OLLAMA_ENABLED is set. RED on HEAD (CR-089): the settings handler writes
    the provider env+attr but never OLLAMA_ENABLED."""
    from app.routers.ollama_proxy import _ollama_enabled
    from app.routers.settings import SettingsUpdateRequest, update_settings_endpoint

    await update_settings_endpoint(SettingsUpdateRequest(internal_llm_provider="ollama"))

    assert _ollama_enabled(), (
        "PATCH /settings set provider=ollama but the proxy/local gate is still "
        "off — OLLAMA_ENABLED not written, dispatch splits (CR-089)"
    )
