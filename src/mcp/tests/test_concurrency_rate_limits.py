# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Concurrency contracts: rate-limit budget allows realistic multi-tab usage."""
from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pytest

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


# F-PERF-04 (/health < 100ms under /agent/query load) moved to
# tests/integration/test_preservation_health_gate.py so CI's preservation
# job actually runs it — here it was excluded by both CI and ci-local.


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
