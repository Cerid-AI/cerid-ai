# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Web search as a first-class external data source (Phase 2b slice 4b).

Bridges the web_search providers (Tavily / SearXNG) into the same external-source
registry that CRAG + the retrieval orchestrator query, so live web results are
discounted, merged, and verified through the one canonical external path — no
longer reachable only via the ``pkb_web_search`` MCP tool + a2a.

Self-gating: ``is_configured()`` requires a REAL provider (``TAVILY_API_KEY`` or
``SEARXNG_URL``). The OpenRouter ``:online`` fallback is intentionally excluded
so a default install (no keys) never adds a per-query LLM call to RAG — its
behaviour is unchanged. ``ENABLE_WEB_SEARCH_IN_RAG`` (default on) is the operator
escape hatch to keep web search for explicit ``pkb_web_search`` calls while
keeping it out of the retrieval path.
"""
from __future__ import annotations

import os

from .base import DataSource, DataSourceResult


class WebSearchDataSource(DataSource):
    """Live web search (Tavily → SearXNG) as a retrieval-path external source."""

    name = "web_search"
    description = "Live web search (Tavily or SearXNG) folded into the RAG external path"
    requires_api_key = False  # gating is custom (see is_configured) — any real provider
    domains: list[str] = []  # all domains

    def __init__(self) -> None:
        import config

        # Operator escape hatch: keep web search available for pkb_web_search
        # while excluding it from the retrieval path.
        self.enabled = getattr(config, "ENABLE_WEB_SEARCH_IN_RAG", True)

    def is_configured(self) -> bool:
        """Only participate in RAG when a real provider is set — NOT the
        always-on OpenRouter :online fallback (which would add cost/latency to
        every query on a default install)."""
        return bool(os.getenv("TAVILY_API_KEY") or os.getenv("SEARXNG_URL"))

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        import config
        from utils.web_search import get_search_provider

        provider = get_search_provider()
        max_results = getattr(config, "WEB_SEARCH_MAX_RESULTS", 5)
        results = await provider.search(query, max_results=max_results)
        return [
            DataSourceResult(
                title=r.title,
                content=r.snippet,
                source_url=r.url,
                source_name=f"Web Search ({provider.name})",
                confidence=r.score,
            )
            for r in results
        ]
