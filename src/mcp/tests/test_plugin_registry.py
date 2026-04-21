# Copyright (c) 2026 Cerid AI. Apache-2.0 license.
"""Tests for PluginRegistryClient — community plugin registry HTTP client."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.plugin_registry import PluginRegistryClient

# Sample registry data
_SAMPLE_REGISTRY = [
    {
        "name": "github-issues",
        "version": "0.1.0",
        "type": "connector",
        "description": "Search GitHub Issues",
        "tags": ["github", "issues"],
    },
    {
        "name": "slack-search",
        "version": "0.2.0",
        "type": "connector",
        "description": "Search Slack messages",
        "tags": ["slack", "messaging"],
    },
    {
        "name": "sentiment-tool",
        "version": "1.0.0",
        "type": "tool",
        "description": "Sentiment analysis tool",
        "tags": ["nlp", "sentiment"],
    },
]


@pytest.fixture
def client():
    return PluginRegistryClient(url="https://example.com/registry.json")


class TestFetchRegistry:

    @pytest.mark.asyncio
    async def test_fetch_parses_json_list(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _SAMPLE_REGISTRY
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_resp
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            result = await client.fetch_registry()
            assert len(result) == 3
            assert result[0]["name"] == "github-issues"

    @pytest.mark.asyncio
    async def test_fetch_handles_dict_wrapper(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"plugins": _SAMPLE_REGISTRY}
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_resp
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            result = await client.fetch_registry()
            assert len(result) == 3

    @pytest.mark.asyncio
    async def test_cache_prevents_refetch(self, client):
        """Second call within TTL returns cached data without HTTP request."""
        client._cache = _SAMPLE_REGISTRY
        client._cache_ts = time.monotonic()  # fresh

        result = await client.fetch_registry()
        assert len(result) == 3  # served from cache, no HTTP call needed

    @pytest.mark.asyncio
    async def test_network_error_returns_stale_cache(self, client):
        client._cache = _SAMPLE_REGISTRY
        client._cache_ts = 0.0  # expired

        with patch("httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.get.side_effect = Exception("network error")
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            result = await client.fetch_registry()
            assert len(result) == 3  # stale cache returned

    @pytest.mark.asyncio
    async def test_network_error_empty_cache_returns_empty(self, client):
        client._cache = []
        client._cache_ts = 0.0

        with patch("httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.get.side_effect = Exception("offline")
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            result = await client.fetch_registry()
            assert result == []


class TestSearch:

    @pytest.mark.asyncio
    async def test_filter_by_type(self, client):
        client._cache = _SAMPLE_REGISTRY
        client._cache_ts = time.monotonic()

        results = await client.search(plugin_type="tool")
        assert len(results) == 1
        assert results[0]["name"] == "sentiment-tool"

    @pytest.mark.asyncio
    async def test_filter_by_query(self, client):
        client._cache = _SAMPLE_REGISTRY
        client._cache_ts = time.monotonic()

        results = await client.search(query="slack")
        assert len(results) == 1
        assert results[0]["name"] == "slack-search"

    @pytest.mark.asyncio
    async def test_combined_filter(self, client):
        client._cache = _SAMPLE_REGISTRY
        client._cache_ts = time.monotonic()

        results = await client.search(query="search", plugin_type="connector")
        assert len(results) == 2  # github-issues + slack-search


class TestGetPlugin:

    @pytest.mark.asyncio
    async def test_found(self, client):
        client._cache = _SAMPLE_REGISTRY
        client._cache_ts = time.monotonic()

        result = await client.get_plugin("slack-search")
        assert result is not None
        assert result["version"] == "0.2.0"

    @pytest.mark.asyncio
    async def test_not_found(self, client):
        client._cache = _SAMPLE_REGISTRY
        client._cache_ts = time.monotonic()

        result = await client.get_plugin("nonexistent")
        assert result is None
