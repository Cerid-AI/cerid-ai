# Copyright (c) 2026 Cerid AI. All rights reserved.
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
import sentry_sdk

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

# Audit V-5 / RC-G: dim self-heal. Blobs saved by this version carry a fixed
# 16-byte magic+dim header so stale blobs from a prior embedder can be
# detected and discarded BEFORE being fed to hnswlib.load_index (which
# will double-free / segfault on a dim mismatch).
#
# Header layout (16 bytes, little-endian):
#   [0:12]  magic  : b"CERIDHNSW\x00v1"
#   [12:16] dim    : uint32
#
# Blobs without this magic are treated as pre-header legacy blobs and
# discarded on first load (cold-start is safer than feeding an unverified
# binary to hnswlib).
_HNSW_MAGIC = b"CERIDHNSW\x00v1"
_HNSW_HEADER_LEN = 16

# HNSW tuning parameters (sized for ~500-entry cache)
_HNSW_SPACE = "cosine"
_HNSW_EF_CONSTRUCTION = 100
_HNSW_M = 16
_HNSW_EF = int(os.getenv("SEMANTIC_CACHE_HNSW_EF", "50"))


def _resolve_hnsw_dim() -> int:
    """Resolve the HNSW index dim from the configured embedder singleton.

    Priority:
    1. ``SEMANTIC_CACHE_DIM`` env var (explicit override — preserves the
       ability to pin the cache dim independently for migration scenarios).
    2. ``core.utils.embeddings.get_embedding_dim()`` — single source of
       truth, keyed on ``EMBEDDING_MODEL``.
    3. 768 fallback (Arctic Embed M v1.5) if resolution fails — matches
       the historical default so behaviour is unchanged for users on the
       default model even if the embedder probe fails at import time.
    """
    override = os.getenv("SEMANTIC_CACHE_DIM")
    if override:
        return int(override)
    try:
        from core.utils.embeddings import get_embedding_dim
        return get_embedding_dim()
    except Exception:
        return 768


# Arctic Embed M v1.5 default (768) when no override / embedder unavailable.
_HNSW_DIM = _resolve_hnsw_dim()


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

    @staticmethod
    def _encode_header(dim: int) -> bytes:
        """Build the 16-byte dim header prefixed to every saved blob."""
        import struct
        return _HNSW_MAGIC + struct.pack("<I", int(dim))

    @staticmethod
    def _parse_header(data: bytes) -> int | None:
        """Return the dim encoded in the blob header, or None if absent.

        ``None`` signals either a pre-header legacy blob or a corrupt blob;
        callers should treat both the same way (delete + cold-start).
        """
        if len(data) < _HNSW_HEADER_LEN:
            return None
        if data[: len(_HNSW_MAGIC)] != _HNSW_MAGIC:
            return None
        import struct
        try:
            (dim,) = struct.unpack("<I", data[len(_HNSW_MAGIC) : _HNSW_HEADER_LEN])
            return int(dim)
        except struct.error:
            return None

    def load_from_redis(self, redis_client: Any) -> bool:
        """Load index from Redis. Returns True if loaded, False if not found
        OR if the stored blob is corrupt (in which case the bad key is
        deleted so the next save rebuilds cleanly).

        Corruption paths:
        - UnicodeDecodeError: a decode-responses Redis client tried to utf-8
          the binary blob (TypedRedis normally has `_r` to bypass, but bare
          clients don't). We catch this, delete the bad key, and cold-start.
        - RuntimeError / OSError from hnswlib.load_index: the blob was a
          different HNSW version or was truncated. Same recovery.
        - Anything else: logged + cold-start; we don't want a cache-load
          failure to propagate to the caller and trip the event-loop
          watchdog during retrieval.
        """
        try:
            # Use the raw (non-decoding) Redis client for binary I/O.
            # TypedRedis wraps a decode_responses=True client which attempts
            # UTF-8 decode on all GET results — the HNSW binary blob is not
            # valid UTF-8, so we bypass decoding via the underlying client.
            raw = getattr(redis_client, "_r", redis_client)
            try:
                data = raw.get(_HNSW_KEY)
            except UnicodeDecodeError:
                # Bare Redis(decode_responses=True) with no `_r` bypass —
                # the blob stored is binary. Drop the bad key and cold-start
                # so subsequent _save_to_redis can rewrite under the right
                # client. Log a distinct message because this needs a dev
                # to see (it means a client was mis-configured somewhere).
                logger.warning(
                    "Semantic cache: Redis client is decode_responses=True "
                    "but no bypass exposed — deleting corrupt/undecodable "
                    "blob at %s and cold-starting",
                    _HNSW_KEY,
                )
                try:
                    raw.delete(_HNSW_KEY)
                except Exception:
                    pass
                return False

            if not data:
                return False

            # Audit V-5 / RC-G: dim self-heal BEFORE touching hnswlib.
            # hnswlib.load_index happily accepts a dim-mismatched blob and
            # then corrupts memory on the first query. We refuse to feed it
            # any blob we can't prove was built at the active dim.
            header_dim = self._parse_header(data)
            if header_dim is None or header_dim != self._dim:
                if header_dim is None:
                    logger.warning(
                        "Semantic cache: HNSW blob at %s is missing the dim "
                        "header (legacy or corrupt) — deleting and "
                        "cold-starting",
                        _HNSW_KEY,
                    )
                else:
                    logger.warning(
                        "Semantic cache: HNSW blob dim=%d does not match "
                        "active embedder dim=%d — deleting stale blob at %s "
                        "and cold-starting",
                        header_dim, self._dim, _HNSW_KEY,
                    )
                try:
                    raw.delete(_HNSW_KEY)
                    # Labels and per-entry payloads were produced against the
                    # stale index — drop them too so lookups can't resolve
                    # garbage label→entry mappings.
                    raw.delete(_LABELS_KEY)
                except Exception:
                    pass
                return False

            # Strip the header before handing the raw hnswlib bytes to disk.
            body = data[_HNSW_HEADER_LEN:]

            with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
                f.write(body)
                tmp_path = f.name

            try:
                import hnswlib

                self._idx = hnswlib.Index(space=_HNSW_SPACE, dim=self._dim)
                try:
                    self._idx.load_index(tmp_path, max_elements=self._max_elements)
                except (RuntimeError, OSError, ValueError) as load_exc:
                    # hnswlib version-skew or truncated blob. Drop and
                    # rebuild on next write.
                    logger.warning(
                        "Semantic cache: HNSW load_index rejected blob "
                        "(%s: %s) — deleting and cold-starting",
                        type(load_exc).__name__, load_exc,
                    )
                    try:
                        raw.delete(_HNSW_KEY)
                    except Exception:
                        pass
                    # Reinitialize fresh so the in-process index is usable.
                    self._idx = hnswlib.Index(space=_HNSW_SPACE, dim=self._dim)
                    self._idx.init_index(
                        max_elements=self._max_elements,
                        ef_construction=_HNSW_EF_CONSTRUCTION,
                        M=_HNSW_M,
                    )
                    self._idx.set_ef(_HNSW_EF)
                    return False
                self._idx.set_ef(_HNSW_EF)
                self._next_label = int(self._idx.get_current_count())
                logger.info(
                    "HNSW index loaded from Redis (%d entries, %d bytes)",
                    self._next_label, len(data),
                )
                return True
            finally:
                os.unlink(tmp_path)

        except Exception:
            logger.exception("semantic_cache.hnsw_load_failed")
            sentry_sdk.capture_exception()
            return False

    def _save_to_redis(self, redis_client: Any) -> None:
        """Serialize index and store in Redis with a dim header prefix."""
        try:
            with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
                tmp_path = f.name

            try:
                self._idx.save_index(tmp_path)
                with open(tmp_path, "rb") as f:
                    body = f.read()
                # Prefix the dim header so load_from_redis can validate this
                # blob against the live embedder dim before handing it to
                # hnswlib (audit V-5 / RC-G).
                raw = getattr(redis_client, "_r", redis_client)
                raw.set(_HNSW_KEY, self._encode_header(self._dim) + body)
            finally:
                os.unlink(tmp_path)
        except Exception:
            logger.exception("semantic_cache.hnsw_save_failed")
            sentry_sdk.capture_exception()

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
    """Get or initialize the HNSW index (lazy singleton).

    Dim is resolved from the embedder singleton at first use (not at import
    time) so that a late-configured ``EMBEDDING_MODEL`` is honoured without
    reloading the module.
    """
    global _index
    if _index is not None:
        return _index

    with _index_lock:
        if _index is not None:
            return _index

        idx = _HNSWIndex(dim=_resolve_hnsw_dim(), max_elements=SEMANTIC_CACHE_MAX_ENTRIES)
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

        # Reinitialize empty index (re-resolve dim in case embedder changed)
        with _index_lock:
            _index = _HNSWIndex(dim=_resolve_hnsw_dim(), max_elements=SEMANTIC_CACHE_MAX_ENTRIES)

        if count:
            logger.info("Semantic cache invalidated: %d keys", count)
        return count
    except Exception as e:
        logger.warning("Semantic cache invalidation failed: %s", e)
        return 0
