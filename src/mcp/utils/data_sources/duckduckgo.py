# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""DuckDuckGo Instant Answers data source -- free, no API key required."""
from __future__ import annotations

import httpx

from errors import RetrievalError

from .base import DataSource, DataSourceResult, logger


class DuckDuckGoSource(DataSource):
    name = "duckduckgo"
    description = "DuckDuckGo Instant Answers -- quick answers, related topics, abstracts. No API key required."
    requires_api_key = False
    domains: list[str] = []  # all domains

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    "https://api.duckduckgo.com/",
                    params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
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
        except (RetrievalError, ValueError, OSError, RuntimeError) as exc:
            logger.debug("DuckDuckGo query failed: %s", exc)
            return []
