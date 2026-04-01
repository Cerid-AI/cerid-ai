# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Base class for external data sources."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger("ai-companion.data_sources")

__all__ = ["DataSource", "DataSourceResult", "DataSourceRegistry"]


class DataSourceResult:
    """A single result from an external data source."""

    def __init__(self, title: str, content: str, source_url: str = "", source_name: str = "", confidence: float = 0.8):
        self.title = title
        self.content = content
        self.source_url = source_url
        self.source_name = source_name
        self.confidence = confidence

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "content": self.content, "source_url": self.source_url, "source_name": self.source_name, "confidence": self.confidence}


class DataSource(ABC):
    """Base class for external data sources."""

    name: str = ""
    description: str = ""
    enabled: bool = True
    requires_api_key: bool = False
    api_key_env_var: str = ""
    domains: list[str] = []  # empty = all domains

    @abstractmethod
    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        """Query this source. Returns results or empty list on failure."""

    def is_configured(self) -> bool:
        """Check if source is properly configured (API key set if required)."""
        if not self.requires_api_key:
            return True
        import os
        return bool(os.getenv(self.api_key_env_var))


class DataSourceRegistry:
    """Registry of all available data sources."""

    def __init__(self) -> None:
        self._sources: dict[str, DataSource] = {}

    def register(self, source: DataSource) -> None:
        self._sources[source.name] = source

    def get_enabled_sources(self, domain: str | None = None) -> list[DataSource]:
        """Get all enabled and configured sources, optionally filtered by domain."""
        return [
            s for s in self._sources.values()
            if s.enabled and s.is_configured()
            and (not domain or not s.domains or domain in s.domains)
        ]

    def has_enabled_sources(self) -> bool:
        return any(s.enabled and s.is_configured() for s in self._sources.values())

    async def query_all(self, query: str, domain: str | None = None) -> list[dict]:
        """Query all enabled sources and return merged results."""
        import asyncio

        sources = self.get_enabled_sources(domain)
        if not sources:
            return []
        tasks = [s.query(query) for s in sources]
        results_lists = await asyncio.gather(*tasks, return_exceptions=True)
        merged = []
        for result_or_exc in results_lists:
            if isinstance(result_or_exc, list):
                merged.extend([r.to_dict() for r in result_or_exc])
            else:
                logger.debug("Data source query failed: %s", result_or_exc)
        return merged

    def list_sources(self) -> list[dict]:
        """List all registered sources with their status."""
        return [
            {"name": s.name, "description": s.description, "enabled": s.enabled,
             "configured": s.is_configured(), "requires_api_key": s.requires_api_key,
             "api_key_env_var": s.api_key_env_var, "domains": s.domains}
            for s in self._sources.values()
        ]


# Module-level singleton
registry = DataSourceRegistry()
