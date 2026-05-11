# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""arXiv adapter (Phase API.1).

Uses the arXiv API (Atom/XML feed):
  http://export.arxiv.org/api/query

No authentication required.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET  # noqa: B314 — parsing trusted arXiv Atom feed
from typing import Any

import httpx

from app.services.external_apis.base import (
    ExternalAPIAdapter,
    ExternalAPIError,
    get_http_client,
)

_BASE_URL = "http://export.arxiv.org/api/query"
_ATOM_NS = "http://www.w3.org/2005/Atom"
_ARXIV_NS = "http://arxiv.org/schemas/atom"


def _tag(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"


class ArxivAdapter(ExternalAPIAdapter):
    """Adapter for the arXiv search API."""

    slug = "arxiv"
    display_name = "arXiv"
    requires_key = False
    key_env_var = None

    async def lookup(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        """Alias for :meth:`search` to satisfy the abstract interface."""
        query: str = args[0] if args else kwargs.get("query", "")
        max_results: int = kwargs.get("max_results", 10)
        return await self.search(query, max_results=max_results)

    async def search(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        """Search arXiv for papers matching ``query``.

        Parameters
        ----------
        query:
            arXiv search query string.  Supports field prefixes:
            ``ti:`` (title), ``au:`` (author), ``abs:`` (abstract),
            ``all:`` (any).
        max_results:
            Maximum number of results (arXiv hard-caps at 1000).

        Returns
        -------
        list[dict] — each with ``arxiv_id``, ``title``, ``authors``,
            ``summary``, ``published``, ``pdf_url``, ``categories``.

        Raises
        ------
        ExternalAPIError
            On non-2xx responses or transport failures, or when the
            response cannot be parsed as Atom XML.
        """
        client = await get_http_client()
        try:
            resp = await client.get(
                _BASE_URL,
                params={
                    "search_query": query,
                    "start": 0,
                    "max_results": min(max_results, 1000),
                    "sortBy": "relevance",
                    "sortOrder": "descending",
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._wrap_http_error(exc) from exc

        try:
            root = ET.fromstring(resp.text)  # nosec B314 — trusted arXiv Atom feed
        except ET.ParseError as exc:
            raise ExternalAPIError(
                provider=self.slug,
                detail=f"failed to parse Atom XML: {exc}",
                status_code=0,
            ) from exc

        results: list[dict[str, Any]] = []
        for entry in root.findall(_tag(_ATOM_NS, "entry")):
            arxiv_id_raw = (entry.findtext(_tag(_ATOM_NS, "id")) or "").strip()
            arxiv_id = arxiv_id_raw.split("/abs/")[-1] if "/abs/" in arxiv_id_raw else arxiv_id_raw

            title = (entry.findtext(_tag(_ATOM_NS, "title")) or "").strip()
            summary = (entry.findtext(_tag(_ATOM_NS, "summary")) or "").strip()
            published = (entry.findtext(_tag(_ATOM_NS, "published")) or "").strip()

            authors = [
                (name_el.text or "").strip()
                for author in entry.findall(_tag(_ATOM_NS, "author"))
                for name_el in author.findall(_tag(_ATOM_NS, "name"))
            ]

            pdf_url = ""
            for link in entry.findall(_tag(_ATOM_NS, "link")):
                if link.get("title") == "pdf":
                    pdf_url = link.get("href", "")
                    break

            categories = [
                c.get("term", "")
                for c in entry.findall(_tag(_ATOM_NS, "category"))
            ]

            results.append({
                "arxiv_id": arxiv_id,
                "title": title,
                "authors": authors,
                "summary": summary,
                "published": published,
                "pdf_url": pdf_url,
                "categories": categories,
            })

        return results

    async def health_check(self) -> bool:
        """Return True when the arXiv API is reachable."""
        client = await get_http_client()
        try:
            resp = await client.get(
                _BASE_URL,
                params={"search_query": "all:test", "max_results": 1},
            )
            return resp.status_code < 500
        except Exception:  # noqa: BLE001
            return False
