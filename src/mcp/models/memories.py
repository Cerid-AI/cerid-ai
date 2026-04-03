# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Typed response models for the ``/memories`` endpoints."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "MemoryItem",
    "MemoryListResponse",
    "MemoryDeleteResponse",
    "MemoryExtractResponse",
]


class _MemoriesBase(BaseModel):
    """Base for all memory response models — allows extra fields for forward compat."""

    model_config = ConfigDict(extra="allow")


class MemoryItem(_MemoriesBase):
    """A single extracted conversation memory."""

    id: str = Field(description="Memory artifact UUID")
    type: str = Field(default="unknown", description="Memory type: fact, decision, preference, action_item")
    content: str = Field(default="", description="Memory summary text")
    conversation_id: str = Field(default="", description="Conversation prefix this memory was extracted from")
    created_at: str | None = Field(default=None, description="ISO 8601 ingestion timestamp")
    source_filename: str = Field(default="", description="Original Neo4j filename")


class MemoryListResponse(_MemoriesBase):
    """Response from ``GET /memories``."""

    memories: list[MemoryItem] = Field(default_factory=list, description="Memory items for the current page")
    total: int = Field(default=0, ge=0, description="Total matching memories across all pages")
    limit: int = Field(default=50, ge=1, description="Page size")
    offset: int = Field(default=0, ge=0, description="Current offset")


class MemoryDeleteResponse(_MemoriesBase):
    """Response from ``DELETE /memories/{id}``."""

    status: str = Field(description="Deletion status, e.g. 'deleted'")
    memory_id: str = Field(description="ID of the deleted memory")


class MemoryExtractResponse(_MemoriesBase):
    """Response from ``POST /memories/extract``."""

    conversation_id: str = Field(default="", description="Conversation identifier")
    memories_extracted: int = Field(default=0, ge=0, description="Number of memories extracted")
    memories_stored: int = Field(default=0, ge=0, description="Number successfully stored")
    results: list[dict[str, Any]] = Field(default_factory=list, description="Per-memory outcome details")
