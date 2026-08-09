# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pydantic response models mirroring the server-side ``models/sdk.py`` contract.

These models are kept in sync with the server definitions. They use
``extra="allow"`` so that new fields added server-side pass through without
breaking existing consumers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class _SDKBase(BaseModel):
    """Base for all SDK response models -- allows extra fields for forward compat."""

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Core endpoints
# ---------------------------------------------------------------------------


class QueryResponse(_SDKBase):
    """Response from ``POST /sdk/v1/query``."""

    context: str = Field(default="")
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=0.0)
    domains_searched: List[str] = Field(default_factory=list)
    total_results: int = Field(default=0)
    token_budget_used: int = Field(default=0)
    graph_results: int = Field(default=0)
    results: List[Dict[str, Any]] = Field(default_factory=list)


class HallucinationResponse(_SDKBase):
    """Response from ``POST /sdk/v1/hallucination``."""

    conversation_id: str = Field(default="")
    timestamp: str = Field(default="")
    skipped: bool = Field(default=False)
    reason: Optional[str] = Field(default=None)
    claims: List[Dict[str, Any]] = Field(default_factory=list)
    summary: Dict[str, int] = Field(
        default_factory=lambda: {"total": 0, "verified": 0, "unverified": 0, "uncertain": 0},
    )


class MemoryExtractResponse(_SDKBase):
    """Response from ``POST /sdk/v1/memory/extract``."""

    conversation_id: str = Field(default="")
    timestamp: str = Field(default="")
    memories_extracted: int = Field(default=0)
    memories_stored: int = Field(default=0)
    skipped_duplicates: int = Field(default=0)
    results: List[Dict[str, Any]] = Field(default_factory=list)


class HealthResponse(_SDKBase):
    """Response from ``GET /sdk/v1/health``."""

    status: str = Field(default="")
    version: str = Field(default="")
    services: Dict[str, str] = Field(default_factory=dict)
    features: Dict[str, bool] = Field(default_factory=dict)


class DetailedHealthResponse(HealthResponse):
    """Response from ``GET /sdk/v1/health/detailed``."""

    circuit_breakers: Dict[str, str] = Field(default_factory=dict)
    degradation_tier: str = Field(default="FULL")
    uptime_seconds: float = Field(default=0.0)


# ---------------------------------------------------------------------------
# Extended endpoints
# ---------------------------------------------------------------------------


class IngestResponse(_SDKBase):
    """Response from ``POST /sdk/v1/ingest`` and ``POST /sdk/v1/ingest/file``."""

    status: str = Field(default="")
    artifact_id: str = Field(default="")
    chunks: int = Field(default=0)
    domain: str = Field(default="")


class CollectionsResponse(_SDKBase):
    """Response from ``GET /sdk/v1/collections``."""

    collections: List[str] = Field(default_factory=list)
    total: int = Field(default=0)


# The server declares the taxonomy/settings/plugins response models with
# Any-typed fields (app/routers/sdk.py "generated: single-return dict-literal
# routes"), and docs/openapi-sdk-v1.json pins them that way. These SDK models
# must not be stricter than that contract, or a legitimate 200 becomes a
# client-side ValidationError.


class TaxonomyResponse(_SDKBase):
    """Response from ``GET /sdk/v1/taxonomy``."""

    domains: Any = Field(default_factory=list)
    taxonomy: Any = Field(default_factory=dict)


class SettingsResponse(_SDKBase):
    """Response from ``GET /sdk/v1/settings``."""

    version: Any = Field(default="")
    tier: Any = Field(default="")
    features: Any = Field(default_factory=dict)


class SearchResponse(_SDKBase):
    """Response from ``POST /sdk/v1/search``."""

    results: List[Dict[str, Any]] = Field(default_factory=list)
    total_results: int = Field(default=0)
    confidence: float = Field(default=0.0)


class PluginListResponse(_SDKBase):
    """Response from ``GET /sdk/v1/plugins``."""

    # The pin types ``plugins`` as an array (of anything); ``total`` is untyped.
    plugins: List[Any] = Field(default_factory=list)
    total: Any = Field(default=0)


# ---------------------------------------------------------------------------
# Async memory extract — 202 Accepted envelope + poll envelope
# ---------------------------------------------------------------------------


class MemoryExtractAcceptedResponse(_SDKBase):
    """Returned from ``POST /sdk/v1/memory/extract`` when the request was
    enqueued for background processing (``MEMORY_QUEUE_MODE=async``)."""

    job_id: str = Field(default="")
    status: str = Field(default="queued")
    status_url: str = Field(default="")
    conversation_id: str = Field(default="")


class MemoryExtractJobStatus(_SDKBase):
    """Response from ``GET /sdk/v1/memory/extract/jobs/{job_id}``.

    Status transitions: queued → started → finished | failed."""

    job_id: str = Field(default="")
    status: str = Field(default="unknown")
    enqueued_at: Optional[str] = Field(default=None)
    started_at: Optional[str] = Field(default=None)
    ended_at: Optional[str] = Field(default=None)
    result: Optional[MemoryExtractResponse] = Field(default=None)
    error: Optional[str] = Field(default=None)


# ---------------------------------------------------------------------------
# Smart-routed LLM completion
# ---------------------------------------------------------------------------


class LLMCompleteResponse(_SDKBase):
    """Response from ``POST /sdk/v1/llm/complete``."""

    content: str = Field(default="")
    model: str = Field(default="")
    provider: str = Field(default="")
    reason: str = Field(default="")
    estimated_cost_per_1k: float = Field(default=0.0)
    tier_p95_ms: int = Field(default=0)


# ---------------------------------------------------------------------------
# Generic external ingest — adapter-shaped payloads
# ---------------------------------------------------------------------------


class IngestExternalResponse(_SDKBase):
    """Response from ``POST /sdk/v1/ingest/external`` — adapter-shaped ingest."""

    accepted: int = Field(default=0)
    skipped: int = Field(default=0)
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    source_type: str = Field(default="unknown")
