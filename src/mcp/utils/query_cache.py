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
# Private fields on the stored JSON payload — stripped before handing to callers.
_STORED_AT_FIELD = "_cache_stored_at"
#: Task 7 fix round 1: the full domain list this entry is indexed under, so
#: any eviction path can SREM it out of every domain index it touches, not
#: just the one currently being processed.
_DOMAINS_SEARCHED_FIELD = "_cache_domains_searched"

#: Redis set of cache keys per domain (Task 7) — lets an ingest into domain D
#: evict exactly the entries whose result touched D, in O(entries touching D),
#: instead of the full SCAN every ingest previously paid for.
_DOMAIN_INDEX_PREFIX = CACHE_PREFIX + "domain_index:"

#: One-time marker, deliberately OUTSIDE the ``qcache:`` prefix so a full
#: flush never wipes it. Entries written before this domain index existed
#: carry no membership in any domain-index set and can only be found by a
#: full scan; the first domain-scoped invalidation pays that cost once to
#: retire them, then never needs to again — every entry stored after this
#: ships is domain-indexed.
_LEGACY_SWEPT_KEY = "qcache_meta:legacy_swept"


def _cache_key(query: str, domain: str, top_k: int, context_hint: str = "") -> str:
    raw = f"{query}|{domain}|{top_k}|{context_hint}"
    return CACHE_PREFIX + hashlib.sha256(raw.encode()).hexdigest()[:32]


def _domain_index_key(domain: str) -> str:
    return _DOMAIN_INDEX_PREFIX + domain


def get_cached(query: str, domain: str, top_k: int, context_hint: str = "") -> dict[str, Any] | None:
    try:
        key = _cache_key(query, domain, top_k, context_hint)
        raw = get_redis().get(key)
        if raw:
            logger.debug(f"Cache hit: {key[:20]}")
            stored = json.loads(raw)
            if isinstance(stored, dict):
                stored_at = stored.pop(_STORED_AT_FIELD, None)
                stored.pop(_DOMAINS_SEARCHED_FIELD, None)
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
    context_hint: str = "", domains_searched: list[str] | None = None,
) -> None:
    """``domains_searched`` (Task 7) is the envelope's actual post-filtering
    domain set — distinct from ``domain`` (the request-scope string baked
    into the cache key hash itself): it drives the per-domain invalidation
    index so an ingest into one domain evicts only entries whose result
    could be stale for it, instead of flushing the whole cache.
    """
    key = _cache_key(query, domain, top_k, context_hint)
    try:
        redis = get_redis()
        # Stamp private fields on a shallow copy so the caller's dict is not
        # mutated and does not leak internal bookkeeping from set → return.
        payload: dict[str, Any] = dict(result)
        payload[_STORED_AT_FIELD] = time.time()
        payload[_DOMAINS_SEARCHED_FIELD] = domains_searched or []
        redis.setex(key, ttl, json.dumps(payload, default=str))
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        logger.exception("query_cache.write_failed")
        sentry_sdk.capture_exception()
        return

    # Domain index (Task 7) — own try/except: a bookkeeping failure here must
    # not make this call report the entry as unstored when the setex above
    # already succeeded.
    if domains_searched:
        try:
            for d in domains_searched:
                idx_key = _domain_index_key(d)
                redis.sadd(idx_key, key)
                redis.expire(idx_key, ttl)
        except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
            logger.exception("query_cache.domain_index_write_failed")
            sentry_sdk.capture_exception()


def _full_flush(redis: Any) -> int:
    """Scan-and-delete every ``qcache:*`` key. Uses SCAN instead of KEYS to
    avoid blocking Redis on large keyspaces. Shared by :func:`invalidate_all`
    and :func:`invalidate_by_domain`'s one-time legacy-sweep fallback."""
    count = 0
    cursor = 0
    while True:
        cursor, keys = redis.scan(cursor, match=CACHE_PREFIX + "*", count=100)
        if keys:
            redis.delete(*keys)
            count += len(keys)
        if cursor == 0:
            break
    return count


def invalidate_all() -> None:
    """Called on ingest to bust all query caches."""
    try:
        redis = get_redis()
        count = _full_flush(redis)
        if count:
            logger.info(f"Invalidated {count} cached queries")
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        logger.exception("query_cache.invalidation_failed")
        sentry_sdk.capture_exception()


def _claim_legacy_sweep(redis: Any) -> bool:
    """Atomically claim the one-time legacy-sweep fallback.

    Task 7 fix round 1: a GET-then-SET here let two concurrent first-ever
    domain-scoped invalidations both observe the sentinel absent and both run
    a full flush. ``SET ... NX`` is a single atomic operation — it returns
    True for exactly one caller (the sentinel didn't exist yet, and this call
    created it) and False for every other caller (concurrent or later), so
    exactly one full flush ever happens.
    """
    return bool(redis.set(_LEGACY_SWEPT_KEY, "1", nx=True))


def invalidate_by_domain(domain: str) -> int:
    """Evict only C1 entries whose cached result touched ``domain`` — O(entries
    touching that domain) via the domain index, instead of the full-keyspace
    SCAN :func:`invalidate_all` performs (Task 7).

    The first call after this feature ships falls back to a full flush to
    retire entries written before the domain index existed (no recorded
    domain set); every entry stored after that ships is domain-indexed, so
    this fallback never repeats.

    Task 7 fix round 1: a member whose payload no longer exists (TTL already
    expired it) is dangling — dropped from this domain's index below but not
    counted as a fresh eviction. A member whose payload IS still live is also
    pruned out of every *other* domain index it was registered under (read
    from the payload's own recorded domain list), so a multi-domain entry
    doesn't leave dangling references behind in domains this invalidation
    never touches.
    """
    try:
        redis = get_redis()
        if _claim_legacy_sweep(redis):
            count = _full_flush(redis)
            if count:
                logger.info(f"Invalidated {count} cached queries (domain={domain}, legacy sweep)")
            return count

        idx_key = _domain_index_key(domain)
        members = redis.smembers(idx_key)
        if not members:
            return 0
        keys = [m.decode() if isinstance(m, bytes) else m for m in members]

        live_keys: list[str] = []
        payloads = redis.mget(keys)
        for key, raw in zip(keys, payloads):
            if not raw:
                continue  # already TTL-expired — dangling, not a fresh eviction
            live_keys.append(key)
            try:
                entry_domains = json.loads(raw).get(_DOMAINS_SEARCHED_FIELD) or []
            except (ValueError, TypeError):
                entry_domains = []
            for d in entry_domains:
                if d == domain:
                    continue
                redis.srem(_domain_index_key(d), key)

        if live_keys:
            redis.delete(*live_keys)
        redis.delete(idx_key)
        if live_keys:
            logger.info(f"Invalidated {len(live_keys)} cached queries (domain={domain})")
        return len(live_keys)
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        logger.exception("query_cache.invalidation_failed")
        sentry_sdk.capture_exception()
        return 0


async def invalidate_cache_non_blocking() -> None:
    """Async wrapper — runs invalidate_all() in a thread to avoid blocking the event loop."""
    await asyncio.to_thread(invalidate_all)


def invalidate_query_caches(trigger: str, redis: Any | None = None, domain: str | None = None) -> None:
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

    ``domain`` (Task 7) scopes both caches' eviction to entries whose result
    touched that domain, instead of flushing everything — an ingest into one
    domain previously busted every cached result regardless of domain,
    pinning ``cache_hit_rate`` at 0.0. Callers with no single domain to scope
    to (``domain=None`` — truly global mutations such as
    ``maintenance.purge_artifacts`` / ``memory.archive_old_memories``) keep
    the full-flush contract unchanged.
    """
    # Lazy import: keep this app-layer util free of a module-load-time core dep.
    from core.retrieval.semantic_cache import invalidate_cache

    if domain is not None:
        invalidate_by_domain(domain)  # C1 — uses get_redis() internally
    else:
        invalidate_all()
    try:
        client = redis if redis is not None else get_redis()
    except (RetrievalError, RuntimeError, OSError) as exc:
        logger.warning(
            "invalidate_query_caches: redis unavailable for C2 (trigger=%s, domain=%s): %s",
            trigger, domain, exc,
        )
        return
    invalidate_cache(client, trigger, domain=domain)  # C2 — internally defensive


async def invalidate_query_caches_non_blocking(
    trigger: str, redis: Any | None = None, domain: str | None = None,
) -> None:
    """Async wrapper — runs :func:`invalidate_query_caches` in a thread so hot
    ingest paths never block the event loop on the SCAN + chroma clear."""
    await asyncio.to_thread(invalidate_query_caches, trigger, redis, domain)


def _threaded_invalidate(trigger: str, redis: Any | None, domain: str | None = None) -> None:
    """Daemon-thread target: fully guarded so NO exception escapes the thread
    (an unhandled thread exception would surface as noise and, worse, a redis
    blip must never crash a background cache bust)."""
    try:
        invalidate_query_caches(trigger, redis, domain)
    except Exception:  # noqa: BLE001 — fire-and-forget; swallow everything in the thread
        logger.exception("invalidate_query_caches_threaded.failed")
        sentry_sdk.capture_exception()


def invalidate_query_caches_threaded(
    trigger: str, redis: Any | None = None, domain: str | None = None,
) -> None:
    """Fire-and-forget combined bust for SYNC call sites with no running event
    loop — the ingest chokepoints run in thread-pool workers, so they cannot
    await. Runs the full C1+C2 invalidation on a daemon thread (mirroring the
    semantic cache's own non-blocking idiom) so the caller never blocks on the
    SCANs. This is the single contract that replaced the prior C2-only hooks."""
    threading.Thread(
        target=_threaded_invalidate,
        args=(trigger, redis, domain),
        daemon=True,
        name=f"qcache-invalidate:{trigger[:40]}",
    ).start()
