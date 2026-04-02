# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the external data source framework."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.circuit_breaker import AsyncCircuitBreaker, CircuitOpenError, CircuitState
from utils.data_sources import registry
from utils.data_sources.base import DataSource, DataSourceRegistry, DataSourceResult
from utils.data_sources.finance import ExchangeRatesSource
from utils.data_sources.wikipedia import WikipediaSource
from utils.data_sources.wolfram import WolframAlphaSource


def test_registry_has_preloaded_sources():
    """Registry should have all preloaded sources."""
    sources = registry.list_sources()
    names = {s["name"] for s in sources}
    assert "wikipedia" in names
    assert "wolfram_alpha" in names
    assert "exchange_rates" in names
    assert "duckduckgo" in names
    assert "openlibrary" in names
    assert "pubchem" in names
    assert len(sources) >= 6


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


# ---------------------------------------------------------------------------
# Circuit breaker integration
# ---------------------------------------------------------------------------


class _StubSource(DataSource):
    """Minimal concrete DataSource for testing."""

    def __init__(self, name: str, results: list[DataSourceResult] | None = None,
                 exc: Exception | None = None, delay: float = 0.0):
        self.name = name
        self.description = f"Stub {name}"
        self.requires_api_key = False
        self.domains: list[str] = []
        self._results = results or []
        self._exc = exc
        self._delay = delay
        self.call_count = 0

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        self.call_count += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._exc:
            raise self._exc
        return self._results


@pytest.mark.asyncio
async def test_circuit_breaker_wraps_queries():
    """Verify data source queries go through circuit breaker by tripping it."""
    test_registry = DataSourceRegistry()
    failing_source = _StubSource("cb_test_fail", exc=RuntimeError("boom"))
    test_registry.register(failing_source)

    from utils.circuit_breaker import get_breaker
    breaker = get_breaker("datasource-cb_test_fail")
    breaker.reset()

    # First 3 calls should go through (circuit breaker failure_threshold=3)
    for _ in range(3):
        results = await test_registry.query_all("test")
        assert results == []

    # After 3 failures the breaker should be OPEN
    assert breaker.state == CircuitState.OPEN

    # Reset call count to verify next call is skipped via circuit breaker
    failing_source.call_count = 0
    results = await test_registry.query_all("test")
    assert results == []
    # The source itself should NOT have been called — breaker blocked it
    assert failing_source.call_count == 0


@pytest.mark.asyncio
async def test_timeout_on_slow_source():
    """A source sleeping 10s should be timed out at 5s (default)."""
    test_registry = DataSourceRegistry()
    slow_source = _StubSource("slow_source", delay=10.0)
    test_registry.register(slow_source)

    from utils.circuit_breaker import get_breaker
    get_breaker("datasource-slow_source").reset()

    start = asyncio.get_event_loop().time()
    results = await test_registry.query_all("test", timeout=0.1)  # Use very short timeout for test speed
    elapsed = asyncio.get_event_loop().time() - start

    assert results == []
    # Should have timed out well before 10s
    assert elapsed < 2.0


@pytest.mark.asyncio
async def test_wikipedia_query_format():
    """Wikipedia source makes the correct API call format."""
    mock_search_resp = MagicMock()
    mock_search_resp.status_code = 200
    mock_search_resp.json.return_value = {
        "query": {"search": [{"title": "Python (programming language)"}]}
    }
    mock_search_resp.raise_for_status = MagicMock()

    mock_summary_resp = MagicMock()
    mock_summary_resp.status_code = 200
    mock_summary_resp.json.return_value = {
        "title": "Python (programming language)",
        "extract": "Python is a high-level programming language.",
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Python"}},
    }

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=[mock_search_resp, mock_summary_resp])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("utils.data_sources.wikipedia.httpx.AsyncClient", return_value=mock_client):
        source = WikipediaSource()
        results = await source.query("Python programming")

    assert len(results) == 1
    assert results[0].title == "Python (programming language)"
    assert "high-level" in results[0].content
    assert results[0].source_name == "Wikipedia"

    # Verify the search API was called with correct params
    first_call = mock_client.get.call_args_list[0]
    assert "en.wikipedia.org/w/api.php" in first_call.args[0]
    assert first_call.kwargs["params"]["srsearch"] == "Python programming"


def test_wolfram_needs_api_key():
    """Wolfram source returns empty when WOLFRAM_APP_ID is not set."""
    source = WolframAlphaSource()
    with patch.dict("os.environ", {}, clear=True):
        assert source.is_configured() is False
        results = asyncio.get_event_loop().run_until_complete(source.query("test"))
        assert results == []


@pytest.mark.asyncio
async def test_query_all_parallel():
    """Sources should run in parallel (timing test)."""
    test_registry = DataSourceRegistry()

    result_a = DataSourceResult("A", "content A", source_name="a")
    result_b = DataSourceResult("B", "content B", source_name="b")

    # Each source takes 0.1s; running in parallel should take ~0.1s total,
    # not 0.2s (sequential).
    source_a = _StubSource("parallel_a", results=[result_a], delay=0.1)
    source_b = _StubSource("parallel_b", results=[result_b], delay=0.1)
    test_registry.register(source_a)
    test_registry.register(source_b)

    from utils.circuit_breaker import get_breaker
    get_breaker("datasource-parallel_a").reset()
    get_breaker("datasource-parallel_b").reset()

    start = asyncio.get_event_loop().time()
    results = await test_registry.query_all("test", timeout=5.0)
    elapsed = asyncio.get_event_loop().time() - start

    assert len(results) == 2
    # Parallel: should take ~0.1s, not ~0.2s. Allow generous margin.
    assert elapsed < 0.5, f"Expected parallel execution but took {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_query_all_partial_failure():
    """2 of 3 sources fail, remaining results are still returned."""
    test_registry = DataSourceRegistry()

    good_result = DataSourceResult("Good", "good content", source_name="good")
    source_good = _StubSource("partial_good", results=[good_result])
    source_fail1 = _StubSource("partial_fail1", exc=RuntimeError("fail1"))
    source_fail2 = _StubSource("partial_fail2", exc=RuntimeError("fail2"))

    test_registry.register(source_good)
    test_registry.register(source_fail1)
    test_registry.register(source_fail2)

    from utils.circuit_breaker import get_breaker
    get_breaker("datasource-partial_good").reset()
    get_breaker("datasource-partial_fail1").reset()
    get_breaker("datasource-partial_fail2").reset()

    results = await test_registry.query_all("test")

    assert len(results) == 1
    assert results[0]["title"] == "Good"
    assert results[0]["content"] == "good content"


def test_source_enable_disable():
    """Enable/disable a source and verify query_all respects the state."""
    test_registry = DataSourceRegistry()

    result = DataSourceResult("Enabled", "enabled content", source_name="toggle")
    source = _StubSource("toggle_test", results=[result])
    test_registry.register(source)

    # Source is enabled by default
    enabled = test_registry.get_enabled_sources()
    assert any(s.name == "toggle_test" for s in enabled)

    # Disable it
    source.enabled = False
    enabled = test_registry.get_enabled_sources()
    assert not any(s.name == "toggle_test" for s in enabled)

    # query_all should skip it
    results = asyncio.get_event_loop().run_until_complete(test_registry.query_all("test"))
    assert results == []

    # Re-enable
    source.enabled = True
    from utils.circuit_breaker import get_breaker
    get_breaker("datasource-toggle_test").reset()
    results = asyncio.get_event_loop().run_until_complete(test_registry.query_all("test"))
    assert len(results) == 1
