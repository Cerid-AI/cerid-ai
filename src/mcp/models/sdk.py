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
# Trading endpoints
# ---------------------------------------------------------------------------


class SDKTradingSignalResponse(_SDKBase):
    """Response from ``POST /sdk/v1/trading/signal`` — signal enrichment via KB."""

    answer: str = Field(default="", description="Enrichment context from KB matching documents")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="KB match confidence")
    sources: list[str] = Field(default_factory=list, description="Source artifact names")
    historical_trades: list[dict[str, Any]] = Field(default_factory=list, description="Past trade outcomes for similar signals")
    domains_searched: list[str] = Field(default_factory=list, description="Domains searched for enrichment")


class SDKHerdDetectResponse(_SDKBase):
    """Response from ``POST /sdk/v1/trading/herd-detect`` — herd behavior detection."""

    violations: list[dict[str, Any]] = Field(default_factory=list, description="Correlation graph violations (asset, prob_sum, severity)")
    historical_matches: list[dict[str, Any]] = Field(default_factory=list, description="Historical herd events from Neo4j")
    sentiment_extreme: bool = Field(default=False, description="Whether sentiment is at extreme levels")


class SDKKellySizeResponse(_SDKBase):
    """Response from ``POST /sdk/v1/trading/kelly-size`` — Kelly criterion sizing."""

    kelly_fraction: float = Field(default=0.0, description="Recommended position fraction (capped at 0.25)")
    cv_edge: float = Field(default=0.0, description="Coefficient of variation edge from KB history")
    kelly_raw: float = Field(default=0.0, description="Raw Kelly fraction before cap")
    strategy: str = Field(default="", description="Strategy name for this sizing")


class SDKCascadeConfirmResponse(_SDKBase):
    """Response from ``POST /sdk/v1/trading/cascade-confirm`` — cascade pattern confirmation."""

    confirmation_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Cascade confirmation confidence")
    historical_cascades: int = Field(default=0, ge=0, description="Number of historical cascade events found")
    match_quality: str = Field(default="no_history", description="Match quality: no_history, no_profitable_history, good, limited, error")


class SDKLongshotSurfaceResponse(_SDKBase):
    """Response from ``POST /sdk/v1/trading/longshot-surface`` — calibration surface query."""

    calibration_points: list[dict[str, Any]] = Field(default_factory=list, description="Calibration records (market_id, implied_prob, actual_outcome, timestamp)")
    count: int = Field(default=0, ge=0, description="Number of calibration points returned")
    asset: str = Field(default="", description="Asset queried")
    date_range: str = Field(default="", description="Date range queried (ISO format)")
