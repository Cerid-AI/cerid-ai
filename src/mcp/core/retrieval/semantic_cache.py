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

logger = logging.getLogger("ai-companion.semantic_cache")

_CACHE_PREFIX = "semcache:"


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

def cache_lookup(
    query_embedding: np.ndarray,
    redis_client: Any,
    threshold: float | None = None,
) -> dict[str, Any] | None:
    """Check if a semantically similar query exists in the cache.

    Returns the cached result dict, or None on miss / disabled / error.
    """
    backend = _get_backend()
    if backend is None:
        return None

    thresh = threshold if threshold is not None else SEMANTIC_CACHE_THRESHOLD

    try:
        if backend.count() == 0:
            return None

        emb = np.asarray(query_embedding, dtype=np.float32).reshape(-1).tolist()
        result = backend.query(
            query_embeddings=[emb],
            n_results=1,
            include=["distances"],
        )

        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        if not ids or not distances:
            return None

        entry_id = str(ids[0])
        # cosine space: distance == 1 - cos_sim
        similarity = 1.0 - float(distances[0])
        if similarity < thresh:
            return None

        result_raw = redis_client.get(_entry_key(entry_id))
        if not result_raw:
            # Payload expired (Redis TTL) but the index still has the
            # embedding — lazy-evict the orphan so the index doesn't grow
            # unbounded as TTLs cycle.
            try:
                backend.delete(ids=[entry_id])
            except Exception:
                logger.debug("semantic_cache.orphan_evict_failed", exc_info=True)
            return None

        logger.info(
            "Semantic cache hit (sim=%.4f, id=%s)", similarity, entry_id[:12]
        )
        return json.loads(result_raw)

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
) -> None:
    """Store a query result in the semantic cache.

    Result payload goes to Redis (with TTL); embedding goes to the chroma
    collection backend (no native TTL — orphans evicted lazily on lookup).
    """
    backend = _get_backend()
    if backend is None:
        return

    cache_ttl = ttl if ttl is not None else SEMANTIC_CACHE_TTL

    try:
        entry_id = hashlib.sha256(query.encode()).hexdigest()[:16]

        redis_client.setex(
            _entry_key(entry_id),
            cache_ttl,
            json.dumps(result, default=str),
        )

        emb = np.asarray(query_embedding, dtype=np.float32).reshape(-1).tolist()
        backend.upsert(
            ids=[entry_id],
            embeddings=[emb],
            metadatas=[{"created_at": time.time()}],
        )

        logger.debug("Semantic cache stored: %s (ttl=%ds)", entry_id[:12], cache_ttl)

    except Exception as e:
        logger.warning("Semantic cache store failed: %s", e)


def flush_cache(redis_client: Any) -> None:
    """No-op kept for public-API stability.

    The prior HNSW backend buffered writes and persisted to Redis on
    shutdown; the chroma backend persists every upsert immediately, so
    there is nothing to flush. Shutdown call sites can keep calling this
    safely — the function is retained as a public-API hinge.
    """
    return None


def invalidate_cache(redis_client: Any) -> int:
    """Clear all semantic cache entries (Redis payloads + index)."""
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
                # Empty `where` clears the whole collection in chromadb 0.5+.
                backend.delete(where={})
            except Exception:
                logger.debug("semantic_cache.backend_clear_failed", exc_info=True)

        if count:
            logger.info("Semantic cache invalidated: %d keys", count)
        return count
    except Exception as e:
        logger.warning("Semantic cache invalidation failed: %s", e)
        return 0


# ---------------------------------------------------------------------------
# Capacity hint (advisory; no eviction yet)
# ---------------------------------------------------------------------------

def _capacity_warning_threshold() -> int:
    """The HNSW backend grew unbounded too — soft limit only.

    Once chromadb 1.x lands and we have a stable `where` operator set,
    revisit eviction (FIFO by ``created_at``). Until then this is a no-op
    consulted by ad-hoc diagnostics.
    """
    return int(SEMANTIC_CACHE_MAX_ENTRIES)
