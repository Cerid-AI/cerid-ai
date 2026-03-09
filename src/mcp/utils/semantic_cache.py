# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Semantic query cache — similarity-based cache using quantized embeddings.

Stores query embeddings as quantized int8 vectors in Redis. A new query
checks cosine similarity against cached entries; if similarity exceeds
the threshold (default 0.92), the cached result is returned.

No new model — reuses OnnxEmbeddingFunction from embeddings.py.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any

import numpy as np

logger = logging.getLogger("ai-companion.semantic_cache")

ENABLE_SEMANTIC_CACHE = os.getenv("ENABLE_SEMANTIC_CACHE", "false").lower() == "true"
SEMANTIC_CACHE_THRESHOLD = float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.92"))
SEMANTIC_CACHE_TTL = int(os.getenv("SEMANTIC_CACHE_TTL", "600"))  # 10 min default
SEMANTIC_CACHE_MAX_ENTRIES = int(os.getenv("SEMANTIC_CACHE_MAX_ENTRIES", "500"))

_CACHE_PREFIX = "semcache:"
_INDEX_KEY = _CACHE_PREFIX + "index"


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


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors."""
    dot = float(np.dot(a, b))
    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _entry_key(entry_id: str) -> str:
    return _CACHE_PREFIX + "entry:" + entry_id


def cache_lookup(
    query_embedding: np.ndarray,
    redis_client: Any,
    threshold: float | None = None,
) -> dict[str, Any] | None:
    """Check if a semantically similar query exists in cache.

    Returns cached result dict or None.
    """
    thresh = threshold if threshold is not None else SEMANTIC_CACHE_THRESHOLD

    try:
        index_raw = redis_client.lrange(_INDEX_KEY, 0, -1)
        if not index_raw:
            return None

        best_sim = 0.0
        best_entry_id: str | None = None

        for raw in index_raw:
            meta = json.loads(raw)
            cached_emb = _dequantize_int8(
                bytes.fromhex(meta["emb_hex"]),
                meta["scale"],
                meta["zero_point"],
            )
            sim = _cosine_similarity(query_embedding, cached_emb)
            if sim > best_sim:
                best_sim = sim
                best_entry_id = meta["id"]

        if best_sim >= thresh and best_entry_id:
            result_raw = redis_client.get(_entry_key(best_entry_id))
            if result_raw:
                logger.info(
                    "Semantic cache hit (sim=%.4f, id=%s)", best_sim, best_entry_id[:12]
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
    max_e = max_entries if max_entries is not None else SEMANTIC_CACHE_MAX_ENTRIES

    try:
        entry_id = hashlib.sha256(
            f"{query}:{time.time()}".encode()
        ).hexdigest()[:16]

        emb_bytes, scale, zero_point = _quantize_int8(query_embedding)

        index_entry = json.dumps({
            "id": entry_id,
            "emb_hex": emb_bytes.hex(),
            "scale": scale,
            "zero_point": zero_point,
            "query": query[:200],
        })

        # Store result payload
        redis_client.setex(
            _entry_key(entry_id),
            cache_ttl,
            json.dumps(result, default=str),
        )

        # Add to index
        redis_client.lpush(_INDEX_KEY, index_entry)

        # Trim index to max entries
        redis_client.ltrim(_INDEX_KEY, 0, max_e - 1)

        logger.debug("Semantic cache stored: %s (ttl=%ds)", entry_id[:12], cache_ttl)

    except Exception as e:
        logger.warning("Semantic cache store failed: %s", e)


def invalidate_cache(redis_client: Any) -> int:
    """Clear all semantic cache entries. Returns count of entries removed."""
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
        if count:
            logger.info("Semantic cache invalidated: %d keys", count)
        return count
    except Exception as e:
        logger.warning("Semantic cache invalidation failed: %s", e)
        return 0
