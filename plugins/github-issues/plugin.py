# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""GitHub Issues connector plugin — searches issues via the GitHub REST API."""
from __future__ import annotations

import logging
import os

import httpx

from plugins.base import ConnectorPlugin
from utils.data_sources.base import DataSource, DataSourceResult

logger = logging.getLogger("ai-companion.plugins.github-issues")


class GitHubIssuesSource(DataSource):
    """DataSource that queries GitHub Issues via the REST search API."""

    name = "github-issues"
    description = "Search GitHub Issues"
    enabled = True
    requires_api_key = True
    api_key_env_var = "GITHUB_TOKEN"

    _API_URL = "https://api.github.com/search/issues"

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        """Search GitHub issues matching *query*.

        Returns up to 5 results with title, body snippet, and URL.
        """
        token = os.getenv(self.api_key_env_var, "")
        if not token:
            logger.debug("GITHUB_TOKEN not set — skipping GitHub Issues search")
            return []

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        params = {"q": query, "per_page": 5, "sort": "updated", "order": "desc"}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(self._API_URL, headers=headers, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning("GitHub Issues search failed: %s", exc)
            return []

        results: list[DataSourceResult] = []
        for item in data.get("items", [])[:5]:
            body = item.get("body") or ""
            snippet = body[:300] + "..." if len(body) > 300 else body
            results.append(
                DataSourceResult(
                    title=item.get("title", ""),
                    content=snippet,
                    source_url=item.get("html_url", ""),
                    source_name="github-issues",
                    confidence=0.7,
                )
            )
        return results


class Plugin(ConnectorPlugin):
    """GitHub Issues connector plugin."""

    name = "github-issues"
    version = "0.1.0"
    description = "Search GitHub Issues via the GitHub REST API"

    def get_data_source(self) -> GitHubIssuesSource:
        return GitHubIssuesSource()


def register():
    """Plugin entry point — called by the Cerid AI plugin loader."""
    return Plugin()
