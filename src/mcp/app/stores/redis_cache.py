# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Redis implementation of CacheStore contract."""

from __future__ import annotations

from typing import Any

from core.contracts.cache import CacheStore


class RedisCacheStore(CacheStore):
    """CacheStore backed by Redis."""

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def get(self, key: str) -> str | None:
        val = self._redis.get(key)
        return val.decode() if isinstance(val, bytes) else val

    async def set(self, key: str, value: str, *, ttl_seconds: int = 300) -> None:
        self._redis.setex(key, ttl_seconds, value)

    async def delete(self, key: str) -> None:
        self._redis.delete(key)

    async def append(self, key: str, value: str, *, max_len: int = 1000) -> None:
        self._redis.lpush(key, value)
        self._redis.ltrim(key, 0, max_len - 1)

    async def get_list(self, key: str, *, start: int = 0, end: int = -1) -> list[str]:
        raw = self._redis.lrange(key, start, end)
        return [v.decode() if isinstance(v, bytes) else v for v in (raw or [])]
