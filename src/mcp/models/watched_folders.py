# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Typed response models for the ``/watched-folders`` endpoints."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "WatchedFolderDetail",
    "WatchedFolderListResponse",
    "WatchedFolderStatusResponse",
]


class _WatchedFoldersBase(BaseModel):
    """Base for all watched-folder response models — allows extra fields for forward compat."""

    model_config = ConfigDict(extra="allow")


class WatchedFolderDetail(_WatchedFoldersBase):
    """A watched folder configuration (returned from create, get, list)."""

    id: str = Field(description="Folder identifier")
    path: str = Field(description="Absolute directory path")
    label: str = Field(default="", description="User-friendly display name")
    enabled: bool = Field(default=True, description="Whether the folder is active")
    domain_override: str | None = Field(default=None, description="Forced domain classification")
    exclude_patterns: list[str] = Field(default_factory=list, description="Glob patterns to exclude")
    search_enabled: bool = Field(default=True, description="Include in RAG queries")
    last_scanned_at: str | None = Field(default=None, description="ISO 8601 last scan timestamp")
    stats: dict[str, Any] = Field(default_factory=dict, description="Scan statistics")
    created_at: str = Field(default="", description="ISO 8601 creation timestamp")


class WatchedFolderListResponse(_WatchedFoldersBase):
    """Response from ``GET /watched-folders``."""

    folders: list[WatchedFolderDetail] = Field(default_factory=list, description="All watched folders")
    total: int = Field(default=0, ge=0, description="Total folder count")


class WatchedFolderStatusResponse(_WatchedFoldersBase):
    """Response from ``GET /watched-folders/{id}/status``."""

    id: str = Field(description="Folder identifier")
    path: str = Field(description="Absolute directory path")
    enabled: bool = Field(default=True, description="Whether the folder is active")
    last_scanned_at: str | None = Field(default=None, description="ISO 8601 last scan timestamp")
    stats: dict[str, Any] = Field(default_factory=dict, description="Scan statistics")
