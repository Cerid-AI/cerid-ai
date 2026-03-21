# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Feature flags, toggles, and plugin configuration."""
from __future__ import annotations

import logging as _logging
import os

# ---------------------------------------------------------------------------
# Plugin System & Feature Tiers
# ---------------------------------------------------------------------------
# Feature tier: "community" (OSS) or "pro" (commercial plugins enabled)
FEATURE_TIER = os.getenv("CERID_TIER", "community")

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
# Community features are always enabled; pro features require CERID_TIER=pro
FEATURE_FLAGS = {
    # Pro-only features (disabled in community tier)
    "ocr_parsing":         FEATURE_TIER == "pro",
    "audio_transcription": FEATURE_TIER == "pro",
    "image_understanding": FEATURE_TIER == "pro",
    "semantic_dedup":      FEATURE_TIER == "pro",
    "advanced_analytics":  FEATURE_TIER == "pro",
    "multi_user":          CERID_MULTI_USER or FEATURE_TIER == "pro",
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
    "enable_adaptive_retrieval": ENABLE_ADAPTIVE_RETRIEVAL,
    "enable_query_decomposition": ENABLE_QUERY_DECOMPOSITION,
    "enable_mmr_diversity": ENABLE_MMR_DIVERSITY,
    "enable_intelligent_assembly": ENABLE_INTELLIGENT_ASSEMBLY,
    "enable_late_interaction": ENABLE_LATE_INTERACTION,
    "enable_semantic_cache": ENABLE_SEMANTIC_CACHE,
    "enable_model_router": ENABLE_MODEL_ROUTER,
    "enable_memory_consolidation": ENABLE_MEMORY_CONSOLIDATION,
    "enable_context_compression": ENABLE_CONTEXT_COMPRESSION,
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
    enabled = [k for k, v in FEATURE_TOGGLES.items() if v]
    disabled = [k for k, v in FEATURE_TOGGLES.items() if not v]
    _config_logger.info(
        "Feature toggles — enabled: [%s], disabled: [%s]",
        ", ".join(sorted(enabled)) or "none",
        ", ".join(sorted(disabled)) or "none",
    )
