# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Semantic query cache — HNSW-indexed similarity cache.

Uses hnswlib (already installed via chroma-hnswlib) for O(log n) nearest
neighbour lookup instead of the previous O(n) linear scan.  The HNSW index
is serialized to Redis for persistence across restarts.

Result payloads remain in individual Redis keys (``semcache:entry:{id}``)
with TTL, unchanged from the original implementation.  Quantized int8
helpers are retained for payload compression but are no longer used for
the index itself — HNSW operates on full float32 embeddings.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
from typing import Any

import numpy as np

from config.features import (
    SEMANTIC_CACHE_MAX_ENTRIES,
    SEMANTIC_CACHE_THRESHOLD,
    SEMANTIC_CACHE_TTL,
)

logger = logging.getLogger("ai-companion.semantic_cache")

_CACHE_PREFIX = "semcache:"
_INDEX_KEY = _CACHE_PREFIX + "index"  # Legacy key (ignored, kept for clean migration)
_HNSW_KEY = _CACHE_PREFIX + "hnsw_index"
_LABELS_KEY = _CACHE_PREFIX + "labels"  # Hash: label_id -> entry_id

# HNSW tuning parameters (sized for ~500-entry cache)
_HNSW_SPACE = "cosine"
_HNSW_DIM = int(os.getenv("SEMANTIC_CACHE_DIM", "768"))  # Arctic Embed M v1.5 default
_HNSW_EF_CONSTRUCTION = 100
_HNSW_M = 16
_HNSW_EF = int(os.getenv("SEMANTIC_CACHE_HNSW_EF", "50"))


# ---------------------------------------------------------------------------
# HNSW Index wrapper
# ---------------------------------------------------------------------------

class _HNSWIndex:
    """Thread-safe hnswlib index wrapper with Redis-backed persistence."""

    # Persist to Redis after this many inserts (avoids per-insert disk I/O)
    _SAVE_INTERVAL = 5

    def __init__(self, dim: int = _HNSW_DIM, max_elements: int = SEMANTIC_CACHE_MAX_ENTRIES) -> None:
        import hnswlib

        self._lock = threading.Lock()
        self._dim = dim
        self._max_elements = max_elements
        self._next_label = 0
        self._dirty_count = 0
        self._idx = hnswlib.Index(space=_HNSW_SPACE, dim=dim)
        self._idx.init_index(
            max_elements=max_elements,
            ef_construction=_HNSW_EF_CONSTRUCTION,
            M=_HNSW_M,
        )
        self._idx.set_ef(_HNSW_EF)

    def load_from_redis(self, redis_client: Any) -> bool:
        """Load index from Redis.  Returns True if loaded, False if not found."""
        try:
            data = redis_client.get(_HNSW_KEY)
            if not data:
                return False

            with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
                f.write(data)
                tmp_path = f.name

            try:
                import hnswlib

                self._idx = hnswlib.Index(space=_HNSW_SPACE, dim=self._dim)
                self._idx.load_index(tmp_path, max_elements=self._max_elements)
                self._idx.set_ef(_HNSW_EF)
                self._next_label = int(self._idx.get_current_count())
                logger.info(
                    "HNSW index loaded from Redis (%d entries, %d bytes)",
                    self._next_label, len(data),
                )
                return True
            finally:
                os.unlink(tmp_path)

        except Exception as e:
            logger.warning("Failed to load HNSW index from Redis: %s", e)
            return False

    def _save_to_redis(self, redis_client: Any) -> None:
        """Serialize index and store in Redis."""
        try:
            with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
                tmp_path = f.name

            try:
                self._idx.save_index(tmp_path)
                with open(tmp_path, "rb") as f:
                    data = f.read()
                redis_client.set(_HNSW_KEY, data)
            finally:
                os.unlink(tmp_path)
        except Exception as e:
            logger.warning("Failed to save HNSW index to Redis: %s", e)

    def add(self, embedding: np.ndarray, entry_id: str, redis_client: Any) -> int:
        """Add an embedding to the index.  Returns the numeric label assigned.

        Persistence is deferred — the HNSW index is saved to Redis only
        every ``_SAVE_INTERVAL`` inserts to avoid synchronous disk I/O on
        every single cache store.  Call :meth:`flush` during shutdown to
        persist any remaining dirty entries.

        If the index has reached its maximum capacity, it is automatically
        resized (doubled) before inserting.
        """
        with self._lock:
            label = self._next_label
            self._next_label += 1

            # Resize proactively when the index is full to avoid hnswlib
            # raising "The number of elements exceeds the specified limit".
            if self._idx.get_current_count() >= self._max_elements:
                new_max = self._max_elements * 2
                logger.info(
                    "HNSW index at capacity (%d) — resizing to %d",
                    self._max_elements, new_max,
                )
                self._idx.resize_index(new_max)
                self._max_elements = new_max

            emb = embedding.reshape(1, -1).astype(np.float32)
            self._idx.add_items(emb, np.array([label]))

            # Store label→entry_id mapping
            redis_client.hset(_LABELS_KEY, str(label), entry_id)

            self._dirty_count += 1
            if self._dirty_count >= self._SAVE_INTERVAL:
                self._save_to_redis(redis_client)
                self._dirty_count = 0

            return label

    def flush(self, redis_client: Any) -> None:
        """Persist the HNSW index to Redis if there are unsaved changes."""
        with self._lock:
            if self._dirty_count > 0:
                self._save_to_redis(redis_client)
                self._dirty_count = 0
                logger.debug("HNSW index flushed to Redis (%d entries)", self._next_label)

    def query(self, embedding: np.ndarray, k: int = 1) -> list[tuple[int, float]]:
        """Query nearest neighbours.

        Returns list of (label, distance) tuples.
        For cosine space, hnswlib returns ``1 - cos_sim`` as distance.
        """
        if self._idx.get_current_count() == 0:
            return []

        emb = embedding.reshape(1, -1).astype(np.float32)
        actual_k = min(k, self._idx.get_current_count())
        labels, distances = self._idx.knn_query(emb, k=actual_k)
        return list(zip(labels[0].tolist(), distances[0].tolist()))

    @property
    def count(self) -> int:
        return int(self._idx.get_current_count())


# Process-level singleton
_index: _HNSWIndex | None = None
_index_lock = threading.Lock()


def _get_index(redis_client: Any) -> _HNSWIndex:
    """Get or initialize the HNSW index (lazy singleton)."""
    global _index
    if _index is not None:
        return _index

    with _index_lock:
        if _index is not None:
            return _index

        idx = _HNSWIndex(dim=_HNSW_DIM, max_elements=SEMANTIC_CACHE_MAX_ENTRIES)
        idx.load_from_redis(redis_client)
        _index = idx
        return _index


# ---------------------------------------------------------------------------
# Quantization helpers (retained for result payload compression)
# ---------------------------------------------------------------------------

def _quantize_int8(embedding: np.ndarray) -> tuple[bytes, float, float]:
    """Quantize float32 embedding to int8 for compact storage.

    Returns (quantized_bytes, scale, zero_point) for dequantization.
    """
    min_val = float(np.min(embedding))
    max_val = float(np.max(embedding))
    if max_val == min_val:
        return bytes(len(embedding)), 1.0, min_val

    scale = (max_val - min_val) / 255.0
    zero_point = min_val
    quantized = np.clip(
        np.round((embedding - zero_point) / scale), 0, 255
    ).astype(np.uint8)
    return quantized.tobytes(), scale, zero_point


def _dequantize_int8(data: bytes, scale: float, zero_point: float) -> np.ndarray:
    """Dequantize int8 bytes back to float32 embedding."""
    arr = np.frombuffer(data, dtype=np.uint8).astype(np.float32)
    return arr * scale + zero_point


def _entry_key(entry_id: str) -> str:
    return _CACHE_PREFIX + "entry:" + entry_id


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cache_lookup(
    query_embedding: np.ndarray,
    redis_client: Any,
    threshold: float | None = None,
) -> dict[str, Any] | None:
    """Check if a semantically similar query exists in cache.

    Uses HNSW index for O(log n) lookup.
    Returns cached result dict or None.
    """
    thresh = threshold if threshold is not None else SEMANTIC_CACHE_THRESHOLD

    try:
        idx = _get_index(redis_client)
        results = idx.query(query_embedding, k=1)
        if not results:
            return None

        label, distance = results[0]
        # hnswlib cosine distance = 1 - cos_sim
        similarity = 1.0 - distance

        if similarity < thresh:
            return None

        # Resolve label → entry_id
        entry_id = redis_client.hget(_LABELS_KEY, str(label))
        if not entry_id:
            return None
        if isinstance(entry_id, bytes):
            entry_id = entry_id.decode()

        result_raw = redis_client.get(_entry_key(entry_id))
        if result_raw:
            logger.info(
                "Semantic cache hit (sim=%.4f, id=%s)", similarity, entry_id[:12]
            )
            return json.loads(result_raw)

    except Exception as e:
        logger.warning("Semantic cache lookup failed: %s", e)

    return None


def cache_store(
    query: str,
    query_embedding: np.ndarray,
    result: dict[str, Any],
    redis_client: Any,
    ttl: int | None = None,
    max_entries: int | None = None,
) -> None:
    """Store a query result in the semantic cache."""
    cache_ttl = ttl if ttl is not None else SEMANTIC_CACHE_TTL

    try:
        entry_id = hashlib.sha256(query.encode()).hexdigest()[:16]
        idx = _get_index(redis_client)

        # Store result payload
        redis_client.setex(
            _entry_key(entry_id),
            cache_ttl,
            json.dumps(result, default=str),
        )

        # Add embedding to HNSW index
        idx.add(query_embedding, entry_id, redis_client)

        logger.debug("Semantic cache stored: %s (ttl=%ds)", entry_id[:12], cache_ttl)

    except Exception as e:
        logger.warning("Semantic cache store failed: %s", e)


def flush_cache(redis_client: Any) -> None:
    """Flush any dirty HNSW index entries to Redis.

    Call during application shutdown to ensure no unsaved embeddings are lost.
    Safe to call even if the index has not been initialized (no-op).
    """
    if _index is not None:
        _index.flush(redis_client)


def invalidate_cache(redis_client: Any) -> int:
    """Clear all semantic cache entries and reinitialize the HNSW index."""
    global _index
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

        # Reinitialize empty index
        with _index_lock:
            _index = _HNSWIndex(dim=_HNSW_DIM, max_elements=SEMANTIC_CACHE_MAX_ENTRIES)

        if count:
            logger.info("Semantic cache invalidated: %d keys", count)
        return count
    except Exception as e:
        logger.warning("Semantic cache invalidation failed: %s", e)
        return 0
