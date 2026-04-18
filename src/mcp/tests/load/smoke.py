#!/usr/bin/env python3
"""Cerid-AI smoke / load test harness. Read-only probes against localhost."""

import asyncio
import random
import statistics
import sys
import time

try:
    import httpx
except ImportError:
    import subprocess

    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "httpx"])
    import httpx

MCP = "http://127.0.0.1:8888"
GUI = "http://127.0.0.1:3000"

HEADERS_GUI = {"X-Client-ID": "gui", "Content-Type": "application/json"}
HEADERS_TRADE = {"X-Client-ID": "trading-agent", "Content-Type": "application/json"}
HEADERS_UNKNOWN = {
    "X-Client-ID": f"probe-{random.randint(1000, 9999)}",
    "Content-Type": "application/json",
}

QUERIES = [
    "what is parallel computing",
    "how do whales feed and migrate",
    "what is the capital of Mongolia",
    "tell me about FastAPI middleware ordering",
    "list the trading strategies used in quantitative finance",
    "what does cerid ai do",
    "photosynthesis summary",
    "capital of antarctica",
]


async def timed(coro):
    t0 = time.perf_counter()
    r = await coro
    return time.perf_counter() - t0, r


async def get(client, path, headers=None):
    t, r = await timed(
        client.get(MCP + path, headers=headers or HEADERS_GUI, timeout=30)
    )
    return t, r.status_code, r.headers.get("content-length", "?"), r.text[:200]


async def post(client, path, body, headers=None):
    t, r = await timed(
        client.post(MCP + path, json=body, headers=headers or HEADERS_GUI, timeout=60)
    )
    try:
        j = r.json()
    except Exception:
        j = {}
    return t, r.status_code, j


async def test_health_latency(client, n=20):
    print(f"\n== TEST A: /health x{n} concurrent (worker-starvation probe) ==")
    results = await asyncio.gather(*[get(client, "/health") for _ in range(n)])
    times = [r[0] for r in results]
    print(
        f"  n={n} p50={statistics.median(times):.3f}s p95={sorted(times)[int(0.95 * n)]:.3f}s max={max(times):.3f}s min={min(times):.3f}s"
    )
    print(f"  statuses={set(r[1] for r in results)}")


async def test_agent_concurrent(client, n=6):
    print(
        f"\n== TEST B: {n} concurrent /agent/query distinct queries (semaphore probe) =="
    )
    qs = random.sample(QUERIES, n)
    results = await asyncio.gather(
        *[
            post(
                client,
                "/agent/query",
                {"query": q, "domains": ["general"], "n_results": 5},
            )
            for q in qs
        ],
        return_exceptions=True,
    )
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            print(f"  q={qs[i][:30]}... ERR {r}")
        else:
            t, status, j = r
            print(
                f"  t={t:5.2f}s http={status} budget={j.get('budget_exceeded')} strategy={j.get('strategy', '?')[:24]} results={len(j.get('results', []))} sources={len(j.get('sources', []))} src_bd={sum(len(v) for v in (j.get('source_breakdown') or {}).values())}"
            )


async def test_cache_hit_rate(client, n=8):
    print(f"\n== TEST C: cache hit-rate (same query {n}x) ==")
    q = QUERIES[0]
    first = await post(
        client, "/agent/query", {"query": q, "domains": ["general"], "n_results": 5}
    )
    print(
        f"  cold  t={first[0]:.2f}s  http={first[1]}  body_keys={list(first[2].keys())[:8]}"
    )
    for i in range(n - 1):
        t, s, j = await post(
            client, "/agent/query", {"query": q, "domains": ["general"], "n_results": 5}
        )
        print(
            f"  warm{i + 1} t={t:.3f}s http={s} cached={j.get('cached', j.get('cache_hit'))}"
        )


async def test_source_shape_invariant(client):
    print(
        "\n== TEST D: response-shape invariant (results vs sources vs source_breakdown) =="
    )
    for q in ["simple thing", "random query about nothing 42 17"]:
        t, s, j = await post(
            client,
            "/agent/query",
            {"query": q, "domains": ["general", "conversations"], "n_results": 5},
        )
        nr = len(j.get("results", []))
        nsrc = len(j.get("sources", []))
        nsb = {k: len(v) for k, v in (j.get("source_breakdown") or {}).items()}
        print(
            f"  q={q!r:35} results={nr} sources={nsrc} source_breakdown={nsb} strategy={j.get('strategy', '')[:22]}"
        )
        # Wave-0 invariant
        assert nr == sum(nsb.values()), (
            f"shape drift: results={nr} vs source_breakdown={nsb}"
        )
        assert nsrc == nr, f"sources({nsrc}) must mirror results({nr})"
        assert "kb" in nsb and "memory" in nsb and "external" in nsb, (
            "source_breakdown keys missing"
        )


async def test_rate_limit(client):
    print(
        "\n== TEST E: rate-limit (fire 25 /agent/query under X-Client-ID=gui which is 20/min) =="
    )
    tasks = [
        post(
            client,
            "/agent/query",
            {"query": f"rate test {i}", "domains": ["general"], "n_results": 3},
            HEADERS_GUI,
        )
        for i in range(25)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    codes = [(r[1] if not isinstance(r, Exception) else "ERR") for r in results]
    from collections import Counter

    codes_cnt = dict(Counter(codes))
    print(f"  status counts: {codes_cnt}")
    # Task 10 invariant: breaching the per-client quota must produce a 429,
    # never drop the connection. "ERR" in the histogram means the server
    # hung up under load — a graceful-backpressure regression.
    assert "ERR" not in codes_cnt, (
        f"connection drops under load (graceful 429 regression): {codes_cnt}"
    )
    assert 429 in codes_cnt, f"expected 429 in mix, got {codes_cnt}"


async def test_sse_chat_stream():
    print("\n== TEST F: /chat/stream TTFB + chunk latency + cancel behaviour ==")
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "model": "openrouter/openai/gpt-4o-mini",
    }
    t0 = time.perf_counter()
    first_byte = None
    total_bytes = 0
    chunks = 0
    async with httpx.AsyncClient(timeout=60.0) as c:
        try:
            async with c.stream(
                "POST", MCP + "/chat/stream", json=body, headers=HEADERS_GUI
            ) as r:
                print(
                    f"  status={r.status_code} content-type={r.headers.get('content-type', '')}"
                )
                async for chunk in r.aiter_bytes():
                    if first_byte is None:
                        first_byte = time.perf_counter() - t0
                    total_bytes += len(chunk)
                    chunks += 1
                    if chunks <= 3:
                        print(
                            f"  chunk[{chunks}] +{time.perf_counter() - t0:.2f}s {len(chunk)}B preview={chunk[:80]!r}"
                        )
                    if chunks >= 40:
                        break
        except Exception as e:
            print(f"  stream ERR: {type(e).__name__}: {e}")
    total = time.perf_counter() - t0
    print(
        f"  TTFB={first_byte}s total={total:.2f}s chunks={chunks} bytes={total_bytes}"
    )


async def test_head_of_line_blocking(client):
    print(
        "\n== TEST G: HOL blocking (fire 3x agent/query in background, measure /health RT) =="
    )

    async def load():
        return await post(
            client,
            "/agent/query",
            {
                "query": "concurrent hol probe " + str(random.random()),
                "domains": ["general"],
                "n_results": 5,
            },
        )

    bg = [asyncio.create_task(load()) for _ in range(3)]
    await asyncio.sleep(0.3)  # let them start
    mids = []
    for i in range(6):
        t, s, _, _ = await get(client, "/health")
        mids.append(t)
        await asyncio.sleep(1.0)
    # drain
    for b in bg:
        try:
            await b
        except Exception:
            pass
    print(
        f"  /health during load: p50={statistics.median(mids):.2f}s p95={sorted(mids)[-1]:.2f}s max={max(mids):.2f}s"
    )
    # Wave-1 invariant (Task 8): /health must not serialize behind KB.
    # Pre-Task-8 baseline was p95 4.67s under 3 bg agent queries; post-fix
    # /health runs on HEALTH_POOL (cap 32) so the max should be sub-second.
    # 1s is a forgiving ceiling that still catches regression.
    assert max(mids) < 1.0, (
        f"HOL regression: /health max {max(mids):.2f}s under 3 bg agent queries"
    )


async def test_cb_trip_after_flap(client):
    print("\n== TEST H: CB state after triggering external flake ==")
    # hit a query likely to invoke wikipedia/duckduckgo to see if CB trips
    t, s, j = await post(
        client,
        "/agent/query",
        {
            "query": "wikipedia fresh random thing xyz " + str(random.random()),
            "domains": ["general"],
            "n_results": 5,
        },
    )
    print(
        f"  query t={t:.2f}s strategy={j.get('strategy', '')[:22]} results={len(j.get('results', []))} ext_count={sum(1 for r in j.get('results', []) if r.get('source_type') == 'external')}"
    )
    # observe subsequent behaviour
    for i in range(3):
        t, s, j = await post(
            client,
            "/agent/query",
            {
                "query": "wikipedia follow up " + str(i),
                "domains": ["general"],
                "n_results": 5,
            },
        )
        print(
            f"  followup{i} t={t:.2f}s ext_count={sum(1 for r in j.get('results', []) if r.get('source_type') == 'external')} strategy={j.get('strategy', '')[:22]}"
        )


async def main():
    async with httpx.AsyncClient(timeout=30.0) as client:
        await test_health_latency(client, 20)
        await test_source_shape_invariant(client)
        await test_cache_hit_rate(client, 4)
        await test_agent_concurrent(client, 6)
        await test_head_of_line_blocking(client)
        await test_cb_trip_after_flap(client)
        await test_rate_limit(client)
    await test_sse_chat_stream()


asyncio.run(main())
