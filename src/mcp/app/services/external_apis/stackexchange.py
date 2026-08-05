# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Stack Exchange adapter (Phase API.1).

Uses the Stack Exchange API v2.3:
  https://api.stackexchange.com/2.3

No authentication required.  Anonymous access is limited to
300 requests per day across all Stack Exchange sites.  Operators
expecting higher volume should supply a ``STACKEXCHANGE_KEY``
environment variable (not currently wired — the anonymous limit
covers typical entity-enrichment workloads).

Note: responses are gzip-compressed by default; httpx handles
decompression automatically.
"""
from __future__ import annotations

import html
from http import HTTPStatus
from typing import Any

import httpx

from app.services.external_apis.base import (
    ExternalAPIAdapter,
    get_http_client,
)

_BASE_URL = "https://api.stackexchange.com/2.3"


class StackExchangeAdapter(ExternalAPIAdapter):
    """Adapter for the Stack Exchange API.

    Anonymous access limit: 300 requests/day.  Add a ``key`` parameter
    (from https://stackapps.com/apps/oauth/register) to raise this to
    10 000 requests/day.
    """

    slug = "stackexchange"
    display_name = "Stack Exchange"
    requires_key = False
    key_env_var = None

    async def lookup(self, *args: Any, **kwargs: Any) -> Any:
        """Alias for :meth:`search` to satisfy the abstract interface."""
        query: str = args[0] if args else kwargs.get("query", "")
        site: str = kwargs.get("site", "stackoverflow")
        pagesize: int = kwargs.get("pagesize", 5)
        return await self.search(query, site=site, pagesize=pagesize)

    async def search(
        self,
        query: str,
        site: str = "stackoverflow",
        pagesize: int = 5,
    ) -> list[dict[str, Any]]:
        """Search for questions on a Stack Exchange site.

        Parameters
        ----------
        query:
            Search string.
        site:
            Stack Exchange site slug (``"stackoverflow"``, ``"serverfault"``,
            ``"superuser"``, etc.).
        pagesize:
            Number of results to return (max 100 per the API).

        Returns
        -------
        list[dict] — each with ``title``, ``link``, ``score``,
            ``answer_count``, ``tags``, ``is_answered``.

        Raises
        ------
        ExternalAPIError
            On non-2xx responses or transport failures.
        """
        client = await get_http_client()
        try:
            resp = await client.get(
                f"{_BASE_URL}/search/advanced",
                params={
                    "order": "desc",
                    "sort": "relevance",
                    "q": query,
                    "site": site,
                    "pagesize": min(pagesize, 100),
                    "filter": "!9_bDE(fI5",  # compact filter
                },
                headers={"Accept-Encoding": "gzip"},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._wrap_http_error(exc) from exc

        data = resp.json()
        return [
            {
                "title": html.unescape(item.get("title", "")),
                "link": item.get("link", ""),
                "score": item.get("score", 0),
                "answer_count": item.get("answer_count", 0),
                "tags": item.get("tags") or [],
                "is_answered": item.get("is_answered", False),
            }
            for item in (data.get("items") or [])[:pagesize]
        ]

    async def health_check(self) -> bool:
        """Return True when the Stack Exchange API is reachable."""
        client = await get_http_client()
        try:
            resp = await client.get(
                f"{_BASE_URL}/info",
                params={"site": "stackoverflow"},
                headers={"Accept-Encoding": "gzip"},
            )
            return resp.status_code < HTTPStatus.INTERNAL_SERVER_ERROR
        except Exception:  # noqa: BLE001
            return False
