# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Wikipedia data source -- free, no API key required."""
from __future__ import annotations

import re

import httpx

from errors import RetrievalError

from .base import DataSource, DataSourceResult, logger

_PROPER_NOUN_RE = re.compile(r"\b([A-Z][a-z]{2,}(?:\s[A-Z][a-z]{2,})*)\b")


class WikipediaSource(DataSource):
    name = "wikipedia"
    description = "Wikipedia -- free encyclopedia. No API key required."
    requires_api_key = False
    domains: list[str] = []  # all domains

    _QUESTION_WORDS = {"What", "When", "Where", "Who", "Why", "How", "Which", "Does", "Can", "Could", "Would", "Should", "Are", "Were", "Was", "The"}

    def score_confidence(self, raw_query: str, result: "DataSourceResult") -> float:
        """Boost when title closely matches query entities; reduce for disambiguation."""
        title_lower = result.title.lower()
        query_lower = raw_query.lower()
        # Boost if result title appears as a substring in the query
        if title_lower and title_lower in query_lower:
            return min(1.0, result.confidence + 0.05)
        # Reduce for disambiguation or stub articles
        if "(disambiguation)" in title_lower or len(result.content) < 50:
            return max(0.0, result.confidence - 0.15)
        return result.confidence

    def adapt_query(self, raw_query: str, keywords: list[str]) -> str:
        """Wikipedia works best with entity names rather than keyword soup."""
        entities = _PROPER_NOUN_RE.findall(raw_query)
        # Filter question/article words that are only capitalized because they start a sentence
        entities = [e for e in entities if e not in self._QUESTION_WORDS]
        if entities:
            return " ".join(entities[:3])
        return " ".join(keywords[:3]) if keywords else raw_query

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        try:
            async with httpx.AsyncClient(
                timeout=5.0,
                headers={"User-Agent": "CeridAI/0.82 (https://github.com/Cerid-AI/cerid-ai)"},
            ) as client:
                resp = await client.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={"action": "query", "list": "search", "srsearch": query,
                            "srlimit": "3", "format": "json"},
                )
                resp.raise_for_status()
                search_results = resp.json().get("query", {}).get("search", [])

                results = []
                for sr in search_results[:2]:
                    title = sr.get("title", "")
                    summary_resp = await client.get(
                        f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}",
                    )
                    if summary_resp.status_code == 200:
                        data = summary_resp.json()
                        results.append(DataSourceResult(
                            title=data.get("title", title),
                            content=data.get("extract", ""),
                            source_url=data.get("content_urls", {}).get("desktop", {}).get("page", ""),
                            source_name="Wikipedia",
                            confidence=0.85,
                        ))
                return results
        except (RetrievalError, httpx.HTTPError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
            logger.debug("Wikipedia query failed: %s", exc)
            return []
