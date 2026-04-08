# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Community plugin registry client — fetches plugin metadata from a GitHub-hosted registry."""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger("ai-companion.plugin_registry")

# Default registry URL (GitHub raw JSON).  Override via PLUGIN_REGISTRY_URL env var.
REGISTRY_URL = os.getenv(
    "PLUGIN_REGISTRY_URL",
    "https://raw.githubusercontent.com/Cerid-AI/plugin-registry/main/registry.json",
)

_CACHE_TTL = 3600  # 1 hour


class PluginRegistryClient:
    """HTTP client for the community plugin registry."""

    def __init__(self, url: str = REGISTRY_URL) -> None:
        self._url = url
        self._cache: list[dict[str, Any]] = []
        self._cache_ts: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_registry(self) -> list[dict[str, Any]]:
        """Fetch the full registry JSON from GitHub.  Caches for 1 hour."""
        now = time.monotonic()
        if self._cache and (now - self._cache_ts) < _CACHE_TTL:
            return self._cache

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(self._url)
                resp.raise_for_status()
                data = resp.json()
                # Registry is either a list or {"plugins": [...]}
                plugins = data if isinstance(data, list) else data.get("plugins", [])
                self._cache = plugins
                self._cache_ts = now
                logger.info("Plugin registry fetched: %d entries", len(plugins))
                return plugins
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch plugin registry: %s", exc)
            # Return stale cache on failure (graceful degradation)
            return self._cache

    async def search(
        self,
        query: str = "",
        plugin_type: str = "",
    ) -> list[dict[str, Any]]:
        """Filter registry entries by query string and/or plugin type."""
        entries = await self.fetch_registry()
        results = entries

        if plugin_type:
            results = [e for e in results if e.get("type", "") == plugin_type]

        if query:
            q = query.lower()
            results = [
                e
                for e in results
                if q in e.get("name", "").lower()
                or q in e.get("description", "").lower()
                or q in " ".join(e.get("tags", [])).lower()
            ]

        return results

    async def get_plugin(self, name: str) -> dict[str, Any] | None:
        """Get a single registry entry by name."""
        entries = await self.fetch_registry()
        for entry in entries:
            if entry.get("name") == name:
                return entry
        return None


# Module-level singleton
plugin_registry_client = PluginRegistryClient()
