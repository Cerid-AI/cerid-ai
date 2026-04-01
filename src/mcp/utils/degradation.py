# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Multi-tier graceful degradation manager.

Tracks system capability based on service health and circuit breaker states.
When services go down, the system automatically degrades to the next tier
instead of failing entirely.

Tiers (highest → lowest):
  FULL    — All features available (RAG, reranking, graph, verification)
  LITE    — Reduced retrieval (top-k only, skip reranking/decomposition)
  DIRECT  — No retrieval; LLM parametric knowledge only
  CACHED  — Semantic cache hits only; no new LLM calls
  OFFLINE — Static error responses only

Dependencies: utils/circuit_breaker.py, deps.py (Redis check)
Error types: none (this module never raises — it only reports status)
"""

from __future__ import annotations

from enum import Enum

__all__ = ["DegradationTier", "DegradationManager", "degradation"]

# Breaker names grouped by logical service.
_CHROMADB_BREAKERS = ("bifrost-rerank",)
_NEO4J_BREAKERS = ("neo4j",)
_LLM_BREAKERS = ("bifrost-verify", "bifrost-claims", "openrouter", "ollama")


class DegradationTier(Enum):
    FULL = "full"
    LITE = "lite"
    DIRECT = "direct"
    CACHED = "cached"
    OFFLINE = "offline"


def _is_breaker_open(name: str) -> bool:
    """Check whether a named circuit breaker is in the OPEN state."""
    try:
        from utils.circuit_breaker import CircuitState, get_breaker
        return get_breaker(name).state == CircuitState.OPEN
    except Exception:  # noqa: BLE001
        return False  # assume healthy if we can't check


def _all_open(names: tuple[str, ...]) -> bool:
    return all(_is_breaker_open(n) for n in names)


def _any_open(names: tuple[str, ...]) -> bool:
    return any(_is_breaker_open(n) for n in names)


def _redis_down() -> bool:
    """Best-effort Redis reachability check via deps."""
    try:
        from deps import get_redis
        get_redis().ping()
        return False
    except Exception:  # noqa: BLE001
        return True


class DegradationManager:
    """Determines the current system capability tier."""

    def current_tier(self) -> DegradationTier:
        if _redis_down():
            return DegradationTier.OFFLINE
        if _all_open(_LLM_BREAKERS):
            return DegradationTier.CACHED
        if _any_open(_CHROMADB_BREAKERS) and _any_open(_NEO4J_BREAKERS):
            return DegradationTier.DIRECT
        if _any_open(_CHROMADB_BREAKERS):
            return DegradationTier.LITE
        return DegradationTier.FULL

    def can_retrieve(self) -> bool:
        """True if tier is FULL or LITE."""
        return self.current_tier() in (DegradationTier.FULL, DegradationTier.LITE)

    def can_verify(self) -> bool:
        """True if tier is FULL, LITE, or DIRECT."""
        return self.current_tier() in (
            DegradationTier.FULL, DegradationTier.LITE, DegradationTier.DIRECT,
        )

    def can_generate(self) -> bool:
        """True if tier is not OFFLINE."""
        return self.current_tier() != DegradationTier.OFFLINE

    def status_report(self) -> dict:
        """Return tier, per-service status, and list of degraded features."""
        tier = self.current_tier()
        svc = {
            "chromadb": "down" if _any_open(_CHROMADB_BREAKERS) else "up",
            "neo4j": "down" if _any_open(_NEO4J_BREAKERS) else "up",
            "llm": "down" if _all_open(_LLM_BREAKERS) else "up",
            "redis": "down" if _redis_down() else "up",
        }
        degraded: list[str] = []
        if tier is not DegradationTier.FULL:
            if svc["chromadb"] == "down":
                degraded.append("reranking")
            if svc["neo4j"] == "down":
                degraded.append("graph_retrieval")
            if svc["llm"] == "down":
                degraded.extend(["generation", "verification"])
            if svc["redis"] == "down":
                degraded.extend(["cache", "rate_limiting", "audit_log"])
        return {"tier": tier.value, "services": svc, "degraded_features": degraded}


degradation = DegradationManager()
