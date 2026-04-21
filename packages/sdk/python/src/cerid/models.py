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


class TaxonomyResponse(_SDKBase):
    """Response from ``GET /sdk/v1/taxonomy``."""

    domains: List[str] = Field(default_factory=list)
    taxonomy: Dict[str, Any] = Field(default_factory=dict)


class SettingsResponse(_SDKBase):
    """Response from ``GET /sdk/v1/settings``."""

    version: str = Field(default="")
    tier: str = Field(default="")
    features: Dict[str, bool] = Field(default_factory=dict)


class SearchResponse(_SDKBase):
    """Response from ``POST /sdk/v1/search``."""

    results: List[Dict[str, Any]] = Field(default_factory=list)
    total_results: int = Field(default=0)
    confidence: float = Field(default=0.0)


class PluginListResponse(_SDKBase):
    """Response from ``GET /sdk/v1/plugins``."""

    plugins: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = Field(default=0)
