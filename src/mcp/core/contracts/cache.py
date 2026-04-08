# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Abstract cache store contract."""

from __future__ import annotations

from abc import ABC, abstractmethod


class CacheStore(ABC):
    """Abstract cache — Redis, memcached, in-memory, etc."""

    @abstractmethod
    async def get(self, key: str) -> str | None: ...

    @abstractmethod
    async def set(
        self, key: str, value: str, *, ttl_seconds: int = 300
    ) -> None: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def append(
        self, key: str, value: str, *, max_len: int = 1000
    ) -> None:
        """Append to an ordered list (Redis LPUSH + LTRIM pattern)."""
        ...

    @abstractmethod
    async def get_list(
        self, key: str, *, start: int = 0, end: int = -1
    ) -> list[str]:
        """Read from an ordered list (Redis LRANGE pattern)."""
        ...
