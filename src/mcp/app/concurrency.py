# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Path-partitioned concurrency pools.

Replaces the prior single process-wide ``_QUERY_SEMAPHORE(2)`` that served
every ``/agent/query`` call — which starved unrelated endpoints like
``/health`` and ``/user-state`` during a chat turn (audit RC-C, smoke
Test G).

Design:
    * KB_POOL       — heavy retrieval / agent / verify / ingest paths.
    * CHAT_POOL     — ``/chat/stream`` (streams hold a slot for a while).
    * HEALTH_POOL   — ``/health``, ``/observability``, setup/status polling.

HEALTH_POOL is effectively unbounded (large) so health polls never queue
behind KB work. Pools are process-local ``asyncio.Semaphore`` wrappers.

To change capacity at runtime (rare), restart the MCP process or override
via env var.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger("ai-companion.concurrency")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


@dataclass
class AsyncPool:
    """Bounded asyncio concurrency pool with observable depth."""

    name: str
    capacity: int
    _sem: asyncio.Semaphore = field(init=False)
    _in_use: int = field(default=0, init=False)
    _waiting: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._sem = asyncio.Semaphore(self.capacity)

    @contextlib.asynccontextmanager
    async def acquire(self):
        self._waiting += 1
        try:
            await self._sem.acquire()
        finally:
            self._waiting -= 1
        self._in_use += 1
        try:
            yield
        finally:
            self._in_use -= 1
            self._sem.release()

    def queue_depth(self) -> tuple[int, int]:
        """Returns ``(in_use, waiting)``."""
        return (self._in_use, self._waiting)


# Defaults tuned for a single MCP process on commodity hardware.
# KB_POOL = min(cpu_count, 4) gives room without overwhelming downstream
# Neo4j / Chroma connection pools.
_cpu = os.cpu_count() or 4
KB_POOL = AsyncPool(name="kb", capacity=_env_int("CERID_KB_POOL_CAPACITY", min(_cpu, 4)))
CHAT_POOL = AsyncPool(name="chat", capacity=_env_int("CERID_CHAT_POOL_CAPACITY", 4))
HEALTH_POOL = AsyncPool(name="health", capacity=_env_int("CERID_HEALTH_POOL_CAPACITY", 32))

logger.info(
    "Concurrency pools initialised: kb=%d, chat=%d, health=%d",
    KB_POOL.capacity, CHAT_POOL.capacity, HEALTH_POOL.capacity,
)


def queue_depths() -> dict:
    """Snapshot for observability."""
    return {
        p.name: {
            "capacity": p.capacity,
            "in_use": p.queue_depth()[0],
            "waiting": p.queue_depth()[1],
        }
        for p in (KB_POOL, CHAT_POOL, HEALTH_POOL)
    }
