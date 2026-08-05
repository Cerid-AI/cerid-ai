# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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


class SDKSearchRequest(BaseModel):
    """Request body for ``POST /sdk/v1/search`` — single-domain KB search."""

    query: str = Field(description="Search query text")
    domain: str = Field(default="general", description="KB domain/collection to search")
    top_k: int = Field(default=3, ge=1, le=100, description="Maximum results to return")
    exclude_packs: bool = Field(
        default=False,
        description="Drop knowledge-pack chunks from retrieval (personal-first KB search).",
    )


class SDKSearchResponse(_SDKBase):
    """Response from ``POST /sdk/v1/search`` — canonical KB search projection."""

    results: list[dict[str, Any]] = Field(default_factory=list, description="Result chunks with relevance + metadata")
    total_results: int = Field(default=0, ge=0, description="Number of results returned")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Average relevance of returned results")


def _default_hallucination_summary() -> dict[str, float | int]:
    """Zero-count summary used when verification is skipped."""
    return {"total": 0, "verified": 0, "unverified": 0, "uncertain": 0}


class SDKHallucinationResponse(_SDKBase):
    """Response from ``POST /sdk/v1/hallucination`` — claim verification."""

    conversation_id: str = Field(default="", description="Conversation identifier")
    timestamp: str = Field(default="", description="ISO 8601 verification timestamp")
    skipped: bool = Field(default=False, description="True if verification was skipped (response too short or no claims)")
    reason: str | None = Field(default=None, description="Reason verification was skipped, if applicable")
    claims: list[dict[str, Any]] = Field(default_factory=list, description="Verified claims with status, confidence, and source info")
    summary: dict[str, float | int] = Field(
        default_factory=_default_hallucination_summary,
        description="Verification summary: per-status counts plus assessed count and the float overall_confidence.",
    )
    mode: str = Field(
        default="thorough",
        description=(
            "Verification depth that was actually applied. ``fast`` returns "
            "extracted claims with status='uncertain' and ``nli_skipped=true`` "
            "without paying NLI cost; ``thorough`` runs the full cross-model "
            "pipeline. Set on the request via ``HallucinationCheckRequest.mode``."
        ),
    )
    nli_skipped: bool = Field(
        default=False,
        description=(
            "True when cross-model NLI verification was skipped (fast mode). "
            "Consumers can use this to decide whether to display a hedged "
            "warning vs. an authoritative verdict."
        ),
    )


class SDKMemoryExtractResponse(_SDKBase):
    """Response from ``POST /sdk/v1/memory/extract`` — memory extraction and storage."""

    conversation_id: str = Field(default="", description="Conversation identifier")
    timestamp: str = Field(default="", description="ISO 8601 extraction timestamp")
    memories_extracted: int = Field(default=0, ge=0, description="Number of memories extracted from text")
    memories_stored: int = Field(default=0, ge=0, description="Number successfully stored in KB")
    skipped_duplicates: int = Field(default=0, ge=0, description="Memories skipped due to deduplication")
    results: list[dict[str, Any]] = Field(default_factory=list, description="Per-memory outcome (status, type, summary)")


class SDKMemoryExtractAcceptedResponse(_SDKBase):
    """202 Accepted envelope when ``MEMORY_QUEUE_MODE=async``.

    Returned from ``POST /sdk/v1/memory/extract`` when the request was
    enqueued for background processing. Callers poll
    ``GET /sdk/v1/memory/extract/jobs/{job_id}`` to retrieve the
    completion result. Falls back to the synchronous
    ``SDKMemoryExtractResponse`` when ``?wait=true`` is set or the
    queue isn't enabled.
    """

    job_id: str = Field(description="RQ job identifier — opaque to callers, use as-is in the status URL")
    status: str = Field(default="queued", description="Initial job status — always 'queued' on accept")
    status_url: str = Field(
        description="Path to GET for the job result. Relative to the SDK base URL.",
    )
    conversation_id: str = Field(default="", description="Mirrors the request field for client-side correlation")


class SDKMemoryExtractJobStatus(_SDKBase):
    """Status envelope for ``GET /sdk/v1/memory/extract/jobs/{job_id}``.

    ``status`` transitions: ``queued → started → finished`` (success) or
    ``queued → started → failed`` (worker error). ``result`` is populated
    only when ``status='finished'``; ``error`` is populated only when
    ``status='failed'``.
    """

    job_id: str = Field(description="RQ job identifier")
    status: str = Field(
        description=(
            "Job lifecycle state — one of: queued | started | finished | "
            "failed | deferred | scheduled | canceled | unknown"
        ),
    )
    enqueued_at: str | None = Field(default=None, description="ISO 8601 timestamp when the job was accepted")
    started_at: str | None = Field(default=None, description="ISO 8601 timestamp when the worker began processing")
    ended_at: str | None = Field(default=None, description="ISO 8601 timestamp on completion (success or failure)")
    result: SDKMemoryExtractResponse | None = Field(
        default=None,
        description="Worker output — populated only when status='finished'",
    )
    error: str | None = Field(
        default=None,
        description="Worker exception summary — populated only when status='failed'",
    )


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
        description=(
            "Task category: chat | internal | verification | classification | research. "
            "Clients may also pass custom values (e.g. 'gtm_creative', 'agent_phase_2'); "
            "unknown values map to safe internal routing."
        ),
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
    slo_budget_ms: int | None = Field(
        default=None, ge=100, le=600_000,
        description=(
            "Optional wall-clock budget in milliseconds. The smart_router "
            "filters tiers by their empirical p95 latency profile; if no "
            "tier fits the budget the response is HTTP 503 with a "
            "Retry-After header carrying the floor p95. Lets fast-path "
            "consumers (e.g. trading-agent) fail-fast and route to direct "
            "providers instead of waiting on a slow tier."
        ),
    )


class SDKLLMCompleteResponse(_SDKBase):
    """Response from ``POST /sdk/v1/llm/complete``."""

    content: str = Field(description="LLM-generated text content")
    model: str = Field(description="Model ID actually used (after smart-routing)")
    provider: str = Field(
        description=(
            "Routing provider label: ollama | quenchforge | openrouter_paid "
            "(legacy openrouter_free stamp removed by CR-027 cost honesty)"
        ),
    )
    reason: str = Field(default="", description="Why this model was selected")
    estimated_cost_per_1k: float = Field(
        default=0.0, ge=0.0,
        description="Estimated cost in USD per 1K tokens (0 for free / Ollama)",
    )
    tier_p95_ms: int = Field(
        default=0, ge=0,
        description=(
            "Empirical p95 wall-clock for the tier this call was routed to. "
            "0 when the tier has no measured profile yet. Useful for client-"
            "side adaptive timeout tuning."
        ),
    )
