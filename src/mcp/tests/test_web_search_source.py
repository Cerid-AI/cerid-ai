# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase 2b slice 4b — web search as a first-class retrieval-path external source."""

import pytest

from app.data_sources.web_search_source import WebSearchDataSource
from utils.web_search import WebSearchResult


class TestGating:
    def test_not_configured_without_provider(self, monkeypatch):
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        monkeypatch.delenv("SEARXNG_URL", raising=False)
        assert WebSearchDataSource().is_configured() is False

    def test_configured_with_tavily(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-x")
        monkeypatch.delenv("SEARXNG_URL", raising=False)
        assert WebSearchDataSource().is_configured() is True

    def test_configured_with_searxng(self, monkeypatch):
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        monkeypatch.setenv("SEARXNG_URL", "http://localhost:8080")
        assert WebSearchDataSource().is_configured() is True

    def test_enabled_reflects_flag(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ENABLE_WEB_SEARCH_IN_RAG", False, raising=False)
        assert WebSearchDataSource().enabled is False
        monkeypatch.setattr(config, "ENABLE_WEB_SEARCH_IN_RAG", True, raising=False)
        assert WebSearchDataSource().enabled is True


class TestQueryMapping:
    @pytest.mark.asyncio
    async def test_maps_websearchresult_to_datasourceresult(self, monkeypatch):
        class _FakeProvider:
            name = "tavily"

            async def search(self, query, max_results=5):
                return [
                    WebSearchResult(
                        title="Result 1",
                        url="https://example.com/1",
                        snippet="a snippet",
                        score=0.7,
                        published_date="2026-01-01",
                    ),
                ]

        monkeypatch.setattr("utils.web_search.get_search_provider", lambda: _FakeProvider())
        results = await WebSearchDataSource().query("what is x")
        assert len(results) == 1
        r = results[0]
        assert r.title == "Result 1"
        assert r.content == "a snippet"        # snippet → content
        assert r.source_url == "https://example.com/1"
        assert r.confidence == 0.7             # score → confidence
        assert "tavily" in r.source_name


class TestRegistryIntegration:
    def test_registered(self):
        from app.data_sources import registry
        assert registry.get("web_search") is not None

    def test_excluded_from_enabled_when_unconfigured(self, monkeypatch):
        """A default install (no provider) must NOT query web search in RAG."""
        from app.data_sources import registry
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        monkeypatch.delenv("SEARXNG_URL", raising=False)
        names = [s.name for s in registry.get_enabled_sources()]
        assert "web_search" not in names

    def test_included_when_configured(self, monkeypatch):
        from app.data_sources import registry
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-x")
        names = [s.name for s in registry.get_enabled_sources()]
        assert "web_search" in names
