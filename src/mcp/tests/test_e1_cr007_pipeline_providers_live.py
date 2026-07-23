# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 post-audit M3-10 — CR-007 PIPELINE_PROVIDERS follows runtime provider switch.

At import the five live stages freeze to boot INTERNAL_LLM_PROVIDER. Without a
rebuild, set_active_provider left them on the old provider until restart.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def restore_provider_plane(monkeypatch: pytest.MonkeyPatch):
    """Restore provider env + config + PIPELINE_PROVIDERS after set_active_provider.

    set_active_provider writes os.environ and config attrs outside monkeypatch
    tracking; without restore, later tests see quenchforge/ollama as default and
    call_internal_llm skips the OpenRouter call_llm path.
    """
    import config
    from core.routing.provider_state import rebuild_pipeline_providers

    saved_env = {
        k: os.environ.get(k)
        for k in (
            "INTERNAL_LLM_PROVIDER",
            "INTERNAL_LLM_MODEL",
            "OLLAMA_ENABLED",
            "PROVIDER_CLAIM_EXTRACTION",
            "PROVIDER_QUERY_DECOMPOSE",
            "PROVIDER_TOPIC_EXTRACTION",
            "PROVIDER_MEMORY_CONFLICT_RESOLVE",
            "PROVIDER_RERANK_LLM",
        )
    }
    saved_provider = getattr(config, "INTERNAL_LLM_PROVIDER", "openrouter")
    saved_model = getattr(config, "INTERNAL_LLM_MODEL", None)
    saved_pipeline = dict(getattr(config, "PIPELINE_PROVIDERS", {}))
    yield
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    config.INTERNAL_LLM_PROVIDER = saved_provider
    if saved_model is not None:
        config.INTERNAL_LLM_MODEL = saved_model
    config.PIPELINE_PROVIDERS = saved_pipeline
    rebuild_pipeline_providers(saved_provider)


def test_set_active_provider_rebuilds_pipeline_providers(
    restore_provider_plane, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config
    from core.routing.provider_state import set_active_provider
    from core.utils.internal_llm import _resolve_stage_provider

    for env in (
        "PROVIDER_CLAIM_EXTRACTION",
        "PROVIDER_QUERY_DECOMPOSE",
        "PROVIDER_TOPIC_EXTRACTION",
        "PROVIDER_MEMORY_CONFLICT_RESOLVE",
        "PROVIDER_RERANK_LLM",
    ):
        monkeypatch.delenv(env, raising=False)

    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "openrouter")
    config.INTERNAL_LLM_PROVIDER = "openrouter"
    config.PIPELINE_PROVIDERS = {k: "openrouter" for k in config.PIPELINE_PROVIDERS}

    set_active_provider("quenchforge", "llama3.1-8b")

    assert config.INTERNAL_LLM_PROVIDER == "quenchforge"
    for stage, prov in config.PIPELINE_PROVIDERS.items():
        assert prov == "quenchforge", f"{stage} still frozen at {prov!r}"
    assert _resolve_stage_provider("query_decompose", "quenchforge") == "quenchforge"


def test_explicit_stage_env_pin_survives_rebuild(
    restore_provider_plane, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config
    from core.routing.provider_state import set_active_provider

    monkeypatch.setenv("PROVIDER_CLAIM_EXTRACTION", "openrouter")
    for env in (
        "PROVIDER_QUERY_DECOMPOSE",
        "PROVIDER_TOPIC_EXTRACTION",
        "PROVIDER_MEMORY_CONFLICT_RESOLVE",
        "PROVIDER_RERANK_LLM",
    ):
        monkeypatch.delenv(env, raising=False)

    set_active_provider("ollama", "llama3.1-8b")
    assert config.PIPELINE_PROVIDERS["claim_extraction"] == "openrouter"
    assert config.PIPELINE_PROVIDERS["query_decompose"] == "ollama"
