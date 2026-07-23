# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 post-audit M1-4 — R6 hybrid OLLAMA_ENABLED survives non-ollama switch.

CR-088/089 correctly set OLLAMA_ENABLED=true when switching TO ollama, but also
force-wrote false on every other switch — killing hybrid (openrouter primary +
local classification). Unit harness for prepush.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch):
    import config
    from core.routing.provider_state import rebuild_pipeline_providers

    saved = {
        "INTERNAL_LLM_PROVIDER": getattr(config, "INTERNAL_LLM_PROVIDER", "__unset__"),
        "INTERNAL_LLM_MODEL": getattr(config, "INTERNAL_LLM_MODEL", "__unset__"),
    }
    saved_env = {
        k: os.environ.get(k)
        for k in ("INTERNAL_LLM_PROVIDER", "INTERNAL_LLM_MODEL", "OLLAMA_ENABLED")
    }
    saved_pipeline = dict(getattr(config, "PIPELINE_PROVIDERS", {}))
    for key in ("INTERNAL_LLM_PROVIDER", "INTERNAL_LLM_MODEL", "OLLAMA_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    yield
    for attr, val in saved.items():
        if val == "__unset__":
            if hasattr(config, attr):
                delattr(config, attr)
        else:
            setattr(config, attr, val)
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    config.PIPELINE_PROVIDERS = saved_pipeline
    provider = getattr(config, "INTERNAL_LLM_PROVIDER", None) or os.environ.get(
        "INTERNAL_LLM_PROVIDER", "openrouter",
    )
    rebuild_pipeline_providers(provider)


def test_switch_to_ollama_still_enables_gate(clean_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """CR-088/089 must remain green: ollama switch sets OLLAMA_ENABLED=true."""
    from core.routing.provider_state import ollama_enabled, set_active_provider

    monkeypatch.setenv("OLLAMA_ENABLED", "false")
    set_active_provider("ollama", "llama3.1-8b")
    assert ollama_enabled() is True
    assert os.environ["OLLAMA_ENABLED"] == "true"


def test_switch_away_preserves_explicit_hybrid(clean_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """R6: hybrid operators keep OLLAMA_ENABLED when primary moves to openrouter."""
    from core.routing.provider_state import ollama_enabled, set_active_provider

    monkeypatch.setenv("OLLAMA_ENABLED", "true")
    set_active_provider("openrouter")
    assert ollama_enabled() is True
    assert os.environ["OLLAMA_ENABLED"] == "true"


def test_switch_to_quenchforge_does_not_force_false(
    clean_env, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.routing.provider_state import set_active_provider

    monkeypatch.setenv("OLLAMA_ENABLED", "true")
    set_active_provider("quenchforge")
    assert os.environ["OLLAMA_ENABLED"] == "true"
