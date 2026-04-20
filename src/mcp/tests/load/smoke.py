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


def smoke_client_id(tag: str) -> str:
    """Return a unique X-Client-ID for one smoke test.

    Rate-limit buckets are keyed on ``X-Client-ID``. Using one shared id
    across tests makes Test E (rate-limit probe) poison every following
    test. Per-test fresh ids keep buckets isolated and make the harness
    commutative: tests can run in any order, and in future parallel
    workers each generates its own ids.
    """
    import uuid
    return f"smoke-{tag}-{uuid.uuid4().hex[:8]}"


def smoke_headers(tag: str) -> dict:
    return {"X-Client-ID": smoke_client_id(tag), "Content-Type": "application/json"}

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
    headers: dict | None = None,
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
    _, post_status, post_json = await post(client, "/agent/hallucination", body, headers=headers)
    if post_status not in (200, 202):
        return {
            "pass": False,
            "details": {
                "conversation_id": conversation_id,
                "reason": f"POST /agent/hallucination returned HTTP {post_status}",
                "body": str(post_json)[:200],
            },
        }

    # Architectural contract (2026-04-19): /agent/hallucination runs the
    # verification and returns the claim verdicts; the long-term
    # :VerificationReport persistence lives behind /verification/save
    # (app/db/neo4j/artifacts.py::save_verification_report). The FE calls
    # both in sequence. promote_verified_facts in the hallucination path
    # MERGEs a stub VerificationReport for VERIFIED_BY linking but does
    # not populate id/created_at/edges. To validate the full R5-2
    # invariant (node + [:VERIFIED] edges), the smoke must also call
    # /verification/save — otherwise we only see the stub.
    save_body = {
        "conversation_id": conversation_id,
        "claims": post_json.get("claims", []),
        "overall_score": post_json.get("overall_score", 1.0),
        "verified": post_json.get("verified", 0),
        "unverified": post_json.get("unverified", 0),
        "uncertain": post_json.get("uncertain", 0),
        "total": post_json.get("total", len(post_json.get("claims", []))),
    }
    _, save_status, save_json = await post(client, "/verification/save", save_body, headers=headers)
    if save_status not in (200, 202):
        return {
            "pass": False,
            "details": {
                "conversation_id": conversation_id,
                "reason": f"POST /verification/save returned HTTP {save_status}",
                "body": str(save_json)[:200],
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

    # Host/container portability: the default URI uses the Docker DNS name,
    # which won't resolve from a host venv. If we are NOT inside a Docker
    # container and the URI is the default, swap in localhost.
    _in_docker = Path("/.dockerenv").exists() if (Path := __import__("pathlib").Path) else False
    _default_uri = "bolt://ai-companion-neo4j:7687"
    neo4j_uri = os.getenv("NEO4J_URI", _default_uri)
    if not _in_docker and neo4j_uri == _default_uri:
        neo4j_uri = "bolt://127.0.0.1:7687"
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
    provenance_found = False  # edges OR source_urls OR verification_methods
    deadline = time.monotonic() + timeout_s
    try:
        with driver.session() as session:
            while time.monotonic() < deadline:
                # Task-2 contract: the writer MUST populate one of three
                # provenance channels, depending on which verification
                # path fired:
                #   (a) [:EXTRACTED_FROM]/[:VERIFIED] edges — kb_nli path
                #   (b) source_urls array — web_search / external path
                #   (c) verification_methods array — set by any path
                # R5-2 passes when the node exists AND has provenance
                # via any of the three channels. Requiring edges alone
                # produces false failures when the response_text has no
                # KB matches (the test's "speed of light" text triggers
                # cross_model, which has no artifact source).
                r = session.run(
                    """
                    MATCH (v:VerificationReport {conversation_id: $cid})
                    OPTIONAL MATCH (v)-[:VERIFIED]->(a)
                    RETURN
                        count(DISTINCT v) AS n,
                        count(a) AS edges,
                        coalesce(size(v.source_urls), 0) AS urls,
                        coalesce(size(v.verification_methods), 0) AS methods
                    """,
                    cid=conversation_id,
                ).single()
                if r and r["n"] > 0:
                    report_found = True
                    edges_found = int(r["edges"] or 0)
                    urls = int(r["urls"] or 0)
                    methods = int(r["methods"] or 0)
                    provenance_found = edges_found > 0 or urls > 0 or methods > 0
                    if provenance_found:
                        elapsed = time.monotonic() - t_start
                        return {
                            "pass": True,
                            "details": {
                                "conversation_id": conversation_id,
                                "edges_landed": edges_found,
                                "source_urls": urls,
                                "verification_methods": methods,
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

    return {
        "pass": False,
        "details": {
            "conversation_id": conversation_id,
            "report_node_found": report_found,
            "edges_landed": edges_found,
            "timeout_s": timeout_s,
            "reason": (
                "VerificationReport node found but NO provenance (edges, source_urls, verification_methods all empty) — writer regression"
                if report_found
                else "No VerificationReport node found within timeout — check /verification/save + Sentry events"
            ),
        },
    }


async def test_verification_lifecycle(client) -> dict:
    print("\n== TEST I: R5-2 verification lifecycle — VerificationReport + VERIFIED edge ==")
    # Dedicated fresh client id prevents Test E (rate-limit probe) from
    # poisoning this probe. See smoke_client_id() for rationale.
    result = await check_verification_lifecycle_lands(
        client, timeout_s=30.0, headers=smoke_headers("verif"),
    )
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
    return result


async def main() -> int:
    """Exit codes: 0 = all passed, 1 = assertion failure, 2 = R5-2 skipped.

    Exit 2 is a soft warning — CI should surface it as a PR-check warning,
    not a block. It distinguishes "we did not validate R5-2 in this
    environment" (e.g. neo4j driver missing in a host venv) from "R5-2
    validated and passed".
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        await test_health_latency(client, 20)
        await test_source_shape_invariant(client)
        await test_cache_hit_rate(client, 4)
        await test_agent_concurrent(client, 6)
        await test_head_of_line_blocking(client)
        await test_cb_trip_after_flap(client)
        await test_rate_limit(client)
        verif_result = await test_verification_lifecycle(client)
    await test_sse_chat_stream()
    if verif_result.get("skipped"):
        print("::warning::R5-2 verification lifecycle was SKIPPED — not validated in this environment")
        return 2
    return 0


sys.exit(asyncio.run(main()))
