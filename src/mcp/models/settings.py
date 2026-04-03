# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pydantic response models for settings endpoints."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "MemoryConfig",
    "SettingsResponse",
    "SettingsUpdateResponse",
]


class MemoryConfig(BaseModel):
    """Nested memory configuration block."""

    min_response_length: int = 100
    memory_types: list[str] = Field(default_factory=list)
    retention_days: int = 0
    storage_domain: str = "conversations"
    extraction_model: str = ""


class SettingsResponse(BaseModel):
    """Response from GET /settings."""

    model_config = ConfigDict(extra="allow")

    # Core settings
    categorize_mode: str = ""
    chunk_max_tokens: int = 0
    chunk_overlap: int = 0
    enable_encryption: bool = False
    enable_feedback_loop: bool = False
    enable_hallucination_check: bool = False
    enable_memory_extraction: bool = False
    enable_model_router: bool = False
    enable_auto_inject: bool = False
    enable_self_rag: bool = False
    hallucination_threshold: float = 0.0
    auto_inject_threshold: float = 0.0
    cost_sensitivity: str = ""
    feature_tier: str = ""
    feature_flags: dict[str, bool] = Field(default_factory=dict)
    feature_toggles: dict[str, bool] = Field(default_factory=dict)
    multi_user: bool = False
    domains: list[str] = Field(default_factory=list)
    taxonomy: dict[str, Any] = Field(default_factory=dict)
    storage_mode: str = ""
    sync_backend: str = ""
    machine_id: str = ""
    version: str = ""
    memory_config: MemoryConfig = Field(default_factory=MemoryConfig)

    # Infrastructure (read-only)
    bifrost_url: str = ""
    bifrost_timeout: int = 0
    chroma_url: str = ""
    neo4j_uri: str = ""
    redis_url: str = ""
    archive_path: str = ""
    chunking_mode: str = ""
    rag_mode: str = ""

    # Search tuning
    hybrid_vector_weight: float = 0.0
    hybrid_keyword_weight: float = 0.0
    rerank_llm_weight: float = 0.0
    rerank_original_weight: float = 0.0
    temporal_half_life_days: int = 0
    temporal_recency_weight: float = 0.0

    # Advanced RAG pipeline
    enable_contextual_chunks: bool = False
    enable_adaptive_retrieval: bool = False
    adaptive_retrieval_light_top_k: int = 3
    enable_query_decomposition: bool = False
    query_decomposition_max_subqueries: int = 4
    enable_mmr_diversity: bool = False
    mmr_lambda: float = 0.7
    enable_intelligent_assembly: bool = False
    enable_late_interaction: bool = False
    late_interaction_top_n: int = 5
    late_interaction_blend_weight: float = 0.15
    enable_semantic_cache: bool = False
    semantic_cache_threshold: float = 0.85

    # Trading agent
    trading_enabled: bool = False

    # Ollama
    ollama_enabled: bool = False
    ollama_url: str = ""
    internal_llm_provider: str = ""
    internal_llm_model: str = ""


class SettingsUpdateResponse(BaseModel):
    """Response from PATCH /settings."""

    status: str
    updated: dict[str, Any] = Field(default_factory=dict)
