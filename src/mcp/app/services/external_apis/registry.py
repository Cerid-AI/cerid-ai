# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""External API registry — in-memory catalogue + Redis-backed enable state.

The registry knows about all eight adapters.  Enabled/disabled state is
persisted in Redis so operator toggles survive process restarts.

Redis key schema: ``cerid:external_apis:{slug}:enabled``  (value: ``"1"`` / ``"0"``).

Default enabled state:

* Keyless adapters (all eight in the initial set) → **enabled** by default.
* Key-required adapters (future; e.g. a premium API) → **disabled** by
  default when the env var is absent.

Integration note
----------------
Phase API.2 specifies that adapters should be registered through
``external_mcp_dispatch.py``.  That module currently has a bespoke
registration mechanism for MCP *servers* (via ``MCPClientManager``), not
standalone HTTP adapters.  Wiring these adapters into the MCP tool
dispatch path is deferred to a follow-up (API.3 integration); for now the
registry serves the REST management surface and the router can proxy
calls to adapter instances directly.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from app.services.external_apis.arxiv import ArxivAdapter
from app.services.external_apis.base import ExternalAPIAdapter
from app.services.external_apis.github import GitHubAdapter
from app.services.external_apis.openlibrary import OpenLibraryAdapter
from app.services.external_apis.osm import OSMAdapter
from app.services.external_apis.packages import PackagesAdapter
from app.services.external_apis.stackexchange import StackExchangeAdapter
from app.services.external_apis.wikidata import WikidataAdapter
from app.services.external_apis.wikipedia import WikipediaAdapter

logger = logging.getLogger("ai-companion.external_apis.registry")

_REDIS_KEY_FMT = "cerid:external_apis:{slug}:enabled"

# ---------------------------------------------------------------------------
# Adapter instances — one per process
# ---------------------------------------------------------------------------

_ADAPTERS: list[ExternalAPIAdapter] = [
    WikipediaAdapter(),
    WikidataAdapter(),
    OpenLibraryAdapter(),
    StackExchangeAdapter(),
    ArxivAdapter(),
    GitHubAdapter(),
    PackagesAdapter(),
    OSMAdapter(),
]

_BY_SLUG: dict[str, ExternalAPIAdapter] = {a.slug: a for a in _ADAPTERS}


def get_adapter(slug: str) -> ExternalAPIAdapter | None:
    """Return the adapter instance for ``slug``, or ``None`` if unknown."""
    return _BY_SLUG.get(slug)


def all_slugs() -> list[str]:
    """Return slugs for all registered adapters, in registration order."""
    return [a.slug for a in _ADAPTERS]


# ---------------------------------------------------------------------------
# Redis-backed enable/disable state
# ---------------------------------------------------------------------------


def _redis_key(slug: str) -> str:
    return _REDIS_KEY_FMT.format(slug=slug)


def _key_configured(adapter: ExternalAPIAdapter) -> bool:
    """Return True when the adapter's required key is present in the environment."""
    if not adapter.requires_key or adapter.key_env_var is None:
        return True
    return bool(os.getenv(adapter.key_env_var, "").strip())


def is_enabled(slug: str, redis_client: Any | None = None) -> bool:
    """Return True when the adapter identified by ``slug`` is enabled.

    Resolution order:
    1. Redis persisted value (``"1"`` / ``"0"``).
    2. Default: True for keyless adapters; False for key-required adapters
       when the key env var is absent.

    Parameters
    ----------
    slug:
        Adapter slug (e.g. ``"wikipedia"``).
    redis_client:
        Optional Redis client.  When ``None``, returns the default state.
    """
    adapter = _BY_SLUG.get(slug)
    if adapter is None:
        return False

    if redis_client is not None:
        try:
            raw = redis_client.get(_redis_key(slug))
            if raw is not None:
                return raw in ("1", b"1", 1)
        except Exception:  # noqa: BLE001 — observability must not break calls
            logger.debug("Redis get failed for %s enabled flag", slug)

    # Default: enabled iff the key requirement is satisfied
    return _key_configured(adapter)


def set_enabled(slug: str, value: bool, redis_client: Any) -> None:
    """Persist the enabled flag for an adapter to Redis.

    Parameters
    ----------
    slug:
        Adapter slug.
    value:
        ``True`` to enable; ``False`` to disable.
    redis_client:
        Redis client — required.  Raises if the write fails.
    """
    redis_client.set(_redis_key(slug), "1" if value else "0")


def list_adapters(redis_client: Any | None = None) -> list[dict[str, Any]]:
    """Return a catalogue of all registered adapters and their current state.

    Parameters
    ----------
    redis_client:
        Optional Redis client for reading persisted enable flags.

    Returns
    -------
    list[dict] — each with: ``slug``, ``display_name``, ``enabled``,
        ``requires_key``, ``key_configured``.
    """
    return [
        {
            "slug": a.slug,
            "display_name": a.display_name,
            "enabled": is_enabled(a.slug, redis_client),
            "requires_key": a.requires_key,
            "key_configured": _key_configured(a),
        }
        for a in _ADAPTERS
    ]
