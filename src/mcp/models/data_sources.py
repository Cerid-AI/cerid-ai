# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Typed response models for the ``/data-sources`` endpoints.

All models use ``ConfigDict(extra="allow")`` for forward compatibility.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BookmarkDetectResponse",
    "BookmarkImportResponse",
    "BookmarkStatusResponse",
    "DataSourceListResponse",
    "DataSourceToggleResponse",
    "RssAddResponse",
    "RssDeleteResponse",
    "RssFeedEntriesResponse",
    "RssFeedListResponse",
    "RssFetchNowResponse",
    "RssPollAllResponse",
]


class _DataSourceBase(BaseModel):
    """Base for all data-source response models."""

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Source listing / toggle
# ---------------------------------------------------------------------------


class DataSourceListResponse(_DataSourceBase):
    """Response from ``GET /data-sources``."""

    sources: list[dict[str, Any]] = Field(default_factory=list, description="Registered data sources")
    total: int = Field(default=0, ge=0, description="Total source count")


class DataSourceToggleResponse(_DataSourceBase):
    """Response from ``POST /data-sources/{name}/enable`` and ``/disable``."""

    status: str = Field(default="", description="New status: 'enabled' or 'disabled'")
    name: str = Field(default="", description="Source name")
    error: str | None = Field(default=None, description="Error message if source not found")


# ---------------------------------------------------------------------------
# Bookmarks
# ---------------------------------------------------------------------------


class BookmarkDetectResponse(_DataSourceBase):
    """Response from ``GET /data-sources/bookmarks/detect``."""

    browsers: list[dict[str, Any]] = Field(default_factory=list, description="Detected browsers with bookmark counts")


class BookmarkImportResponse(_DataSourceBase):
    """Response from ``POST /data-sources/bookmarks/import``."""

    status: str = Field(default="", description="Import status")
    imported: int = Field(default=0, ge=0, description="Number of bookmarks imported")
    skipped: int = Field(default=0, ge=0, description="Number of bookmarks skipped")
    errors: int = Field(default=0, ge=0, description="Number of import errors")


class BookmarkStatusResponse(_DataSourceBase):
    """Response from ``GET /data-sources/bookmarks/status``."""

    last_import: str | None = Field(default=None, description="ISO 8601 timestamp of last import")
    total_imported: int = Field(default=0, ge=0, description="Cumulative imported count")
    total_skipped: int = Field(default=0, ge=0, description="Cumulative skipped count")
    total_errors: int = Field(default=0, ge=0, description="Cumulative error count")


# ---------------------------------------------------------------------------
# RSS / Atom feeds
# ---------------------------------------------------------------------------


class RssAddResponse(_DataSourceBase):
    """Response from ``POST /data-sources/rss``."""

    status: str = Field(default="", description="Operation status (e.g. 'added')")
    feed: dict[str, Any] | None = Field(default=None, description="Created feed record")
    validation: str = Field(default="", description="Feed validation message")
    error: str | None = Field(default=None, description="Error message if validation failed")
    url: str = Field(default="", description="Feed URL")


class RssFeedListResponse(_DataSourceBase):
    """Response from ``GET /data-sources/rss``."""

    feeds: list[dict[str, Any]] = Field(default_factory=list, description="Configured feeds")
    total: int = Field(default=0, ge=0, description="Total feed count")


class RssDeleteResponse(_DataSourceBase):
    """Response from ``DELETE /data-sources/rss/{feed_id}``."""

    status: str = Field(default="", description="Operation status (e.g. 'removed')")
    feed_id: str = Field(default="", description="Removed feed ID")
    error: str | None = Field(default=None, description="Error if feed not found")


class RssFetchNowResponse(_DataSourceBase):
    """Response from ``POST /data-sources/rss/{feed_id}/fetch-now``."""

    status: str = Field(default="", description="Operation status (e.g. 'polled')")
    result: dict[str, Any] | None = Field(default=None, description="Poll result")
    error: str | None = Field(default=None, description="Error if feed not found")


class RssFeedEntriesResponse(_DataSourceBase):
    """Response from ``GET /data-sources/rss/{feed_id}/entries``."""

    entries: list[dict[str, Any]] = Field(default_factory=list, description="Feed entries")
    total: int = Field(default=0, ge=0, description="Total entry count in feed")
    showing: int = Field(default=0, ge=0, description="Number of entries returned")
    note: str = Field(default="", description="Additional info (e.g. '304 Not Modified')")
    error: str | None = Field(default=None, description="Error if feed not found or fetch failed")


class RssPollAllResponse(_DataSourceBase):
    """Response from ``POST /data-sources/rss/poll-all``."""

    status: str = Field(default="", description="Operation status (e.g. 'polled')")
    feeds_polled: int = Field(default=0, ge=0, description="Number of feeds polled")
    total_new_entries: int = Field(default=0, ge=0, description="Total new entries ingested")
    total_errors: int = Field(default=0, ge=0, description="Total errors across feeds")
    results: list[dict[str, Any]] = Field(default_factory=list, description="Per-feed poll results")
