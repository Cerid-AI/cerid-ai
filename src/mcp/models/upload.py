# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Typed response models for the ``/upload`` and ``/archive`` endpoints."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "UploadResponse",
    "SupportedExtensionsResponse",
    "ArchiveFileItem",
    "ArchiveFilesResponse",
]


class _UploadBase(BaseModel):
    """Base for all upload response models — allows extra fields for forward compat."""

    model_config = ConfigDict(extra="allow")


class UploadResponse(_UploadBase):
    """Response from ``POST /upload`` — file ingestion result."""

    status: str = Field(default="ok", description="Ingestion status")
    artifact_id: str = Field(default="", description="UUID of created artifact")
    chunks: int = Field(default=0, ge=0, description="Number of chunks created")
    domain: str = Field(default="", description="Domain the content was ingested into")
    filename: str = Field(default="", description="Original upload filename")
    categorize_mode: str = Field(default="smart", description="Categorization mode used")
    metadata: dict[str, Any] = Field(default_factory=dict, description="File metadata")


class SupportedExtensionsResponse(_UploadBase):
    """Response from ``GET /upload/supported``."""

    extensions: list[str] = Field(default_factory=list, description="Supported file extensions")
    count: int = Field(default=0, ge=0, description="Number of supported extensions")


class ArchiveFileItem(_UploadBase):
    """A single file in the archive directory."""

    filename: str = Field(description="File name")
    domain: str = Field(description="Domain folder")
    size: int = Field(default=0, ge=0, description="File size in bytes")
    path: str = Field(description="Relative path from archive root")


class ArchiveFilesResponse(_UploadBase):
    """Response from ``GET /archive/files``."""

    files: list[ArchiveFileItem] = Field(default_factory=list, description="Archive files")
    total: int = Field(default=0, ge=0, description="Total file count")
    storage_mode: str = Field(default="", description="Current storage mode")
