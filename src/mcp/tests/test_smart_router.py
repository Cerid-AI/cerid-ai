# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for smart LLM router (utils/smart_router.py)."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from utils.smart_router import (
    CAPABLE_MODELS,
    CHEAP_MODELS,
    FREE_MODELS,
    Complexity,
    TaskType,
    _classify_complexity,
    get_model_registry,
    route,
)

# ---------------------------------------------------------------------------
# _classify_complexity (heuristic, no LLM call)
# ---------------------------------------------------------------------------


class TestClassifyComplexity:
    def test_classify_simple_query(self):
        """Short greeting / factual lookup -> SIMPLE."""
        assert _classify_complexity("what is the capital of France?") == Complexity.SIMPLE

    def test_classify_moderate_query(self):
        """Normal question without complex/research keywords -> MODERATE."""
        assert _classify_complexity(
            "How does photosynthesis work in most plants?"
        ) == Complexity.MODERATE

    def test_classify_complex_query(self):
        """Multi-part question with complex keywords -> COMPLEX."""
        result = _classify_complexity(
            "Design and implement a thread-safe caching system with LRU eviction"
        )
        assert result == Complexity.COMPLEX

    def test_classify_research_query(self):
        """Research keywords (latest, news, 2026) -> RESEARCH."""
        assert _classify_complexity("latest news about AI in 2026") == Complexity.RESEARCH

    def test_classify_long_query_as_complex(self):
        """Queries over 100 words -> COMPLEX."""
        long_query = " ".join(["word"] * 101)
        assert _classify_complexity(long_query) == Complexity.COMPLEX

    def test_classify_empty_query(self):
        """Empty query -> MODERATE (default)."""
        assert _classify_complexity("") == Complexity.MODERATE


# ---------------------------------------------------------------------------
# route() -- chat routing
# ---------------------------------------------------------------------------


class TestRouteChat:
    @pytest.mark.asyncio
    async def test_route_chat_simple(self):
        """Simple chat -> free model (cheapest available)."""
        with patch("utils.smart_router._check_ollama", new_callable=AsyncMock, return_value=False):
            decision = await route("what is 2+2?", task_type=TaskType.CHAT)

        assert decision.provider == "openrouter_free"
        assert decision.model == FREE_MODELS["llama-3.3"]
        assert decision.estimated_cost_per_1k == 0.0

    @pytest.mark.asyncio
    async def test_route_chat_complex(self):
        """Complex chat -> capable model."""
        with patch("utils.smart_router._check_ollama", new_callable=AsyncMock, return_value=False):
            decision = await route(
                "implement a distributed lock with Redis and analyze the trade-offs",
                task_type=TaskType.CHAT,
            )

        assert decision.provider == "openrouter_paid"
        assert decision.model == str(CAPABLE_MODELS["claude-sonnet"]["id"])
        assert decision.estimated_cost_per_1k > 0

    @pytest.mark.asyncio
    async def test_route_verification(self):
        """Verification task -> dedicated verification model."""
        decision = await route(task_type=TaskType.VERIFICATION)

        assert decision.provider == "openrouter_paid"
        assert "verification" in decision.reason

    @pytest.mark.asyncio
    async def test_route_internal_ollama_available(self):
        """Internal task + Ollama available -> Ollama model (free, local)."""
        with patch("utils.smart_router._check_ollama", new_callable=AsyncMock, return_value=True), \
             patch("utils.smart_router._ollama_models", ["llama3.2:3b", "phi3:mini"]):
            decision = await route(task_type=TaskType.INTERNAL)

        assert decision.provider == "ollama"
        assert decision.estimated_cost_per_1k == 0.0

    @pytest.mark.asyncio
    async def test_route_internal_ollama_down(self):
        """Internal task + Ollama down -> free OpenRouter fallback."""
        with patch("utils.smart_router._check_ollama", new_callable=AsyncMock, return_value=False):
            decision = await route(task_type=TaskType.INTERNAL)

        assert decision.provider == "openrouter_free"
        assert decision.model == FREE_MODELS["llama-3.3"]


# ---------------------------------------------------------------------------
# route() -- cost sensitivity
# ---------------------------------------------------------------------------


class TestRouteCostSensitivity:
    @pytest.mark.asyncio
    async def test_route_cost_sensitivity_high_moderate(self):
        """High cost sensitivity + moderate query -> free model preferred."""
        with patch("utils.smart_router._check_ollama", new_callable=AsyncMock, return_value=False):
            decision = await route(
                "How does photosynthesis work?",
                task_type=TaskType.CHAT,
                cost_sensitivity="high",
            )

        assert decision.provider == "openrouter_free"
        assert decision.estimated_cost_per_1k == 0.0

    @pytest.mark.asyncio
    async def test_route_cost_sensitivity_low_moderate(self):
        """Low cost sensitivity + moderate -> upgraded model."""
        with patch("utils.smart_router._check_ollama", new_callable=AsyncMock, return_value=False):
            decision = await route(
                "How does photosynthesis work in eukaryotic cells?",
                task_type=TaskType.CHAT,
                cost_sensitivity="low",
            )

        assert decision.provider == "openrouter_paid"
        assert decision.estimated_cost_per_1k > 0

    @pytest.mark.asyncio
    async def test_route_cost_sensitivity_high_complex(self):
        """High cost sensitivity + complex -> cheapest capable model, not free."""
        with patch("utils.smart_router._check_ollama", new_callable=AsyncMock, return_value=False):
            decision = await route(
                "implement and debug a distributed consensus algorithm",
                task_type=TaskType.CHAT,
                cost_sensitivity="high",
            )

        # Complex queries with high cost still use a capable model, not free
        assert decision.provider == "openrouter_paid"
        assert decision.model == str(CHEAP_MODELS["gemini-flash"]["id"])


# ---------------------------------------------------------------------------
# route() -- failover: Ollama down for internal tasks
# ---------------------------------------------------------------------------


class TestRouteFailover:
    @pytest.mark.asyncio
    async def test_route_with_failover_ollama_down(self):
        """Ollama unavailable for internal task -> falls back to OpenRouter free."""
        with patch("utils.smart_router._check_ollama", new_callable=AsyncMock, return_value=False):
            decision = await route(task_type=TaskType.INTERNAL)

        assert decision.provider == "openrouter_free"
        assert decision.model == FREE_MODELS["llama-3.3"]
        assert "free tier" in decision.reason


# ---------------------------------------------------------------------------
# Ollama check caching
# ---------------------------------------------------------------------------


class TestOllamaCheckCaching:
    @pytest.mark.asyncio
    async def test_ollama_check_caching(self):
        """Repeated checks within 60s use cached result."""
        import utils.smart_router as sr

        # Set cached state directly
        sr._ollama_available = True
        sr._ollama_checked_at = time.monotonic()
        sr._ollama_models = ["llama3.2:3b"]

        try:
            # This should use the cache, not make a network call
            result = await sr._check_ollama()
            assert result is True
        finally:
            # Restore defaults
            sr._ollama_available = None
            sr._ollama_checked_at = 0
            sr._ollama_models = []

    @pytest.mark.asyncio
    async def test_ollama_check_cache_expired(self):
        """Cache older than 60s triggers a re-check."""
        import utils.smart_router as sr

        sr._ollama_available = True
        sr._ollama_checked_at = time.monotonic() - 120  # expired

        try:
            with patch.dict("os.environ", {"OLLAMA_ENABLED": "false"}):
                result = await sr._check_ollama()
                # OLLAMA_ENABLED=false -> False regardless of cached value
                assert result is False
        finally:
            sr._ollama_available = None
            sr._ollama_checked_at = 0
            sr._ollama_models = []


class TestGetModelRegistry:
    def test_get_model_registry(self):
        """Returns all model tiers."""
        registry = get_model_registry()

        assert set(registry.keys()) == {"free", "cheap", "capable", "research", "expert"}
        # Values in non-free tiers are model ID strings with provider/model format
        for tier in ("cheap", "capable", "research", "expert"):
            for model_id in registry[tier].values():
                assert isinstance(model_id, str) and "/" in model_id
