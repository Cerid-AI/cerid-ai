# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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
import threading
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


def invalidate_query_caches(trigger: str, redis: Any | None = None) -> None:
    """Bust BOTH query-result caches in one call — the single invalidation
    contract every content mutation funnels through (audit CL-14).

    - C1 (this module, ``qcache:*``) is app-bound and always uses the app Redis
      singleton via :func:`get_redis`.
    - C2 (``core.retrieval.semantic_cache``, ``semcache:*``) takes a Redis handle;
      the ``redis`` arg routes only C2 and defaults to :func:`get_redis` when omitted.

    Both underlying invalidators are internally defensive, so a failure of one
    cache never aborts the other. ``trigger`` names the mutation for the
    ``cache_invalidation_count`` metric. The graph serving cache (C3,
    ``cerid:graph:emb3d:*``) is deliberately NOT busted here — it is a nightly
    viz cache owned by scheduler jobs (CL-6); content-removal busts it separately.
    """
    # Lazy import: keep this app-layer util free of a module-load-time core dep.
    from core.retrieval.semantic_cache import invalidate_cache

    invalidate_all()  # C1 — uses get_redis() internally
    try:
        client = redis if redis is not None else get_redis()
    except (RetrievalError, RuntimeError, OSError) as exc:
        logger.warning("invalidate_query_caches: redis unavailable for C2 (trigger=%s): %s", trigger, exc)
        return
    invalidate_cache(client, trigger)  # C2 — internally defensive


async def invalidate_query_caches_non_blocking(trigger: str, redis: Any | None = None) -> None:
    """Async wrapper — runs :func:`invalidate_query_caches` in a thread so hot
    ingest paths never block the event loop on the SCAN + chroma clear."""
    await asyncio.to_thread(invalidate_query_caches, trigger, redis)


def _threaded_invalidate(trigger: str, redis: Any | None) -> None:
    """Daemon-thread target: fully guarded so NO exception escapes the thread
    (an unhandled thread exception would surface as noise and, worse, a redis
    blip must never crash a background cache bust)."""
    try:
        invalidate_query_caches(trigger, redis)
    except Exception:  # noqa: BLE001 — fire-and-forget; swallow everything in the thread
        logger.exception("invalidate_query_caches_threaded.failed")
        sentry_sdk.capture_exception()


def invalidate_query_caches_threaded(trigger: str, redis: Any | None = None) -> None:
    """Fire-and-forget combined bust for SYNC call sites with no running event
    loop — the ingest chokepoints run in thread-pool workers, so they cannot
    await. Runs the full C1+C2 invalidation on a daemon thread (mirroring the
    semantic cache's own non-blocking idiom) so the caller never blocks on the
    SCANs. This is the single contract that replaced the prior C2-only hooks."""
    threading.Thread(
        target=_threaded_invalidate,
        args=(trigger, redis),
        daemon=True,
        name=f"qcache-invalidate:{trigger[:40]}",
    ).start()
