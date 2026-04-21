# src/mcp/tests/test_latency_slo.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Latency SLO assertions — CI fails on regression past the budget.

Marked with ``@pytest.mark.benchmark_slo`` so they're excluded from the
default pytest run (they need the running MCP stack to hit real DBs) and
gated to a dedicated CI job that boots the stack first.

Budgets tracked:
  - /agent/query cold (simple query, single domain, top_k=3): p95 < 3 s
  - /agent/query warm (identical query in cache):            max < 0.3 s
  - /chat/stream TTFT:                                       p95 < 2 s

Tier system:
  - STRICT (Tier A): user-visible SLOs — test fails immediately if exceeded.
  - ADVISORY (Tier B): same test records stats via benchmark.extra_info but
    does NOT fail; used to surface trends in CI comment bots.

All tests in this file are STRICT (Tier A) at the R5-1 budgets. R2
(streaming RAG) will tighten them to production targets:
  - cold 2 s / warm 200 ms / TTFT 1 s.

Running locally:
    # With a running MCP stack (scripts/start-cerid.sh):
    make slo
    # or directly:
    cd src/mcp && pytest tests/test_latency_slo.py -m benchmark_slo --benchmark-only -v
"""
from __future__ import annotations

import os
import time
import uuid

import httpx
import pytest

# Only runs when the MCP stack is available.
pytestmark = pytest.mark.benchmark_slo

MCP_URL = os.getenv("CERID_MCP_URL", "http://localhost:8888")


def _cold_query_payload() -> dict:
    """Unique query per call — defeats the Redis query cache."""
    return {
        "query": f"slo-cold-{uuid.uuid4().hex[:12]}",
        "domains": ["general"],
        "top_k": 3,
    }


@pytest.mark.benchmark(group="agent_query_cold", min_rounds=5, disable_gc=True)
def test_agent_query_cold_under_3s(benchmark):
    """R5-1 STRICT: cold /agent/query under 3 s p95 on single populated domain."""
    with httpx.Client(base_url=MCP_URL, timeout=10.0) as client:

        def _run():
            response = client.post(
                "/agent/query",
                json=_cold_query_payload(),
                headers={"X-Client-ID": "slo-harness"},
            )
            response.raise_for_status()
            return response

        benchmark.pedantic(_run, rounds=5, iterations=1, warmup_rounds=0)
    # benchmark.stats populated after run; use max as conservative p95 proxy
    assert benchmark.stats.stats.max < 3.0, (
        f"cold /agent/query exceeded 3s SLO: "
        f"max={benchmark.stats.stats.max:.2f}s, "
        f"mean={benchmark.stats.stats.mean:.2f}s"
    )


@pytest.mark.benchmark(group="agent_query_warm", min_rounds=10, disable_gc=True)
def test_agent_query_warm_under_300ms(benchmark):
    """R5-1 STRICT: warm cache /agent/query under 300 ms max."""
    with httpx.Client(base_url=MCP_URL, timeout=5.0) as client:
        # Warm the cache once with a stable query
        warm_payload = {"query": "slo-warm-stable", "domains": ["general"], "top_k": 3}
        client.post("/agent/query", json=warm_payload, headers={"X-Client-ID": "slo-harness"})

        def _run():
            response = client.post(
                "/agent/query",
                json=warm_payload,
                headers={"X-Client-ID": "slo-harness"},
            )
            response.raise_for_status()
            return response

        benchmark.pedantic(_run, rounds=10, iterations=1, warmup_rounds=1)
    assert benchmark.stats.stats.max < 0.3, (
        f"warm /agent/query exceeded 300 ms SLO: "
        f"max={benchmark.stats.stats.max*1000:.0f}ms"
    )


@pytest.mark.benchmark(group="chat_stream_ttft", min_rounds=3, disable_gc=True)
def test_chat_stream_ttft_under_2s(benchmark):
    """R5-1 STRICT: /chat/stream Time-To-First-Token under 2 s p95.

    TTFT = time from request send to first SSE ``data:`` chunk arrival.
    """

    def _ttft_once() -> float:
        payload = {
            "model": "openai/gpt-4o-mini",
            "messages": [{"role": "user", "content": "say hi"}],
            "stream": True,
            "max_tokens": 10,
        }
        start = time.perf_counter()
        with httpx.Client(base_url=MCP_URL, timeout=10.0) as client:
            with client.stream(
                "POST",
                "/chat/stream",
                json=payload,
                headers={"X-Client-ID": "slo-harness", "Content-Type": "application/json"},
            ) as response:
                for line in response.iter_lines():
                    if line.startswith("data:"):
                        return time.perf_counter() - start
        raise RuntimeError("No data chunk received")

    benchmark.pedantic(_ttft_once, rounds=3, iterations=1, warmup_rounds=0)
    assert benchmark.stats.stats.max < 2.0, (
        f"chat/stream TTFT exceeded 2s SLO: max={benchmark.stats.stats.max:.2f}s"
    )
