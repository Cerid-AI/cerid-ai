# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Redis-based query cache for /query and /agent/query results.

Cache keys use a SHA-256 hash of (query, domain, top_k).
TTL: 5 minutes. Invalidated on any ingest.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, Optional

from deps import get_redis

logger = logging.getLogger("ai-companion.cache")

CACHE_PREFIX = "qcache:"
DEFAULT_TTL = 300  # 5 minutes


def _cache_key(query: str, domain: str, top_k: int) -> str:
    raw = f"{query}|{domain}|{top_k}"
    return CACHE_PREFIX + hashlib.sha256(raw.encode()).hexdigest()[:32]


def get_cached(query: str, domain: str, top_k: int) -> Optional[Dict[str, Any]]:
    try:
        key = _cache_key(query, domain, top_k)
        raw = get_redis().get(key)
        if raw:
            logger.debug(f"Cache hit: {key[:20]}")
            return json.loads(raw)
    except Exception as e:
        logger.warning(f"Cache read failed: {e}")
    return None


def set_cached(
    query: str, domain: str, top_k: int, result: Dict[str, Any], ttl: int = DEFAULT_TTL
) -> None:
    try:
        key = _cache_key(query, domain, top_k)
        get_redis().setex(key, ttl, json.dumps(result, default=str))
    except Exception as e:
        logger.warning(f"Cache write failed: {e}")


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
    except Exception as e:
        logger.warning(f"Cache invalidation failed: {e}")
