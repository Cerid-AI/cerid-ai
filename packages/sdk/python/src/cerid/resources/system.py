# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""System resource: health, settings, plugins."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cerid.errors import _raise_for_status
from cerid.models import (
    DetailedHealthResponse,
    HealthResponse,
    PluginListResponse,
    SettingsResponse,
)

if TYPE_CHECKING:
    import httpx

    from cerid._base import _BaseClient


class SystemResource:
    """Synchronous system operations."""

    def __init__(self, client: _BaseClient, http: httpx.Client) -> None:
        self._client = client
        self._http = http

    def health(self) -> HealthResponse:
        """Service connectivity and feature flags."""
        resp = self._http.get(self._client._url("/health"))
        _raise_for_status(resp)
        return HealthResponse.model_validate(resp.json())

    def health_detailed(self) -> DetailedHealthResponse:
        """Extended health with circuit breaker states and degradation tier."""
        resp = self._http.get(self._client._url("/health/detailed"))
        _raise_for_status(resp)
        return DetailedHealthResponse.model_validate(resp.json())

    def settings(self) -> SettingsResponse:
        """Read-only server configuration: version, tier, feature flags."""
        resp = self._http.get(self._client._url("/settings"))
        _raise_for_status(resp)
        return SettingsResponse.model_validate(resp.json())

    def plugins(self) -> PluginListResponse:
        """List all loaded plugins with their status."""
        resp = self._http.get(self._client._url("/plugins"))
        _raise_for_status(resp)
        return PluginListResponse.model_validate(resp.json())


class AsyncSystemResource:
    """Asynchronous system operations."""

    def __init__(self, client: _BaseClient, http: httpx.AsyncClient) -> None:
        self._client = client
        self._http = http

    async def health(self) -> HealthResponse:
        """Service connectivity and feature flags."""
        resp = await self._http.get(self._client._url("/health"))
        _raise_for_status(resp)
        return HealthResponse.model_validate(resp.json())

    async def health_detailed(self) -> DetailedHealthResponse:
        """Extended health with circuit breaker states and degradation tier."""
        resp = await self._http.get(self._client._url("/health/detailed"))
        _raise_for_status(resp)
        return DetailedHealthResponse.model_validate(resp.json())

    async def settings(self) -> SettingsResponse:
        """Read-only server configuration: version, tier, feature flags."""
        resp = await self._http.get(self._client._url("/settings"))
        _raise_for_status(resp)
        return SettingsResponse.model_validate(resp.json())

    async def plugins(self) -> PluginListResponse:
        """List all loaded plugins with their status."""
        resp = await self._http.get(self._client._url("/plugins"))
        _raise_for_status(resp)
        return PluginListResponse.model_validate(resp.json())
