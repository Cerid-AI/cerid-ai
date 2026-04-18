# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Task 8: path-partitioned concurrency pools must not starve unrelated paths."""
from __future__ import annotations

import asyncio
import time

import pytest


@pytest.mark.asyncio
async def test_pool_does_not_block_unrelated_path():
    """HEALTH_POOL slots remain available even when KB_POOL is saturated."""
    from app.concurrency import HEALTH_POOL, KB_POOL

    # Saturate KB_POOL (assume capacity >= 1; we only need all slots held)
    holds: list[asyncio.Task] = []
    event = asyncio.Event()

    async def _hold_kb():
        async with KB_POOL.acquire():
            await event.wait()

    for _ in range(KB_POOL.capacity):
        holds.append(asyncio.create_task(_hold_kb()))
    # give them a tick to enter the pool
    await asyncio.sleep(0.01)

    # Now prove HEALTH_POOL isn't blocked
    start = time.perf_counter()
    async with HEALTH_POOL.acquire():
        pass
    elapsed = time.perf_counter() - start

    event.set()
    await asyncio.gather(*holds)

    assert elapsed < 0.1, f"HEALTH_POOL blocked {elapsed:.3f}s while KB saturated"


@pytest.mark.asyncio
async def test_kb_pool_exposes_depth():
    """queue_depth returns (in_use, waiting) for dashboard visibility."""
    from app.concurrency import KB_POOL

    assert KB_POOL.queue_depth() == (0, 0)
    async with KB_POOL.acquire():
        assert KB_POOL.queue_depth()[0] == 1


@pytest.mark.asyncio
async def test_pool_releases_on_exception():
    """Exception inside the `async with` block still releases the slot."""
    from app.concurrency import KB_POOL

    with pytest.raises(RuntimeError):
        async with KB_POOL.acquire():
            raise RuntimeError("boom")

    # Pool should be fully available
    assert KB_POOL.queue_depth() == (0, 0)
