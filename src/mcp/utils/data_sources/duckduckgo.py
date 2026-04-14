# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""DuckDuckGo Instant Answers data source -- free, no API key required."""
from __future__ import annotations

import re

import httpx

from errors import RetrievalError

from .base import DataSource, DataSourceResult, logger

_QUOTED_RE = re.compile(r'"([^"]+)"')


class DuckDuckGoSource(DataSource):
    name = "duckduckgo"
    description = "DuckDuckGo Instant Answers -- quick answers, related topics, abstracts. No API key required."
    requires_api_key = False
    domains: list[str] = []  # all domains

    def score_confidence(self, raw_query: str, result: "DataSourceResult") -> float:
        """Boost .gov/.edu source URLs; reduce for tangential related topics."""
        url = result.source_url.lower()
        if ".gov" in url or ".edu" in url:
            return min(1.0, result.confidence + 0.10)
        # Related topics with very short content are likely tangential
        if len(result.content) < 30:
            return max(0.0, result.confidence - 0.10)
        return result.confidence

    def adapt_query(self, raw_query: str, keywords: list[str]) -> str:
        """Use keywords plus any quoted phrases from the original query."""
        quoted = _QUOTED_RE.findall(raw_query)
        parts = list(keywords[:4])
        for q in quoted[:2]:
            if q not in " ".join(parts):
                parts.append(f'"{q}"')
        return " ".join(parts) if parts else raw_query

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Removed skip_disambig — disambiguation pages contain useful structured data
                resp = await client.get(
                    "https://api.duckduckgo.com/",
                    params={"q": query, "format": "json", "no_html": "1"},
                )
                resp.raise_for_status()
                data = resp.json()

                results: list[DataSourceResult] = []

                # Abstract (instant answer)
                abstract = data.get("AbstractText", "")
                if abstract:
                    results.append(DataSourceResult(
                        title=data.get("Heading", query),
                        content=abstract,
                        source_url=data.get("AbstractURL", ""),
                        source_name="DuckDuckGo",
                        confidence=0.80,
                    ))

                # Related topics (up to 2)
                for topic in data.get("RelatedTopics", [])[:2]:
                    text = topic.get("Text", "")
                    url = topic.get("FirstURL", "")
                    if text and not isinstance(topic.get("Topics"), list):
                        results.append(DataSourceResult(
                            title=text[:80],
                            content=text,
                            source_url=url,
                            source_name="DuckDuckGo",
                            confidence=0.70,
                        ))

                return results
        except (RetrievalError, httpx.HTTPError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
            logger.debug("DuckDuckGo query failed: %s", exc)
            return []
