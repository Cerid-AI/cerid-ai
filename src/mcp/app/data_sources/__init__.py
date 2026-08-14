# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""External data source framework -- pluggable APIs for knowledge enrichment.

Provides preloaded sources (Wikipedia, Wolfram Alpha, exchange rates) and a
registry for user-configurable custom REST API endpoints.

Enabled/disabled state is persisted in Redis (mirrors the external-apis
adapter pattern) so operator toggles survive process restarts. Redis key
schema: ``cerid:data_sources:{name}:enabled`` (value ``"1"`` / ``"0"``).
Persistence is best-effort: when Redis is unavailable the registry falls
back to in-memory state, matching the pre-persistence behaviour.

Dependencies: httpx (async HTTP), config/settings.py
Error types: none (source failures are silent -- never blocks retrieval)
"""
from __future__ import annotations

from typing import Any

from .base import DataSource, DataSourceRegistry, DataSourceResult, registry
from .bookmarks import BookmarksSource
from .duckduckgo import DuckDuckGoSource
from .email_imap import EmailImapSource
from .finance import ExchangeRatesSource
from .openlibrary import OpenLibrarySource
from .pubchem import PubChemSource
from .web_search_source import WebSearchDataSource
from .wikipedia import WikipediaSource
from .wolfram import WolframAlphaSource

# Auto-register preloaded sources
registry.register(WikipediaSource())
registry.register(WolframAlphaSource())
registry.register(ExchangeRatesSource())
registry.register(DuckDuckGoSource())
registry.register(OpenLibrarySource())
registry.register(PubChemSource())
registry.register(BookmarksSource())
registry.register(EmailImapSource())
registry.register(WebSearchDataSource())

# ---------------------------------------------------------------------------
# Redis-backed enable/disable persistence
# ---------------------------------------------------------------------------

_ENABLED_KEY_FMT = "cerid:data_sources:{name}:enabled"

# Set once hydration from Redis has succeeded; a failed/skipped hydration
# leaves this False so the next access retries (Redis may come up later).
_hydrated = False


def _enabled_key(name: str) -> str:
    return _ENABLED_KEY_FMT.format(name=name)


def _get_redis_client() -> Any | None:
    """Return the application Redis client, or None if unavailable."""
    try:
        from app.deps import get_redis
        return get_redis()
    except Exception:  # noqa: BLE001 — Redis absence degrades to in-memory state
        return None


def hydrate_enabled_state(
    redis_client: Any | None = None,
    *,
    reg: DataSourceRegistry = registry,
    force: bool = False,
) -> bool:
    """Load persisted enabled flags from Redis into the in-process registry.

    Idempotent: runs once per process unless ``force=True``. Sources without
    a persisted key keep their code default. Returns True when hydration ran
    against a live Redis client (and marks the process hydrated); False when
    Redis was unavailable or a read failed (so the next access retries).
    """
    global _hydrated
    if _hydrated and not force:
        return True
    client = redis_client if redis_client is not None else _get_redis_client()
    if client is None:
        return False
    for source in reg._sources.values():
        try:
            raw = client.get(_enabled_key(source.name))
        except Exception as exc:  # silent-catch-allowed: Redis read failure keeps in-memory defaults
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error("app.data_sources.hydrate_enabled_state", exc)
            return False
        if raw is not None:
            source.enabled = raw in ("1", b"1", 1)
    if reg is registry:
        _hydrated = True
    return True


def persist_enabled_state(
    name: str, enabled: bool, redis_client: Any | None = None,
) -> bool:
    """Write one source's enabled flag through to Redis (best-effort).

    Returns True on a successful write; False when Redis is unavailable or
    the write failed. Callers keep the in-memory flip either way so the API
    stays backward-compatible with Redis-less deployments.
    """
    client = redis_client if redis_client is not None else _get_redis_client()
    if client is None:
        return False
    try:
        client.set(_enabled_key(name), "1" if enabled else "0")
        return True
    except Exception as exc:  # silent-catch-allowed: persistence is best-effort; in-memory state already updated
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error("app.data_sources.persist_enabled_state", exc)
        return False


__all__ = [
    "DataSource",
    "DataSourceResult",
    "DataSourceRegistry",
    "hydrate_enabled_state",
    "persist_enabled_state",
    "registry",
]
