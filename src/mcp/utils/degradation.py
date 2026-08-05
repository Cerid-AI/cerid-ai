# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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

__all__ = ["DegradationTier", "DegradationManager"]

# Breaker names grouped by logical service.
_CHROMADB_BREAKERS = ("chromadb", "bifrost-rerank")
_NEO4J_BREAKERS = ("neo4j",)
# The active LLM breakers: cloud (openrouter) + the two local chat backends
# (ollama / quenchforge-chat, per internal_llm._call_ollama). The retired
# bifrost-* breakers were dropped — they never open, so an all-open "LLM down"
# check that included them could never fire (E1 CR-052).
_LLM_BREAKERS = ("openrouter", "ollama", "quenchforge-chat")


class DegradationTier(Enum):
    FULL = "full"
    LITE = "lite"
    DIRECT = "direct"
    CACHED = "cached"
    OFFLINE = "offline"


def _is_breaker_open(name: str) -> bool:
    """Check whether a named circuit breaker is in the OPEN state."""
    try:
        from core.utils.circuit_breaker import CircuitState, get_breaker
        return get_breaker(name).state == CircuitState.OPEN
    except Exception:  # noqa: BLE001
        return False  # assume healthy if we can't check


def _configured_llm_breakers() -> tuple[str, ...]:
    """Breakers that count for the CACHED tier on *this* install (E1 R4 / CR-052).

    ``get_breaker()`` auto-creates CLOSED breakers for never-dispatched names,
    so requiring *all* of ``_LLM_BREAKERS`` open made CACHED unreachable on a
    cloud-only default (ollama + quenchforge-chat stay CLOSED forever). Only
    breakers for providers that are enabled / configured count.
    """
    import os

    provider = os.getenv("INTERNAL_LLM_PROVIDER", "openrouter").strip().lower()
    has_or = bool(os.getenv("OPENROUTER_API_KEY", "").strip())
    ollama_on = os.getenv("OLLAMA_ENABLED", "").strip().lower() in ("true", "1", "yes")
    qf_url = bool(os.getenv("QUENCHFORGE_URL", "").strip())

    names: list[str] = []
    if provider == "openrouter" or has_or:
        names.append("openrouter")
    if provider == "ollama" or ollama_on:
        names.append("ollama")
    if provider == "quenchforge" or qf_url:
        names.append("quenchforge-chat")

    # Default install / unknown provider: openrouter is the cloud plane.
    return tuple(names) if names else ("openrouter",)


def _all_open(names: tuple[str, ...]) -> bool:
    if not names:
        return False
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
        redis_is_down = _redis_down()
        chromadb_open = _any_open(_CHROMADB_BREAKERS)
        neo4j_open = _any_open(_NEO4J_BREAKERS)

        # OFFLINE only when Redis is down AND retrieval backends are also down.
        # Redis alone going down should degrade to DIRECT (skip caching, still
        # do retrieval) rather than blocking everything.
        if redis_is_down and (chromadb_open or neo4j_open):
            return DegradationTier.OFFLINE
        # E1 R4: only configured LLM breakers must be open for CACHED.
        if _all_open(_configured_llm_breakers()):
            return DegradationTier.CACHED
        if redis_is_down:
            # Redis down but ChromaDB + Neo4j healthy: skip caching, still retrieve
            return DegradationTier.DIRECT
        if chromadb_open and neo4j_open:
            return DegradationTier.DIRECT
        if chromadb_open:
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
            "llm": "down" if _all_open(_configured_llm_breakers()) else "up",
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
