# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Open Library adapter (Phase API.1).

Uses the Open Library API:
  https://openlibrary.org/api

No authentication required.
"""
from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx

from app.services.external_apis.base import (
    ExternalAPIAdapter,
    get_http_client,
)

_BASE_URL = "https://openlibrary.org"


class OpenLibraryAdapter(ExternalAPIAdapter):
    """Adapter for the Open Library book metadata API."""

    slug = "openlibrary"
    display_name = "Open Library"
    requires_key = False
    key_env_var = None

    async def lookup(self, isbn_or_olid: str) -> dict[str, Any]:  # type: ignore[override]
        """Fetch metadata for a book by ISBN or Open Library ID (OLID).

        Parameters
        ----------
        isbn_or_olid:
            Either a 10/13-digit ISBN (e.g. ``"9780140449136"``) or an
            Open Library work/edition ID (e.g. ``"OL45804W"``).

        Returns
        -------
        dict with keys: ``title``, ``authors``, ``publish_date``,
            ``publishers``, ``subjects``, ``description``, ``cover_url``.

        Raises
        ------
        ExternalAPIError
            On non-2xx responses or transport failures.
        """
        client = await get_http_client()
        # Normalise: if it starts with OL, treat as works or editions ID
        if isbn_or_olid.upper().startswith("OL"):
            key = isbn_or_olid.upper()
            if not key.endswith(("W", "M", "A")):
                key = f"{key}W"
            url = f"{_BASE_URL}/works/{key}.json"
        else:
            url = f"{_BASE_URL}/isbn/{isbn_or_olid}.json"

        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._wrap_http_error(exc) from exc

        data = resp.json()
        description_raw = data.get("description") or ""
        if isinstance(description_raw, dict):
            description_raw = description_raw.get("value", "")
        covers = data.get("covers") or []
        cover_url = f"https://covers.openlibrary.org/b/id/{covers[0]}-L.jpg" if covers else None
        return {
            "title": data.get("title", ""),
            "authors": [
                a.get("author", {}).get("key", "") if isinstance(a, dict) else str(a)
                for a in (data.get("authors") or [])
            ],
            "publish_date": data.get("first_publish_date") or data.get("publish_date"),
            "publishers": data.get("publishers") or [],
            "subjects": (data.get("subjects") or [])[:10],
            "description": str(description_raw),
            "cover_url": cover_url,
        }

    async def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search Open Library for books matching ``query``.

        Parameters
        ----------
        query:
            Free-text search string.
        limit:
            Maximum number of results to return (capped at 100).

        Returns
        -------
        list[dict] — each with ``title``, ``author_name``, ``first_publish_year``,
            ``key`` (OL work ID).

        Raises
        ------
        ExternalAPIError
            On non-2xx responses or transport failures.
        """
        client = await get_http_client()
        try:
            resp = await client.get(
                f"{_BASE_URL}/search.json",
                params={"q": query, "limit": min(limit, 100), "fields": "key,title,author_name,first_publish_year"},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._wrap_http_error(exc) from exc

        data = resp.json()
        return [
            {
                "key": doc.get("key", ""),
                "title": doc.get("title", ""),
                "author_name": doc.get("author_name") or [],
                "first_publish_year": doc.get("first_publish_year"),
            }
            for doc in (data.get("docs") or [])[:limit]
        ]

    async def health_check(self) -> bool:
        """Return True when the Open Library API is reachable."""
        client = await get_http_client()
        try:
            resp = await client.get(f"{_BASE_URL}/isbn/9780140449136.json")
            return resp.status_code < HTTPStatus.INTERNAL_SERVER_ERROR
        except Exception:  # noqa: BLE001
            return False
