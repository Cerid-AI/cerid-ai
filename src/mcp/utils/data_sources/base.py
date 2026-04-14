# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Base class for external data sources."""
from __future__ import annotations

import asyncio
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

    def adapt_query(self, raw_query: str, keywords: list[str]) -> str:
        """Transform a query for this source's API.

        Subclasses override to produce source-optimal query strings.
        Default: join keywords with spaces (current behavior).
        """
        return " ".join(keywords) if keywords else raw_query

    def is_relevant(self, raw_query: str, keywords: list[str]) -> bool:
        """Quick heuristic: is this source likely relevant to this query?

        Subclasses override for domain-specific pre-filtering.
        Default: True (all sources queried — preserves current behavior).
        """
        return True

    def score_confidence(self, raw_query: str, result: "DataSourceResult") -> float:
        """Adjust result confidence based on query-source fit.

        Subclasses override for source-specific scoring (e.g., boost when
        Wikipedia title matches the query entity, reduce when Wolfram returns
        a non-answer). Default: return the result's original confidence.
        """
        return result.confidence

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

    async def query_all(
        self,
        query: str,
        domain: str | None = None,
        timeout: float = 5.0,
        *,
        raw_query: str | None = None,
        keywords: list[str] | None = None,
    ) -> list[dict]:
        """Query all enabled sources and return merged results.

        Each source query is wrapped in a circuit breaker (``datasource-{name}``)
        and an ``asyncio.wait_for`` timeout to prevent slow sources from blocking
        the entire pipeline.

        When ``raw_query`` and ``keywords`` are provided, each source's
        ``adapt_query()`` produces a source-optimised query string and
        ``is_relevant()`` can skip irrelevant sources entirely.  If omitted,
        ``query`` is passed through unchanged (backward-compatible).
        """
        from utils.circuit_breaker import CircuitOpenError, get_breaker

        sources = self.get_enabled_sources(domain)
        if not sources:
            logger.info(
                "query_all: no enabled sources (domain=%s, registered=%d, enabled=%d, configured=%d)",
                domain,
                len(self._sources),
                sum(1 for s in self._sources.values() if s.enabled),
                sum(1 for s in self._sources.values() if s.enabled and s.is_configured()),
            )
            return []

        # Pre-filter: skip sources that declare themselves irrelevant.
        # Always call is_relevant — it receives both raw_query and keywords
        # so sources like Wolfram can check the raw query for math intent
        # even when keyword extraction produced an empty list.
        _rq = raw_query or query
        _kw = keywords or []
        relevant = [s for s in sources if s.is_relevant(_rq, _kw)]
        skipped = len(sources) - len(relevant)
        if skipped:
            logger.info(
                "query_all: %d/%d sources skipped by is_relevant (domain=%s)",
                skipped, len(sources), domain,
            )
        sources = relevant
        if not sources:
            return []

        logger.info(
            "query_all: querying %d sources for domain=%s: %s",
            len(sources), domain, [s.name for s in sources],
        )

        async def _guarded_query(source: DataSource) -> list[DataSourceResult]:
            # Always call adapt_query — it decides whether to use raw or keywords
            adapted = source.adapt_query(_rq, _kw)
            breaker = get_breaker(f"datasource-{source.name}")
            try:
                results = await breaker.call(
                    lambda _q=adapted: asyncio.wait_for(source.query(_q), timeout=timeout),
                )
                logger.info("Data source %s returned %d results", source.name, len(results))
                return results
            except CircuitOpenError:
                logger.warning("Data source %s circuit OPEN — skipping (breaker tripped)", source.name)
                return []
            except asyncio.TimeoutError:
                logger.warning("Data source %s timed out after %.1fs", source.name, timeout)
                return []

        tasks = [_guarded_query(s) for s in sources]
        results_lists = await asyncio.gather(*tasks, return_exceptions=True)
        merged = []
        for i, result_or_exc in enumerate(results_lists):
            if isinstance(result_or_exc, list):
                source = sources[i]
                for r in result_or_exc:
                    r.confidence = source.score_confidence(_rq, r)
                    merged.append(r.to_dict())
            else:
                logger.warning("Data source %s query failed: %s", sources[i].name, result_or_exc)
        logger.info("query_all: merged %d results from %d sources", len(merged), len(sources))
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
