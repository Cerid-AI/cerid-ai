# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Smart LLM routing -- picks the best model based on task, complexity, and availability.

Routes through this priority chain:
1. Task-specific model (verification, expert) -- always respected
2. Ollama (if available and suitable) -- free, instant, local
3. Free OpenRouter models -- for simple/internal operations
4. Paid OpenRouter models -- for complex queries requiring quality

The router maintains an availability cache so Ollama detection doesn't
add latency on every call (checks every 60 seconds).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from enum import Enum

import config

logger = logging.getLogger("ai-companion.smart_router")


class TaskType(Enum):
    """Categories of LLM tasks with different routing requirements."""

    CHAT = "chat"  # User-facing conversation
    INTERNAL = "internal"  # Pipeline ops (memory, synopsis, decomposition)
    VERIFICATION = "verification"  # Claim checking (needs specific models)
    VERIFICATION_WEB = "verification_web"  # Current event verification (needs :online)
    VERIFICATION_EXPERT = "verification_expert"  # Expert mode (premium model)
    CLASSIFICATION = "classification"  # Intent/domain classification


class Complexity(Enum):
    """Query complexity levels."""

    SIMPLE = "simple"  # Factual, short answer, basic math
    MODERATE = "moderate"  # Explanation, comparison, analysis
    COMPLEX = "complex"  # Multi-step reasoning, code generation, long-form
    RESEARCH = "research"  # Current events, real-time data, web search needed


@dataclass
class RouteDecision:
    """Result of routing decision."""

    model: str
    provider: str  # "ollama", "openrouter_free", "openrouter_paid"
    reason: str
    estimated_cost_per_1k: float  # USD per 1K tokens (0 for free)


# ---------------------------------------------------------------------------
# Model registry -- centralized model definitions
# ---------------------------------------------------------------------------

FREE_MODELS = {
    "llama-3.3": "meta-llama/llama-3.3-70b-instruct",
    "qwen-2.5": "qwen/qwen-2.5-72b-instruct",
}

CHEAP_MODELS = {
    "gpt-4o-mini": {"id": "openai/gpt-4o-mini", "cost": 0.00015},
    "gemini-flash": {"id": "google/gemini-2.5-flash", "cost": 0.0003},
}

CAPABLE_MODELS = {
    "claude-sonnet": {"id": "anthropic/claude-sonnet-4.6", "cost": 0.003},
    "gpt-4o": {"id": "openai/gpt-4o", "cost": 0.0025},
}

RESEARCH_MODELS = {
    "grok-online": {"id": "x-ai/grok-4.1-fast:online", "cost": 0.0002},
}

EXPERT_MODELS = {
    "grok-4": {"id": "x-ai/grok-4:online", "cost": 0.003},
}

# ---------------------------------------------------------------------------
# Ollama availability cache
# ---------------------------------------------------------------------------

_ollama_available: bool | None = None
_ollama_checked_at: float = 0
_OLLAMA_CHECK_INTERVAL = 60  # seconds
_ollama_models: list[str] = []


async def _check_ollama() -> bool:
    """Check if Ollama is reachable and has models.  Cached for 60 seconds."""
    global _ollama_available, _ollama_checked_at, _ollama_models

    now = time.monotonic()
    if _ollama_available is not None and (now - _ollama_checked_at) < _OLLAMA_CHECK_INTERVAL:
        return _ollama_available

    ollama_enabled = os.getenv("OLLAMA_ENABLED", "false").lower() == "true"
    if not ollama_enabled:
        _ollama_available = False
        _ollama_checked_at = now
        return False

    try:
        import httpx

        ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                _ollama_models = [m.get("name", "") for m in data.get("models", [])]
                _ollama_available = len(_ollama_models) > 0
            else:
                _ollama_available = False
    except Exception:
        _ollama_available = False

    _ollama_checked_at = now
    return _ollama_available


# ---------------------------------------------------------------------------
# Complexity classification (heuristic -- no LLM call)
# ---------------------------------------------------------------------------


def _classify_complexity(query: str) -> Complexity:
    """Heuristic complexity classification (no LLM call needed).

    Uses simple rules to avoid the chicken-and-egg problem of needing
    an LLM to classify a query before sending it to an LLM.
    """
    query_lower = query.lower().strip()
    word_count = len(query_lower.split())

    # Research indicators -- needs real-time data
    research_keywords = [
        "latest",
        "recent",
        "current",
        "today",
        "news",
        "2025",
        "2026",
        "trending",
        "stock price",
        "weather",
        "score",
        "election",
    ]
    if any(kw in query_lower for kw in research_keywords):
        return Complexity.RESEARCH

    # Simple indicators -- short factual queries
    simple_patterns = [
        "what is",
        "who is",
        "how many",
        "capital of",
        "define ",
        "what does",
        "when was",
        "where is",
        "how old",
    ]
    if word_count <= 15 and any(
        query_lower.startswith(p) or p in query_lower for p in simple_patterns
    ):
        return Complexity.SIMPLE

    # Complex indicators -- multi-step, code, analysis
    complex_keywords = [
        "implement",
        "build",
        "create",
        "design",
        "architect",
        "refactor",
        "debug",
        "optimize",
        "compare and contrast",
        "pros and cons",
        "step by step",
        "write a",
        "code",
        "function",
        "class",
    ]
    if any(kw in query_lower for kw in complex_keywords) or word_count > 100:
        return Complexity.COMPLEX

    # Default: moderate
    return Complexity.MODERATE


async def _classify_with_best_available(query: str) -> Complexity:
    """Classify query complexity using the best available method.

    Priority:
    1. Ollama LLM classification (free, ~200ms, more accurate on edge cases)
    2. Heuristic classification (instant, no LLM call, good for clear-cut cases)

    Ollama classification only runs if Ollama is available. The LLM is asked
    to classify — it never answers the user query directly.
    """
    # Always run heuristic first — it's instant and handles clear-cut cases
    heuristic_result = _classify_complexity(query)

    # If heuristic is confident (research/complex), trust it — skip LLM call
    if heuristic_result in (Complexity.RESEARCH, Complexity.COMPLEX):
        return heuristic_result

    # For simple/moderate (ambiguous), try Ollama classification if available
    ollama_ok = await _check_ollama()
    if not ollama_ok or not _ollama_models:
        return heuristic_result

    try:
        import httpx

        ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        # Pick smallest available model for classification (fastest)
        small_models = ["phi3", "gemma2", "qwen2.5", "llama3.2", "mistral"]
        classifier_model = _ollama_models[0]
        for pref in small_models:
            matching = [m for m in _ollama_models if pref in m]
            if matching:
                classifier_model = matching[0]
                break

        prompt = (
            "Classify this user query into exactly one category.\n"
            "Categories: simple, moderate, complex, research\n"
            "- simple: factual lookups, definitions, basic math, short answers\n"
            "- moderate: explanations, comparisons, how-to guides\n"
            "- complex: multi-step reasoning, code generation, architecture, analysis\n"
            "- research: needs current/real-time data, recent events, live info\n\n"
            "Respond with ONLY the category name. No explanation.\n\n"
            f"Query: {query[:500]}"
        )

        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"{ollama_url}/api/chat",
                json={
                    "model": classifier_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"temperature": 0.0, "num_predict": 10},
                },
            )
            resp.raise_for_status()
            content = resp.json().get("message", {}).get("content", "").strip().lower()

            # Parse LLM response
            for level in Complexity:
                if level.value in content:
                    logger.debug(
                        "Ollama classified '%s...' as %s (heuristic was %s)",
                        query[:40], level.value, heuristic_result.value,
                    )
                    return level

    except Exception as e:
        logger.debug("Ollama classification failed (%s), using heuristic", e)

    return heuristic_result


# ---------------------------------------------------------------------------
# Main routing function
# ---------------------------------------------------------------------------


async def route(
    query: str = "",
    *,
    task_type: TaskType = TaskType.CHAT,
    cost_sensitivity: str = "medium",
) -> RouteDecision:
    """Pick the best model for this query and task type.

    Cost sensitivity (from user settings):
    - "high": maximize free/cheap models, only use paid for research/expert
    - "medium": balance quality and cost (default)
    - "low": prefer capable models, cost is not a concern

    Priority:
    1. Task-specific models (verification, expert) -- always used
    2. Ollama (if available) -- free, instant, for internal ops only
    3. Free OpenRouter models -- for simple/internal tasks
    4. Paid OpenRouter models -- for complex/research tasks
    """

    # 1. Task-specific models -- always take precedence
    if task_type == TaskType.VERIFICATION:
        model = getattr(config, "VERIFICATION_MODEL", "openrouter/openai/gpt-4o-mini")
        if model.startswith("openrouter/"):
            model = model[len("openrouter/"):]
        return RouteDecision(
            model=model,
            provider="openrouter_paid",
            reason="dedicated verification model",
            estimated_cost_per_1k=0.00015,
        )

    if task_type == TaskType.VERIFICATION_WEB:
        model = getattr(
            config,
            "VERIFICATION_CURRENT_EVENT_MODEL",
            "openrouter/x-ai/grok-4.1-fast:online",
        )
        if model.startswith("openrouter/"):
            model = model[len("openrouter/"):]
        return RouteDecision(
            model=model,
            provider="openrouter_paid",
            reason="web-search verification",
            estimated_cost_per_1k=0.0002,
        )

    if task_type == TaskType.VERIFICATION_EXPERT:
        model = getattr(config, "VERIFICATION_EXPERT_MODEL", "openrouter/x-ai/grok-4:online")
        if model.startswith("openrouter/"):
            model = model[len("openrouter/"):]
        return RouteDecision(
            model=model,
            provider="openrouter_paid",
            reason="expert verification",
            estimated_cost_per_1k=0.003,
        )

    # 2. Internal operations -- try Ollama first, then free models
    if task_type in (TaskType.INTERNAL, TaskType.CLASSIFICATION):
        ollama_ok = await _check_ollama()
        if ollama_ok and _ollama_models:
            # Pick best available Ollama model
            preferred = ["llama3.2", "qwen2.5", "phi3", "mistral", "gemma2"]
            model = _ollama_models[0]  # default to first available
            for pref in preferred:
                matching = [m for m in _ollama_models if pref in m]
                if matching:
                    model = matching[0]
                    break
            return RouteDecision(
                model=model,
                provider="ollama",
                reason="local model (free, instant)",
                estimated_cost_per_1k=0.0,
            )

        # No Ollama -- use free OpenRouter model
        return RouteDecision(
            model=FREE_MODELS["llama-3.3"],
            provider="openrouter_free",
            reason="free tier model",
            estimated_cost_per_1k=0.0,
        )

    # 3. Chat -- classify complexity, then route to the RIGHT OpenRouter model
    # Ollama is never used for chat answers (quality too low for user-facing)
    # But Ollama CAN classify the query (free, instant) to pick the best model
    complexity = await _classify_with_best_available(query)

    # Cost sensitivity shifts model selection:
    # HIGH: use free models more aggressively (even for moderate queries)
    # LOW: use capable models more aggressively (even for simple queries)
    cs = cost_sensitivity.lower()

    if complexity == Complexity.RESEARCH:
        # Research always needs web search — no free alternative
        if cs == "high":
            # High cost sensitivity: use cheaper Grok Fast instead of Grok 4
            return RouteDecision(
                model=RESEARCH_MODELS["grok-online"]["id"],
                provider="openrouter_paid",
                reason="research query — using cheaper web model (high cost sensitivity)",
                estimated_cost_per_1k=0.0002,
            )
        return RouteDecision(
            model=RESEARCH_MODELS["grok-online"]["id"],
            provider="openrouter_paid",
            reason="research query — real-time data needed",
            estimated_cost_per_1k=0.0002,
        )

    if complexity == Complexity.SIMPLE:
        # Simple: always free — even low cost sensitivity doesn't waste money here
        return RouteDecision(
            model=FREE_MODELS["llama-3.3"],
            provider="openrouter_free",
            reason="simple query — free tier sufficient",
            estimated_cost_per_1k=0.0,
        )

    if complexity == Complexity.COMPLEX:
        if cs == "high":
            # High cost sensitivity: use cheap model even for complex (trade quality for savings)
            return RouteDecision(
                model=CHEAP_MODELS["gpt-4o-mini"]["id"],
                provider="openrouter_paid",
                reason="complex query — downgraded to cheap model (high cost sensitivity)",
                estimated_cost_per_1k=0.00015,
            )
        if cs == "low":
            # Low cost sensitivity: use best available model
            return RouteDecision(
                model=CAPABLE_MODELS["claude-sonnet"]["id"],
                provider="openrouter_paid",
                reason="complex query — best model (low cost sensitivity)",
                estimated_cost_per_1k=0.003,
            )
        # Medium: capable model (default)
        return RouteDecision(
            model=CAPABLE_MODELS["claude-sonnet"]["id"],
            provider="openrouter_paid",
            reason="complex query — strong reasoning needed",
            estimated_cost_per_1k=0.003,
        )

    # Moderate complexity
    if cs == "high":
        # High cost sensitivity: use free model for moderate queries too
        return RouteDecision(
            model=FREE_MODELS["llama-3.3"],
            provider="openrouter_free",
            reason="moderate query — using free model (high cost sensitivity)",
            estimated_cost_per_1k=0.0,
        )
    if cs == "low":
        # Low cost sensitivity: upgrade moderate to capable model
        return RouteDecision(
            model=CHEAP_MODELS["gemini-flash"]["id"],
            provider="openrouter_paid",
            reason="moderate query — upgraded model (low cost sensitivity)",
            estimated_cost_per_1k=0.0003,
        )
    # Medium: cheap paid model balances quality and cost
    return RouteDecision(
        model=CHEAP_MODELS["gpt-4o-mini"]["id"],
        provider="openrouter_paid",
        reason="moderate query — cost-effective balance",
        estimated_cost_per_1k=0.00015,
    )


# ---------------------------------------------------------------------------
# Failover-aware routing (wraps route() with provider resolution)
# ---------------------------------------------------------------------------


def route_with_failover(
    query: str = "",
    *,
    task_type: TaskType = TaskType.CHAT,
    cost_sensitivity: str = "medium",
    redis_client=None,  # noqa: ANN001
) -> RouteDecision:
    """Route with full failover chain and degraded mode detection.

    This is a synchronous wrapper that:
    1. Loads the model provider config from Redis
    2. Checks for degraded mode (no providers configured)
    3. Calls the async ``route()`` internally via the event loop
    4. Resolves the actual provider+key for the chosen model

    Note: this function itself is sync because deps.get_redis() returns
    a synchronous Redis client. The inner ``route()`` call uses the
    already-running event loop via ``asyncio``.
    """
    import asyncio

    from config.model_providers import (
        get_degraded_status,
        load_config,
        resolve_provider_for_model,
    )

    cfg = load_config(redis_client)
    degraded = get_degraded_status(cfg)

    if degraded.get("degraded") and task_type == TaskType.CHAT:
        return RouteDecision(
            model="none",
            provider="degraded",
            reason="No LLM provider configured \u2014 add a provider in Settings",
            estimated_cost_per_1k=0.0,
        )

    # Get the ideal route (async function — run in current event loop)
    loop = asyncio.get_event_loop()
    if loop.is_running():
        # We're already inside an async context (FastAPI) — create a task
        # Instead, provide an async version for callers in async context
        raise RuntimeError(
            "route_with_failover() is sync — use aroute_with_failover() in async context"
        )
    decision = loop.run_until_complete(
        route(query, task_type=task_type, cost_sensitivity=cost_sensitivity)
    )

    # Resolve which provider actually serves this model
    provider_name, _api_key = resolve_provider_for_model(decision.model, cfg)

    if provider_name == "none":
        # Try free fallback
        provider_name, _api_key = resolve_provider_for_model(
            "meta-llama/llama-3.3-70b-instruct:free", cfg
        )
        if provider_name != "none":
            decision = RouteDecision(
                model="meta-llama/llama-3.3-70b-instruct",
                provider=f"{provider_name}_free",
                reason=f"{decision.reason} (downgraded: no provider for original model)",
                estimated_cost_per_1k=0.0,
            )
        else:
            decision = RouteDecision(
                model="none",
                provider="degraded",
                reason="No provider available for any model \u2014 configure in Settings",
                estimated_cost_per_1k=0.0,
            )
    else:
        decision.provider = provider_name

    return decision


async def aroute_with_failover(
    query: str = "",
    *,
    task_type: TaskType = TaskType.CHAT,
    cost_sensitivity: str = "medium",
    redis_client=None,  # noqa: ANN001
) -> RouteDecision:
    """Async version of route_with_failover() for use in FastAPI handlers.

    Failover chain:
    1. Check degraded mode (no providers configured at all)
    2. Get ideal route via ``route()``
    3. Resolve provider: direct key → OpenRouter → free fallback → degraded
    """
    from config.model_providers import (
        get_degraded_status,
        load_config,
        resolve_provider_for_model,
    )

    cfg = load_config(redis_client)
    degraded = get_degraded_status(cfg)

    if degraded.get("degraded") and task_type == TaskType.CHAT:
        return RouteDecision(
            model="none",
            provider="degraded",
            reason="No LLM provider configured \u2014 add a provider in Settings",
            estimated_cost_per_1k=0.0,
        )

    # Get the ideal route
    decision = await route(
        query, task_type=task_type, cost_sensitivity=cost_sensitivity
    )

    # Resolve which provider actually serves this model
    provider_name, _api_key = resolve_provider_for_model(decision.model, cfg)

    if provider_name == "none":
        # Try free fallback
        provider_name, _api_key = resolve_provider_for_model(
            "meta-llama/llama-3.3-70b-instruct:free", cfg
        )
        if provider_name != "none":
            decision = RouteDecision(
                model="meta-llama/llama-3.3-70b-instruct",
                provider=f"{provider_name}_free",
                reason=f"{decision.reason} (downgraded: no provider for original model)",
                estimated_cost_per_1k=0.0,
            )
        else:
            decision = RouteDecision(
                model="none",
                provider="degraded",
                reason="No provider available for any model \u2014 configure in Settings",
                estimated_cost_per_1k=0.0,
            )
    else:
        decision.provider = provider_name

    return decision


# ---------------------------------------------------------------------------
# Registry export (for Settings UI)
# ---------------------------------------------------------------------------


def get_model_registry() -> dict:
    """Return the full model registry for the Settings UI."""
    return {
        "free": FREE_MODELS,
        "cheap": {k: v["id"] for k, v in CHEAP_MODELS.items()},
        "capable": {k: v["id"] for k, v in CAPABLE_MODELS.items()},
        "research": {k: v["id"] for k, v in RESEARCH_MODELS.items()},
        "expert": {k: v["id"] for k, v in EXPERT_MODELS.items()},
    }
