# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Task 17: smart-router uses scored classification, not first-keyword-match.

Guards against the regression where a bare 'analyze' or 'considering' token
pushes any query to COMPLEX/claude-sonnet — wasting tokens on simple queries
and leaving the free-llama path nearly dead. Also guards that every model
registry entry carries the ``openrouter/`` prefix so the Bifrost path keeps
working when USE_BIFROST is flipped on.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


def test_simple_greeting_is_simple():
    """Bare 'analyze' token doesn't push a 4-word greeting into COMPLEX."""
    from core.routing.smart_router import Complexity, classify_task_type
    tt = classify_task_type("analyze this simple greeting")
    assert tt in (Complexity.SIMPLE, Complexity.MODERATE), f"got {tt}"


def test_code_review_is_complex():
    """Long, code-heavy query with multiple signal tokens scores COMPLEX."""
    from core.routing.smart_router import Complexity, classify_task_type
    tt = classify_task_type(
        "Review this Python class: def handle(request): for item in items: "
        "if item.x > 0: validate(item). Refactor for clarity and analyze edge cases."
    )
    assert tt == Complexity.COMPLEX, f"got {tt}"


@pytest.mark.asyncio
async def test_cost_sensitivity_high_prefers_cheap():
    """With cost_sensitivity='high', moderate task picks a CHEAP or FREE model."""
    from core.routing.smart_router import route
    with patch(
        "core.routing.smart_router._check_ollama",
        new_callable=AsyncMock, return_value=False,
    ):
        decision = await route(
            query="summarize this article briefly",
            cost_sensitivity="high",
            kb_injection_count=0,
        )
    assert "gemini" in decision.model.lower() or "llama" in decision.model.lower(), (
        f"got {decision.model}"
    )


@pytest.mark.asyncio
async def test_cost_sensitivity_low_allows_capable():
    """cost_sensitivity='low' on a moderate+ task picks CAPABLE tier."""
    from core.routing.smart_router import route
    with patch(
        "core.routing.smart_router._check_ollama",
        new_callable=AsyncMock, return_value=False,
    ):
        decision = await route(
            query=(
                "analyze this architectural diagram and suggest improvements "
                "considering performance, scalability, and reliability"
            ),
            cost_sensitivity="low",
            kb_injection_count=3,
        )
    assert "sonnet" in decision.model.lower() or "opus" in decision.model.lower(), (
        f"got {decision.model}"
    )


def test_model_ids_have_openrouter_prefix():
    """Every registry entry must start with 'openrouter/' so Bifrost path
    doesn't silently break when USE_BIFROST is re-enabled."""
    from core.routing.smart_router import (
        CAPABLE_MODELS,
        CHEAP_MODELS,
        EXPERT_MODELS,
        FREE_MODELS,
        RESEARCH_MODELS,
    )
    for registry in (FREE_MODELS, CHEAP_MODELS, CAPABLE_MODELS, RESEARCH_MODELS, EXPERT_MODELS):
        for key, model in registry.items():
            mid = model["id"] if isinstance(model, dict) else model
            assert mid.startswith("openrouter/"), f"{key} = {mid}"
