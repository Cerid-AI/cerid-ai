# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Wikipedia data source -- free, no API key required."""
from __future__ import annotations

import httpx

from errors import RetrievalError

from .base import DataSource, DataSourceResult, logger


class WikipediaSource(DataSource):
    name = "wikipedia"
    description = "Wikipedia -- free encyclopedia. No API key required."
    requires_api_key = False
    domains: list[str] = []  # all domains

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
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
        except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
            logger.debug("Wikipedia query failed: %s", exc)
            return []
