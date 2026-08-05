# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Wikipedia REST API adapter (Phase API.1).

Uses the Wikimedia Action API REST v1:
  https://en.wikipedia.org/api/rest_v1

No authentication required.  All calls are subject to Wikimedia's
anonymous rate limits.  Aggressive caching on the caller's side is
recommended for high-volume entity-enrichment workloads.
"""
from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx

from app.services.external_apis.base import (
    ExternalAPIAdapter,
    get_http_client,
)

_BASE_URL = "https://en.wikipedia.org/api/rest_v1"


class WikipediaAdapter(ExternalAPIAdapter):
    """Adapter for the Wikipedia REST v1 API."""

    slug = "wikipedia"
    display_name = "Wikipedia"
    requires_key = False
    key_env_var = None

    async def lookup(self, title: str) -> dict[str, Any]:  # type: ignore[override]
        """Fetch the page summary for ``title``.

        Parameters
        ----------
        title:
            Wikipedia page title (URL-safe; spaces should be underscores or
            percent-encoded).  Example: ``"Python_(programming_language)"``.

        Returns
        -------
        dict with keys:
            ``title``, ``extract``, ``thumbnail_url`` (or ``None``),
            ``content_url``, ``last_updated``.

        Raises
        ------
        ExternalAPIError
            On non-2xx responses or transport failures.
        """
        client = await get_http_client()
        url = f"{_BASE_URL}/page/summary/{title}"
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._wrap_http_error(exc) from exc

        data = resp.json()
        thumbnail = data.get("thumbnail") or {}
        return {
            "title": data.get("title", title),
            "extract": data.get("extract", ""),
            "thumbnail_url": thumbnail.get("source"),
            "content_url": (data.get("content_urls") or {}).get("desktop", {}).get("page"),
            "last_updated": data.get("timestamp"),
        }

    async def health_check(self) -> bool:
        """Return True when the Wikipedia REST endpoint is reachable.

        A 404 is acceptable — the endpoint responded.
        """
        client = await get_http_client()
        try:
            resp = await client.get(f"{_BASE_URL}/page/summary/Cerid_AI")
            return resp.status_code < HTTPStatus.INTERNAL_SERVER_ERROR
        except Exception:  # noqa: BLE001
            return False
