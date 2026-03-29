# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Feature flags, toggles, and plugin configuration.

This is the canonical location for all tier-gating primitives:
- ``is_feature_enabled()``  — runtime check
- ``require_feature()``     — async endpoint decorator (raises 403)
- ``check_feature()``       — sync helper (raises CeridError)
"""
from __future__ import annotations

import asyncio as _asyncio
import functools as _functools
import logging as _logging
import os
from collections.abc import Callable as _Callable
from typing import Any as _Any

# ---------------------------------------------------------------------------
# Plugin System & Feature Tiers
# ---------------------------------------------------------------------------
# Feature tier: "community" (OSS), "pro" (commercial plugins), or "enterprise"
FEATURE_TIER = os.getenv("CERID_TIER", "community")

# Hierarchical tier levels: enterprise ⊃ pro ⊃ community
_TIER_LEVELS = {"community": 0, "pro": 1, "enterprise": 2}

# Plugin directory (relative to app root or absolute path)
PLUGIN_DIR = os.getenv("CERID_PLUGIN_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugins"))

# Comma-separated list of plugin names to load (empty = auto-discover all)
_enabled_plugins_raw = os.getenv("CERID_ENABLED_PLUGINS", "")
ENABLED_PLUGINS = [p.strip() for p in _enabled_plugins_raw.split(",") if p.strip()] if _enabled_plugins_raw else []

# ---------------------------------------------------------------------------
# Multi-User Auth
# ---------------------------------------------------------------------------
CERID_MULTI_USER = os.getenv("CERID_MULTI_USER", "false").lower() == "true"
CERID_JWT_SECRET = os.getenv("CERID_JWT_SECRET", "")
CERID_JWT_ACCESS_TTL = int(os.getenv("CERID_JWT_ACCESS_TTL", "900"))   # 15 min
CERID_JWT_REFRESH_TTL = int(os.getenv("CERID_JWT_REFRESH_TTL", "604800"))  # 7 days
DEFAULT_TENANT_ID = os.getenv("CERID_DEFAULT_TENANT", "default")

# Feature flags: controls what's available per tier
# Community features are always enabled; pro features require CERID_TIER=pro or enterprise
FEATURE_FLAGS = {
    # Pro-only features (requires pro or enterprise tier)
    "ocr_parsing":         _TIER_LEVELS.get(FEATURE_TIER, 0) >= _TIER_LEVELS["pro"],
    "audio_transcription": _TIER_LEVELS.get(FEATURE_TIER, 0) >= _TIER_LEVELS["pro"],
    "image_understanding": _TIER_LEVELS.get(FEATURE_TIER, 0) >= _TIER_LEVELS["pro"],
    "semantic_dedup":      _TIER_LEVELS.get(FEATURE_TIER, 0) >= _TIER_LEVELS["pro"],
    "advanced_analytics":  _TIER_LEVELS.get(FEATURE_TIER, 0) >= _TIER_LEVELS["pro"],
    "metamorphic_verification": _TIER_LEVELS.get(FEATURE_TIER, 0) >= _TIER_LEVELS["pro"],
    # Enterprise-only features
    "multi_user":          CERID_MULTI_USER or _TIER_LEVELS.get(FEATURE_TIER, 0) >= _TIER_LEVELS["enterprise"],
    "sso_saml":            _TIER_LEVELS.get(FEATURE_TIER, 0) >= _TIER_LEVELS["enterprise"],  # Scaffolded — no implementation yet. Enterprise roadmap item.
    "audit_logging":       _TIER_LEVELS.get(FEATURE_TIER, 0) >= _TIER_LEVELS["enterprise"],
    "priority_support":    _TIER_LEVELS.get(FEATURE_TIER, 0) >= _TIER_LEVELS["enterprise"],
    # Pro-only: custom smart RAG mode with per-source weights
    "custom_smart_rag": _TIER_LEVELS.get(FEATURE_TIER, 0) >= _TIER_LEVELS["pro"],
    # Community features (always enabled)
    "hierarchical_taxonomy": True,
    "file_upload_gui":       True,
    "encryption_at_rest":    True,
    "truth_audit":           True,
    "live_metrics":          True,
}

# ---------------------------------------------------------------------------
# Feature toggles
# ---------------------------------------------------------------------------
ENABLE_HALLUCINATION_CHECK = os.getenv("ENABLE_HALLUCINATION_CHECK", "true").lower() == "true"
ENABLE_FEEDBACK_LOOP = os.getenv("ENABLE_FEEDBACK_LOOP", "false").lower() == "true"
ENABLE_MEMORY_EXTRACTION = os.getenv("ENABLE_MEMORY_EXTRACTION", "true").lower() == "true"
ENABLE_ENCRYPTION = os.getenv("ENABLE_ENCRYPTION", "false").lower() == "true"
ENABLE_AUTO_INJECT = os.getenv("ENABLE_AUTO_INJECT", "false").lower() == "true"
ENABLE_SELF_RAG = os.getenv("ENABLE_SELF_RAG", "true").lower() == "true"
ENABLE_CONTEXTUAL_CHUNKS = os.getenv("ENABLE_CONTEXTUAL_CHUNKS", "false").lower() == "true"
ENABLE_MEMORY_RECALL = os.getenv("ENABLE_MEMORY_RECALL", "true").lower() == "true"
# CERID_ENCRYPTION_KEY is read directly from env by utils/encryption.py
# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# ---------------------------------------------------------------------------
# Advanced RAG Pipeline (Phase 34)
# ---------------------------------------------------------------------------
ENABLE_ADAPTIVE_RETRIEVAL = os.getenv("ENABLE_ADAPTIVE_RETRIEVAL", "true").lower() == "true"
ADAPTIVE_RETRIEVAL_LIGHT_TOP_K = int(os.getenv("ADAPTIVE_RETRIEVAL_LIGHT_TOP_K", "3"))

ENABLE_QUERY_DECOMPOSITION = os.getenv("ENABLE_QUERY_DECOMPOSITION", "true").lower() == "true"
QUERY_DECOMPOSITION_MAX_SUBQUERIES = int(os.getenv("QUERY_DECOMPOSITION_MAX_SUBQUERIES", "4"))

ENABLE_MMR_DIVERSITY = os.getenv("ENABLE_MMR_DIVERSITY", "true").lower() == "true"
MMR_LAMBDA = float(os.getenv("MMR_LAMBDA", "0.7"))

ENABLE_INTELLIGENT_ASSEMBLY = os.getenv("ENABLE_INTELLIGENT_ASSEMBLY", "true").lower() == "true"

ENABLE_LATE_INTERACTION = os.getenv("ENABLE_LATE_INTERACTION", "false").lower() == "true"
LATE_INTERACTION_TOP_N = int(os.getenv("LATE_INTERACTION_TOP_N", "8"))
LATE_INTERACTION_BLEND_WEIGHT = float(os.getenv("LATE_INTERACTION_BLEND_WEIGHT", "0.15"))

ENABLE_SEMANTIC_CACHE = os.getenv("ENABLE_SEMANTIC_CACHE", "true").lower() == "true"
SEMANTIC_CACHE_THRESHOLD = float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.92"))
SEMANTIC_CACHE_TTL = int(os.getenv("SEMANTIC_CACHE_TTL", "600"))
SEMANTIC_CACHE_MAX_ENTRIES = int(os.getenv("SEMANTIC_CACHE_MAX_ENTRIES", "500"))
SEMANTIC_CACHE_HNSW_EF = int(os.getenv("SEMANTIC_CACHE_HNSW_EF", "50"))

# Parent-child chunking retrieval (Phase 51)
ENABLE_PARENT_CHILD_RETRIEVAL = os.getenv("ENABLE_PARENT_CHILD_RETRIEVAL", "false").lower() in ("true", "1")

# Degradation tiers: circuit-breaker-aware graceful degradation (Phase 51)
ENABLE_DEGRADATION_TIERS = os.getenv("ENABLE_DEGRADATION_TIERS", "false").lower() == "true"

# ---------------------------------------------------------------------------
# Smart Orchestration
# NOTE: ENABLE_MODEL_ROUTER is a client-side hint only.
# It is exposed to the GUI via GET /settings but never enforced server-side.
# ---------------------------------------------------------------------------
ENABLE_MODEL_ROUTER = os.getenv("ENABLE_MODEL_ROUTER", "false").lower() == "true"
COST_SENSITIVITY = os.getenv("COST_SENSITIVITY", "medium")  # low/medium/high

# ---------------------------------------------------------------------------
# Memory Consolidation (Phase 35)
# ---------------------------------------------------------------------------
ENABLE_MEMORY_CONSOLIDATION = os.getenv("ENABLE_MEMORY_CONSOLIDATION", "true").lower() == "true"
ENABLE_CONTEXT_COMPRESSION = os.getenv("ENABLE_CONTEXT_COMPRESSION", "true").lower() == "true"

# ---------------------------------------------------------------------------
# Unified toggle registry — single source of truth for all boolean toggles.
# Module-level ENABLE_* vars above remain for backward compatibility.
# Use ``set_toggle()`` (utils/features.py) for runtime mutations.
# ---------------------------------------------------------------------------
FEATURE_TOGGLES: dict[str, bool] = {
    "enable_hallucination_check": ENABLE_HALLUCINATION_CHECK,
    "enable_feedback_loop": ENABLE_FEEDBACK_LOOP,
    "enable_memory_extraction": ENABLE_MEMORY_EXTRACTION,
    "enable_encryption": ENABLE_ENCRYPTION,
    "enable_auto_inject": ENABLE_AUTO_INJECT,
    "enable_self_rag": ENABLE_SELF_RAG,
    "enable_contextual_chunks": ENABLE_CONTEXTUAL_CHUNKS,
    "enable_memory_recall": ENABLE_MEMORY_RECALL,
    "enable_adaptive_retrieval": ENABLE_ADAPTIVE_RETRIEVAL,
    "enable_query_decomposition": ENABLE_QUERY_DECOMPOSITION,
    "enable_mmr_diversity": ENABLE_MMR_DIVERSITY,
    "enable_intelligent_assembly": ENABLE_INTELLIGENT_ASSEMBLY,
    "enable_late_interaction": ENABLE_LATE_INTERACTION,
    "enable_semantic_cache": ENABLE_SEMANTIC_CACHE,
    "enable_model_router": ENABLE_MODEL_ROUTER,
    "enable_memory_consolidation": ENABLE_MEMORY_CONSOLIDATION,
    "enable_context_compression": ENABLE_CONTEXT_COMPRESSION,
    "enable_parent_child_retrieval": ENABLE_PARENT_CHILD_RETRIEVAL,
}

# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------
_config_logger = _logging.getLogger("ai-companion.config")

COST_SENSITIVITY = COST_SENSITIVITY.strip().lower()
if COST_SENSITIVITY not in ("low", "medium", "high"):
    _config_logger.warning(
        "Invalid COST_SENSITIVITY=%r, defaulting to 'medium'", COST_SENSITIVITY
    )
    COST_SENSITIVITY = "medium"


def log_feature_toggles() -> None:
    """Log all feature toggle states at startup."""
    _config_logger.info("Feature tier: %s", FEATURE_TIER)
    enabled = [k for k, v in FEATURE_TOGGLES.items() if v]
    disabled = [k for k, v in FEATURE_TOGGLES.items() if not v]
    _config_logger.info(
        "Feature toggles — enabled: [%s], disabled: [%s]",
        ", ".join(sorted(enabled)) or "none",
        ", ".join(sorted(disabled)) or "none",
    )


# ---------------------------------------------------------------------------
# Tier-based feature gating (canonical location)
# ---------------------------------------------------------------------------

def is_feature_enabled(feature_name: str) -> bool:
    """Check if a tier-gated feature is enabled (fail-closed for unknown)."""
    if feature_name not in FEATURE_FLAGS:
        _config_logger.warning(
            "Unknown feature flag: '%s' — defaulting to disabled", feature_name
        )
        return False
    return FEATURE_FLAGS[feature_name]


def require_feature(feature_name: str) -> _Callable:
    """Decorator that gates a FastAPI endpoint behind a feature flag (async only).

    Usage::

        @router.post("/endpoint")
        @require_feature("ocr_parsing")
        async def my_endpoint():
            ...
    """
    def decorator(func: _Callable) -> _Callable:
        if not _asyncio.iscoroutinefunction(func):
            raise TypeError(
                f"@require_feature can only decorate async functions, "
                f"but '{func.__name__}' is synchronous."
            )

        @_functools.wraps(func)
        async def wrapper(*args: _Any, **kwargs: _Any) -> _Any:
            if not is_feature_enabled(feature_name):
                from fastapi import HTTPException

                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"Feature '{feature_name}' requires a higher Cerid AI tier. "
                        f"Current tier: {FEATURE_TIER}. "
                        f"Upgrade your tier to enable."
                    ),
                )
            return await func(*args, **kwargs)
        return wrapper
    return decorator


def check_feature(feature_name: str) -> None:
    """Synchronous tier check — raises ``FeatureGateError`` if feature is disabled.

    Use in service functions and sync helpers where ``@require_feature`` cannot
    be applied (it requires async).  Routers should prefer ``@require_feature``.
    """
    if not is_feature_enabled(feature_name):
        from errors import FeatureGateError

        raise FeatureGateError(
            f"Feature '{feature_name}' requires a higher Cerid AI tier. "
            f"Current tier: {FEATURE_TIER}. Upgrade your tier to enable.",
        )


def check_tier(required_tier: str, *, context: str = "") -> None:
    """Synchronous tier comparison — raises ``FeatureGateError`` if unmet.

    Use for dynamic tier checks where the required tier comes from metadata
    (e.g. plugin manifests) rather than a named feature flag.
    """
    if not is_tier_met(required_tier):
        from errors import FeatureGateError

        msg = f"Requires '{required_tier}' tier (current: '{FEATURE_TIER}')."
        if context:
            msg = f"{context} {msg}"
        raise FeatureGateError(msg)


def is_tier_met(required_tier: str) -> bool:
    """Check if the current tier meets the requirement (no exception)."""
    current_level = _TIER_LEVELS.get(FEATURE_TIER, 0)
    required_level = _TIER_LEVELS.get(required_tier, 0)
    return current_level >= required_level


def _get_feature_tier(feature_name: str) -> str:
    """Determine the minimum tier required for a feature flag."""
    # Enterprise-only features
    if feature_name in ("sso_saml", "audit_logging", "priority_support"):
        return "enterprise"
    if feature_name == "multi_user":
        return "enterprise"  # multi_user is enterprise (env override available)
    # Community features (always enabled)
    if feature_name in ("hierarchical_taxonomy", "file_upload_gui",
                        "encryption_at_rest", "truth_audit", "live_metrics"):
        return "community"
    # Everything else is pro
    return "pro"


def get_feature_status() -> dict:
    """Return the status of all feature flags."""
    return {
        "tier": FEATURE_TIER,
        "features": {
            name: {
                "enabled": enabled,
                "tier_required": _get_feature_tier(name),
            }
            for name, enabled in FEATURE_FLAGS.items()
        },
    }
