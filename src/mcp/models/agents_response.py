# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Typed response models for the ``/agent/*`` endpoints in ``routers/agents.py``.

These models annotate ``response_model`` on user-facing agent endpoints so
that OpenAPI schemas are generated automatically and outbound payloads get
runtime validation.

All models use ``ConfigDict(extra="allow")`` for forward compatibility —
agents may evolve their return shapes without requiring a schema migration.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AgentQueryResponse",
    "CompressResponse",
    "HallucinationCheckResponse",
    "HallucinationReport",
    "MemoryArchiveResponse",
    "MemoryExtractionResponse",
    "MemoryItem",
    "MemoryRecallResponse",
    "StatusResponse",
    "TriageBatchResponse",
    "TriageResponse",
    "VerificationReportResponse",
    "VerificationSaveResponse",
]


class _AgentResponseBase(BaseModel):
    """Base for all agent response models — allows extra fields for forward compat."""

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


class StatusResponse(_AgentResponseBase):
    """Generic ``{"status": "ok"}`` envelope used by simple acknowledgement endpoints."""

    status: str = Field(description="Operation status (e.g. 'ok', 'saved')")


# ---------------------------------------------------------------------------
# /agent/query
# ---------------------------------------------------------------------------


class AgentQueryResponse(_AgentResponseBase):
    """Response from ``POST /agent/query`` — multi-domain KB search."""

    answer: str = Field(default="", description="Assembled answer / empty when KB-only mode")
    sources: list[dict[str, Any]] = Field(default_factory=list, description="Result chunks with relevance scores")
    context: str = Field(default="", description="Assembled context string from matching chunks")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Average relevance of returned sources")
    domains_searched: list[str] = Field(default_factory=list, description="Domains that were actually searched")
    total_results: int = Field(default=0, ge=0, description="Total results after dedup and filtering")
    token_budget_used: int = Field(default=0, ge=0, description="Character count of assembled context")
    graph_results: int = Field(default=0, ge=0, description="Results contributed by graph expansion")
    results: list[dict[str, Any]] = Field(default_factory=list, description="All results with full metadata")
    private_mode: bool = Field(default=False, description="True when private mode suppressed KB context")


# ---------------------------------------------------------------------------
# /agent/triage
# ---------------------------------------------------------------------------


class TriageResponse(_AgentResponseBase):
    """Response from ``POST /agent/triage`` — single-file triage + ingest."""

    status: str = Field(default="", description="Ingestion status (success, duplicate, skipped, error)")
    artifact_id: str = Field(default="", description="UUID of created/updated artifact")
    domain: str = Field(default="", description="Domain the content was ingested into")
    chunks: int = Field(default=0, ge=0, description="Number of chunks created")
    filename: str = Field(default="", description="Original filename")
    categorize_mode: str = Field(default="", description="Categorization mode used")
    triage_status: str = Field(default="", description="Triage agent result status")
    is_structured: bool = Field(default=False, description="Whether the content was identified as structured data")


class TriageBatchResponse(_AgentResponseBase):
    """Response from ``POST /agent/triage/batch`` — batch file triage."""

    total: int = Field(default=0, ge=0, description="Total files processed")
    succeeded: int = Field(default=0, ge=0, description="Files successfully ingested")
    failed: int = Field(default=0, ge=0, description="Files that failed")
    duplicates: int = Field(default=0, ge=0, description="Files skipped as duplicates")
    results: list[dict[str, Any]] = Field(default_factory=list, description="Per-file outcome")


# ---------------------------------------------------------------------------
# /agent/hallucination
# ---------------------------------------------------------------------------


class HallucinationCheckResponse(_AgentResponseBase):
    """Response from ``POST /agent/hallucination`` — claim verification."""

    conversation_id: str = Field(default="", description="Conversation identifier")
    timestamp: str = Field(default="", description="ISO 8601 verification timestamp")
    skipped: bool = Field(default=False, description="True if verification was skipped")
    reason: str | None = Field(default=None, description="Reason verification was skipped")
    claims: list[dict[str, Any]] = Field(default_factory=list, description="Verified claims")
    summary: dict[str, int] = Field(
        default_factory=lambda: {"total": 0, "verified": 0, "unverified": 0, "uncertain": 0},
        description="Claim verification counts by status",
    )


class HallucinationReport(_AgentResponseBase):
    """Response from ``GET /agent/hallucination/{conversation_id}`` — stored report."""

    conversation_id: str = Field(default="", description="Conversation identifier")
    timestamp: str = Field(default="", description="ISO 8601 verification timestamp")
    claims: list[dict[str, Any]] = Field(default_factory=list, description="Verified claims with feedback")
    summary: dict[str, int] = Field(default_factory=dict, description="Claim counts by status")
    model: str | None = Field(default=None, description="Model used for verification")


# ---------------------------------------------------------------------------
# /agent/memory
# ---------------------------------------------------------------------------


class MemoryItem(_AgentResponseBase):
    """A single recalled memory entry."""

    id: str = Field(default="", description="Memory ID")
    text: str = Field(default="", description="Memory text")
    score: float = Field(default=0.0, description="Salience-adjusted score")
    access_count: int = Field(default=0, ge=0, description="Number of times recalled")
    memory_type: str = Field(default="empirical", description="Memory type classification")
    age_days: float = Field(default=0.0, description="Age in days since creation")
    source_authority: float = Field(default=0.7, description="Source authority weight")
    summary: str = Field(default="", description="Memory summary")
    base_similarity: float = Field(default=0.0, description="Raw cosine similarity before adjustments")
    created_at: str = Field(default="", description="ISO 8601 creation timestamp")


class MemoryRecallResponse(_AgentResponseBase):
    """Response from ``POST /agent/memory/recall`` — salience-aware recall."""

    memories: list[MemoryItem] = Field(default_factory=list, description="Recalled memories")
    total_recalled: int = Field(default=0, ge=0, description="Number of memories returned")
    timestamp: str = Field(default="", description="ISO 8601 timestamp")


class MemoryExtractionResponse(_AgentResponseBase):
    """Response from ``POST /agent/memory/extract`` — extract and store memories."""

    conversation_id: str = Field(default="", description="Conversation identifier")
    timestamp: str = Field(default="", description="ISO 8601 extraction timestamp")
    memories_extracted: int = Field(default=0, ge=0, description="Number of memories extracted")
    memories_stored: int = Field(default=0, ge=0, description="Number successfully stored")
    skipped_duplicates: int = Field(default=0, ge=0, description="Duplicates skipped")
    results: list[dict[str, Any]] = Field(default_factory=list, description="Per-memory outcome")
    status: str = Field(default="", description="Operation status")
    reason: str = Field(default="", description="Reason if skipped")


class MemoryArchiveResponse(_AgentResponseBase):
    """Response from ``POST /agent/memory/archive`` — archive old memories."""

    timestamp: str = Field(default="", description="ISO 8601 timestamp")
    retention_days: int = Field(default=180, ge=0, description="Retention threshold in days")
    cutoff_date: str = Field(default="", description="ISO 8601 cutoff date")
    archived_count: int = Field(default=0, ge=0, description="Number of memories archived")
    error: str | None = Field(default=None, description="Error message if archival partially failed")


# ---------------------------------------------------------------------------
# /chat/compress
# ---------------------------------------------------------------------------


class CompressResponse(_AgentResponseBase):
    """Response from ``POST /chat/compress`` — conversation compression."""

    messages: list[dict[str, str]] = Field(default_factory=list, description="Compressed message list")
    original_tokens: int = Field(default=0, ge=0, description="Token estimate before compression")
    compressed_tokens: int = Field(default=0, ge=0, description="Token estimate after compression")


# ---------------------------------------------------------------------------
# /verification
# ---------------------------------------------------------------------------


class VerificationSaveResponse(_AgentResponseBase):
    """Response from ``POST /verification/save``."""

    status: str = Field(description="Save status (e.g. 'saved')")
    report_id: str = Field(default="", description="UUID of saved verification report")


class VerificationReportResponse(_AgentResponseBase):
    """Response from ``GET /verification/{conversation_id}`` — saved report."""

    conversation_id: str = Field(default="", description="Conversation identifier")
    claims: list[dict[str, Any]] = Field(default_factory=list, description="Verified claims")
    overall_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Overall verification score")
    verified: int = Field(default=0, ge=0, description="Number of verified claims")
    unverified: int = Field(default=0, ge=0, description="Number of unverified claims")
    uncertain: int = Field(default=0, ge=0, description="Number of uncertain claims")
    total: int = Field(default=0, ge=0, description="Total claim count")
    created_at: str = Field(default="", description="ISO 8601 creation timestamp")


