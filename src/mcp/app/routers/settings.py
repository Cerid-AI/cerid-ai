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
from core.utils.version import get_version
from utils.features import set_toggle

router = APIRouter()
logger = logging.getLogger("ai-companion.settings")


def _redact_url(url: str) -> str:
    """Redact password from a connection URL (e.g. redis://:pass@host → redis://***@host)."""
    return re.sub(r"://([^@]*?)@", "://***@", url) if "@" in url else url


def _current_mcp_client_mode() -> str:
    """Read the current MCP_CLIENT_MODE for the GUI governance tab."""
    from app.services.mcp_client_policy import current_mode
    return current_mode().value


def _current_mcp_client_allowlist() -> set[str]:
    """Read the current MCP_CLIENT_ALLOWLIST for the GUI governance tab."""
    from app.services.mcp_client_policy import current_allowlist
    return current_allowlist()


def _is_strict_agents_only() -> bool:
    """Read the current STRICT_AGENTS_ONLY flag for the GUI governance tab."""
    from app.services.strict_agents_policy import is_strict_mode
    return is_strict_mode()


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
        None, ge=0.0, le=1.0, description="Minimum relevance score for auto-injection"
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
    pack_relevance_weight: float | None = Field(
        None, ge=0.0, le=2.0,
        description="Knowledge-pack down-weight multiplier (Slice 7.2). <1.0 makes "
                    "personal data win ties; 1.0 = neutral. Advanced / SERVER scope.",
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

    # Cycle 3.2 — SPLADE-v3 sparse retrieval + 3-way RRF fusion.
    enable_sparse_retrieval: bool | None = Field(
        None,
        description=(
            "Toggle SPLADE-v3 learned-sparse retrieval (third retriever "
            "alongside vector + BM25). Defaults OFF; recommended once "
            "the corpus crosses CERID_RECOMMEND_SPARSE_AT documents."
        ),
    )
    enable_hype: bool | None = Field(
        None,
        description=(
            "Toggle HyPE (Hypothetical Prompt Embeddings) retrieval. "
            "Backed by env RETRIEVAL_HYPE_ENABLED. Surfaced via the "
            "recommender's Enable payload."
        ),
    )
    enable_parent_child_retrieval: bool | None = Field(
        None,
        description=(
            "Toggle parent-child (small-to-big) chunk retrieval. Backed "
            "by env PARENT_CHILD_ENABLED. Surfaced via the recommender's "
            "Enable payload."
        ),
    )
    # v0.93.8 — per-workload GPU routing for Quenchforge (AMD Mac).
    embeddings_provider: str | None = Field(
        None,
        description=(
            "Where to run dense embeddings: 'sidecar' (default — auto-"
            "detects CoreML/CUDA on Mac ARM64/Linux), 'quenchforge' "
            "(opt-in GPU on Intel Mac + AMD), or 'in-process' (CPU)."
        ),
    )
    rerank_provider: str | None = Field(
        None,
        description=(
            "Where to run the cross-encoder reranker: 'sidecar' "
            "(default), 'quenchforge' (opt-in GPU on Intel Mac + AMD), "
            "or 'in-process' (CPU)."
        ),
    )
    quenchforge_embed_model: str | None = Field(
        None,
        description=(
            "GGUF model name Quenchforge serves on /v1/embeddings. "
            "Must produce 768-dim vectors to match ChromaDB. See "
            "docs/AMD_GPU_MODEL_RECOMMENDATIONS.md for vetted picks."
        ),
    )
    quenchforge_rerank_model: str | None = Field(
        None,
        description=(
            "GGUF reranker model name Quenchforge serves on /v1/rerank. "
            "BGE Reranker v2 m3 is the default recommendation."
        ),
    )
    # v0.93.9 — close the GET/PATCH asymmetry. internal_llm_provider was in
    # the GET response but operators could only change it via container env,
    # which forced a restart. Now mutable at runtime so provider flips take
    # effect on the next call_internal_llm invocation.
    internal_llm_provider: str | None = Field(
        None,
        description=(
            "Where pipeline-internal LLM calls go: 'openrouter' (default cloud), "
            "'ollama' (local stock Ollama daemon), or 'quenchforge' (local "
            "Mac+AMD via Quenchforge gateway). Affects ingest enrichment, "
            "LLM-rerank, memory extraction, claim extraction, etc."
        ),
    )
    internal_llm_model: str | None = Field(
        None,
        description=(
            "Model name passed to the internal-LLM provider. Empty resolves "
            "to provider-specific default (Ollama: OLLAMA_DEFAULT_MODEL; "
            "OpenRouter: meta-llama/llama-3.3-70b-instruct:free per call_llm "
            "fallback). For Quenchforge, must match a GGUF filename under "
            "the Quenchforge models dir."
        ),
    )
    hybrid_fusion_mode: str | None = Field(
        None,
        description=(
            "Fusion strategy: weighted_sum (legacy), rrf (vector+BM25), "
            "or tri_rrf (vector+BM25+SPLADE). Auto-picked to tri_rrf "
            "when the sparse toggle flips on."
        ),
    )
    hybrid_rrf_sparse_weight: float | None = Field(
        None, ge=0.0, le=5.0,
        description="Per-retriever weight for SPLADE-v3 in tri_rrf fusion.",
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
        # Sprint 1 governance flags (read live each call so operators flipping
        # these without restart show the current state in the UI).
        "mcp_client_mode": _current_mcp_client_mode(),
        "mcp_client_allowlist": sorted(_current_mcp_client_allowlist()),
        "strict_agents_only": _is_strict_agents_only(),
        "domains": config.DOMAINS,
        "taxonomy": config.TAXONOMY,
        "storage_mode": config.STORAGE_MODE,
        "sync_backend": config.SYNC_BACKEND,
        "machine_id": config.MACHINE_ID,
        "version": get_version(),
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
        "pack_relevance_weight": config.PACK_RELEVANCE_WEIGHT,
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
        # Cycle 3.2 — SPLADE-v3 sparse + tri_rrf fusion config.
        "enable_sparse_retrieval": os.getenv(
            "RETRIEVAL_SPARSE_ENABLED", "false",
        ).strip().lower() in {"1", "true", "yes", "on"},
        "hybrid_fusion_mode": getattr(config, "HYBRID_FUSION_MODE", "weighted_sum"),
        "hybrid_rrf_sparse_weight": getattr(config, "HYBRID_RRF_SPARSE_WEIGHT", 1.0),
        # v0.93.8 — per-workload GPU routing for Quenchforge.
        "embeddings_provider": os.getenv("EMBEDDINGS_PROVIDER", "sidecar"),
        "rerank_provider": os.getenv("RERANK_PROVIDER", "sidecar"),
        "quenchforge_embed_model": os.getenv("QUENCHFORGE_EMBED_MODEL", ""),
        "quenchforge_rerank_model": os.getenv("QUENCHFORGE_RERANK_MODEL", ""),
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

    if req.pack_relevance_weight is not None:
        config.PACK_RELEVANCE_WEIGHT = req.pack_relevance_weight  # type: ignore[assignment]
        updated["pack_relevance_weight"] = req.pack_relevance_weight

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

    # Cycle 3.2 — sparse retrieval + tri_rrf fusion.
    # RETRIEVAL_SPARSE_ENABLED is an env-var flag (mirrors HyPE +
    # parent-child). Mutating the live os.environ value lets us also
    # update the module-level SPARSE_ENABLED constant in
    # core.retrieval.sparse without restarting, so the next ingest /
    # search call sees the new state.
    if req.enable_sparse_retrieval is not None:
        os.environ["RETRIEVAL_SPARSE_ENABLED"] = (
            "true" if req.enable_sparse_retrieval else "false"
        )
        try:
            from core.retrieval import sparse as _sparse_mod
            _sparse_mod.SPARSE_ENABLED = bool(req.enable_sparse_retrieval)
        except Exception as _exc:  # noqa: BLE001 — observability boundary
            logger.warning("sparse module reload failed: %s", _exc)
        updated["enable_sparse_retrieval"] = req.enable_sparse_retrieval

    # ST3 — HyPE + parent-child are env-var flags (mirrors sparse above).
    # The recommender emits {"enable_hype": True} / {"enable_parent_child_
    # retrieval": True}; accepting them here is what makes the GUI "Enable"
    # button succeed instead of 400ing.
    if req.enable_hype is not None:
        os.environ["RETRIEVAL_HYPE_ENABLED"] = (
            "true" if req.enable_hype else "false"
        )
        updated["enable_hype"] = req.enable_hype

    if req.enable_parent_child_retrieval is not None:
        os.environ["PARENT_CHILD_ENABLED"] = (
            "true" if req.enable_parent_child_retrieval else "false"
        )
        updated["enable_parent_child_retrieval"] = req.enable_parent_child_retrieval

    if req.hybrid_fusion_mode is not None:
        valid_modes = ("weighted_sum", "rrf", "tri_rrf")
        if req.hybrid_fusion_mode not in valid_modes:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid hybrid_fusion_mode: '{req.hybrid_fusion_mode}'. "
                    f"Must be one of {valid_modes}"
                ),
            )
        config.HYBRID_FUSION_MODE = req.hybrid_fusion_mode
        updated["hybrid_fusion_mode"] = req.hybrid_fusion_mode

    if req.hybrid_rrf_sparse_weight is not None:
        config.HYBRID_RRF_SPARSE_WEIGHT = req.hybrid_rrf_sparse_weight  # type: ignore[assignment]
        updated["hybrid_rrf_sparse_weight"] = req.hybrid_rrf_sparse_weight

    # v0.93.8 — per-workload GPU routing flags.  These are env-var
    # backed (consumed by core.utils.embeddings._maybe_embed_via_quenchforge
    # and core.agents.query_agent._maybe_rerank_via_quenchforge) so a
    # PATCH takes effect on the next request without restart.
    if req.embeddings_provider is not None:
        valid_providers = ("sidecar", "quenchforge", "in-process")
        if req.embeddings_provider not in valid_providers:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid embeddings_provider: '{req.embeddings_provider}'. "
                    f"Must be one of {valid_providers}"
                ),
            )
        os.environ["EMBEDDINGS_PROVIDER"] = req.embeddings_provider
        updated["embeddings_provider"] = req.embeddings_provider

    if req.rerank_provider is not None:
        valid_providers = ("sidecar", "quenchforge", "in-process")
        if req.rerank_provider not in valid_providers:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid rerank_provider: '{req.rerank_provider}'. "
                    f"Must be one of {valid_providers}"
                ),
            )
        os.environ["RERANK_PROVIDER"] = req.rerank_provider
        updated["rerank_provider"] = req.rerank_provider

    if req.quenchforge_embed_model is not None:
        os.environ["QUENCHFORGE_EMBED_MODEL"] = req.quenchforge_embed_model
        updated["quenchforge_embed_model"] = req.quenchforge_embed_model

    if req.quenchforge_rerank_model is not None:
        os.environ["QUENCHFORGE_RERANK_MODEL"] = req.quenchforge_rerank_model
        updated["quenchforge_rerank_model"] = req.quenchforge_rerank_model

    # v0.93.9 — internal_llm_provider + internal_llm_model live-mutation.
    # core.utils.internal_llm reads config.INTERNAL_LLM_PROVIDER at call
    # time (via getattr(config, "INTERNAL_LLM_PROVIDER", "openrouter")),
    # so we need to mutate BOTH os.environ (for any child process that
    # re-reads) AND the module attribute (for the live process).
    if req.internal_llm_provider is not None:
        valid_internal = ("openrouter", "ollama", "quenchforge")
        if req.internal_llm_provider not in valid_internal:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid internal_llm_provider: "
                    f"'{req.internal_llm_provider}'. "
                    f"Must be one of {valid_internal}"
                ),
            )
        os.environ["INTERNAL_LLM_PROVIDER"] = req.internal_llm_provider
        config.INTERNAL_LLM_PROVIDER = req.internal_llm_provider  # type: ignore[attr-defined]
        updated["internal_llm_provider"] = req.internal_llm_provider

    if req.internal_llm_model is not None:
        os.environ["INTERNAL_LLM_MODEL"] = req.internal_llm_model
        config.INTERNAL_LLM_MODEL = req.internal_llm_model  # type: ignore[attr-defined]
        updated["internal_llm_model"] = req.internal_llm_model

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
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error('app.routers.settings', exc)
        logger.warning("Failed to persist settings to sync dir: %s", exc)

    logger.info(f"Settings updated: {updated}")
    return {"status": "success", "updated": updated}


# ── Private mode endpoints ──────────────────────────────────────────────────

_PRIVATE_MODE_KEY = "cerid:private_mode:global"
# Per-tab/session level overrides — written when a tab declares its
# private level via the X-Cerid-Session header.  Used by the L4
# session-wipe endpoint to confirm a tab is in full-ephemeral mode
# before clearing its state.
_PRIVATE_MODE_SESSION_PREFIX = "cerid:private_mode:session:"


@router.get("/settings/private-mode")
async def get_private_mode():
    """Return current private mode level (0 = disabled)."""
    try:
        redis = get_redis()
        level = redis.get(_PRIVATE_MODE_KEY)
        return {"level": int(level) if level is not None else 0}
    except Exception as exc:
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error('app.routers.settings', exc)
        return {"level": 0}


class PrivateModeRequest(BaseModel):
    # Cycle 3.2 / v0.93.5 — L4 ("Full ephemeral") joins the validated
    # range.  The UI has rendered L4 since v0.92.1 but the backend
    # validator rejected it as 422, leaving the contract half-shipped.
    # Honoring L4 here means: backend accepts the level, surfaces it in
    # GET responses, and provides the session-wipe endpoint that lets
    # the frontend confirm the wipe-on-close lifecycle on the way out.
    level: int = Field(
        ..., ge=0, le=4,
        description="Private mode level (0=off, 1=skip saves, 2=skip KB, 3=skip audit, 4=full ephemeral)",
    )


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


class SessionWipeRequest(BaseModel):
    """Body for the L4 session-wipe endpoint.

    Frontend fires this via ``navigator.sendBeacon()`` on ``beforeunload``
    when L4 is active.  ``conversation_id`` is the canonical chat thread
    id the frontend already tracks for localStorage caching; the backend
    uses it to scope the wipe (so a wipe from one L4 tab doesn't affect
    another open tab's state).
    """

    conversation_id: str = Field(
        ..., min_length=1, max_length=128,
        description="Conversation thread id whose ephemeral state should be erased.",
    )


@router.post("/settings/private-mode/session-wipe", status_code=200)
async def wipe_private_session(req: SessionWipeRequest):
    """L4 contract: erase ephemeral session state for a conversation.

    The L4 ("Full ephemeral") level promises that closing the tab wipes
    the conversation, memory state, and any cached query results — even
    the audit log is bypassed.  The frontend calls this endpoint via
    ``sendBeacon`` from a ``beforeunload`` handler when L4 is active.

    What we wipe:

    * The global ``cerid:private_mode:global`` flag (so the next request
      from any tab defaults back to L0).
    * Any per-session override at ``cerid:private_mode:session:{id}``.
    * The audit-log stream entries scoped to this conversation, if
      any were written (defense in depth — L3 already bypasses them,
      but L4 makes the absence explicit).

    Returns ``{wiped: true, level_after: 0, conversation_id}`` on
    success.  The endpoint is idempotent — re-firing with the same id
    is safe.

    Audit-trail note: this endpoint INTENTIONALLY logs the wipe at INFO
    level with the conversation_id so operators can verify the L4
    lifecycle in their logs without compromising the conversation
    contents themselves.
    """
    redis = get_redis()
    session_key = f"{_PRIVATE_MODE_SESSION_PREFIX}{req.conversation_id}"
    with redis.pipeline() as pipe:
        pipe.delete(_PRIVATE_MODE_KEY)
        pipe.delete(session_key)
        pipe.execute()
    logger.info(
        "private_mode.l4_session_wiped",
        extra={"conversation_id": req.conversation_id},
    )
    return {
        "wiped": True,
        "level_after": 0,
        "conversation_id": req.conversation_id,
    }


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
    # Single mutation point — set_tier rebinds the canonical global, recomputes
    # FEATURE_FLAGS, and syncs the config-namespace copy so no reader goes stale.
    features_mod.set_tier(req.tier)
    logger.info("Feature tier updated to '%s', flags refreshed", req.tier)
    return {"tier": req.tier, "feature_flags": config.FEATURE_FLAGS}
