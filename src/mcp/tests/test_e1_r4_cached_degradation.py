# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 post-audit M3-6 — R4/CR-052 CACHED tier uses configured LLM breakers only.

On a cloud-only install (openrouter key, no ollama/quenchforge), a total
OpenRouter outage must trip CACHED. Pre-fix required ALL of
_LLM_BREAKERS open, and get_breaker auto-creates CLOSED for never-used names.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import utils.degradation as degradation
from utils.degradation import DegradationManager, DegradationTier


@pytest.fixture
def cloud_only(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")  # pragma: allowlist secret
    monkeypatch.delenv("OLLAMA_ENABLED", raising=False)
    monkeypatch.delenv("QUENCHFORGE_URL", raising=False)
    yield


def test_configured_breakers_cloud_only(cloud_only) -> None:
    assert degradation._configured_llm_breakers() == ("openrouter",)


def test_configured_breakers_hybrid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")  # pragma: allowlist secret
    monkeypatch.setenv("OLLAMA_ENABLED", "true")
    names = degradation._configured_llm_breakers()
    assert "openrouter" in names
    assert "ollama" in names
    assert "quenchforge-chat" not in names


def test_cached_tier_when_only_configured_openrouter_is_open(cloud_only) -> None:
    """OpenRouter OPEN + never-dispatched ollama CLOSED → CACHED (R4)."""
    from core.utils.circuit_breaker import CircuitState

    class _B:
        def __init__(self, state):
            self.state = state

    def _fake_get(name: str):
        if name == "openrouter":
            return _B(CircuitState.OPEN)
        return _B(CircuitState.CLOSED)  # auto-created never-used

    with (
        patch.object(degradation, "get_breaker", _fake_get, create=True),
        patch.object(degradation, "_is_breaker_open", side_effect=lambda n: n == "openrouter"),
        patch.object(degradation, "_redis_down", return_value=False),
    ):
        tier = DegradationManager().current_tier()
    assert tier is DegradationTier.CACHED


def test_not_cached_when_configured_hybrid_partially_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hybrid: openrouter OPEN but ollama CLOSED → not all configured open."""
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")  # pragma: allowlist secret
    monkeypatch.setenv("OLLAMA_ENABLED", "true")

    with (
        patch.object(
            degradation,
            "_is_breaker_open",
            side_effect=lambda n: n == "openrouter",
        ),
        patch.object(degradation, "_redis_down", return_value=False),
        patch.object(degradation, "_any_open", return_value=False),
    ):
        tier = DegradationManager().current_tier()
    assert tier is not DegradationTier.CACHED
