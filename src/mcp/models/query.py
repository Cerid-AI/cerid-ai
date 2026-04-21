# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Typed response models for the ``/query`` endpoint."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "QuerySourceItem",
    "QueryResponse",
]


class _QueryBase(BaseModel):
    """Base for all query response models — allows extra fields for forward compat."""

    model_config = ConfigDict(extra="allow")


class QuerySourceItem(_QueryBase):
    """A single source chunk in the query response."""

    content: str = Field(default="", description="Truncated chunk text (first 200 chars)")
    relevance: float = Field(default=0.0, ge=0.0, le=1.0, description="Hybrid relevance score")
    artifact_id: str = Field(default="", description="Source artifact UUID")
    filename: str = Field(default="", description="Source filename")
    domain: str = Field(default="", description="Knowledge domain")
    chunk_index: int = Field(default=0, ge=0, description="Chunk position within artifact")


class QueryResponse(_QueryBase):
    """Response from ``POST /query`` — RAG context retrieval."""

    context: str = Field(default="", description="Assembled context string for LLM consumption")
    sources: list[QuerySourceItem] = Field(default_factory=list, description="Source chunks with relevance scores")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Average relevance of returned sources")
    timestamp: str = Field(default="", description="ISO 8601 query timestamp")
