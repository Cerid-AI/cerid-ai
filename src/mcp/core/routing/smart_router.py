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

# Every model ID carries the ``openrouter/`` prefix so the Bifrost path keeps
# working if ``USE_BIFROST=true`` is ever flipped on — the OpenRouter-direct
# path strips the prefix via ``_strip_openrouter_prefix`` in ``llm_client``.
# Audit C-7: previously the registry stored bare IDs and relied on chat.py
# stripping a prefix that was never added, silently breaking Bifrost.

FREE_MODELS = {
    "llama-3.3": "openrouter/meta-llama/llama-3.3-70b-instruct",
}

CHEAP_MODELS: dict[str, dict[str, str | float]] = {
    "gpt-4o-mini": {"id": "openrouter/openai/gpt-4o-mini", "cost": 0.00015},
    "gemini-flash": {"id": "openrouter/google/gemini-2.5-flash", "cost": 0.0003},
}

CAPABLE_MODELS: dict[str, dict[str, str | float]] = {
    "claude-sonnet": {"id": "openrouter/anthropic/claude-sonnet-4.6", "cost": 0.003},
    "gpt-4o": {"id": "openrouter/openai/gpt-4o", "cost": 0.0025},
}

RESEARCH_MODELS: dict[str, dict[str, str | float]] = {
    "grok-online": {"id": "openrouter/x-ai/grok-4.1-fast:online", "cost": 0.0002},
}

EXPERT_MODELS: dict[str, dict[str, str | float]] = {
    "grok-4": {"id": "openrouter/x-ai/grok-4:online", "cost": 0.003},
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
        ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        from core.utils.internal_llm import _get_ollama_client
        client = await _get_ollama_client()
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
# Complexity classification (scored heuristic -- no LLM call)
# ---------------------------------------------------------------------------
#
# Audit C-5/C-6: the old classifier used first-keyword-match, so any query
# containing {code, function, class, analyze, considering} short-circuited
# to COMPLEX → Claude Sonnet — wasting tokens on trivial queries and leaving
# the free-Llama path nearly dead.  The scored approach below lets a single
# weak signal fall through; a short "analyze this" no longer hijacks routing.

# Research indicators -- needs real-time data.  Short-circuits complexity
# scoring entirely because research always routes through Grok-online
# regardless of how "complex" the query text looks.
_RESEARCH_KEYWORDS: tuple[str, ...] = (
    "latest", "recent", "current", "today", "news",
    "2025", "2026", "trending", "stock price", "weather",
    "score", "election",
)

# Short factual-lookup patterns -- when a short query clearly asks for a
# definition or fact, skip the scoring path.
_SIMPLE_PATTERNS: tuple[str, ...] = (
    "what is", "who is", "how many", "capital of",
    "define ", "what does", "when was", "where is", "how old",
)

# Weighted signals for COMPLEX classification.  Chosen so a single weak
# signal cannot push a short query into COMPLEX:
#   * COMPLEX requires total >= 3.0 (usually at least one strong + one weak)
#   * MODERATE requires complex >= 1.5 OR moderate >= 1.0
#   * SIMPLE is the default
_COMPLEX_SIGNALS: dict[str, dict] = {
    "code": {
        "keywords": (
            "def ", "class ", "function", "import ", "```",
            "refactor", "implement", "debug",
        ),
        "weight": 2.0,
    },
    "multi_aspect": {
        "keywords": (
            "considering", "tradeoff", "trade-off", "compare",
            "analyze", "analyse", "evaluate", "critique",
        ),
        "weight": 1.0,
    },
    "length": {"threshold": 200, "weight": 1.5},  # chars
    "domain_depth": {
        "keywords": (
            "architecture", "architectural", "algorithm", "distributed",
            "consensus", "thread-safe", "race condition", "scalability",
            "concurrency",
        ),
        "weight": 1.5,
    },
}

_MODERATE_SIGNALS: dict[str, dict] = {
    "summarize": {
        "keywords": ("summarize", "summary", "overview", "explain"),
        "weight": 1.0,
    },
    "length": {"threshold": 80, "weight": 0.5},
}


def classify_task_type(query: str) -> Complexity:
    """Score-based task-complexity classifier (replaces keyword-first-match).

    Thresholds:
      * COMPLEX:  ``complex_score`` >= 3.0
      * MODERATE: ``complex_score`` >= 1.5 or ``moderate_score`` >= 1.0
      * SIMPLE:   else

    Research keywords (``latest``, ``news``, ``2026`` …) short-circuit to
    :attr:`Complexity.RESEARCH`; short factual patterns (``what is …``)
    short-circuit to :attr:`Complexity.SIMPLE` so we don't penalise bare
    definitional lookups.
    """
    q = query.lower().strip()
    word_count = len(q.split())

    # Research short-circuit
    if any(kw in q for kw in _RESEARCH_KEYWORDS):
        return Complexity.RESEARCH

    # Factual short-circuit
    if word_count <= 15 and any(
        q.startswith(p) or p in q for p in _SIMPLE_PATTERNS
    ):
        return Complexity.SIMPLE

    complex_score = 0.0
    moderate_score = 0.0

    for spec in _COMPLEX_SIGNALS.values():
        if "keywords" in spec and any(kw in q for kw in spec["keywords"]):
            complex_score += spec["weight"]
        if "threshold" in spec and len(query) >= spec["threshold"]:
            complex_score += spec["weight"]

    for spec in _MODERATE_SIGNALS.values():
        if "keywords" in spec and any(kw in q for kw in spec["keywords"]):
            moderate_score += spec["weight"]
        if "threshold" in spec and len(query) >= spec["threshold"]:
            moderate_score += spec["weight"]

    # Very long queries (100+ words) are almost always complex regardless
    # of keywords — preserves the old ``word_count > 100`` escape hatch.
    if word_count > 100:
        complex_score += 2.0

    if complex_score >= 3.0:
        return Complexity.COMPLEX
    if complex_score >= 1.5 or moderate_score >= 1.0:
        return Complexity.MODERATE
    return Complexity.SIMPLE


# Backward-compat alias: the bridge module and the existing test suite import
# ``_classify_complexity``.  The new ``classify_task_type`` is the public name
# (per Task 17 spec); ``_classify_complexity`` stays as a thin alias so we
# don't break call-sites on an internal refactor.
_classify_complexity = classify_task_type


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

        ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        # Pick smallest available model for classification (fastest)
        small_models = ["phi3", "gemma2", "llama3.2", "mistral"]
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

        from core.utils.internal_llm import _get_ollama_client
        client = await _get_ollama_client()
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
    total_chars: int = 0,
    kb_injection_count: int = 0,
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
            preferred = ["llama3.2", "phi3", "mistral", "gemma2"]
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

    # KB-injection MODERATE tilt (Task 17): 3+ injected documents indicate a
    # retrieval-augmented question.  Bump SIMPLE→MODERATE so the free model
    # doesn't try to reason over a large context window.  Never pushes to
    # COMPLEX on its own — that's what the classifier is for.
    if kb_injection_count >= 3 and complexity == Complexity.SIMPLE:
        complexity = Complexity.MODERATE
        logger.info("Escalated SIMPLE→MODERATE: %d KB injections", kb_injection_count)

    # Total-context escalations: very large contexts need bigger-window models.
    if total_chars > 40_000:
        complexity = Complexity.COMPLEX
        logger.info("Escalated to COMPLEX: %d total chars (large context)", total_chars)
    elif total_chars > 12_000 and complexity == Complexity.SIMPLE:
        complexity = Complexity.MODERATE
        logger.info("Escalated SIMPLE→MODERATE: %d total chars", total_chars)

    cs = cost_sensitivity.lower()

    # ---- Decision table (Task 17) -----------------------------------------
    #   SIMPLE   | any       → FREE
    #   MODERATE | high      → FREE (llama)
    #            | medium    → CHEAP (gpt-4o-mini)
    #            | low       → CAPABLE (claude-sonnet)
    #   COMPLEX  | high      → CHEAP (gemini-flash)  # override — user asked
    #            | medium    → CAPABLE (claude-sonnet)
    #            | low       → CAPABLE (claude-sonnet; EXPERT behind a flag)
    #   RESEARCH | any       → RESEARCH (grok-online)
    # -----------------------------------------------------------------------

    if complexity == Complexity.RESEARCH:
        return RouteDecision(
            model=str(RESEARCH_MODELS["grok-online"]["id"]),
            provider="openrouter_paid",
            reason=(
                "research query — cheaper web model (high cost sensitivity)"
                if cs == "high" else "research query — real-time data needed"
            ),
            estimated_cost_per_1k=0.0002,
        )

    if complexity == Complexity.SIMPLE:
        return RouteDecision(
            model=FREE_MODELS["llama-3.3"],
            provider="openrouter_free",
            reason="simple query — free tier sufficient",
            estimated_cost_per_1k=0.0,
        )

    if complexity == Complexity.COMPLEX:
        if cs == "high":
            # Complex + high: still capable-tier but cheapest (gemini-flash).
            # gpt-4o-mini is too weak for multi-step reasoning.
            return RouteDecision(
                model=str(CHEAP_MODELS["gemini-flash"]["id"]),
                provider="openrouter_paid",
                reason="complex query — cheapest capable model (high cost sensitivity)",
                estimated_cost_per_1k=0.0003,
            )
        # medium or low → CAPABLE.  Escalation to EXPERT is kept behind a
        # separate flag so "low cost sensitivity" doesn't silently 10x spend.
        reason = (
            "complex query — best model (low cost sensitivity)"
            if cs == "low" else "complex query — strong reasoning needed"
        )
        return RouteDecision(
            model=str(CAPABLE_MODELS["claude-sonnet"]["id"]),
            provider="openrouter_paid",
            reason=reason,
            estimated_cost_per_1k=0.003,
        )

    # Moderate complexity
    if cs == "high":
        return RouteDecision(
            model=FREE_MODELS["llama-3.3"],
            provider="openrouter_free",
            reason="moderate query — free model (high cost sensitivity)",
            estimated_cost_per_1k=0.0,
        )
    if cs == "low":
        # Task 17 decision table: MODERATE + low → CAPABLE (was CHEAP).
        return RouteDecision(
            model=str(CAPABLE_MODELS["claude-sonnet"]["id"]),
            provider="openrouter_paid",
            reason="moderate query — capable model (low cost sensitivity)",
            estimated_cost_per_1k=0.003,
        )
    # Medium: cheap paid model balances quality and cost
    return RouteDecision(
        model=str(CHEAP_MODELS["gpt-4o-mini"]["id"]),
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

    from core.routing.model_providers import (
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
    from core.routing.model_providers import (
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
