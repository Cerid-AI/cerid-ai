# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""/health gate — the assertion that actually runs in CI.

F-PERF-04 (/health stays fast under /agent/query load) previously lived in
``tests/test_concurrency_rate_limits.py``: integration-marked, hardwired to
the personal stack on :8888, excluded by CI's ``test`` job and ``ci-local``
alike — so no CI job ever exercised /health at all. This file is its home in
the preservation suite, where CI's ``preservation`` job runs it against the
live containerized stack (``CERID_PRESERVATION_MCP``).

Runner constraint: the preservation job installs only the slim HTTP dep set
(pytest + httpx + neo4j + fakeredis + respx), so this module must not import
``app.*`` / ``core.*`` and drives load with threads rather than asyncio.
"""
from __future__ import annotations

import concurrent.futures
import time

import httpx


def test_health_returns_200_and_names_its_services(http_client):
    """/health answers 200 with the status/services contract on a healthy stack.

    A 503 here is the endpoint doing its job (a service is down or an
    invariant violated) — and on the CI stack that is a real failure.
    """
    r = http_client.get("/health")
    assert r.status_code == 200, f"/health HTTP {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert body.get("status") in ("healthy", "degraded"), body.get("status")
    assert isinstance(body.get("services"), dict) and body["services"], (
        "services block missing/empty — the payload no longer reports infra health"
    )


def test_health_stays_fast_under_agent_query_load(http_client, mcp_base, http_headers):
    """F-PERF-04: /health worst-of-6 stays < 100ms while /agent/query runs.

    The stale-while-revalidate cache in ``health_check_endpoint`` exists for
    exactly this budget; if /health starts doing I/O inline again, this goes
    red. Load requests use their own client so a saturated connection pool
    can't masquerade as a slow endpoint.
    """
    def _agent_query(i: int) -> int | None:
        try:
            with httpx.Client(base_url=mcp_base, headers=http_headers, timeout=60.0) as c:
                return c.post(
                    "/agent/query",
                    json={"query": f"health-gate load probe {i}", "domains": ["general"]},
                ).status_code
        except httpx.HTTPError:
            return None  # load generation only; the assertion below is on /health

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        background = [pool.submit(_agent_query, i) for i in range(3)]
        time.sleep(0.3)
        health_times: list[float] = []
        for _ in range(6):
            t0 = time.perf_counter()
            r = http_client.get("/health")
            health_times.append(time.perf_counter() - t0)
            assert r.status_code == 200, f"/health HTTP {r.status_code} under load"
            time.sleep(1.0)
        concurrent.futures.wait(background)

    # 6 samples cannot support a p95 — nearest-rank p95 of n <= 11 IS the max
    # (same arithmetic as app/processor/metrics.py::_percentile). Assert on
    # the max and say so, instead of labelling it a percentile it cannot be.
    worst = max(health_times)
    assert worst < 0.1, (
        f"/health worst-of-{len(health_times)}={worst:.3f}s under load "
        "(threshold 100ms; n too small for a p95, so max is the statistic)"
    )
