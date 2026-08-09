# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""OpenStreetMap Nominatim adapter (Phase API.1).

Uses the Nominatim geocoding API:
  https://nominatim.openstreetmap.org

No authentication required.  **Nominatim's usage policy requires a
maximum of 1 request per second.** This adapter enforces the limit
via a module-level asyncio lock + a 1-second inter-call delay.

See: https://operations.osmfoundation.org/policies/nominatim/
"""
from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import Any

import httpx

from app.services.external_apis.base import (
    ExternalAPIAdapter,
    get_http_client,
)

_BASE_URL = "https://nominatim.openstreetmap.org"

# Nominatim policy: max 1 request per second.
_rate_lock = asyncio.Lock()
_MIN_INTERVAL = 1.0  # seconds
_last_call_time: float = 0.0


async def _rate_limited_get(client: httpx.AsyncClient, url: str, params: dict[str, Any]) -> httpx.Response:
    """Execute an HTTP GET, enforcing the 1-req/s Nominatim rate limit."""
    global _last_call_time
    async with _rate_lock:
        now = asyncio.get_event_loop().time()
        elapsed = now - _last_call_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        resp = await client.get(url, params=params)
        _last_call_time = asyncio.get_event_loop().time()
    return resp


class OSMAdapter(ExternalAPIAdapter):
    """Adapter for OpenStreetMap Nominatim geocoding.

    Nominatim's usage policy requires ≤ 1 request/second.  This adapter
    serialises all calls through a module-level asyncio lock and inserts
    the mandatory inter-call delay automatically.
    """

    slug = "osm"
    display_name = "OpenStreetMap Nominatim"
    requires_key = False
    key_env_var = None

    async def lookup(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        """Alias for :meth:`geocode` to satisfy the abstract interface."""
        query: str = args[0] if args else kwargs.get("query", "")
        return await self.geocode(query)

    async def geocode(self, query: str) -> list[dict[str, Any]]:
        """Forward geocode a place name or address.

        Parameters
        ----------
        query:
            Free-text location description (e.g. ``"Berlin, Germany"``).

        Returns
        -------
        list[dict] — each with ``place_id``, ``display_name``,
            ``lat``, ``lon``, ``type``, ``importance``, ``boundingbox``.
            Ordered by Nominatim's default relevance score.

        Raises
        ------
        ExternalAPIError
            On non-2xx responses or transport failures.
        """
        client = await get_http_client()
        params: dict[str, Any] = {
            "q": query,
            "format": "jsonv2",
            "limit": 5,
            "addressdetails": 1,
        }
        try:
            resp = await _rate_limited_get(client, f"{_BASE_URL}/search", params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._wrap_http_error(exc) from exc

        results = resp.json()
        return [
            {
                "place_id": item.get("place_id"),
                "display_name": item.get("display_name", ""),
                "lat": item.get("lat"),
                "lon": item.get("lon"),
                "type": item.get("type", ""),
                "importance": item.get("importance"),
                "boundingbox": item.get("boundingbox") or [],
            }
            for item in results
        ]

    async def reverse(self, lat: float, lon: float) -> dict[str, Any]:
        """Reverse geocode coordinates to a place.

        Parameters
        ----------
        lat:
            Latitude in decimal degrees.
        lon:
            Longitude in decimal degrees.

        Returns
        -------
        dict with keys: ``place_id``, ``display_name``, ``address``,
            ``lat``, ``lon``, ``type``.

        Raises
        ------
        ExternalAPIError
            On non-2xx responses or transport failures.
        """
        client = await get_http_client()
        params: dict[str, Any] = {
            "lat": lat,
            "lon": lon,
            "format": "jsonv2",
            "zoom": 18,
        }
        try:
            resp = await _rate_limited_get(client, f"{_BASE_URL}/reverse", params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._wrap_http_error(exc) from exc

        data = resp.json()
        return {
            "place_id": data.get("place_id"),
            "display_name": data.get("display_name", ""),
            "address": data.get("address") or {},
            "lat": data.get("lat"),
            "lon": data.get("lon"),
            "type": data.get("type", ""),
        }

    async def health_check(self) -> bool:
        """Return True when the Nominatim API is reachable."""
        client = await get_http_client()
        params: dict[str, Any] = {"q": "London", "format": "jsonv2", "limit": 1}
        try:
            resp = await _rate_limited_get(client, f"{_BASE_URL}/search", params)
            return resp.status_code < HTTPStatus.INTERNAL_SERVER_ERROR
        except Exception:  # noqa: BLE001
            return False
