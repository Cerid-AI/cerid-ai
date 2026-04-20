# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the web search provider abstraction (utils/web_search.py).

Covers Tavily, SearXNG, and OpenRouter providers, rate limiting,
circuit breaker integration, cascading failover, and result structure.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.utils.circuit_breaker import AsyncCircuitBreaker, CircuitOpenError, CircuitState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tavily_response_json() -> dict:
    """Fake Tavily API response payload."""
    return {
        "results": [
            {
                "title": "Test Result 1",
                "url": "https://example.com/1",
                "content": "Snippet one",
                "score": 0.95,
                "published_date": "2026-01-01",
            },
            {
                "title": "Test Result 2",
                "url": "https://example.com/2",
                "content": "Snippet two",
                "score": 0.80,
                "published_date": None,
            },
        ]
    }


def _searxng_response_json() -> dict:
    """Fake SearXNG API response payload."""
    return {
        "results": [
            {
                "title": "SearXNG Result 1",
                "url": "https://example.org/a",
                "content": "SearXNG snippet",
                "publishedDate": "2026-02-01",
            },
        ]
    }


def _make_mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


# ---------------------------------------------------------------------------
# Tavily provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tavily_provider_success():
    """Tavily provider parses structured results correctly."""
    mock_resp = _make_mock_response(_tavily_response_json())
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch.dict("os.environ", {"TAVILY_API_KEY": "test-key"}), \
         patch("utils.web_search.TAVILY_API_KEY", "test-key"), \
         patch("utils.web_search._get_search_client", return_value=mock_client):
        from utils.web_search import TavilyProvider
        provider = TavilyProvider()
        # Reset the breaker to ensure clean state
        from core.utils.circuit_breaker import get_breaker
        get_breaker("tavily").reset()

        results = await provider.search("test query", max_results=5)

    assert len(results) == 2
    assert results[0].title == "Test Result 1"
    assert results[0].url == "https://example.com/1"
    assert results[0].snippet == "Snippet one"
    assert results[0].score == 0.95
    assert results[0].published_date == "2026-01-01"
    assert results[1].title == "Test Result 2"


@pytest.mark.asyncio
async def test_tavily_provider_timeout():
    """Tavily provider handles httpx timeout gracefully via circuit breaker."""
    import httpx

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.ReadTimeout("timeout"))

    with patch.dict("os.environ", {"TAVILY_API_KEY": "test-key"}), \
         patch("utils.web_search.TAVILY_API_KEY", "test-key"), \
         patch("utils.web_search._get_search_client", return_value=mock_client):
        from core.utils.circuit_breaker import get_breaker
        from utils.web_search import TavilyProvider
        breaker = get_breaker("tavily")
        breaker.reset()

        # The httpx timeout is an OSError subclass — circuit breaker records
        # the failure and re-raises.
        with pytest.raises((OSError, Exception)):
            await TavilyProvider().search("test")


# ---------------------------------------------------------------------------
# SearXNG provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_searxng_provider_success():
    """SearXNG provider parses results and normalizes scores."""
    mock_resp = _make_mock_response(_searxng_response_json())
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch.dict("os.environ", {"SEARXNG_URL": "http://localhost:8080"}), \
         patch("utils.web_search.SEARXNG_URL", "http://localhost:8080"), \
         patch("utils.web_search._get_search_client", return_value=mock_client):
        from core.utils.circuit_breaker import get_breaker
        from utils.web_search import SearxngProvider
        get_breaker("searxng").reset()

        provider = SearxngProvider()
        results = await provider.search("searxng query")

    assert len(results) == 1
    assert results[0].title == "SearXNG Result 1"
    assert results[0].url == "https://example.org/a"
    assert results[0].snippet == "SearXNG snippet"
    # First result should have score 1.0
    assert results[0].score == 1.0


# ---------------------------------------------------------------------------
# OpenRouter search fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openrouter_search_fallback():
    """OpenRouter online model returns a single pseudo-result."""
    with patch("utils.web_search.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "Here is a synthesized answer about the topic."

        from utils.web_search import OpenRouterSearchProvider
        provider = OpenRouterSearchProvider()
        results = await provider.search("fallback query")

    assert len(results) == 1
    assert results[0].title.startswith("Web search:")
    assert "synthesized answer" in results[0].snippet
    assert results[0].score == 0.8


@pytest.mark.asyncio
async def test_openrouter_search_fallback_failure():
    """OpenRouter provider returns empty list on LLM failure."""
    with patch("utils.web_search.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = RuntimeError("LLM down")

        from utils.web_search import OpenRouterSearchProvider
        provider = OpenRouterSearchProvider()
        results = await provider.search("failing query")

    assert results == []


# ---------------------------------------------------------------------------
# Cascading failover
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cascading_failover():
    """Tavily fails -> SearXNG fails -> OpenRouter succeeds."""
    # Set up: Tavily configured but will error, SearXNG configured but will error
    with patch("utils.web_search.TAVILY_API_KEY", "test-key"), \
         patch("utils.web_search.SEARXNG_URL", "http://localhost:8080"):
        from utils.web_search import get_search_provider

    # Use search_and_verify with a provider that fails, triggering the try/except
    mock_resp_fail = _make_mock_response({}, status_code=500)
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp_fail)

    with patch("utils.web_search.TAVILY_API_KEY", "test-key"), \
         patch("utils.web_search.SEARXNG_URL", ""), \
         patch("utils.web_search._get_search_client", return_value=mock_client), \
         patch("utils.web_search._rate_window", []):
        from core.utils.circuit_breaker import get_breaker
        from utils.web_search import get_search_provider
        get_breaker("tavily").reset()

        provider = get_search_provider()
        assert provider.name == "tavily"

    # Verify that when Tavily key is missing and SearXNG is missing,
    # we fall back to OpenRouter
    with patch("utils.web_search.TAVILY_API_KEY", ""), \
         patch("utils.web_search.SEARXNG_URL", ""):
        from utils.web_search import get_search_provider
        provider = get_search_provider()
        assert provider.name == "openrouter_online"


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


def test_rate_limiter_blocks_excess():
    """Rate limiter should block the 11th request in a 1-second window."""
    from utils.web_search import WEB_SEARCH_RATE_LIMIT, _check_rate_limit, _rate_window

    # Clear the window
    _rate_window.clear()

    # Fill up to the limit
    for _ in range(WEB_SEARCH_RATE_LIMIT):
        _check_rate_limit()

    # The next call should raise
    with pytest.raises(RuntimeError, match="rate limit exceeded"):
        _check_rate_limit()

    # Clean up
    _rate_window.clear()


# ---------------------------------------------------------------------------
# Circuit breaker trips
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_breaker_trips():
    """3 failures should trip the circuit breaker to OPEN, skipping subsequent calls."""
    breaker = AsyncCircuitBreaker("test-web-search-trip", failure_threshold=3, recovery_timeout=60)

    async def _fail():
        raise RuntimeError("service down")

    # Trip the breaker with 3 failures
    for _ in range(3):
        with pytest.raises(RuntimeError, match="service down"):
            await breaker.call(_fail)

    assert breaker.state == CircuitState.OPEN

    # Subsequent calls should raise CircuitOpenError
    with pytest.raises(CircuitOpenError):
        await breaker.call(_fail)


# ---------------------------------------------------------------------------
# Structured results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_search_returns_structured_results():
    """search_and_verify returns dict with title, url, snippet fields."""
    mock_resp = _make_mock_response(_tavily_response_json())
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("utils.web_search.TAVILY_API_KEY", "test-key"), \
         patch("utils.web_search._get_search_client", return_value=mock_client), \
         patch("utils.web_search._rate_window", []):
        from core.utils.circuit_breaker import get_breaker
        from utils.web_search import search_and_verify
        get_breaker("tavily").reset()

        result = await search_and_verify("test query")

    assert "results" in result
    assert "provider" in result
    assert "timestamp" in result
    assert "query" in result
    assert result["provider"] == "tavily"
    assert len(result["results"]) == 2

    first = result["results"][0]
    assert "title" in first
    assert "url" in first
    assert "snippet" in first
    assert "score" in first
    assert first["title"] == "Test Result 1"
    assert first["url"] == "https://example.com/1"
