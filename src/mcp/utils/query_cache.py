# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Redis-based query cache for /query and /agent/query results.

Cache keys use a SHA-256 hash of (query, domain, top_k).
TTL: 5 minutes. Invalidated on any ingest.

Cached responses are enriched on read with ``cached: True`` and
``cache_age_ms`` so callers (and the metrics middleware, which stamps
``X-Cache: HIT``) can distinguish warm from cold without timing it.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

import sentry_sdk

from deps import get_redis
from errors import RetrievalError

logger = logging.getLogger("ai-companion.cache")

CACHE_PREFIX = "qcache:"
DEFAULT_TTL = 300  # 5 minutes
# Private field on the stored JSON payload — stripped before handing to callers.
_STORED_AT_FIELD = "_cache_stored_at"


def _cache_key(query: str, domain: str, top_k: int, context_hint: str = "") -> str:
    raw = f"{query}|{domain}|{top_k}|{context_hint}"
    return CACHE_PREFIX + hashlib.sha256(raw.encode()).hexdigest()[:32]


def get_cached(query: str, domain: str, top_k: int, context_hint: str = "") -> dict[str, Any] | None:
    try:
        key = _cache_key(query, domain, top_k, context_hint)
        raw = get_redis().get(key)
        if raw:
            logger.debug(f"Cache hit: {key[:20]}")
            stored = json.loads(raw)
            if isinstance(stored, dict):
                stored_at = stored.pop(_STORED_AT_FIELD, None)
                now = time.time()
                if isinstance(stored_at, (int, float)):
                    age_ms = max(0, int((now - stored_at) * 1000))
                else:
                    age_ms = 0
                stored["cached"] = True
                stored["cache_age_ms"] = age_ms
            return stored
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        logger.exception("query_cache.read_failed")
        sentry_sdk.capture_exception()
    return None


def set_cached(
    query: str, domain: str, top_k: int, result: dict[str, Any], ttl: int = DEFAULT_TTL,
    context_hint: str = "",
) -> None:
    try:
        key = _cache_key(query, domain, top_k, context_hint)
        # Stamp a private timestamp on a shallow copy so the caller's dict is
        # not mutated and does not leak the "cached" flag from set → return.
        payload: dict[str, Any] = dict(result)
        payload[_STORED_AT_FIELD] = time.time()
        get_redis().setex(key, ttl, json.dumps(payload, default=str))
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        logger.exception("query_cache.write_failed")
        sentry_sdk.capture_exception()


def invalidate_all() -> None:
    """Called on ingest to bust all query caches.

    Uses SCAN instead of KEYS to avoid blocking Redis on large keyspaces.
    """
    try:
        redis = get_redis()
        count = 0
        cursor = 0
        while True:
            cursor, keys = redis.scan(cursor, match=CACHE_PREFIX + "*", count=100)
            if keys:
                redis.delete(*keys)
                count += len(keys)
            if cursor == 0:
                break
        if count:
            logger.info(f"Invalidated {count} cached queries")
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        logger.exception("query_cache.invalidation_failed")
        sentry_sdk.capture_exception()


async def invalidate_cache_non_blocking() -> None:
    """Async wrapper — runs invalidate_all() in a thread to avoid blocking the event loop."""
    await asyncio.to_thread(invalidate_all)
