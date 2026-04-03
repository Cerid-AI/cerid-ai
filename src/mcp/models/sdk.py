# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Typed response models for the ``/sdk/v1/`` stable consumer API.

These models serve two purposes:
1. OpenAPI schema generation — external consumers get machine-readable contracts
2. Runtime validation — FastAPI validates outbound payloads against these schemas

The models use ``model_config = ConfigDict(extra="allow")`` so that new fields
added by internal agents pass through without breaking the contract.  This
ensures backward compatibility: consumers ignore unknown fields, and cerid-ai
can evolve agent return shapes independently of the SDK schema.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _SDKBase(BaseModel):
    """Base for all SDK response models — allows extra fields for forward compat."""

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Core endpoints
# ---------------------------------------------------------------------------


class SDKQueryResponse(_SDKBase):
    """Response from ``POST /sdk/v1/query`` — multi-domain KB search."""

    context: str = Field(default="", description="Assembled context string from matching chunks")
    sources: list[dict[str, Any]] = Field(default_factory=list, description="Result chunks with relevance scores and metadata")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Average relevance of returned sources")
    domains_searched: list[str] = Field(default_factory=list, description="Domains that were actually searched")
    total_results: int = Field(default=0, ge=0, description="Total results after dedup and filtering")
    token_budget_used: int = Field(default=0, ge=0, description="Character count of assembled context")
    graph_results: int = Field(default=0, ge=0, description="Results contributed by graph expansion")
    results: list[dict[str, Any]] = Field(default_factory=list, description="All results with full metadata")


class SDKHallucinationResponse(_SDKBase):
    """Response from ``POST /sdk/v1/hallucination`` — claim verification."""

    conversation_id: str = Field(default="", description="Conversation identifier")
    timestamp: str = Field(default="", description="ISO 8601 verification timestamp")
    skipped: bool = Field(default=False, description="True if verification was skipped (response too short or no claims)")
    reason: str | None = Field(default=None, description="Reason verification was skipped, if applicable")
    claims: list[dict[str, Any]] = Field(default_factory=list, description="Verified claims with status, confidence, and source info")
    summary: dict[str, int] = Field(
        default_factory=lambda: {"total": 0, "verified": 0, "unverified": 0, "uncertain": 0},
        description="Claim verification counts by status",
    )


class SDKMemoryExtractResponse(_SDKBase):
    """Response from ``POST /sdk/v1/memory/extract`` — memory extraction and storage."""

    conversation_id: str = Field(default="", description="Conversation identifier")
    timestamp: str = Field(default="", description="ISO 8601 extraction timestamp")
    memories_extracted: int = Field(default=0, ge=0, description="Number of memories extracted from text")
    memories_stored: int = Field(default=0, ge=0, description="Number successfully stored in KB")
    skipped_duplicates: int = Field(default=0, ge=0, description="Memories skipped due to deduplication")
    results: list[dict[str, Any]] = Field(default_factory=list, description="Per-memory outcome (status, type, summary)")


class SDKHealthResponse(_SDKBase):
    """Response from ``GET /sdk/v1/health`` — service health with feature flags."""

    status: str = Field(description="Overall status: 'healthy' or 'degraded'")
    version: str = Field(description="API version string")
    services: dict[str, str] = Field(description="Per-service connectivity status")
    features: dict[str, bool] = Field(default_factory=dict, description="Consumer-relevant feature toggles")


# ---------------------------------------------------------------------------
# Phase 1 — expanded SDK endpoints
# ---------------------------------------------------------------------------


class SDKIngestRequest(BaseModel):
    """Request body for ``POST /sdk/v1/ingest``."""

    content: str = Field(..., min_length=1, description="Text content to ingest")
    domain: str = Field("general", description="Target knowledge domain")
    tags: str = Field("", description="Comma-separated tags")


class SDKIngestFileRequest(BaseModel):
    """Request body for ``POST /sdk/v1/ingest/file``."""

    file_path: str = Field(..., description="Path to file (in cerid-archive or absolute)")
    domain: str = Field("", description="Domain (empty for auto-detect)")
    tags: str = Field("", description="Comma-separated tags")
    categorize_mode: str = Field("", description="Categorization: manual, smart, or pro")


class SDKIngestResponse(_SDKBase):
    """Response from ``POST /sdk/v1/ingest`` and ``POST /sdk/v1/ingest/file``."""

    status: str = Field(description="'ok' or 'error'")
    artifact_id: str = Field(default="", description="UUID of created artifact")
    chunks: int = Field(default=0, ge=0, description="Number of chunks created")
    domain: str = Field(default="", description="Domain the content was ingested into")


class SDKCollectionsResponse(_SDKBase):
    """Response from ``GET /sdk/v1/collections``."""

    collections: list[str] = Field(default_factory=list, description="Collection names")
    total: int = Field(default=0, ge=0, description="Total collection count")


class SDKTaxonomyResponse(_SDKBase):
    """Response from ``GET /sdk/v1/taxonomy``."""

    domains: list[str] = Field(default_factory=list, description="Active domain names")
    taxonomy: dict[str, Any] = Field(default_factory=dict, description="Full taxonomy tree")


class SDKDetailedHealthResponse(SDKHealthResponse):
    """Response from ``GET /sdk/v1/health/detailed``."""

    circuit_breakers: dict[str, str] = Field(default_factory=dict, description="Breaker states")
    degradation_tier: str = Field(default="FULL", description="Current degradation level")
    uptime_seconds: float = Field(default=0.0, description="Server uptime")


class SDKSettingsResponse(_SDKBase):
    """Response from ``GET /sdk/v1/settings`` — read-only server config."""

    version: str = Field(description="Server version")
    tier: str = Field(description="Feature tier: community, pro, or enterprise")
    features: dict[str, bool] = Field(default_factory=dict, description="Feature flags")


class SDKSearchRequest(BaseModel):
    """Request body for ``POST /sdk/v1/search``."""

    query: str = Field(..., min_length=1, description="Search query")
    domain: str = Field("general", description="Domain to search")
    top_k: int = Field(5, ge=1, le=50, description="Number of results")


class SDKSearchResponse(_SDKBase):
    """Response from ``POST /sdk/v1/search`` — raw vector search."""

    results: list[dict[str, Any]] = Field(default_factory=list, description="Search results with relevance")
    total_results: int = Field(default=0, ge=0, description="Total results returned")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Average relevance")


class SDKPluginListResponse(_SDKBase):
    """Response from ``GET /sdk/v1/plugins``."""

    plugins: list[dict[str, Any]] = Field(default_factory=list, description="Loaded plugins with status")
    total: int = Field(default=0, ge=0, description="Total plugin count")
