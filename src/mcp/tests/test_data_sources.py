# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the external data source framework."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from utils.data_sources import registry
from utils.data_sources.base import DataSourceRegistry
from utils.data_sources.finance import ExchangeRatesSource
from utils.data_sources.wikipedia import WikipediaSource
from utils.data_sources.wolfram import WolframAlphaSource


def test_registry_has_preloaded_sources():
    """Registry should have exactly 3 preloaded sources."""
    sources = registry.list_sources()
    names = {s["name"] for s in sources}
    assert "wikipedia" in names
    assert "wolfram_alpha" in names
    assert "exchange_rates" in names
    assert len(sources) == 3


def test_wikipedia_source_configured():
    """WikipediaSource needs no API key, so is_configured() should be True."""
    source = WikipediaSource()
    assert source.is_configured() is True
    assert source.requires_api_key is False


def test_wolfram_not_configured_without_key():
    """WolframAlphaSource requires WOLFRAM_APP_ID; should be False when unset."""
    source = WolframAlphaSource()
    with patch.dict("os.environ", {}, clear=True):
        assert source.is_configured() is False


def test_exchange_rates_currency_filter():
    """ExchangeRatesSource should only respond to currency-related queries."""
    source = ExchangeRatesSource()

    # Non-currency query returns empty
    result = asyncio.get_event_loop().run_until_complete(source.query("python programming"))
    assert result == []


def test_registry_list_sources():
    """list_sources returns correct metadata for all 3 sources."""
    sources = registry.list_sources()
    by_name = {s["name"]: s for s in sources}

    wiki = by_name["wikipedia"]
    assert wiki["requires_api_key"] is False
    assert wiki["configured"] is True

    wolfram = by_name["wolfram_alpha"]
    assert wolfram["requires_api_key"] is True
    assert wolfram["api_key_env_var"] == "WOLFRAM_APP_ID"  # pragma: allowlist secret

    exchange = by_name["exchange_rates"]
    assert exchange["domains"] == ["finance"]


def test_query_all_handles_failures():
    """query_all should return empty list when all sources raise exceptions."""
    test_registry = DataSourceRegistry()

    class FailSource(WikipediaSource):
        name = "fail_source"
        async def query(self, query: str, **kwargs):
            raise RuntimeError("boom")

    test_registry.register(FailSource())
    results = asyncio.get_event_loop().run_until_complete(test_registry.query_all("test"))
    assert results == []
