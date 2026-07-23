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

import json
import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from http import HTTPStatus

import config
from core.utils.swallowed import log_swallowed_error

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
    tier_p95_ms: int = 0  # Empirical p95 wall-clock for this tier (0 = unknown)


class BudgetUnsatisfiableError(Exception):
    """No tier in the eligible set has a p95 within the caller's slo_budget_ms.

    Carries ``retry_after_ms`` — the smallest tier-p95 in the eligible set,
    so the caller knows the floor it would have to accept to land here. The
    SDK handler converts this into a 503 with a ``Retry-After`` header and
    a structured detail body so callers (e.g. cerid-trading-agent) can fail
    fast and route to direct providers instead of waiting on a slow tier.
    """

    def __init__(self, retry_after_ms: int, eligible_tier: str = "", floor_p95_ms: int = 0) -> None:
        super().__init__(
            f"slo_budget_ms exceeded — smallest eligible tier p95 is {floor_p95_ms} ms"
        )
        self.retry_after_ms = retry_after_ms
        self.eligible_tier = eligible_tier
        self.floor_p95_ms = floor_p95_ms


# ---------------------------------------------------------------------------
# Tier latency profile -- empirical p95 wall-clock by tier
# ---------------------------------------------------------------------------
#
# Drives the ``slo_budget_ms`` filter in ``route()``. Values are observed
# p95s from production traffic (rounded to readability). Update when a
# tier's measured p95 drifts >20% — the smart_router uses these as hard
# eligibility filters, so optimistic estimates here cause false-503s and
# pessimistic estimates cause budget-exceeded responses to leak through.
#
# Ollama is treated as a separate "tier" because it's local — its latency
# profile is independent of the OpenRouter tiers.
TIER_P95_MS: dict[str, int] = {
    "ollama": 5000,                # local CPU inference; varies by model
    "openrouter_free": 12000,      # llama-3.3 via free pool — long tail
    "openrouter_cheap": 10000,     # gpt-4o-mini, gemini-flash
    "openrouter_capable": 25000,   # claude-sonnet, gpt-4o
    "openrouter_research": 45000,  # grok-online (web search adds tail)
    "openrouter_expert": 75000,    # grok-4:online with structured output
    "verification": 10000,         # gpt-4o-mini-class
    "verification_web": 45000,     # grok-4.1-fast:online
    "verification_expert": 75000,  # grok-4:online
}


def _check_budget(tier_key: str, slo_budget_ms: int | None) -> int:
    """Return the tier's p95 if it fits the budget; raise otherwise.

    A ``slo_budget_ms`` of None disables the check (legacy behaviour).
    """
    p95 = TIER_P95_MS.get(tier_key, 0)
    if slo_budget_ms is None:
        return p95
    if p95 > slo_budget_ms:
        raise BudgetUnsatisfiableError(
            retry_after_ms=p95,
            eligible_tier=tier_key,
            floor_p95_ms=p95,
        )
    return p95


# ---------------------------------------------------------------------------
# Model registry -- centralized model definitions
# ---------------------------------------------------------------------------

# Every model ID carries the ``openrouter/`` prefix for historical
# compatibility with Bifrost-era call sites. The OpenRouter-direct path
# strips the prefix via ``_strip_openrouter_prefix`` in ``llm_client``.
# Audit C-7: keeping the prefix avoids a class of silent misroute bugs
# where chat.py stripped a prefix that was never added.

FREE_MODELS = {
    "llama-3.3": "openrouter/meta-llama/llama-3.3-70b-instruct",
}

# E1 CR-027: the FREE tier dispatches this PAID llama-3.3 slug, so its
# RouteDecision must stamp the real paid rate + provider="openrouter_paid" —
# not provider="openrouter_free"/cost=0.0. Matches the paid llama-3.3 per-1K
# rate the CR-013 local->cloud fallback uses (llm_client._FALLBACK_COST_PER_1K).
_LLAMA_33_PAID_COST_PER_1K = 0.00015

# Cheap tier: catalog-refreshed 2026-05-20 against OpenRouter live models.
# Dict keys are stable identifiers used at call sites — only the underlying
# model ID + cost change with each catalog refresh.
# - "gpt-4o-mini" slot: gpt-4o-mini. gpt-5-nano was removed (reasoning model:
#   max_tokens consumed as reasoning budget → 0 output chars).
# - "gemini-flash" slot: gemini-3.1-flash-lite is cheaper + newer + larger
#   context than gemini-2.5-flash.
CHEAP_MODELS: dict[str, dict[str, str | float]] = {
    "gpt-4o-mini": {"id": "openrouter/openai/gpt-4o-mini", "cost": 0.00015},
    "gemini-flash": {"id": "openrouter/google/gemini-3.1-flash-lite", "cost": 0.00025},
}

CAPABLE_MODELS: dict[str, dict[str, str | float]] = {
    "claude-sonnet": {"id": "openrouter/anthropic/claude-sonnet-4.6", "cost": 0.003},
    "gpt-4o": {"id": "openrouter/openai/gpt-4o", "cost": 0.0025},
}

# Research / online-search tier: catalog-refreshed 2026-05-20.
# Previous IDs (grok-4.1-fast:online, grok-4:online) were REMOVED from
# OpenRouter's catalog — source of the 2026-05-20 Sentry 4xx burst.
# Current xAI lineup is grok-4.20 / grok-4.20-multi-agent / grok-4.3.
# `:online` is OpenRouter's generic web-search overlay; works on any
# model ID.
RESEARCH_MODELS: dict[str, dict[str, str | float]] = {
    "grok-online": {"id": "openrouter/x-ai/grok-4.3:online", "cost": 0.00125},
}

EXPERT_MODELS: dict[str, dict[str, str | float]] = {
    "grok-4": {"id": "openrouter/x-ai/grok-4.20:online", "cost": 0.00125},
}

# ---------------------------------------------------------------------------
# Routing-tiers overlay (weekly catalog refresh of the tier ids above)
# ---------------------------------------------------------------------------
# The weekly model_auto_update job resolves each tier id through
# ``model_catalog.resolve_latest`` against the live OpenRouter catalog and
# persists a ``{original_id: resolved_id}`` map to the path in
# ``config.ROUTING_TIERS_OVERLAY_PATH``. We read it lazily here (mtime-cached)
# so the tier tables stay current without editing source. Missing / unreadable
# / malformed overlay → ``{}`` and every id resolves to itself (fail soft).
# ``core`` reads only the path string from ``config`` — never imports ``app``.

_tier_overlay: dict[str, str] = {}
_tier_overlay_mtime: float = -1.0


def _load_tier_overlay() -> dict[str, str]:
    """Return the cached overlay map, reloading when the file's mtime changes."""
    global _tier_overlay, _tier_overlay_mtime
    path = getattr(config, "ROUTING_TIERS_OVERLAY_PATH", "")
    if not path:
        return {}
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        # No overlay yet (or unreadable) — fail soft to identity resolution.
        if _tier_overlay:
            _tier_overlay = {}
            _tier_overlay_mtime = -1.0
        return {}
    if mtime != _tier_overlay_mtime:
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            _tier_overlay = {
                str(k): str(v)
                for k, v in raw.items()
                if isinstance(k, str) and isinstance(v, str)
            } if isinstance(raw, dict) else {}
        except (OSError, ValueError) as exc:  # noqa: BLE001 — resilient overlay read

            log_swallowed_error("smart_router.tier_overlay", exc)
            _tier_overlay = {}
        _tier_overlay_mtime = mtime
    return _tier_overlay


def _resolve_tier_id(raw_id: str) -> str:
    """Map a tier-table id through the overlay; identity when absent."""
    return _load_tier_overlay().get(raw_id, raw_id)


def tier_source_ids() -> list[str]:
    """Distinct *source* (pre-overlay) ids across every tier table.

    The weekly auto-update job feeds these through
    ``model_catalog.resolve_latest`` to build the overlay, so this is the
    authoritative list of upgrade-eligible tier ids. Order-stable + de-duped.
    """
    ids: list[str] = list(FREE_MODELS.values())
    for table in (CHEAP_MODELS, CAPABLE_MODELS, RESEARCH_MODELS, EXPERT_MODELS):
        ids.extend(str(entry["id"]) for entry in table.values())
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out

# ---------------------------------------------------------------------------
# Ollama availability cache
# ---------------------------------------------------------------------------

_ollama_available: bool | None = None
_ollama_checked_at: float = 0
_OLLAMA_CHECK_INTERVAL = 60  # seconds
_ollama_models: list[str] = []


async def _check_ollama() -> bool:
    """Check if the local Ollama-protocol backend is reachable with at least one model.

    Supports both stock Ollama and Quenchforge — they share the wire format,
    so this function selects the URL based on ``INTERNAL_LLM_PROVIDER``:

    - ``quenchforge`` → ``QUENCHFORGE_URL``, opt-in by virtue of being selected.
    - anything else → ``OLLAMA_URL``, gated on ``OLLAMA_ENABLED=true``.

    Cached for 60 seconds. The legacy name is preserved so external callers in
    ``app/routers/providers.py`` keep working.
    """
    global _ollama_available, _ollama_checked_at, _ollama_models

    now = time.monotonic()
    if _ollama_available is not None and (now - _ollama_checked_at) < _OLLAMA_CHECK_INTERVAL:
        return _ollama_available

    provider = os.getenv("INTERNAL_LLM_PROVIDER", "openrouter").lower()
    if provider == "quenchforge":
        ollama_url = os.getenv("QUENCHFORGE_URL") or os.getenv(
            "OLLAMA_URL", "http://localhost:11434"
        )
    else:
        ollama_enabled = os.getenv("OLLAMA_ENABLED", "false").lower() == "true"
        if not ollama_enabled:
            _ollama_available = False
            _ollama_checked_at = now
            return False
        ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")

    try:
        from core.utils.internal_llm import _get_ollama_client
        client = await _get_ollama_client()
        resp = await client.get(f"{ollama_url}/api/tags")
        if resp.status_code == HTTPStatus.OK:
            data = resp.json()
            _ollama_models = [m.get("name", "") for m in data.get("models", [])]
            _ollama_available = len(_ollama_models) > 0
        else:
            _ollama_available = False
    except Exception as exc:
        log_swallowed_error('core.routing.smart_router', exc)
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
        log_swallowed_error("core.routing.smart_router.ollama_classify", e)
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
    slo_budget_ms: int | None = None,
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

    ``slo_budget_ms`` (optional) is a wall-clock deadline. The router
    filters tiers by their ``TIER_P95_MS`` profile; any tier with
    ``p95 > budget`` is ineligible. If no tier fits, raises
    ``BudgetUnsatisfiableError`` rather than silently downgrading —
    callers (e.g. cerid-trading-agent) need a fast 503 + Retry-After
    so they can route to direct providers instead of waiting.
    """

    # 1. Task-specific models -- always take precedence
    if task_type == TaskType.VERIFICATION:
        p95 = _check_budget("verification", slo_budget_ms)
        model = config.VERIFICATION_MODEL
        if model.startswith("openrouter/"):
            model = model[len("openrouter/"):]
        return RouteDecision(
            model=model,
            provider="openrouter_paid",
            reason="dedicated verification model",
            estimated_cost_per_1k=0.00015,
            tier_p95_ms=p95,
        )

    if task_type == TaskType.VERIFICATION_WEB:
        p95 = _check_budget("verification_web", slo_budget_ms)
        # Catalog-refreshed 2026-05-20: grok-4.1-fast removed from
        # OpenRouter; grok-4.3 is the current cheap-search-tier xAI model.
        model = config.VERIFICATION_CURRENT_EVENT_MODEL
        if model.startswith("openrouter/"):
            model = model[len("openrouter/"):]
        return RouteDecision(
            model=model,
            provider="openrouter_paid",
            reason="web-search verification",
            estimated_cost_per_1k=0.00125,
            tier_p95_ms=p95,
        )

    if task_type == TaskType.VERIFICATION_EXPERT:
        p95 = _check_budget("verification_expert", slo_budget_ms)
        # Catalog-refreshed 2026-05-20: grok-4 / grok-4:online removed
        # from OpenRouter; grok-4.20 is the current expert-tier xAI model.
        # The expert branch wants the web-search (:online) variant — see the
        # VERIFICATION_EXPERT_WEB_MODEL note in config/settings.py.
        model = config.VERIFICATION_EXPERT_WEB_MODEL
        if model.startswith("openrouter/"):
            model = model[len("openrouter/"):]
        return RouteDecision(
            model=model,
            provider="openrouter_paid",
            reason="expert verification",
            estimated_cost_per_1k=0.00125,
            tier_p95_ms=p95,
        )

    # 2. Internal operations -- try Ollama first, then free models
    if task_type in (TaskType.INTERNAL, TaskType.CLASSIFICATION):
        ollama_ok = await _check_ollama()
        if ollama_ok and _ollama_models:
            # Ollama-first when reachable. Even with a budget filter,
            # Ollama's local p95 (~5 s) clears most reasonable budgets.
            try:
                p95 = _check_budget("ollama", slo_budget_ms)
            except BudgetUnsatisfiableError:
                # Budget too tight even for local — fall through to the
                # free-tier check; if that fails too, the caller gets
                # the 503 from there.
                pass
            else:
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
                    tier_p95_ms=p95,
                )

        # No Ollama -- use free OpenRouter model
        p95 = _check_budget("openrouter_free", slo_budget_ms)
        return RouteDecision(
            model=_resolve_tier_id(FREE_MODELS["llama-3.3"]),
            provider="openrouter_paid",
            reason="cheap tier — llama-3.3 (no local backend)",
            estimated_cost_per_1k=_LLAMA_33_PAID_COST_PER_1K,
            tier_p95_ms=p95,
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
        p95 = _check_budget("openrouter_research", slo_budget_ms)
        return RouteDecision(
            model=_resolve_tier_id(str(RESEARCH_MODELS["grok-online"]["id"])),
            provider="openrouter_paid",
            reason=(
                "research query — cheaper web model (high cost sensitivity)"
                if cs == "high" else "research query — real-time data needed"
            ),
            estimated_cost_per_1k=0.0002,
            tier_p95_ms=p95,
        )

    if complexity == Complexity.SIMPLE:
        # ENABLE_MODEL_CASCADE: when on, prefer the local backend for
        # SIMPLE chat answers before falling through to the free OpenRouter
        # tier. Off by default — historic guidance was "Ollama is never
        # used for chat answers (quality too low for user-facing)", which
        # we're flipping selectively for operators on capable hardware
        # (notably the Mac Pro + Vega II Quenchforge target).
        if getattr(config, "ENABLE_MODEL_CASCADE", False):
            ollama_ok = await _check_ollama()
            if ollama_ok and _ollama_models:
                try:
                    p95 = _check_budget("ollama", slo_budget_ms)
                except BudgetUnsatisfiableError:
                    pass
                else:
                    preferred = ["llama3.2", "phi3", "mistral", "gemma2"]
                    model = _ollama_models[0]
                    for pref in preferred:
                        matching = [m for m in _ollama_models if pref in m]
                        if matching:
                            model = matching[0]
                            break
                    return RouteDecision(
                        model=model,
                        provider="ollama",
                        reason="simple query — local cascade (ENABLE_MODEL_CASCADE)",
                        estimated_cost_per_1k=0.0,
                        tier_p95_ms=p95,
                    )
        p95 = _check_budget("openrouter_free", slo_budget_ms)
        return RouteDecision(
            model=_resolve_tier_id(FREE_MODELS["llama-3.3"]),
            provider="openrouter_paid",
            reason="simple query — cheap tier (llama-3.3) sufficient",
            estimated_cost_per_1k=_LLAMA_33_PAID_COST_PER_1K,
            tier_p95_ms=p95,
        )

    if complexity == Complexity.COMPLEX:
        if cs == "high":
            # Complex + high: still capable-tier but cheapest (gemini-flash).
            # gpt-4o-mini is too weak for multi-step reasoning.
            p95 = _check_budget("openrouter_cheap", slo_budget_ms)
            return RouteDecision(
                model=_resolve_tier_id(str(CHEAP_MODELS["gemini-flash"]["id"])),
                provider="openrouter_paid",
                reason="complex query — cheapest capable model (high cost sensitivity)",
                estimated_cost_per_1k=0.0003,
                tier_p95_ms=p95,
            )
        # E1 CR-029: COMPLEX + low escalates to the EXPERT tier when the operator
        # opts in via ENABLE_EXPERT_ESCALATION. Off by default so "low cost
        # sensitivity" doesn't silently 10x spend — but the tier is now reachable
        # (it was maintained/refreshed/advertised with no branch that could select
        # it). Only 'low' escalates; 'medium' stays CAPABLE.
        if cs == "low" and getattr(config, "ENABLE_EXPERT_ESCALATION", False):
            p95 = _check_budget("openrouter_expert", slo_budget_ms)
            expert = EXPERT_MODELS["grok-4"]
            return RouteDecision(
                model=_resolve_tier_id(str(expert["id"])),
                provider="openrouter_paid",
                reason="complex query — expert tier (low cost sensitivity, escalation enabled)",
                estimated_cost_per_1k=float(expert["cost"]),
                tier_p95_ms=p95,
            )
        # medium or low → CAPABLE.  Escalation to EXPERT requires
        # ENABLE_EXPERT_ESCALATION (above) so low sensitivity doesn't 10x spend.
        p95 = _check_budget("openrouter_capable", slo_budget_ms)
        reason = (
            "complex query — best model (low cost sensitivity)"
            if cs == "low" else "complex query — strong reasoning needed"
        )
        return RouteDecision(
            model=_resolve_tier_id(str(CAPABLE_MODELS["claude-sonnet"]["id"])),
            provider="openrouter_paid",
            reason=reason,
            estimated_cost_per_1k=0.003,
            tier_p95_ms=p95,
        )

    # Moderate complexity
    if cs == "high":
        p95 = _check_budget("openrouter_free", slo_budget_ms)
        return RouteDecision(
            model=_resolve_tier_id(FREE_MODELS["llama-3.3"]),
            provider="openrouter_paid",
            reason="moderate query — cheap llama-3.3 (high cost sensitivity)",
            estimated_cost_per_1k=_LLAMA_33_PAID_COST_PER_1K,
            tier_p95_ms=p95,
        )
    if cs == "low":
        # Task 17 decision table: MODERATE + low → CAPABLE (was CHEAP).
        p95 = _check_budget("openrouter_capable", slo_budget_ms)
        return RouteDecision(
            model=_resolve_tier_id(str(CAPABLE_MODELS["claude-sonnet"]["id"])),
            provider="openrouter_paid",
            reason="moderate query — capable model (low cost sensitivity)",
            estimated_cost_per_1k=0.003,
            tier_p95_ms=p95,
        )
    # Medium: cheap paid model balances quality and cost
    p95 = _check_budget("openrouter_cheap", slo_budget_ms)
    return RouteDecision(
        model=_resolve_tier_id(str(CHEAP_MODELS["gpt-4o-mini"]["id"])),
        provider="openrouter_paid",
        reason="moderate query — cost-effective balance",
        estimated_cost_per_1k=0.00015,
        tier_p95_ms=p95,
    )


# ---------------------------------------------------------------------------
# Registry export (for Settings UI)
# ---------------------------------------------------------------------------


def get_model_registry() -> dict:
    """Return the full model registry for the Settings UI.

    Tier ids are resolved through the routing-tiers overlay so the UI reflects
    the same (catalog-refreshed) ids ``route()`` actually dispatches to.
    """
    return {
        "free": {k: _resolve_tier_id(v) for k, v in FREE_MODELS.items()},
        "cheap": {k: _resolve_tier_id(str(v["id"])) for k, v in CHEAP_MODELS.items()},
        "capable": {k: _resolve_tier_id(str(v["id"])) for k, v in CAPABLE_MODELS.items()},
        "research": {k: _resolve_tier_id(str(v["id"])) for k, v in RESEARCH_MODELS.items()},
        "expert": {k: _resolve_tier_id(str(v["id"])) for k, v in EXPERT_MODELS.items()},
    }
