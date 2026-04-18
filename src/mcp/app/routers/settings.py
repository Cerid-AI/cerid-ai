# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Settings endpoints — expose server configuration to the GUI."""
from __future__ import annotations

import logging
import os
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import config
import config.features as features_mod
from app.deps import get_redis
from utils.features import set_toggle

router = APIRouter()
logger = logging.getLogger("ai-companion.settings")

# Version constant (single source of truth for the API)
_VERSION = "0.83.0"


def _redact_url(url: str) -> str:
    """Redact password from a connection URL (e.g. redis://:pass@host → redis://***@host)."""
    return re.sub(r"://([^@]*?)@", "://***@", url) if "@" in url else url


# ── Pydantic models ──────────────────────────────────────────────────────────

class SettingsUpdateRequest(BaseModel):
    """Subset of settings that can be changed at runtime without restart."""

    categorize_mode: str | None = Field(
        None, description="Categorization tier: manual, smart, or pro"
    )
    enable_feedback_loop: bool | None = Field(
        None, description="Toggle conversation feedback loop"
    )
    enable_hallucination_check: bool | None = Field(
        None, description="Toggle hallucination detection on feedback"
    )
    enable_memory_extraction: bool | None = Field(
        None, description="Toggle memory extraction from conversations"
    )
    hallucination_threshold: float | None = Field(
        None, ge=0.0, le=1.0, description="Confidence threshold for hallucination flagging"
    )
    cost_sensitivity: str | None = Field(
        None, description="Cost sensitivity level: low, medium, or high"
    )
    enable_auto_inject: bool | None = Field(
        None, description="Toggle automatic KB context injection for high-confidence results"
    )
    auto_inject_threshold: float | None = Field(
        None, ge=0.5, le=1.0, description="Minimum relevance score for auto-injection"
    )
    enable_model_router: bool | None = Field(
        None, description="Toggle automatic model routing based on query complexity"
    )
    storage_mode: str | None = Field(
        None, description="Storage mode: extract_only or archive"
    )
    enable_self_rag: bool | None = Field(
        None, description="Toggle Self-RAG validation loop for retrieval refinement"
    )
    hybrid_vector_weight: float | None = Field(
        None, ge=0.0, le=1.0, description="Weight for vector similarity in hybrid search"
    )
    hybrid_keyword_weight: float | None = Field(
        None, ge=0.0, le=1.0, description="Weight for keyword matching in hybrid search"
    )
    rerank_llm_weight: float | None = Field(
        None, ge=0.0, le=1.0, description="Weight for LLM-based reranking score"
    )
    rerank_original_weight: float | None = Field(
        None, ge=0.0, le=1.0, description="Weight for original relevance score in reranking"
    )
    # Advanced RAG pipeline toggles
    enable_contextual_chunks: bool | None = Field(
        None, description="Toggle LLM-generated situational summaries on chunks"
    )
    enable_adaptive_retrieval: bool | None = Field(
        None, description="Toggle adaptive retrieval gate (skip/reduce for simple queries)"
    )
    adaptive_retrieval_light_top_k: int | None = Field(
        None, ge=1, le=20, description="Top-K for light retrieval mode"
    )
    enable_query_decomposition: bool | None = Field(
        None, description="Toggle multi-part query decomposition into parallel sub-queries"
    )
    query_decomposition_max_subqueries: int | None = Field(
        None, ge=2, le=8, description="Maximum sub-queries for query decomposition"
    )
    enable_mmr_diversity: bool | None = Field(
        None, description="Toggle MMR diversity reordering of results"
    )
    mmr_lambda: float | None = Field(
        None, ge=0.0, le=1.0, description="MMR lambda (1=pure relevance, 0=pure diversity)"
    )
    enable_intelligent_assembly: bool | None = Field(
        None, description="Toggle three-pass context assembly with facet coverage"
    )
    enable_late_interaction: bool | None = Field(
        None, description="Toggle ColBERT-inspired late interaction scoring"
    )
    late_interaction_top_n: int | None = Field(
        None, ge=2, le=20, description="Number of candidates for late interaction scoring"
    )
    late_interaction_blend_weight: float | None = Field(
        None, ge=0.0, le=0.5, description="Blend weight for late interaction score"
    )
    enable_semantic_cache: bool | None = Field(
        None, description="Toggle semantic query cache"
    )
    semantic_cache_threshold: float | None = Field(
        None, ge=0.5, le=1.0, description="Similarity threshold for cache hits"
    )
    enable_memory_consolidation: bool | None = Field(
        None, description="Enable memory dedup/consolidation during extraction"
    )
    enable_context_compression: bool | None = Field(
        None, description="Enable LLM-based conversation context compression"
    )
    rag_mode: str | None = Field(
        None, description="RAG mode: smart, always, or off"
    )


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/settings")
async def get_settings_endpoint():
    """Return current server settings (read-only view of safe-to-expose config)."""
    return {
        "categorize_mode": config.CATEGORIZE_MODE,
        "chunk_max_tokens": config.CHUNK_MAX_TOKENS,
        "chunk_overlap": config.CHUNK_OVERLAP,
        "enable_encryption": config.ENABLE_ENCRYPTION,
        "enable_feedback_loop": config.ENABLE_FEEDBACK_LOOP,
        "enable_hallucination_check": config.ENABLE_HALLUCINATION_CHECK,
        "enable_memory_extraction": config.ENABLE_MEMORY_EXTRACTION,
        "enable_model_router": config.ENABLE_MODEL_ROUTER,
        "enable_auto_inject": config.ENABLE_AUTO_INJECT,
        "enable_self_rag": config.ENABLE_SELF_RAG,
        "hallucination_threshold": config.HALLUCINATION_THRESHOLD,
        "auto_inject_threshold": config.AUTO_INJECT_THRESHOLD,
        "cost_sensitivity": config.COST_SENSITIVITY,
        "feature_tier": config.FEATURE_TIER,
        "feature_flags": config.FEATURE_FLAGS,
        "feature_toggles": config.FEATURE_TOGGLES,
        "multi_user": config.CERID_MULTI_USER,
        "domains": config.DOMAINS,
        "taxonomy": config.TAXONOMY,
        "storage_mode": config.STORAGE_MODE,
        "sync_backend": config.SYNC_BACKEND,
        "machine_id": config.MACHINE_ID,
        "version": _VERSION,
        "memory_config": {
            "min_response_length": 100,
            "memory_types": ["fact", "decision", "preference", "action_item"],
            "retention_days": config.MEMORY_RETENTION_DAYS,
            "storage_domain": "conversations",
            "extraction_model": "Llama 3.3 (free tier)",
        },
        # Infrastructure (read-only)
        "bifrost_timeout": config.BIFROST_TIMEOUT,  # legacy name, generic LLM call timeout
        "chroma_url": config.CHROMA_URL,
        "neo4j_uri": config.NEO4J_URI,
        "redis_url": _redact_url(config.REDIS_URL),
        "archive_path": config.ARCHIVE_PATH,
        "chunking_mode": config.CHUNKING_MODE,
        # Search tuning (read-write)
        "hybrid_vector_weight": config.HYBRID_VECTOR_WEIGHT,
        "hybrid_keyword_weight": config.HYBRID_KEYWORD_WEIGHT,
        "rerank_llm_weight": config.RERANK_LLM_WEIGHT,
        "rerank_original_weight": config.RERANK_ORIGINAL_WEIGHT,
        "temporal_half_life_days": config.TEMPORAL_HALF_LIFE_DAYS,
        "temporal_recency_weight": config.TEMPORAL_RECENCY_WEIGHT,
        # Advanced RAG pipeline (read-write)
        "enable_contextual_chunks": features_mod.ENABLE_CONTEXTUAL_CHUNKS,
        "enable_adaptive_retrieval": features_mod.ENABLE_ADAPTIVE_RETRIEVAL,
        "adaptive_retrieval_light_top_k": features_mod.ADAPTIVE_RETRIEVAL_LIGHT_TOP_K,
        "enable_query_decomposition": features_mod.ENABLE_QUERY_DECOMPOSITION,
        "query_decomposition_max_subqueries": features_mod.QUERY_DECOMPOSITION_MAX_SUBQUERIES,
        "enable_mmr_diversity": features_mod.ENABLE_MMR_DIVERSITY,
        "mmr_lambda": features_mod.MMR_LAMBDA,
        "enable_intelligent_assembly": features_mod.ENABLE_INTELLIGENT_ASSEMBLY,
        "enable_late_interaction": features_mod.ENABLE_LATE_INTERACTION,
        "late_interaction_top_n": features_mod.LATE_INTERACTION_TOP_N,
        "late_interaction_blend_weight": features_mod.LATE_INTERACTION_BLEND_WEIGHT,
        "enable_semantic_cache": features_mod.ENABLE_SEMANTIC_CACHE,
        "semantic_cache_threshold": features_mod.SEMANTIC_CACHE_THRESHOLD,
        "enable_memory_consolidation": features_mod.ENABLE_MEMORY_CONSOLIDATION,
        "enable_context_compression": features_mod.ENABLE_CONTEXT_COMPRESSION,
        # Trading agent integration
        "trading_enabled": config.CERID_TRADING_ENABLED,
        # Ollama add-on
        "ollama_enabled": os.getenv("OLLAMA_ENABLED", "false").lower() in ("true", "1"),
        "ollama_url": os.getenv("OLLAMA_URL", "http://localhost:11434"),
        "internal_llm_provider": config.INTERNAL_LLM_PROVIDER,
        "internal_llm_model": config.INTERNAL_LLM_MODEL or config.OLLAMA_DEFAULT_MODEL,
        "rag_mode": getattr(config, "RAG_MODE", "smart"),
    }


@router.patch("/settings")
async def update_settings_endpoint(req: SettingsUpdateRequest):
    """Update a subset of settings at runtime.

    Only settings that make sense to change without a restart are accepted.
    """
    updated: dict[str, str | bool | float] = {}

    if req.categorize_mode is not None:
        valid_modes = ("manual", "smart", "pro")
        if req.categorize_mode not in valid_modes:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid categorize_mode: '{req.categorize_mode}'. Must be one of {valid_modes}",
            )
        config.CATEGORIZE_MODE = req.categorize_mode
        updated["categorize_mode"] = req.categorize_mode

    if req.enable_feedback_loop is not None:
        set_toggle("enable_feedback_loop", req.enable_feedback_loop)
        updated["enable_feedback_loop"] = req.enable_feedback_loop

    if req.enable_hallucination_check is not None:
        set_toggle("enable_hallucination_check", req.enable_hallucination_check)
        updated["enable_hallucination_check"] = req.enable_hallucination_check

    if req.enable_memory_extraction is not None:
        set_toggle("enable_memory_extraction", req.enable_memory_extraction)
        updated["enable_memory_extraction"] = req.enable_memory_extraction

    if req.hallucination_threshold is not None:
        config.HALLUCINATION_THRESHOLD = req.hallucination_threshold  # type: ignore[assignment]
        updated["hallucination_threshold"] = req.hallucination_threshold

    if req.cost_sensitivity is not None:
        valid_levels = ("low", "medium", "high")
        if req.cost_sensitivity not in valid_levels:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid cost_sensitivity: '{req.cost_sensitivity}'. Must be one of {valid_levels}",
            )
        config.COST_SENSITIVITY = req.cost_sensitivity
        updated["cost_sensitivity"] = req.cost_sensitivity

    if req.enable_auto_inject is not None:
        set_toggle("enable_auto_inject", req.enable_auto_inject)
        updated["enable_auto_inject"] = req.enable_auto_inject

    if req.auto_inject_threshold is not None:
        config.AUTO_INJECT_THRESHOLD = req.auto_inject_threshold  # type: ignore[assignment]
        updated["auto_inject_threshold"] = req.auto_inject_threshold

    if req.enable_model_router is not None:
        set_toggle("enable_model_router", req.enable_model_router)
        updated["enable_model_router"] = req.enable_model_router

    if req.storage_mode is not None:
        valid_storage_modes = ("extract_only", "archive")
        if req.storage_mode not in valid_storage_modes:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid storage_mode: '{req.storage_mode}'. Must be one of {valid_storage_modes}",
            )
        config.STORAGE_MODE = req.storage_mode
        updated["storage_mode"] = req.storage_mode

    if req.enable_self_rag is not None:
        set_toggle("enable_self_rag", req.enable_self_rag)
        updated["enable_self_rag"] = req.enable_self_rag

    if req.hybrid_vector_weight is not None:
        config.HYBRID_VECTOR_WEIGHT = req.hybrid_vector_weight  # type: ignore[assignment]
        updated["hybrid_vector_weight"] = req.hybrid_vector_weight

    if req.hybrid_keyword_weight is not None:
        config.HYBRID_KEYWORD_WEIGHT = req.hybrid_keyword_weight  # type: ignore[assignment]
        updated["hybrid_keyword_weight"] = req.hybrid_keyword_weight

    if req.rerank_llm_weight is not None:
        config.RERANK_LLM_WEIGHT = req.rerank_llm_weight  # type: ignore[assignment]
        updated["rerank_llm_weight"] = req.rerank_llm_weight

    if req.rerank_original_weight is not None:
        config.RERANK_ORIGINAL_WEIGHT = req.rerank_original_weight  # type: ignore[assignment]
        updated["rerank_original_weight"] = req.rerank_original_weight

    # Advanced RAG pipeline — boolean toggles via set_toggle(), numeric params
    # via direct dual-mutation (not in FEATURE_TOGGLES registry).
    if req.enable_contextual_chunks is not None:
        set_toggle("enable_contextual_chunks", req.enable_contextual_chunks)
        updated["enable_contextual_chunks"] = req.enable_contextual_chunks

    if req.enable_adaptive_retrieval is not None:
        set_toggle("enable_adaptive_retrieval", req.enable_adaptive_retrieval)
        updated["enable_adaptive_retrieval"] = req.enable_adaptive_retrieval

    if req.adaptive_retrieval_light_top_k is not None:
        features_mod.ADAPTIVE_RETRIEVAL_LIGHT_TOP_K = req.adaptive_retrieval_light_top_k
        config.ADAPTIVE_RETRIEVAL_LIGHT_TOP_K = req.adaptive_retrieval_light_top_k  # type: ignore[assignment]
        updated["adaptive_retrieval_light_top_k"] = req.adaptive_retrieval_light_top_k

    if req.enable_query_decomposition is not None:
        set_toggle("enable_query_decomposition", req.enable_query_decomposition)
        updated["enable_query_decomposition"] = req.enable_query_decomposition

    if req.query_decomposition_max_subqueries is not None:
        features_mod.QUERY_DECOMPOSITION_MAX_SUBQUERIES = req.query_decomposition_max_subqueries
        config.QUERY_DECOMPOSITION_MAX_SUBQUERIES = req.query_decomposition_max_subqueries  # type: ignore[assignment]
        updated["query_decomposition_max_subqueries"] = req.query_decomposition_max_subqueries

    if req.enable_mmr_diversity is not None:
        set_toggle("enable_mmr_diversity", req.enable_mmr_diversity)
        updated["enable_mmr_diversity"] = req.enable_mmr_diversity

    if req.mmr_lambda is not None:
        features_mod.MMR_LAMBDA = req.mmr_lambda
        config.MMR_LAMBDA = req.mmr_lambda  # type: ignore[assignment]
        updated["mmr_lambda"] = req.mmr_lambda

    if req.enable_intelligent_assembly is not None:
        set_toggle("enable_intelligent_assembly", req.enable_intelligent_assembly)
        updated["enable_intelligent_assembly"] = req.enable_intelligent_assembly

    if req.enable_late_interaction is not None:
        set_toggle("enable_late_interaction", req.enable_late_interaction)
        updated["enable_late_interaction"] = req.enable_late_interaction

    if req.late_interaction_top_n is not None:
        features_mod.LATE_INTERACTION_TOP_N = req.late_interaction_top_n
        config.LATE_INTERACTION_TOP_N = req.late_interaction_top_n  # type: ignore[assignment]
        updated["late_interaction_top_n"] = req.late_interaction_top_n

    if req.late_interaction_blend_weight is not None:
        features_mod.LATE_INTERACTION_BLEND_WEIGHT = req.late_interaction_blend_weight
        config.LATE_INTERACTION_BLEND_WEIGHT = req.late_interaction_blend_weight  # type: ignore[assignment]
        updated["late_interaction_blend_weight"] = req.late_interaction_blend_weight

    if req.enable_semantic_cache is not None:
        set_toggle("enable_semantic_cache", req.enable_semantic_cache)
        updated["enable_semantic_cache"] = req.enable_semantic_cache

    if req.semantic_cache_threshold is not None:
        features_mod.SEMANTIC_CACHE_THRESHOLD = req.semantic_cache_threshold
        config.SEMANTIC_CACHE_THRESHOLD = req.semantic_cache_threshold  # type: ignore[assignment]
        updated["semantic_cache_threshold"] = req.semantic_cache_threshold

    if req.enable_memory_consolidation is not None:
        set_toggle("enable_memory_consolidation", req.enable_memory_consolidation)
        updated["enable_memory_consolidation"] = req.enable_memory_consolidation

    if req.enable_context_compression is not None:
        set_toggle("enable_context_compression", req.enable_context_compression)
        updated["enable_context_compression"] = req.enable_context_compression

    if req.rag_mode is not None:
        valid_rag_modes = ("smart", "always", "off")
        if req.rag_mode not in valid_rag_modes:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid rag_mode: '{req.rag_mode}'. Must be one of {valid_rag_modes}",
            )
        config.RAG_MODE = req.rag_mode
        updated["rag_mode"] = req.rag_mode

    if not updated:
        raise HTTPException(
            status_code=400,
            detail="No valid fields provided. See API docs for updatable fields.",
        )

    # Persist to sync directory for cross-machine/restart durability.
    # Audit P1-11: macOS advisory lock collisions with Dropbox surface as
    # OSError(EDEADLK). Retry briefly (3x, 100/200/400ms) before giving up
    # so a transient lock does not silently drop the user's settings write.
    try:
        if getattr(config, "SYNC_DIR", ""):
            from app.sync.user_state import write_settings_with_retry
            await write_settings_with_retry(config.SYNC_DIR, updated)
    except Exception as exc:
        logger.warning("Failed to persist settings to sync dir: %s", exc)

    logger.info(f"Settings updated: {updated}")
    return {"status": "success", "updated": updated}


# ── Private mode endpoints ──────────────────────────────────────────────────

_PRIVATE_MODE_KEY = "cerid:private_mode:global"


@router.get("/settings/private-mode")
async def get_private_mode():
    """Return current private mode level (0 = disabled)."""
    try:
        redis = get_redis()
        level = redis.get(_PRIVATE_MODE_KEY)
        return {"level": int(level) if level is not None else 0}
    except Exception:
        return {"level": 0}


class PrivateModeRequest(BaseModel):
    level: int = Field(..., ge=0, le=3, description="Private mode level (0=off, 1-3)")


@router.post("/settings/private-mode")
async def set_private_mode(req: PrivateModeRequest):
    """Set private mode level."""
    redis = get_redis()
    redis.set(_PRIVATE_MODE_KEY, str(req.level))
    logger.info("Private mode set to level %d", req.level)
    return {"level": req.level}


@router.delete("/settings/private-mode")
async def reset_private_mode():
    """Reset private mode to level 0 (disabled)."""
    redis = get_redis()
    redis.delete(_PRIVATE_MODE_KEY)
    logger.info("Private mode reset to 0")
    return {"level": 0}


# ── Tier endpoint ───────────────────────────────────────────────────────────

class TierRequest(BaseModel):
    tier: str = Field(..., description="Feature tier: community, pro, or enterprise")


@router.post("/settings/tier")
async def set_tier(req: TierRequest):
    """Update feature tier at runtime and recalculate feature flags."""
    valid_tiers = ("community", "pro", "enterprise")
    if req.tier not in valid_tiers:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tier: '{req.tier}'. Must be one of {valid_tiers}",
        )
    config.FEATURE_TIER = req.tier
    features_mod.FEATURE_TIER = req.tier
    features_mod._refresh_flags()
    logger.info("Feature tier updated to '%s', flags refreshed", req.tier)
    return {"tier": req.tier, "feature_flags": config.FEATURE_FLAGS}
