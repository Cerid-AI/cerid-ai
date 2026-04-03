# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pydantic response models for ingestion endpoints."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "IngestResponse",
    "IngestFileResponse",
    "IngestBatchResponse",
]


class NearDuplicateInfo(BaseModel):
    """Near-duplicate detection result attached to ingest responses."""

    artifact_id: str
    filename: str = ""
    similarity: float = 0.0


class RelatedIngestArtifact(BaseModel):
    """Related artifact discovered during ingestion."""

    id: str
    filename: str = ""
    domain: str = ""
    relationship_type: str = ""


class IngestResponse(BaseModel):
    """Response from POST /ingest (content ingestion).

    The shape varies by status (success/duplicate/error) but all fields
    are optional to accommodate all variants via a single model.
    """

    model_config = ConfigDict(extra="allow")

    status: str
    artifact_id: str | None = None
    domain: str = ""
    chunks: int = 0
    timestamp: str | None = None
    # Success-only fields
    relationships_created: int = 0
    related: list[RelatedIngestArtifact] = Field(default_factory=list)
    # Duplicate-only fields
    duplicate_of: str | None = None
    # Near-duplicate info
    near_duplicate_of: NearDuplicateInfo | None = None
    # Error-only fields
    error: str | None = None
    # Skipped (triage)
    reason: str | None = None


class IngestFileMetadata(BaseModel):
    """Metadata block returned with ingest_file responses."""

    model_config = ConfigDict(extra="allow")

    filename: str = ""
    domain: str = ""
    sub_category: str = ""
    keywords_json: str | None = None
    summary: str | None = None
    tags_json: str | None = None
    file_type: str = ""
    estimated_tokens: int | None = None


class IngestFileResponse(IngestResponse):
    """Response from POST /ingest_file (file ingestion).

    Extends IngestResponse with file-specific fields.
    """

    filename: str = ""
    categorize_mode: str = ""
    metadata: IngestFileMetadata = Field(default_factory=IngestFileMetadata)


class IngestBatchItemResult(BaseModel):
    """Individual result within a batch ingest response."""

    model_config = ConfigDict(extra="allow")

    status: str = ""
    artifact_id: str | None = None
    domain: str = ""
    chunks: int = 0
    error: str | None = None
    file_path: str | None = None
    filename: str | None = None


class IngestBatchResponse(BaseModel):
    """Response from POST /ingest_batch."""

    results: list[dict[str, Any]] = Field(default_factory=list)
    succeeded: int = 0
    failed: int = 0
