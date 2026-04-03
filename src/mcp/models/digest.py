# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Typed response models for the ``/digest`` endpoint."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DigestArtifactItem",
    "DigestArtifacts",
    "DigestRelationships",
    "DigestResponse",
]


class _DigestBase(BaseModel):
    """Base for all digest response models — allows extra fields for forward compat."""

    model_config = ConfigDict(extra="allow")


class DigestArtifactItem(_DigestBase):
    """A recent artifact in the digest."""

    id: str = Field(description="Artifact UUID")
    filename: str = Field(default="", description="Original filename")
    domain: str = Field(default="", description="Knowledge domain")
    summary: str = Field(default="", description="Truncated summary (first 100 chars)")
    ingested_at: str | None = Field(default=None, description="ISO 8601 ingestion timestamp")


class DigestArtifacts(_DigestBase):
    """Artifact summary within the digest."""

    count: int = Field(default=0, ge=0, description="Number of recent artifacts")
    items: list[DigestArtifactItem] = Field(default_factory=list, description="Recent artifact details")
    by_domain: dict[str, int] = Field(default_factory=dict, description="Artifact counts by domain")


class DigestRelationships(_DigestBase):
    """Relationship summary within the digest."""

    new_count: int = Field(default=0, ge=0, description="New relationships in the period")


class DigestResponse(_DigestBase):
    """Response from ``GET /digest`` — activity summary."""

    period_hours: int = Field(default=24, description="Lookback window in hours")
    generated_at: str = Field(default="", description="ISO 8601 generation timestamp")
    artifacts: DigestArtifacts = Field(default_factory=DigestArtifacts, description="Recent artifact summary")
    relationships: DigestRelationships = Field(
        default_factory=DigestRelationships, description="Relationship summary"
    )
    health: dict[str, Any] = Field(default_factory=dict, description="System health snapshot")
    recent_events: int = Field(default=0, ge=0, description="Number of recent activity events")
