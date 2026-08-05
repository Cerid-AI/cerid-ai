# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Concurrency contracts: rate-limit budget allows realistic multi-tab usage."""
from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pytest

from core.utils.swallowed import log_swallowed_error

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

MCP = "http://127.0.0.1:8888"


def _auth_headers() -> dict[str, str]:
    """Auth header for the live stack, mirroring ``tests/beta/conftest.py``.

    Without ``X-API-Key`` every request 401s against a stack with a key
    configured, so these concurrency/SLO assertions never exercised the real
    handler. An empty key stays unauthenticated (backward compatible).
    """
    key = os.getenv("CERID_API_KEY", "")
    return {"X-API-Key": key} if key else {}


@pytest.mark.integration
async def test_six_concurrent_agent_query_all_succeed():
    """F-AUTO-01: 6 concurrent /agent/query on a fresh client-id must all return 200."""
    client_id = f"test-concurrent-{uuid.uuid4().hex[:8]}"
    headers = {"X-Client-ID": client_id, "Content-Type": "application/json", **_auth_headers()}
    async with httpx.AsyncClient(timeout=60.0) as client:
        tasks = [
            client.post(
                f"{MCP}/agent/query",
                json={"query": f"concurrent test query {i}", "domains": ["general"]},
                headers=headers,
            )
            for i in range(6)
        ]
        responses = await asyncio.gather(*tasks)
    statuses = [r.status_code for r in responses]
    assert all(s == 200 for s in statuses), (
        f"Expected all 200, got {statuses}; 429 count={statuses.count(429)}"
    )


@pytest.mark.integration
async def test_health_p95_under_agent_load():
    """F-PERF-04: /health stays < 100ms while /agent/query runs concurrently.

    Asserted on the worst of 6 samples — see the comment at the assertion for
    why that, and not a "p95", is the honest statistic at this sample count.
    """
    import time
    client_id = f"test-hol-{uuid.uuid4().hex[:8]}"
    headers = {"X-Client-ID": client_id, "Content-Type": "application/json", **_auth_headers()}
    async with httpx.AsyncClient(timeout=60.0) as client:
        bg = [
            asyncio.create_task(client.post(
                f"{MCP}/agent/query",
                json={"query": f"hol probe {i}", "domains": ["general"]},
                headers=headers,
            ))
            for i in range(3)
        ]
        await asyncio.sleep(0.3)
        health_times = []
        for _ in range(6):
            t0 = time.perf_counter()
            r = await client.get(f"{MCP}/health")
            health_times.append(time.perf_counter() - t0)
            assert r.status_code == 200
            await asyncio.sleep(1.0)
        for t in bg:
            try:
                await t
            except Exception as exc:
                log_swallowed_error("tests.test_concurrency_rate_limits", exc)
    # 6 samples cannot support a p95. Nearest-rank p95 indexes off
    # ``len - 1`` (``values[min(n - 1, int(round(pct * (n - 1))))]``, the form
    # used by app/processor/metrics.py::_percentile), and for n <= 11 that
    # lands on the last element — so the p95 of 6 samples IS the max. The old
    # ``sorted(...)[int(0.95 * len(...))]`` indexed off ``n``, which is the
    # same off-by-one fixed in utils/metrics.py::_aggregate; it returned max
    # here too, just by accident rather than by definition. Assert on the max
    # and say so, instead of labelling it a percentile it cannot be.
    worst = max(health_times)
    assert worst < 0.1, (
        f"/health worst-of-{len(health_times)}={worst:.3f}s under load "
        "(threshold 100ms; n too small for a p95, so max is the statistic)"
    )


@pytest.mark.integration
async def test_memory_extract_p99_under_10s():
    """F-AUTO-03: /sdk/v1/memory/extract p99 must be < 10s."""
    import time
    headers = {"X-Client-ID": f"test-mem-{uuid.uuid4().hex[:8]}", "Content-Type": "application/json", **_auth_headers()}
    sample = (
        "We decided to use PostgreSQL over MySQL for the project because of better JSON support. "
        "The project deadline is next Friday. "
        "The user prefers dark mode interfaces."
    )
    times = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for _ in range(5):
            t0 = time.perf_counter()
            r = await client.post(
                f"{MCP}/sdk/v1/memory/extract",
                json={"response_text": sample, "conversation_id": str(uuid.uuid4())},
                headers=headers,
            )
            times.append(time.perf_counter() - t0)
            # 200 = synchronous extraction completed inline.
            # 202 = enqueued to the memory worker (async mode, which
            # auto-enables on local-inference installs where a synchronous
            # extraction cannot meet this very SLO). Both satisfy the budget;
            # the point of the assertion is that the *request* returns fast.
            assert r.status_code in (200, 202), (
                f"unexpected status {r.status_code}: {r.text[:200]}"
            )
    p99 = max(times)
    assert p99 < 10.0, f"memory_extract p99={p99:.2f}s exceeds 10s SLO"
