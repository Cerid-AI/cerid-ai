# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Web search provider abstraction — Tavily, SearXNG, or OpenRouter :online fallback.

Provides a unified interface for explicit web searches triggered by the
hallucination pipeline (ignorance claims), the ``pkb_web_search`` MCP tool,
or any service that needs fresh external context.

Priority: Tavily (structured API) > SearXNG (self-hosted) > OpenRouter online
(LLM with implicit web search, always available as fallback).
"""

from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

import config.settings as config
from utils.llm_client import call_llm
from utils.circuit_breaker import get_breaker

_logger = logging.getLogger("ai-companion.web_search")

# ---------------------------------------------------------------------------
# Configuration (read from env, no settings.py modifications needed)
# ---------------------------------------------------------------------------

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
SEARXNG_URL = os.getenv("SEARXNG_URL", "")
ENABLE_AUTO_LEARN = os.getenv("ENABLE_AUTO_LEARN", "false").lower() == "true"
WEB_SEARCH_MAX_RESULTS = int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5"))
WEB_SEARCH_RATE_LIMIT = int(os.getenv("WEB_SEARCH_RATE_LIMIT", "10"))

# ---------------------------------------------------------------------------
# Rate limiter — sliding window per minute
# ---------------------------------------------------------------------------

_rate_window: list[float] = []


def _check_rate_limit() -> None:
    """Enforce per-minute rate limit.  Raises RuntimeError if exceeded."""
    now = time.monotonic()
    cutoff = now - 60.0
    # Prune entries older than 60s
    while _rate_window and _rate_window[0] < cutoff:
        _rate_window.pop(0)
    if len(_rate_window) >= WEB_SEARCH_RATE_LIMIT:
        raise RuntimeError(
            f"Web search rate limit exceeded ({WEB_SEARCH_RATE_LIMIT}/min)"
        )
    _rate_window.append(now)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class WebSearchResult:
    """Single web search result."""

    title: str
    url: str
    snippet: str
    score: float  # relevance 0-1
    published_date: str | None = None


# ---------------------------------------------------------------------------
# Abstract provider
# ---------------------------------------------------------------------------


class WebSearchProvider(ABC):
    """Abstract base for web search providers."""

    name: str = "unknown"

    @abstractmethod
    async def search(
        self, query: str, max_results: int = 5
    ) -> list[WebSearchResult]:
        """Execute a web search and return structured results."""


# ---------------------------------------------------------------------------
# Tavily provider (primary)
# ---------------------------------------------------------------------------


class TavilyProvider(WebSearchProvider):
    """Tavily AI search API — primary provider.

    Uses ``TAVILY_API_KEY`` env var.
    Endpoint: https://api.tavily.com/search
    """

    name = "tavily"

    def __init__(self) -> None:
        self._api_key = TAVILY_API_KEY
        if not self._api_key:
            raise ValueError("TAVILY_API_KEY not set")

    async def search(
        self, query: str, max_results: int = 5
    ) -> list[WebSearchResult]:
        breaker = get_breaker("tavily")

        async def _call() -> list[WebSearchResult]:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": self._api_key,
                        "query": query,
                        "max_results": min(max_results, 10),
                        "search_depth": "advanced",
                        "include_answer": False,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            results: list[WebSearchResult] = []
            for item in data.get("results", []):
                results.append(
                    WebSearchResult(
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                        snippet=item.get("content", ""),
                        score=float(item.get("score", 0.0)),
                        published_date=item.get("published_date"),
                    )
                )
            return results

        return await breaker.call(_call)


# ---------------------------------------------------------------------------
# SearXNG provider (self-hosted alternative)
# ---------------------------------------------------------------------------


class SearxngProvider(WebSearchProvider):
    """SearXNG self-hosted — alternative provider for privacy-first users.

    Uses ``SEARXNG_URL`` env var (e.g. ``http://localhost:8080``).
    Endpoint: ``{SEARXNG_URL}/search?q={query}&format=json``
    """

    name = "searxng"

    def __init__(self) -> None:
        self._base_url = SEARXNG_URL.rstrip("/")
        if not self._base_url:
            raise ValueError("SEARXNG_URL not set")

    async def search(
        self, query: str, max_results: int = 5
    ) -> list[WebSearchResult]:
        breaker = get_breaker("searxng")

        async def _call() -> list[WebSearchResult]:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self._base_url}/search",
                    params={
                        "q": query,
                        "format": "json",
                        "pageno": 1,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            results: list[WebSearchResult] = []
            for i, item in enumerate(data.get("results", [])[:max_results]):
                # SearXNG returns a score field but it varies by engine.
                # Normalize: first result = 1.0, linearly decaying.
                normalized_score = max(0.0, 1.0 - (i * 0.15))
                results.append(
                    WebSearchResult(
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                        snippet=item.get("content", ""),
                        score=round(normalized_score, 2),
                        published_date=item.get("publishedDate"),
                    )
                )
            return results

        return await breaker.call(_call)


# ---------------------------------------------------------------------------
# OpenRouter online model provider (always-available fallback)
# ---------------------------------------------------------------------------


class OpenRouterSearchProvider(WebSearchProvider):
    """OpenRouter online model — uses LLM with web search capability.

    Wraps the existing Bifrost/OpenRouter infrastructure.  The ``:online``
    suffixed model performs implicit web search and returns a synthesized
    answer.  We parse it into a single pseudo-result.
    """

    name = "openrouter_online"

    async def search(
        self, query: str, max_results: int = 5
    ) -> list[WebSearchResult]:
        model = config.VERIFICATION_CURRENT_EVENT_MODEL  # :online model
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a web search assistant.  Answer the user's query "
                    "with factual, up-to-date information.  Include source URLs "
                    "where possible.  Be concise."
                ),
            },
            {"role": "user", "content": query},
        ]
        try:
            content = await call_llm(
                messages,
                breaker_name="web-search",
                model=model,
                temperature=0.2,
                max_tokens=1500,
                timeout=25.0,
            )
        except Exception:
            _logger.warning("OpenRouter online search failed", exc_info=True)
            return []

        # The :online model returns a prose answer, not structured results.
        # Wrap it as a single result.
        return [
            WebSearchResult(
                title=f"Web search: {query[:80]}",
                url="",
                snippet=content[:2000],
                score=0.8,
                published_date=None,
            )
        ]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_search_provider() -> WebSearchProvider:
    """Get the configured web search provider.

    Priority:
    1. Tavily (if ``TAVILY_API_KEY`` is set)
    2. SearXNG (if ``SEARXNG_URL`` is set)
    3. OpenRouter online model (always available as fallback)
    """
    if TAVILY_API_KEY:
        _logger.info("Using Tavily web search provider")
        return TavilyProvider()
    if SEARXNG_URL:
        _logger.info("Using SearXNG web search provider")
        return SearxngProvider()
    _logger.info("Using OpenRouter online model as web search fallback")
    return OpenRouterSearchProvider()


# ---------------------------------------------------------------------------
# Search + Verify + optional ingest
# ---------------------------------------------------------------------------


async def search_and_verify(
    query: str,
    chroma_client: Any = None,
    neo4j_driver: Any = None,
    redis_client: Any = None,
    max_results: int = 5,
    auto_ingest: bool = False,
) -> dict:
    """Search the web, optionally verify results through Self-RAG, optionally ingest.

    Parameters
    ----------
    query : str
        Search query.
    chroma_client, neo4j_driver, redis_client :
        Optional database clients for Self-RAG verification and ingestion.
    max_results : int
        Maximum results to return (capped at 10).
    auto_ingest : bool
        If ``True`` **and** ``ENABLE_AUTO_LEARN`` is enabled, verified results
        are ingested into the KB.

    Returns
    -------
    dict
        ``query``, ``results``, ``verified_results`` (if Self-RAG available),
        ``ingested_count``, ``provider``, ``timestamp``.
    """
    _check_rate_limit()

    effective_max = min(max_results, 10)
    provider = get_search_provider()
    timestamp = datetime.now(timezone.utc).isoformat()

    try:
        raw_results = await provider.search(query, max_results=effective_max)
    except Exception:
        _logger.exception("Web search failed (provider=%s)", provider.name)
        return {
            "query": query,
            "results": [],
            "verified_results": None,
            "ingested_count": 0,
            "provider": provider.name,
            "timestamp": timestamp,
            "error": "Search failed — see server logs",
        }

    _logger.info(
        "Web search returned %d results (provider=%s, query=%.60s)",
        len(raw_results),
        provider.name,
        query,
    )

    # Serialize results
    results_dicts = [
        {
            "title": r.title,
            "url": r.url,
            "snippet": r.snippet,
            "score": r.score,
            "published_date": r.published_date,
        }
        for r in raw_results
    ]

    # ── Optional Self-RAG verification ────────────────────────────────────
    verified_results: list[dict] | None = None
    if chroma_client and neo4j_driver and redis_client and raw_results:
        try:
            from agents.self_rag import self_rag_enhance

            # Build a pseudo query_result for Self-RAG
            combined_text = "\n\n".join(
                f"[{r.title}] {r.snippet}" for r in raw_results
            )
            pseudo_query_result: dict[str, Any] = {
                "results": results_dicts,
                "context": combined_text,
                "confidence": sum(r.score for r in raw_results) / len(raw_results),
                "domains_searched": ["general"],
                "total_results": len(raw_results),
            }
            enhanced = await self_rag_enhance(
                query_result=pseudo_query_result,
                response_text=combined_text,
                chroma_client=chroma_client,
                neo4j_driver=neo4j_driver,
                redis_client=redis_client,
            )
            verified_results = enhanced.get("results", results_dicts)
        except Exception:
            _logger.warning("Self-RAG verification failed", exc_info=True)

    # ── Optional auto-ingest ──────────────────────────────────────────────
    ingested_count = 0
    should_ingest = auto_ingest and ENABLE_AUTO_LEARN
    if should_ingest and raw_results:
        try:
            from services.ingestion import ingest_content

            for r in raw_results:
                if not r.snippet.strip():
                    continue
                content = f"# {r.title}\n\nSource: {r.url}\n\n{r.snippet}"
                result = ingest_content(content, "general")
                if result.get("status") == "ok":
                    ingested_count += 1
            _logger.info("Auto-ingested %d web search results", ingested_count)
        except Exception:
            _logger.warning("Auto-ingest of web results failed", exc_info=True)

    return {
        "query": query,
        "results": results_dicts,
        "verified_results": verified_results,
        "ingested_count": ingested_count,
        "provider": provider.name,
        "timestamp": timestamp,
    }
