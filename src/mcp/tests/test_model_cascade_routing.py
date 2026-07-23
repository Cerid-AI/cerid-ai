# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for ENABLE_MODEL_CASCADE — chat routing cascades SIMPLE queries to
the local backend when one is reachable.

When the flag is off, SIMPLE chat queries route to FREE_MODELS["llama-3.3"]
on OpenRouter (the pre-PR-5 baseline). When the flag is on AND a local
backend is reachable with at least one model, SIMPLE queries route there
instead. RESEARCH/MODERATE/COMPLEX paths are untouched.
"""
from __future__ import annotations

import pytest

from core.routing import smart_router as sr
from core.routing.smart_router import Complexity, RouteDecision, TaskType, route


async def _route(monkeypatch: pytest.MonkeyPatch, *, cascade: bool, ollama_ok: bool, models: list[str]) -> RouteDecision:
    monkeypatch.setattr(sr.config, "ENABLE_MODEL_CASCADE", cascade, raising=False)

    async def _fake_check_ollama() -> bool:
        return ollama_ok

    monkeypatch.setattr(sr, "_check_ollama", _fake_check_ollama)
    monkeypatch.setattr(sr, "_ollama_models", models, raising=False)

    # Pin classifier to SIMPLE so we test the cascade branch deterministically.
    async def _fake_classify(_q: str) -> Complexity:
        return Complexity.SIMPLE

    monkeypatch.setattr(sr, "_classify_with_best_available", _fake_classify)
    return await route("what is the capital of France?", task_type=TaskType.CHAT)


@pytest.mark.asyncio
async def test_simple_chat_uses_free_tier_when_cascade_off(monkeypatch: pytest.MonkeyPatch) -> None:
    decision = await _route(monkeypatch, cascade=False, ollama_ok=True, models=["llama3.2:3b"])
    assert decision.provider == "openrouter_paid"
    assert "llama-3.3" in decision.model.lower()


@pytest.mark.asyncio
async def test_simple_chat_uses_local_when_cascade_on_and_ollama_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = await _route(monkeypatch, cascade=True, ollama_ok=True, models=["llama3.2:3b"])
    assert decision.provider == "ollama"
    assert decision.estimated_cost_per_1k == 0.0
    assert "ENABLE_MODEL_CASCADE" in decision.reason


@pytest.mark.asyncio
async def test_simple_chat_falls_back_to_free_when_cascade_on_but_no_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cascade on + ollama down → use the same free-tier fallback as before."""
    decision = await _route(monkeypatch, cascade=True, ollama_ok=False, models=[])
    assert decision.provider == "openrouter_paid"


@pytest.mark.asyncio
async def test_simple_chat_falls_back_when_ollama_has_no_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reachable but empty Ollama → cascade declines to point users at a no-op."""
    decision = await _route(monkeypatch, cascade=True, ollama_ok=True, models=[])
    assert decision.provider == "openrouter_paid"


@pytest.mark.asyncio
async def test_cascade_picks_preferred_model_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """Picks the preferred model family ahead of arbitrary first-listed."""
    decision = await _route(
        monkeypatch,
        cascade=True,
        ollama_ok=True,
        models=["some-random-model:latest", "llama3.2:3b", "phi3:mini"],
    )
    # llama3.2 ranks ahead of phi3 in the preference list.
    assert decision.model == "llama3.2:3b"
    assert decision.provider == "ollama"
