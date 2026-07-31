# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Semantic query cache — chromadb-collection backend.

Embedding+similarity index lives in a dedicated chromadb collection
(``semantic_query_cache``). Result payloads remain in Redis with TTL,
unchanged from the prior HNSW implementation.

The application layer registers the collection at startup via
:func:`set_cache_backend`; this module stays layering-correct (no
chromadb import in ``core/``) by accepting any duck-typed handle that
matches :class:`_CacheBackend`. ``chromadb.Collection`` conforms.

Public API preserved across the 2026-05-08 hnswlib retirement: callers
still pass a ``redis_client`` (used for the result-payload key/TTL) and
the index is opaque to them.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any, Protocol

import numpy as np
import sentry_sdk

from config.features import (
    SEMANTIC_CACHE_MAX_ENTRIES,
    SEMANTIC_CACHE_THRESHOLD,
    SEMANTIC_CACHE_TTL,
)
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.semantic_cache")

_CACHE_PREFIX = "semcache:"

#: Redis sorted set (entry_id -> stored-at timestamp) used for FIFO
#: size-bound eviction (Phase 2.2). Shares the ``semcache:`` prefix so
#: ``invalidate_cache``'s SCAN clears it for free alongside every entry.
_AGE_INDEX_KEY = _CACHE_PREFIX + "age_index"

#: Watermark of the last full invalidation, consulted by the stale-hit
#: check in ``cache_lookup``. Deliberately OUTSIDE the ``semcache:``
#: prefix so it survives ``invalidate_cache``'s SCAN+DELETE sweep instead
#: of being wiped by the very event it needs to remember.
_LAST_INVALIDATED_KEY = "semcache_meta:last_invalidated_at"
_LAST_INVALIDATED_TTL_SECONDS = 7 * 24 * 60 * 60  # outlives any single entry's TTL


def _entry_key(entry_id: str) -> str:
    return _CACHE_PREFIX + "entry:" + entry_id


# ---------------------------------------------------------------------------
# Backend Protocol
# ---------------------------------------------------------------------------

class _CacheBackend(Protocol):
    """Duck-typed contract for the embedding-index handle.

    Production: a ``chromadb.Collection`` (registered by ``app/main.py`` at
    startup). Tests: a fake conforming to this shape.
    """
    def query(
        self,
        *,
        query_embeddings: list[list[float]],
        n_results: int = 1,
        include: list[str] | None = None,
    ) -> dict[str, Any]: ...

    def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]] | None = None,
    ) -> None: ...

    def delete(
        self,
        ids: list[str] | None = None,
        where: dict[str, Any] | None = None,
    ) -> None: ...

    def get(self) -> dict[str, Any]: ...

    def count(self) -> int: ...


# ---------------------------------------------------------------------------
# Backend registration
# ---------------------------------------------------------------------------

_backend: _CacheBackend | None = None
_backend_lock = threading.Lock()


def set_cache_backend(backend: _CacheBackend | None) -> None:
    """Register (or clear) the semantic-cache index backend.

    Wired by ``app/main.py`` at startup once the chroma collection is
    available. Pass ``None`` to disable the cache (treated identically
    to ``ENABLE_SEMANTIC_CACHE=false``).
    """
    global _backend
    with _backend_lock:
        _backend = backend


def _get_backend() -> _CacheBackend | None:
    return _backend


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _record_cache_hit(redis_client: Any, hit: bool) -> None:
    """Record a semantic-cache hit (1.0) or genuine miss (0.0) into the
    time-series collector that /observability/quality reads (Phase 4.3 —
    ``cache_hit_rate`` was declared in METRIC_NAMES but never recorded).

    Best-effort: never raises into the cache-lookup return path. Called only
    on genuine hit/miss outcomes, NOT on disabled/error returns (those would
    skew the rate toward a misleading 0).
    """
    try:
        from utils.metrics import MetricsCollector
        MetricsCollector(redis_client).record_metric(
            "cache_hit_rate", 1.0 if hit else 0.0,
        )
    except Exception as exc:
        log_swallowed_error("core.retrieval.semantic_cache.hit_metric", exc)


def _record_cache_invalidation(redis_client: Any, count: int, trigger: str) -> None:
    """Record an invalidation event (entries dropped + trigger source) so
    operators can see which mutation paths drive cache churn (Phase 2.2 —
    ``invalidate_cache`` previously had no production caller at all).

    Best-effort: never raises into ``invalidate_cache``'s return path.
    """
    try:
        from utils.metrics import MetricsCollector
        MetricsCollector(redis_client).record_metric(
            "cache_invalidation_count", float(count), tags={"trigger": trigger},
        )
    except Exception as exc:
        log_swallowed_error("core.retrieval.semantic_cache.invalidation_metric", exc)


def _check_stale_hit(redis_client: Any, payload: dict[str, Any], entry_id: str) -> bool:
    """Return True when a candidate predates the last known invalidation —
    evidence the invalidation SCAN missed it or the backend ``delete(where={})``
    failed, rather than the corpus being confirmed fresh. The caller must NOT
    serve a stale entry (CR-046). One extra Redis GET on the hit path only;
    never raises.
    """
    try:
        watermark_raw = redis_client.get(_LAST_INVALIDATED_KEY)
        if not watermark_raw:
            return False  # no invalidation has run yet — nothing to compare against
        last_invalidated_at = float(watermark_raw)
        stored_at = float(payload.get("stored_at", 0.0))
        if stored_at < last_invalidated_at:
            logger.warning(
                "Semantic cache stale hit: id=%s stored_at=%.0f last_invalidated_at=%.0f",
                entry_id[:12], stored_at, last_invalidated_at,
            )
            from utils.metrics import MetricsCollector
            MetricsCollector(redis_client).record_metric("cache_stale_hit_count", 1.0)
            return True
    except Exception as exc:
        log_swallowed_error("core.retrieval.semantic_cache.stale_hit_check", exc)
    return False


def _scope_token(
    domains: list[str] | None,
    allowed_domains: list[str] | None = None,
    *,
    memory_enabled: bool = True,
) -> str:
    """Canonical token for the domain filter, consumer wall, and memory gate.

    ``allowed_domains`` is the calling consumer's effective domain restriction
    (``None`` = unrestricted, e.g. gui/a2a/_default). Folding it into the token
    stops a strict consumer (cerid-finance, trading-agent) from receiving an
    unrestricted consumer's cached result under the same raw domain filter —
    the C2 half of CR-001, mirroring the C1 ``context_hint`` consumer scope.

    ``memory_enabled`` (E1 R16 / CR-016): a memory-ON envelope must never be
    served to the same query with Memory OFF. Default True keeps the historical
    token so unrestricted+memory-on entries stay backward-compatible.

    Backward-compatible by construction: when ``allowed_domains is None`` and
    memory is on, the token is the historical domain-only string.
    """
    domain_part = ",".join(sorted(domains)) if domains else "__all__"
    if allowed_domains is None:
        base = domain_part
    else:
        allow_part = ",".join(sorted(allowed_domains)) if allowed_domains else "__all__"
        base = f"{domain_part}|allow={allow_part}"
    if not memory_enabled:
        return f"{base}|mem=0"
    return base


# ANN candidates examined per lookup — the nearest embedding may belong to a
# different domain scope, so we scan a few neighbors for a same-scope hit.
_LOOKUP_CANDIDATES = 3


def cache_lookup(
    query_embedding: np.ndarray,
    redis_client: Any,
    threshold: float | None = None,
    domains: list[str] | None = None,
    allowed_domains: list[str] | None = None,
    *,
    memory_enabled: bool = True,
) -> dict[str, Any] | None:
    """Check if a semantically similar query exists in the cache.

    ``domains`` must match the scope the entry was stored under — the same
    query text against different domain filters returns different results
    (live-caught 2026-07-13: a cross-domain query was served another
    domain's cached result at sim=1.0). ``allowed_domains`` must likewise match
    the consumer wall the entry was stored under, so a strict consumer never
    receives an unrestricted consumer's result (CR-001). ``memory_enabled``
    scopes Memory ON vs OFF (E1 R16). Legacy scope-less entries never match
    and age out via TTL.

    Returns the cached result dict, or None on miss / disabled / error.
    """
    backend = _get_backend()
    if backend is None:
        return None

    thresh = threshold if threshold is not None else SEMANTIC_CACHE_THRESHOLD
    scope = _scope_token(domains, allowed_domains, memory_enabled=memory_enabled)

    try:
        if backend.count() == 0:
            _record_cache_hit(redis_client, hit=False)
            return None

        emb = np.asarray(query_embedding, dtype=np.float32).reshape(-1).tolist()
        result = backend.query(
            query_embeddings=[emb],
            n_results=_LOOKUP_CANDIDATES,
            include=["distances"],
        )

        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        for entry_id_raw, distance in zip(ids, distances):
            entry_id = str(entry_id_raw)
            # cosine space: distance == 1 - cos_sim
            similarity = 1.0 - float(distance)
            if similarity < thresh:
                break  # candidates are distance-ordered; the rest are worse

            result_raw = redis_client.get(_entry_key(entry_id))
            if not result_raw:
                # Payload expired (Redis TTL) but the index still has the
                # embedding — lazy-evict the orphan so the index doesn't
                # grow unbounded as TTLs cycle. Also drop age-index score
                # so FIFO eviction bookkeeping stays accurate.
                try:
                    backend.delete(ids=[entry_id])
                    redis_client.zrem(_AGE_INDEX_KEY, entry_id)
                except Exception as exc:
                    log_swallowed_error("core.retrieval.semantic_cache.orphan_evict", exc)
                continue

            payload = json.loads(result_raw)
            if not (isinstance(payload, dict) and "result" in payload):
                continue  # legacy scope-less entry — never serve, let TTL evict
            if payload.get("domain_scope") != scope:
                continue

            if _check_stale_hit(redis_client, payload, entry_id):
                # Predates the last invalidation — the SCAN/delete missed it.
                # Do NOT serve it; evict the orphan and try the next candidate
                # (or fall through to a miss) so a stale answer can't be returned
                # after the corpus changed (CR-046). Drop age-index entry too
                # (stale-evict age-index leak residual).
                try:
                    backend.delete(ids=[entry_id])
                    redis_client.delete(_entry_key(entry_id))
                    redis_client.zrem(_AGE_INDEX_KEY, entry_id)
                except Exception as exc:
                    log_swallowed_error("core.retrieval.semantic_cache.stale_evict", exc)
                continue

            logger.info(
                "Semantic cache hit (sim=%.4f, id=%s, scope=%s)",
                similarity, entry_id[:12], scope,
            )
            _record_cache_hit(redis_client, hit=True)
            return payload["result"]

        _record_cache_hit(redis_client, hit=False)
        return None

    except Exception:
        logger.exception("semantic_cache.lookup_failed")
        sentry_sdk.capture_exception()
        return None


def cache_store(
    query: str,
    query_embedding: np.ndarray,
    result: dict[str, Any],
    redis_client: Any,
    ttl: int | None = None,
    max_entries: int | None = None,
    domains: list[str] | None = None,
    allowed_domains: list[str] | None = None,
    *,
    memory_enabled: bool = True,
) -> None:
    """Store a query result in the semantic cache.

    Result payload goes to Redis (with TTL); embedding goes to the chroma
    collection backend (no native TTL — orphans evicted lazily on lookup).
    The entry is keyed and scope-tagged by ``domains``, the consumer's
    ``allowed_domains`` wall, and ``memory_enabled`` so lookups never cross
    domain filters, consumer isolation, or Memory ON/OFF (CR-001 / R16).
    """
    backend = _get_backend()
    if backend is None:
        return

    # Empty and degraded results are never cached: an empty is cheap to
    # recompute and usually a load artifact (budget expiry, rerank errors);
    # serving it at sim>=threshold poisons every similar query for the TTL
    # (live-caught 2026-07-13 by the retrieval harness).
    if result.get("budget_exceeded") or not result.get("sources"):
        logger.debug("Semantic cache skip: empty/degraded result not stored")
        return

    cache_ttl = ttl if ttl is not None else SEMANTIC_CACHE_TTL
    scope = _scope_token(domains, allowed_domains, memory_enabled=memory_enabled)

    try:
        entry_id = hashlib.sha256(f"{scope}|{query}".encode()).hexdigest()[:16]
        now = time.time()

        redis_client.setex(
            _entry_key(entry_id),
            cache_ttl,
            json.dumps(
                {"domain_scope": scope, "result": result, "stored_at": now},
                default=str,
            ),
        )

        emb = np.asarray(query_embedding, dtype=np.float32).reshape(-1).tolist()
        backend.upsert(
            ids=[entry_id],
            embeddings=[emb],
            metadatas=[{"created_at": now, "domain_scope": scope}],
        )

        logger.debug(
            "Semantic cache stored: %s (ttl=%ds, scope=%s)",
            entry_id[:12], cache_ttl, scope,
        )

    except Exception as e:
        log_swallowed_error('core.retrieval.semantic_cache', e)
        logger.warning("Semantic cache store failed: %s", e)
        return

    # Size bound (Phase 2.2) — FIFO eviction once the age index passes
    # ``max_entries`` / ``SEMANTIC_CACHE_MAX_ENTRIES``. Own try/except: a
    # bookkeeping failure here must not make this call report the entry as
    # unstored when the redis/chroma writes above already succeeded.
    bound = max_entries if max_entries is not None else SEMANTIC_CACHE_MAX_ENTRIES
    try:
        redis_client.zadd(_AGE_INDEX_KEY, {entry_id: now})
        _evict_oldest_if_over_bound(redis_client, backend, bound)
    except Exception as exc:
        log_swallowed_error("core.retrieval.semantic_cache.age_index", exc)


def _evict_oldest_if_over_bound(redis_client: Any, backend: _CacheBackend, bound: int) -> None:
    """FIFO-evict the oldest entries once the age index passes ``bound``.

    Phase 2.2 enforcement point — ``max_entries`` / ``SEMANTIC_CACHE_MAX_ENTRIES``
    was accepted by ``cache_store`` but never consulted before this (the
    prior ``_capacity_warning_threshold`` was advisory-only, no eviction).
    The age index is a Redis sorted set scored oldest-first, so eviction
    never needs to enumerate the chroma backend.
    """
    overflow = int(redis_client.zcard(_AGE_INDEX_KEY)) - bound
    if overflow <= 0:
        return
    oldest = redis_client.zrange(_AGE_INDEX_KEY, 0, overflow - 1)
    if not oldest:
        return
    stale_ids = [eid.decode() if isinstance(eid, bytes) else eid for eid in oldest]

    redis_client.delete(*(_entry_key(eid) for eid in stale_ids))
    redis_client.zrem(_AGE_INDEX_KEY, *stale_ids)
    try:
        backend.delete(ids=stale_ids)
    except Exception as exc:
        log_swallowed_error("core.retrieval.semantic_cache.evict_backend", exc)

    logger.info(
        "Semantic cache evicted %d oldest entries (bound=%d)", len(stale_ids), bound,
    )


def flush_cache(redis_client: Any) -> None:
    """No-op kept for public-API stability.

    The prior HNSW backend buffered writes and persisted to Redis on
    shutdown; the chroma backend persists every upsert immediately, so
    there is nothing to flush. Shutdown call sites can keep calling this
    safely — the function is retained as a public-API hinge.
    """
    return None


def invalidate_cache(redis_client: Any, trigger: str = "unspecified") -> int:
    """Clear all semantic cache entries (Redis payloads + index).

    ``trigger`` names the caller (e.g. ``"ingestion.ingest_content"``,
    ``"kb_admin.clear_domain"``) and is recorded on the
    ``cache_invalidation_count`` metric so operators can see which mutation
    paths drive cache churn (Phase 2.2 — this function previously had no
    production caller at all, leaving up to a full TTL of stale results
    served after any corpus mutation).
    """
    try:
        count = 0
        cursor = 0
        while True:
            cursor, keys = redis_client.scan(
                cursor, match=_CACHE_PREFIX + "*", count=100
            )
            if keys:
                redis_client.delete(*keys)
                count += len(keys)
            if cursor == 0:
                break

        backend = _get_backend()
        if backend is not None:
            try:
                # chromadb 1.x rejects an empty `where` ("Expected where to have
                # exactly one operator") — the 0.5-era clear-all idiom used here
                # threw on every mutation, so the embedding index was never
                # actually cleared. Delete by id instead.
                existing = backend.get() or {}
                ids = [str(i) for i in (existing.get("ids") or [])]
                if ids:
                    backend.delete(ids=ids)
            except Exception as exc:
                log_swallowed_error(
                    "core.retrieval.semantic_cache.backend_clear",
                    exc,
                    redis_client=redis_client,
                )

        # Watermark for cache_lookup's stale-hit check — deliberately
        # outside the semcache: prefix so the SCAN above never sweeps it.
        try:
            redis_client.setex(
                _LAST_INVALIDATED_KEY, _LAST_INVALIDATED_TTL_SECONDS, str(time.time()),
            )
        except Exception as exc:
            log_swallowed_error("core.retrieval.semantic_cache.watermark_write", exc)

        _record_cache_invalidation(redis_client, count, trigger)

        if count:
            logger.info(
                "Semantic cache invalidated: %d keys (trigger=%s)", count, trigger,
            )
        return count
    except Exception as e:
        log_swallowed_error('core.retrieval.semantic_cache', e)
        logger.warning("Semantic cache invalidation failed: %s", e)
        return 0


def invalidate_cache_non_blocking(redis_client: Any, trigger: str = "unspecified") -> None:
    """Fire-and-forget wrapper around :func:`invalidate_cache`.

    The ingest mutation chokepoints (``app/services/ingestion.py``) are
    synchronous functions invoked as often from a thread-pool worker
    (``asyncio.to_thread``, the ``/ingest`` REST path) as from the
    event-loop thread, so ``utils/query_cache``'s async
    ``create_task``-on-a-running-loop idiom doesn't apply uniformly here —
    a thread-pool worker has no running event loop to schedule onto. A
    daemon thread keeps ``invalidate_cache``'s Redis SCAN + chroma
    ``delete(where={})`` off the ingest path regardless of calling context.
    """
    threading.Thread(
        target=invalidate_cache, args=(redis_client, trigger), daemon=True,
    ).start()
