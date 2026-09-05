# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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


@pytest.mark.asyncio
async def test_acquire_times_out_instead_of_waiting_forever():
    from app.concurrency import AsyncPool, PoolTimeout

    pool = AsyncPool(name="t", capacity=1)
    event = asyncio.Event()

    async def holder():
        async with pool.acquire():
            await event.wait()

    task = asyncio.create_task(holder())
    await asyncio.sleep(0.01)
    with pytest.raises(PoolTimeout):
        async with pool.acquire(timeout=0.05):
            pass
    assert pool.queue_depth() == (1, 0)
    event.set()
    await task
    assert pool.queue_depth() == (0, 0)


@pytest.mark.asyncio
async def test_agent_query_pool_timeout_returns_queued_degraded():
    """Saturated KB_POOL must fail-open with a distinct queued reason, not the 20s copy."""
    import contextlib
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.concurrency import PoolTimeout
    from app.routers import agents
    from app.routers.agents import AgentQueryRequest

    req = AgentQueryRequest(query="hello world", skip_cache=True)
    request = MagicMock()
    request.headers = {"x-client-id": "gui"}
    request.is_disconnected = AsyncMock(return_value=False)

    @contextlib.asynccontextmanager
    async def boom_acquire(*_a, **_k):
        raise PoolTimeout("kb pool acquire timed out")
        yield  # pragma: no cover — __aenter__ raises

    with (
        patch.object(agents, "private_blocks", return_value=False),
        patch.object(agents.KB_POOL, "acquire", boom_acquire),
        patch("core.agents.query_agent.agent_query_full", new=AsyncMock()) as spy,
    ):
        result = await agents.agent_query_endpoint(req, request)
    spy.assert_not_called()
    assert result["budget_exceeded"] is False
    assert result.get("strategy") != "degraded_budget_exhausted"
    assert result["budget_seconds"] == 2.0
    reason = result["degraded_reason"]
    assert "queued" in reason.lower()
    assert "configured budget" not in reason.lower()
    assert "large collections" not in reason.lower()

