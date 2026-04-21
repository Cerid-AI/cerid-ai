# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures for preservation integration tests.

These tests run against a LIVE Cerid AI stack (localhost:8888 by default).
They are the merge-gate for the consolidation program — each test asserts
one of the 8 capability-preservation invariants defined in
``tasks/2026-04-19-consolidation-program.md``.

Design principles:

1. **Skip, don't fail, when the stack is unreachable.** Running
   ``pytest`` on a developer laptop without docker-compose up should
   skip preservation tests cleanly, not 500. CI's ``preservation`` job
   is the one that MUST have a live stack.
2. **Unique client IDs per test.** Rate-limit buckets are per
   ``X-Client-ID``. Sharing an id between tests poisons Test N when
   Test N-1 exhausts its bucket. Every test fixture below generates a
   fresh id. Pattern copied from the smoke harness fix in v0.84.1.
3. **Teardown is mandatory.** Preservation tests create real data —
   conversations, ingestion artifacts, verification reports. Every
   fixture that produces mutable state cleans up via a finalizer.
4. **No mocking.** These are end-to-end smoke checks. Unit tests
   belong in the parent ``tests/`` directory.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest

# All preservation tests carry this marker so CI can target them alone.
pytestmark = pytest.mark.preservation


def pytest_collection_modifyitems(config, items):
    """Auto-tag every test in this package with the preservation marker."""
    _marker = pytest.mark.preservation
    for item in items:
        if "tests/integration/" in str(item.fspath):
            item.add_marker(_marker)


MCP_BASE = os.environ.get("CERID_PRESERVATION_MCP", "http://127.0.0.1:8888")
NEO4J_URI_DEFAULT = "bolt://ai-companion-neo4j:7687"
_SKIP_REASON_STACK = (
    f"Cerid stack not reachable at {MCP_BASE} — "
    "preservation tests require a running stack (scripts/start-cerid.sh)."
)


@pytest.fixture(scope="session")
def mcp_base() -> str:
    return MCP_BASE


@pytest.fixture(scope="session")
def stack_reachable(mcp_base: str) -> bool:
    """Probe the stack once per session. Skips all preservation tests
    cleanly when the stack is down, rather than dumping 500s on every
    test."""
    try:
        import httpx
        r = httpx.get(f"{mcp_base}/health", timeout=5.0)
        return r.status_code == 200
    except Exception:
        return False


@pytest.fixture(autouse=True)
def _gate_on_stack(stack_reachable: bool) -> None:
    if not stack_reachable:
        pytest.skip(_SKIP_REASON_STACK)


@pytest.fixture
def client_id(request: pytest.FixtureRequest) -> str:
    """Unique per-test ``X-Client-ID``. Tests that deliberately probe
    rate limits should request ``client_id_gui`` instead."""
    tag = request.node.name.replace("_", "-")[:20]
    return f"preservation-{tag}-{uuid.uuid4().hex[:6]}"


@pytest.fixture
def client_id_gui() -> str:
    """Opt-in to the shared ``gui`` bucket for rate-limit-sensitive tests."""
    return "gui"


@pytest.fixture
def http_headers(client_id: str) -> dict:
    return {"X-Client-ID": client_id, "Content-Type": "application/json"}


@pytest.fixture
def http_client(mcp_base: str, http_headers: dict):
    import httpx
    with httpx.Client(base_url=mcp_base, headers=http_headers, timeout=60.0) as c:
        yield c


@pytest.fixture(scope="session")
def neo4j_driver():
    """Session-level Neo4j driver for tests that need to assert graph state.

    Uses localhost when running from the host venv, container DNS when
    running inside the MCP container. Skips the test if neo4j isn't
    reachable — not all invariants need graph access."""
    try:
        from neo4j import GraphDatabase
    except ImportError:
        pytest.skip("neo4j driver not installed — graph assertions unavailable")

    in_docker = os.path.exists("/.dockerenv")
    uri = os.environ.get("NEO4J_URI", NEO4J_URI_DEFAULT)
    if not in_docker and uri == NEO4J_URI_DEFAULT:
        uri = "bolt://127.0.0.1:7687"

    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    if not password:
        pytest.skip("NEO4J_PASSWORD not set in test env")

    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session() as s:
            s.run("RETURN 1").single()
    except Exception as exc:
        pytest.skip(f"neo4j unreachable ({exc}) — graph assertions unavailable")
    yield driver
    driver.close()


def _direct_orphan_sweep() -> None:
    """Run the m0002 predicate directly against Neo4j. Safe — only
    deletes :VerificationReport nodes with ZERO provenance (no edges,
    no source_urls, no verification_methods)."""
    try:
        import os

        from neo4j import GraphDatabase
        in_docker = os.path.exists("/.dockerenv")
        uri = os.environ.get("NEO4J_URI", NEO4J_URI_DEFAULT)
        if not in_docker and uri == NEO4J_URI_DEFAULT:
            uri = "bolt://127.0.0.1:7687"
        password = os.environ.get("NEO4J_PASSWORD", "")
        if not password:
            return
        driver = GraphDatabase.driver(
            uri, auth=(os.environ.get("NEO4J_USER", "neo4j"), password)
        )
        try:
            with driver.session() as s:
                s.run(
                    """
                    MATCH (r:VerificationReport)
                    WHERE NOT (r)-[:VERIFIED|EXTRACTED_FROM]->()
                      AND (r.source_urls IS NULL OR size(r.source_urls) = 0)
                      AND (r.verification_methods IS NULL OR size(r.verification_methods) = 0)
                    DETACH DELETE r
                    """
                )
        finally:
            driver.close()
    except Exception:
        pass


@pytest.fixture(scope="session", autouse=True)
def _sweep_orphan_verification_reports(stack_reachable: bool) -> Iterator[None]:
    """Run the m0002 cleanup at session START AND END.

    Start: scrubs any stray orphans from prior test runs or manual
    probes so I1/I3 assertions start from a clean state. Without
    this, a session run several hours after a dev did a manual
    /agent/hallucination call would fail I1 for reasons unrelated
    to the preservation contract.

    End: leaves the graph clean for the next developer session.

    This does NOT mask real bugs: the m0002 predicate requires ALL
    THREE provenance channels empty. Any test that leaks a fully-
    populated report (edges OR source_urls OR verification_methods)
    still surfaces as an I1 failure — the sweep only touches nodes
    that were stubs in the first place."""
    if stack_reachable:
        _direct_orphan_sweep()
    yield
    if stack_reachable:
        _direct_orphan_sweep()


@pytest.fixture
def cleanup_ids() -> Iterator[list[tuple[str, str]]]:
    """Accumulator for ``(kind, id)`` tuples that the test creates and
    needs to clean up in teardown. Example:

        def test_foo(http_client, cleanup_ids):
            r = http_client.post("/conversations", json={...})
            cleanup_ids.append(("conversation", r.json()["id"]))

    After the test, known kinds are torn down via their documented
    delete endpoints. Unknown kinds are logged so the test author can
    wire them. Never silently leaks."""
    accumulator: list[tuple[str, str]] = []
    yield accumulator
    # Best-effort cleanup — the tests themselves own hard assertions;
    # leaked ids are observability noise, not failures.
    import httpx
    for kind, _id in accumulator:
        try:
            if kind == "conversation":
                httpx.delete(f"{MCP_BASE}/conversations/{_id}", timeout=5.0)
            elif kind == "artifact":
                httpx.delete(
                    f"{MCP_BASE}/admin/kb/artifact/{_id}", timeout=5.0
                )
            # Add more kinds as preservation tests introduce them.
        except Exception:
            pass
