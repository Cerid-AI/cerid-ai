# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the external data source framework."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.data_sources import registry
from app.data_sources.base import DataSource, DataSourceRegistry, DataSourceResult
from app.data_sources.finance import ExchangeRatesSource
from app.data_sources.wikipedia import WikipediaSource
from app.data_sources.wolfram import WolframAlphaSource
from core.utils.circuit_breaker import CircuitState


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
    result = asyncio.run(source.query("python programming"))
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
    results = asyncio.run(test_registry.query_all("test"))
    assert results == []


# ---------------------------------------------------------------------------
# Circuit breaker integration
# ---------------------------------------------------------------------------


class _StubSource(DataSource):
    """Minimal concrete DataSource for testing."""

    def __init__(self, name: str, results: list[DataSourceResult] | None = None,
                 exc: Exception | None = None, delay: float = 0.0,
                 mcp_connector_name: str = ""):
        self.name = name
        self.description = f"Stub {name}"
        self.requires_api_key = False
        self.domains: list[str] = []
        self.mcp_connector_name = mcp_connector_name
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

    from core.utils.circuit_breaker import get_breaker
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
async def test_query_all_skips_open_breaker_without_call(caplog):
    """A source whose MCP connector breaker is OPEN must be skipped without
    an MCP call and without a per-query WARNING — gmail/google_calendar sat
    OPEN for days, logging 160 failed calls/day (3 warnings each) (Task 1)."""
    import logging
    import time

    from core.mcp_clients.client_pool import (
        _FAILURE_THRESHOLD,
        get_pool,
        reset_pool_for_tests,
    )

    reset_pool_for_tests()
    try:
        pool = get_pool()
        pool.register("google_workspace", "http://sibling:8080/mcp")
        state = pool._connectors["google_workspace"]
        state.opened_at = time.monotonic()
        state.failures = _FAILURE_THRESHOLD

        test_registry = DataSourceRegistry()
        source = _StubSource(
            "mcp_source",
            results=[DataSourceResult("T", "C", source_name="mcp_source")],
            mcp_connector_name="google_workspace",
        )
        test_registry.register(source)

        results = await test_registry.query_all("test")
        assert results == []
        assert source.call_count == 0

        # A second query while the breaker is still open must not re-log —
        # the breaker itself already logged once when it opened.
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            results2 = await test_registry.query_all("test")
        assert results2 == []
        assert source.call_count == 0
        assert not any(r.levelno >= logging.WARNING for r in caplog.records)
    finally:
        reset_pool_for_tests()


@pytest.mark.asyncio
async def test_query_all_skips_real_gmail_source_when_breaker_open(monkeypatch):
    """The real GmailDataSource (not a stub) must never reach the MCP
    connector pool while google_workspace's breaker is OPEN — this is the
    class that actually produced the 160 failed calls/day evidence."""
    import time

    from core.mcp_clients.client_pool import (
        _FAILURE_THRESHOLD,
        get_pool,
        reset_pool_for_tests,
    )
    from plugins.gmail.data_source import GmailDataSource

    monkeypatch.setenv("CERID_CONNECTORS_BEARER", "tok")  # pragma: allowlist secret
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "id")
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "someone@example.com")

    reset_pool_for_tests()
    try:
        pool = get_pool()
        pool.register("google_workspace", "http://sibling:8080/mcp")
        state = pool._connectors["google_workspace"]
        state.opened_at = time.monotonic()
        state.failures = _FAILURE_THRESHOLD

        test_registry = DataSourceRegistry()
        test_registry.register(GmailDataSource())

        with patch.object(pool, "call_tool", new_callable=AsyncMock) as mock_call_tool:
            results = await test_registry.query_all("hello")

        assert results == []
        mock_call_tool.assert_not_called()
    finally:
        reset_pool_for_tests()


def test_query_all_default_timeout_is_external_source_timeout():
    """query_all's default per-source timeout is the shared external-source
    budget, not a locally hardcoded 5.0s (Task 1)."""
    import inspect

    from config.constants import EXTERNAL_SOURCE_QUERY_TIMEOUT

    sig = inspect.signature(DataSourceRegistry.query_all)
    assert sig.parameters["timeout"].default == EXTERNAL_SOURCE_QUERY_TIMEOUT


@pytest.mark.asyncio
async def test_timeout_on_slow_source():
    """A source sleeping 10s should be timed out at 5s (default)."""
    test_registry = DataSourceRegistry()
    slow_source = _StubSource("slow_source", delay=10.0)
    test_registry.register(slow_source)

    from core.utils.circuit_breaker import get_breaker
    get_breaker("datasource-slow_source").reset()

    start = asyncio.get_running_loop().time()
    results = await test_registry.query_all("test", timeout=0.1)  # Use very short timeout for test speed
    elapsed = asyncio.get_running_loop().time() - start

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
        # Needs >=50 chars to pass the WikipediaSource stub-filter —
        # real Wikipedia summaries are typically 200-400 chars.
        "extract": (
            "Python is a high-level, general-purpose programming language "
            "that emphasizes code readability with the use of significant "
            "indentation."
        ),
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Python"}},
    }

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=[mock_search_resp, mock_summary_resp])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.data_sources.wikipedia.httpx.AsyncClient", return_value=mock_client):
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


@pytest.mark.asyncio
async def test_wikipedia_drops_stub_results():
    """Regression: short (< _MIN_CONTENT_LEN) Wikipedia summaries must be
    dropped at the source, not scored and forwarded to NLI. Stubs add no
    evidence value and used to slide past the -0.15 disambiguation penalty.
    """
    mock_search_resp = MagicMock()
    mock_search_resp.status_code = 200
    mock_search_resp.json.return_value = {
        "query": {"search": [
            {"title": "Real Article"},
            {"title": "Stub Article"},
        ]}
    }
    mock_search_resp.raise_for_status = MagicMock()

    real_summary = MagicMock()
    real_summary.status_code = 200
    real_summary.json.return_value = {
        "title": "Real Article",
        "extract": (
            "This is a long enough extract that should survive the stub "
            "filter since it contains more than fifty characters of content."
        ),
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Real"}},
    }
    stub_summary = MagicMock()
    stub_summary.status_code = 200
    stub_summary.json.return_value = {
        "title": "Stub Article",
        "extract": "Too short.",  # 10 chars — way under the 50-char floor
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Stub"}},
    }

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=[mock_search_resp, real_summary, stub_summary])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.data_sources.wikipedia.httpx.AsyncClient", return_value=mock_client):
        source = WikipediaSource()
        results = await source.query("anything")

    titles = [r.title for r in results]
    assert "Real Article" in titles, "full-length extract should pass"
    assert "Stub Article" not in titles, (
        "Stub summary (len < WikipediaSource._MIN_CONTENT_LEN) slipped through the filter"
    )


@pytest.mark.asyncio
async def test_wikipedia_failure_routes_through_log_swallowed_error(monkeypatch):
    """Regression: Wikipedia HTTP failures must surface at /health.swallowed_errors_last_hour
    via log_swallowed_error, not vanish into logger.debug."""
    captured: list = []

    def fake_log(module: str, exc: Exception, **kwargs):
        captured.append((module, type(exc).__name__, str(exc), kwargs))

    monkeypatch.setattr(
        "app.data_sources.wikipedia.log_swallowed_error",
        fake_log,
    )

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("DNS lookup failed"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.data_sources.wikipedia.httpx.AsyncClient", return_value=mock_client):
        source = WikipediaSource()
        results = await source.query("anything")

    assert results == []
    assert len(captured) == 1
    module, exc_name, _, kwargs = captured[0]
    assert module == "app.data_sources.wikipedia.query"
    assert exc_name == "ConnectError"
    assert "context" in kwargs and kwargs["context"].get("query") == "anything"


def test_wolfram_needs_api_key():
    """Wolfram source returns empty when WOLFRAM_APP_ID is not set."""
    source = WolframAlphaSource()
    with patch.dict("os.environ", {}, clear=True):
        assert source.is_configured() is False
        results = asyncio.run(source.query("test"))
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

    from core.utils.circuit_breaker import get_breaker
    get_breaker("datasource-parallel_a").reset()
    get_breaker("datasource-parallel_b").reset()

    start = asyncio.get_running_loop().time()
    results = await test_registry.query_all("test", timeout=5.0)
    elapsed = asyncio.get_running_loop().time() - start

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

    from core.utils.circuit_breaker import get_breaker
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
    results = asyncio.run(test_registry.query_all("test"))
    assert results == []

    # Re-enable
    source.enabled = True
    from core.utils.circuit_breaker import get_breaker
    get_breaker("datasource-toggle_test").reset()
    results = asyncio.run(test_registry.query_all("test"))
    assert len(results) == 1


class TestEmailStatusReadFailure:
    """A failed status read must not render as a healthy zero state.

    The router's ``@handle_errors`` fallback returned
    ``{"last_poll": None, "messages_ingested": 0, "errors": []}`` — the
    exact shape of a correctly-configured mailbox that has not polled yet,
    with the one field that would reveal the problem emptied.
    """

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.routers.data_sources import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app, raise_server_exceptions=False)

    def test_status_read_failure_is_marked(self, monkeypatch):
        import app.data_sources.email_imap as email_imap

        async def _boom():
            raise RuntimeError("redis connection reset")

        monkeypatch.setattr(email_imap, "get_email_status", _boom)

        resp = self._client().get("/data-sources/email/status")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status_read_failed"] is True
        assert body["errors"], "a failed read must not report an empty error list"

    def test_a_healthy_read_is_not_marked(self, monkeypatch):
        import app.data_sources.email_imap as email_imap

        async def _status():
            return {
                "last_poll": None,
                "messages_ingested": 0,
                "errors": [],
                "configured": True,
            }

        monkeypatch.setattr(email_imap, "get_email_status", _status)

        body = self._client().get("/data-sources/email/status").json()

        assert body.get("status_read_failed") in (None, False)
        assert body["errors"] == []
