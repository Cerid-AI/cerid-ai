# Copyright (c) 2026 Justin Michaels. All rights reserved.
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
# LLM completion endpoint (smart-routed)
# ---------------------------------------------------------------------------


class SDKLLMCompleteRequest(BaseModel):
    """Request for ``POST /sdk/v1/llm/complete`` — smart-routed LLM completion.

    Consumers describe the task; the smart_router selects the appropriate
    model tier (FREE / CHEAP / CAPABLE / RESEARCH / EXPERT) based on
    task_type, complexity, and cost_sensitivity. Falls back to Ollama
    when available and suitable.
    """

    messages: list[dict[str, str]] = Field(
        description="OpenAI-format messages: [{role, content}, ...]",
    )
    task_type: str = Field(
        default="internal",
        description="Task category: chat | internal | verification | classification | research",
    )
    query: str = Field(
        default="",
        description="Optional query summary for the router's complexity classifier",
    )
    cost_sensitivity: str = Field(
        default="medium",
        description="Cost preference: low (cheapest) | medium | high (best quality)",
    )
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(default=500, ge=1, le=8000)
    response_format: dict[str, Any] | None = Field(
        default=None,
        description="OpenAI-compatible response format spec (e.g., {'type': 'json_object'})",
    )


class SDKLLMCompleteResponse(_SDKBase):
    """Response from ``POST /sdk/v1/llm/complete``."""

    content: str = Field(description="LLM-generated text content")
    model: str = Field(description="Model ID actually used (after smart-routing)")
    provider: str = Field(description="Routing provider: ollama | openrouter_free | openrouter_paid")
    reason: str = Field(default="", description="Why this model was selected")
    estimated_cost_per_1k: float = Field(
        default=0.0, ge=0.0,
        description="Estimated cost in USD per 1K tokens (0 for free / Ollama)",
    )
