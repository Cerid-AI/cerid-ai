# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Retrieval-level cache -- caches ChromaDB query results in Redis.

Key design: quantized query embedding (int8) -> serialized chunk results.
Invalidation: generation counter incremented on KB ingest/delete.
Expected hit rate: 60-80% in production (repeated/similar queries).

Dependencies: Redis (via deps.get_redis), config/constants.py
Error types: none (cache misses are silent, never blocks retrieval)
"""
from __future__ import annotations

import hashlib
import json
import logging

import numpy as np

from config.constants import RETRIEVAL_CACHE_TTL

logger = logging.getLogger("ai-companion.retrieval_cache")


class RetrievalCache:
    """Redis-backed cache for ChromaDB retrieval results."""

    PREFIX = "cerid:retrieval:"
    GENERATION_KEY = "cerid:retrieval:generation"

    def __init__(self, ttl: int = RETRIEVAL_CACHE_TTL) -> None:
        self._ttl = ttl
        self._hits = 0
        self._misses = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _redis(self):
        """Lazy Redis access -- import here to avoid circular deps."""
        from deps import get_redis
        return get_redis()

    def _generation(self) -> int:
        """Current generation counter (0 if unset)."""
        try:
            val = self._redis().get(self.GENERATION_KEY)
            return int(val) if val else 0
        except Exception:
            return 0

    @staticmethod
    def _hash_embedding(query_embedding: list[float]) -> str:
        """Quantize embedding to int8 and return SHA-256 hex digest."""
        arr = np.array(query_embedding, dtype=np.float32)
        quantized = np.clip(np.round(arr * 127), -128, 127).astype(np.int8)
        return hashlib.sha256(quantized.tobytes()).hexdigest()

    def _cache_key(self, query_embedding: list[float], top_k: int) -> str:
        gen = self._generation()
        emb_hash = self._hash_embedding(query_embedding)
        return f"{self.PREFIX}{gen}:{emb_hash}:{top_k}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, query_embedding: list[float], top_k: int = 10) -> list[dict] | None:
        """Return cached chunk results or None on miss."""
        try:
            raw = self._redis().get(self._cache_key(query_embedding, top_k))
            if raw is not None:
                self._hits += 1
                logger.debug("Retrieval cache hit (top_k=%d)", top_k)
                return json.loads(raw)
        except Exception as exc:
            logger.debug("Retrieval cache get error: %s", exc)

        self._misses += 1
        return None

    def set(
        self,
        query_embedding: list[float],
        top_k: int,
        results: list[dict],
    ) -> None:
        """Store results with TTL."""
        try:
            key = self._cache_key(query_embedding, top_k)
            self._redis().setex(key, self._ttl, json.dumps(results, default=str))
            logger.debug("Retrieval cache set (top_k=%d, ttl=%ds)", top_k, self._ttl)
        except Exception as exc:
            logger.debug("Retrieval cache set error: %s", exc)

    def invalidate_all(self) -> None:
        """Increment generation counter -- all existing keys become stale."""
        try:
            new_gen = self._redis().incr(self.GENERATION_KEY)
            logger.info("Retrieval cache invalidated (generation=%d)", new_gen)
        except Exception as exc:
            logger.debug("Retrieval cache invalidate error: %s", exc)

    def hit_rate(self) -> dict:
        """Return hit/miss statistics."""
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "rate": round(self._hits / total, 4) if total else 0.0,
        }


# Module-level singleton
retrieval_cache = RetrievalCache()

__all__ = ["RetrievalCache", "retrieval_cache"]
