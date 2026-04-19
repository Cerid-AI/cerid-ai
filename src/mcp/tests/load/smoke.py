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
    # Pre-Task-8 baseline was p95 4.67s + max 4.67s under 3 bg agent queries.
    # Post-fix /health runs on HEALTH_POOL (cap 32) but the underlying
    # cypher-probe + /health cache miss on first call still costs ~2s on
    # macOS under docker.  3.0s is the new threshold: catches the original
    # regression while tolerating the residual cold-cache cost.
    assert max(mids) < 3.0, (
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


async def check_verification_lifecycle_lands(
    client: httpx.AsyncClient,
    *,
    timeout_s: float = 30.0,
) -> dict:
    """R5-2 invariant: a real hallucination-check flow lands a VerificationReport + VERIFIED edge.

    Posts a short response text to /agent/hallucination with a stable conversation_id,
    then polls Neo4j for up to timeout_s seconds to confirm a VerificationReport node
    and at least one [:VERIFIED]->(Artifact) edge landed.

    Returns {"pass": bool, "details": {...}}.

    Skips gracefully when ENABLE_HALLUCINATION_CHECK=false or neo4j is unreachable.
    """
    import os
    import time
    import uuid

    # Conditional gate: skip when feature is off.
    enable_check = os.getenv("ENABLE_HALLUCINATION_CHECK", "true").lower() in ("true", "1", "yes")
    if not enable_check:
        return {
            "pass": True,
            "skipped": True,
            "details": {"reason": "ENABLE_HALLUCINATION_CHECK=false — invariant not applicable"},
        }

    conversation_id = str(uuid.uuid4())
    t_start = time.monotonic()

    # Trigger a hallucination-check via POST /agent/hallucination with a minimal payload.
    # This is the same route the ingestion pipeline calls — it extracts claims, verifies
    # them, and saves a VerificationReport with VERIFIED edges to referenced Artifacts.
    body = {
        "response_text": (
            "The speed of light in a vacuum is approximately 299,792 kilometres per second. "
            "Water freezes at 0 degrees Celsius at standard atmospheric pressure."
        ),
        "conversation_id": conversation_id,
        "user_query": "What is the speed of light?",
    }
    _, post_status, post_json = await post(client, "/agent/hallucination", body)
    if post_status not in (200, 202):
        return {
            "pass": False,
            "details": {
                "conversation_id": conversation_id,
                "reason": f"POST /agent/hallucination returned HTTP {post_status}",
                "body": str(post_json)[:200],
            },
        }

    # Import Neo4j driver inline — smoke.py runs as a standalone script with no
    # project-level imports available. neo4j is a dep of the MCP container.
    try:
        from neo4j import GraphDatabase  # type: ignore[import]
    except ImportError:
        return {
            "pass": True,
            "skipped": True,
            "details": {"reason": "neo4j driver not installed in this environment — skipping Neo4j poll"},
        }

    neo4j_uri = os.getenv("NEO4J_URI", "bolt://ai-companion-neo4j:7687")
    neo4j_user = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password = os.getenv("NEO4J_PASSWORD", "")

    try:
        driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
    except Exception as exc:
        return {
            "pass": True,
            "skipped": True,
            "details": {"reason": f"Neo4j driver init failed: {exc} — skipping Neo4j poll"},
        }

    edges_found = 0
    report_found = False
    deadline = time.monotonic() + timeout_s
    try:
        with driver.session() as session:
            while time.monotonic() < deadline:
                # Check for the VerificationReport node first
                r_node = session.run(
                    "MATCH (v:VerificationReport {conversation_id: $cid}) RETURN count(v) AS n",
                    cid=conversation_id,
                ).single()
                if r_node and r_node["n"] > 0:
                    report_found = True
                    # Now check for VERIFIED edges
                    r_edges = session.run(
                        "MATCH (v:VerificationReport {conversation_id: $cid})"
                        "-[:VERIFIED]->(a) "
                        "RETURN count(a) AS edges",
                        cid=conversation_id,
                    ).single()
                    if r_edges is not None:
                        edges_found = r_edges["edges"]
                        if edges_found > 0:
                            elapsed = time.monotonic() - t_start
                            return {
                                "pass": True,
                                "details": {
                                    "conversation_id": conversation_id,
                                    "edges_landed": edges_found,
                                    "elapsed_s": round(elapsed, 2),
                                },
                            }
                await asyncio.sleep(1.0)
    except Exception as exc:
        return {
            "pass": False,
            "details": {
                "conversation_id": conversation_id,
                "reason": f"Neo4j poll error: {exc}",
                "edges_landed": edges_found,
            },
        }
    finally:
        driver.close()

    # Timed out without finding edges.
    # Note: it's possible claims were verified but no Artifact nodes existed to link
    # (e.g. KB is empty). In that case report_found=True but edges_found=0 is expected.
    # We still fail to catch regressions where the MERGE itself is broken.
    return {
        "pass": False,
        "details": {
            "conversation_id": conversation_id,
            "report_node_found": report_found,
            "edges_landed": edges_found,
            "timeout_s": timeout_s,
            "reason": (
                "VerificationReport node found but no [:VERIFIED] edges within timeout"
                if report_found
                else "No VerificationReport node found within timeout — check R4-2 Sentry events"
            ),
        },
    }


async def test_verification_lifecycle(client):
    print("\n== TEST I: R5-2 verification lifecycle — VerificationReport + VERIFIED edge ==")
    result = await check_verification_lifecycle_lands(client, timeout_s=30.0)
    if result.get("skipped"):
        print(f"  SKIPPED — {result['details']['reason']}")
    elif result["pass"]:
        d = result["details"]
        print(
            f"  PASS — conversation_id={d['conversation_id'][:8]}... "
            f"edges_landed={d['edges_landed']} elapsed={d['elapsed_s']}s"
        )
    else:
        d = result["details"]
        print(
            f"  FAIL — {d.get('reason', 'unknown')} "
            f"(conversation_id={d.get('conversation_id', '?')[:8]}...)"
        )
        assert False, f"R5-2 verification lifecycle invariant failed: {d}"


async def main():
    async with httpx.AsyncClient(timeout=30.0) as client:
        await test_health_latency(client, 20)
        await test_source_shape_invariant(client)
        await test_cache_hit_rate(client, 4)
        await test_agent_concurrent(client, 6)
        await test_head_of_line_blocking(client)
        await test_cb_trip_after_flap(client)
        await test_rate_limit(client)
        await test_verification_lifecycle(client)
    await test_sse_chat_stream()


asyncio.run(main())
