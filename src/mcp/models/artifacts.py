# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pydantic response models for artifact endpoints."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ArtifactSummary",
    "ArtifactDetailMetadata",
    "ArtifactChunk",
    "ArtifactDetail",
    "RelatedArtifact",
    "RecategorizeResponse",
    "ArtifactFeedbackResponse",
]


class ArtifactSummary(BaseModel):
    """Single artifact in a list response (from Neo4j list_artifacts)."""

    model_config = ConfigDict(extra="allow")

    id: str
    filename: str = ""
    domain: str = ""
    sub_category: str = ""
    tags: str = "[]"
    keywords: str | None = None
    summary: str | None = None
    chunk_count: int | None = None
    chunk_ids: str | None = None
    ingested_at: str | None = None
    recategorized_at: str | None = None
    quality_score: float | None = None
    client_source: str = ""


class ArtifactChunk(BaseModel):
    """A single chunk within an artifact detail response."""

    index: int
    text: str = ""


class ArtifactDetailMetadata(BaseModel):
    """Nested metadata block in artifact detail response."""

    sub_category: str = ""
    tags: str = "[]"
    keywords: str | None = None
    summary: str | None = None
    ingested_at: str | None = None
    recategorized_at: str | None = None


class ArtifactDetail(BaseModel):
    """Full artifact detail response (Neo4j metadata + reassembled chunks)."""

    model_config = ConfigDict(extra="allow")

    artifact_id: str
    title: str = ""
    domain: str = ""
    filename: str = ""
    source_type: str = ""
    chunk_count: int = 0
    total_content: str = ""
    chunks: list[ArtifactChunk] = Field(default_factory=list)
    metadata: ArtifactDetailMetadata = Field(default_factory=ArtifactDetailMetadata)


class RelatedArtifact(BaseModel):
    """Single related artifact from graph traversal."""

    model_config = ConfigDict(extra="allow")

    id: str
    filename: str = ""
    domain: str = ""
    relationship_type: str = ""
    score: float | None = None


class RecategorizeResponse(BaseModel):
    """Response from POST /recategorize."""

    status: str
    artifact_id: str
    old_domain: str
    new_domain: str
    sub_category: str = ""
    chunks_moved: int = 0


class ArtifactFeedbackResponse(BaseModel):
    """Response from POST /artifacts/{id}/feedback."""

    status: str
    artifact_id: str
    signal: str
    old_score: float
    new_score: float
