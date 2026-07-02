# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Package registry adapter — PyPI + npm (Phase API.1).

Sources:
* PyPI JSON API: https://pypi.org/pypi/{package}/json
* npm registry:  https://registry.npmjs.org/{package}

No authentication required for either registry.
"""
from __future__ import annotations

from http import HTTPStatus
from typing import Any, Literal

import httpx

from app.services.external_apis.base import (
    ExternalAPIAdapter,
    get_http_client,
)

_PYPI_URL = "https://pypi.org/pypi/{package}/json"
_NPM_URL = "https://registry.npmjs.org/{package}"


class PackagesAdapter(ExternalAPIAdapter):
    """Adapter for PyPI and npm package metadata."""

    slug = "packages"
    display_name = "PyPI / npm"
    requires_key = False
    key_env_var = None

    async def lookup(  # type: ignore[override]
        self,
        name: str,
        ecosystem: Literal["pypi", "npm"] = "pypi",
    ) -> dict[str, Any]:
        """Dispatch to :meth:`pypi` or :meth:`npm` based on ``ecosystem``.

        Parameters
        ----------
        name:
            Package name (case-insensitive for PyPI; exact for npm).
        ecosystem:
            ``"pypi"`` or ``"npm"``.

        Raises
        ------
        ExternalAPIError
            On non-2xx responses or transport failures.
        ValueError
            When ``ecosystem`` is not ``"pypi"`` or ``"npm"``.
        """
        if ecosystem == "pypi":
            return await self.pypi(name)
        if ecosystem == "npm":
            return await self.npm(name)
        raise ValueError(f"Unknown ecosystem: {ecosystem!r}. Use 'pypi' or 'npm'.")

    async def pypi(self, package: str) -> dict[str, Any]:
        """Fetch metadata for a PyPI package.

        Parameters
        ----------
        package:
            Package name on PyPI (e.g. ``"httpx"``).

        Returns
        -------
        dict with keys: ``name``, ``version``, ``summary``,
            ``author``, ``license``, ``home_page``, ``requires_python``.

        Raises
        ------
        ExternalAPIError
            On non-2xx responses.
        """
        client = await get_http_client()
        url = _PYPI_URL.format(package=package)
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._wrap_http_error(exc) from exc

        data = resp.json()
        info = data.get("info") or {}
        return {
            "name": info.get("name", ""),
            "version": info.get("version", ""),
            "summary": info.get("summary") or "",
            "author": info.get("author") or "",
            "license": info.get("license") or "",
            "home_page": info.get("home_page") or info.get("project_url") or "",
            "requires_python": info.get("requires_python") or "",
        }

    async def npm(self, package: str) -> dict[str, Any]:
        """Fetch metadata for an npm package.

        Parameters
        ----------
        package:
            Package name on the npm registry (e.g. ``"react"``).
            Scoped packages must be URL-encoded (``@scope%2Fname``).

        Returns
        -------
        dict with keys: ``name``, ``version``, ``description``,
            ``author``, ``license``, ``homepage``, ``keywords``.

        Raises
        ------
        ExternalAPIError
            On non-2xx responses.
        """
        client = await get_http_client()
        url = _NPM_URL.format(package=package)
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._wrap_http_error(exc) from exc

        data = resp.json()
        dist_tags = data.get("dist-tags") or {}
        latest_version = dist_tags.get("latest", "")
        versions = data.get("versions") or {}
        version_meta = versions.get(latest_version) or {}
        author_raw = version_meta.get("author") or {}
        author_str = (
            author_raw.get("name", "") if isinstance(author_raw, dict)
            else str(author_raw)
        )
        return {
            "name": data.get("name", ""),
            "version": latest_version,
            "description": data.get("description") or "",
            "author": author_str,
            "license": version_meta.get("license") or data.get("license") or "",
            "homepage": data.get("homepage") or version_meta.get("homepage") or "",
            "keywords": data.get("keywords") or [],
        }

    async def health_check(self) -> bool:
        """Return True when the PyPI registry is reachable."""
        client = await get_http_client()
        try:
            resp = await client.get(_PYPI_URL.format(package="pip"))
            return resp.status_code < HTTPStatus.INTERNAL_SERVER_ERROR
        except Exception:  # noqa: BLE001
            return False
