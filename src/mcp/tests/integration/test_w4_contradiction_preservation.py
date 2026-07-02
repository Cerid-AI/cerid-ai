# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""I17 — Contradiction ledger preservation gate (Phase W.4).

Invariant: a contradiction finding logged via the service layer MUST:
  1. Persist to Neo4j (verified via the :func:`get_by_id` service call)
  2. Surface in the ``GET /wiki/contradictions`` list endpoint
  3. Be retrievable via ``GET /wiki/contradictions/{finding_id}``
  4. Return the documented response shape on all three surfaces

This test runs against a LIVE Cerid AI stack (localhost:8888 by default)
using the :mod:`tests.integration.conftest` fixtures. It is marked
``@pytest.mark.preservation`` so CI can target it with
``pytest -m preservation``.

**DO NOT register I17 in docs/PRESERVATION.md yet.** That happens when
the full W phase (W.1 + W.4) ships together. This test exists and runs
locally; the production gate-wiring is deferred to the W-phase cut.

Design notes
------------
* Uses TestClient (in-process) against the FastAPI app so the test works
  without a running HTTP server — just a live Neo4j. When the full stack
  IS running, the http_client fixture path also exercises routing.
* The synthetic contradiction is cleaned up in teardown via a direct
  Neo4j call (DETACH DELETE on the :ContradictionFinding node and its
  associated :Claim nodes).
"""
from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = pytest.mark.preservation

_ENTITY_SLUG = "test-entity-w4-preservation"


# ---------------------------------------------------------------------------
# Helper: build a synthetic ContradictionFinding
# ---------------------------------------------------------------------------


def _make_synthetic_finding():
    from app.services.contradiction_log import ContradictionFinding

    fid = f"w4-preservation-{uuid.uuid4().hex[:12]}"
    return ContradictionFinding(
        finding_id=fid,
        claim_a_id=f"claim-a-{fid}",
        claim_b_id=f"claim-b-{fid}",
        claim_a_text="The experiment succeeded on the first attempt.",
        claim_b_text="Three failed attempts were recorded before success.",
        entity_slug=_ENTITY_SLUG,
        severity="high",
        query_ctx_id="w4-preservation-ctx",
        source_artifacts=["artifact-w4-001", "artifact-w4-002"],
    )


# ---------------------------------------------------------------------------
# Teardown helper
# ---------------------------------------------------------------------------


def _cleanup_finding(neo4j_driver, finding_id: str, claim_a_id: str, claim_b_id: str) -> None:
    """Remove the synthetic ContradictionFinding and its Claim nodes."""
    with contextlib.suppress(Exception):  # best-effort cleanup; leaked nodes swept later
        with neo4j_driver.session() as session:
            session.run(
                """
                MATCH (f:ContradictionFinding {finding_id: $finding_id})
                OPTIONAL MATCH (ca:Claim {claim_id: $claim_a_id})
                OPTIONAL MATCH (cb:Claim {claim_id: $claim_b_id})
                DETACH DELETE f, ca, cb
                """,
                finding_id=finding_id,
                claim_a_id=claim_a_id,
                claim_b_id=claim_b_id,
            )


# ---------------------------------------------------------------------------
# I17 preservation test
# ---------------------------------------------------------------------------


@pytest.mark.preservation
def test_i17_contradiction_log_persistence_and_surface(
    stack_reachable: bool,
    neo4j_driver,
) -> None:
    """I17: planted contradiction → persists → surfaces via API.

    Steps
    -----
    1. Log a synthetic finding via the service layer (simulates the NLI guard).
    2. Verify the finding can be retrieved by ID via the service layer.
    3. Query the FastAPI router via TestClient and verify list response shape.
    4. Query the single-finding endpoint and verify documented shape.
    5. Teardown: delete the synthetic nodes from Neo4j.
    """
    import asyncio

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Override get_neo4j in the service and adapter layers to use the
    # test driver directly (avoids needing CERID_API_KEY env set).
    import app.services.contradiction_log as svc

    # Wire the app with contradiction router + override Neo4j dep to use
    # the live test driver so no environment variables are needed.
    from app.routers import contradictions as contradictions_router
    from app.services.contradiction_log import get_by_id, log_contradiction

    _orig_svc_get_neo4j = getattr(svc, "_get_neo4j_for_test", None)

    finding = _make_synthetic_finding()

    try:
        # -- Step 1: Log via service ------------------------------------------
        with (
            pytest.MonkeyPatch().context() as mp,
        ):
            mp.setattr("app.deps.get_neo4j", lambda: neo4j_driver)

            # Patch the lazy get_neo4j inside the service module's closures
            import app.deps as deps_mod
            original_get_neo4j = deps_mod.get_neo4j
            deps_mod.get_neo4j = lambda: neo4j_driver

            try:
                returned_id = asyncio.run(
                    log_contradiction(finding)
                )
            finally:
                deps_mod.get_neo4j = original_get_neo4j

        assert returned_id == finding.finding_id, (
            f"log_contradiction returned {returned_id!r}, expected {finding.finding_id!r}"
        )

        # -- Step 2: Retrieve by ID via service --------------------------------
        import app.deps as deps_mod
        original_get_neo4j = deps_mod.get_neo4j
        deps_mod.get_neo4j = lambda: neo4j_driver
        try:
            fetched = asyncio.run(
                get_by_id(finding.finding_id)
            )
        finally:
            deps_mod.get_neo4j = original_get_neo4j

        assert fetched is not None, "get_by_id returned None — finding was not persisted"
        assert fetched.finding_id == finding.finding_id
        assert fetched.severity == "high"
        assert fetched.entity_slug == _ENTITY_SLUG

        # -- Step 3: Query list endpoint via TestClient -----------------------
        test_app = FastAPI()
        test_app.include_router(contradictions_router.router)

        import app.deps as deps_mod
        original_get_neo4j = deps_mod.get_neo4j
        deps_mod.get_neo4j = lambda: neo4j_driver

        try:
            with TestClient(test_app) as client:
                r = client.get(
                    "/wiki/contradictions",
                    params={"entity_slug": _ENTITY_SLUG, "limit": 10},
                )
        finally:
            deps_mod.get_neo4j = original_get_neo4j

        assert r.status_code == 200, f"GET /wiki/contradictions HTTP {r.status_code}: {r.text[:300]}"
        body = r.json()

        # Documented shape: total, limit, findings[]
        assert "total" in body, "Response missing 'total' field"
        assert "limit" in body, "Response missing 'limit' field"
        assert "findings" in body, "Response missing 'findings' field"
        assert isinstance(body["findings"], list), "'findings' must be a list"

        finding_ids_in_list = [f["finding_id"] for f in body["findings"]]
        assert finding.finding_id in finding_ids_in_list, (
            f"Logged finding {finding.finding_id!r} not found in list response. "
            f"Got: {finding_ids_in_list}"
        )

        # -- Step 4: Single-finding endpoint -----------------------------------
        import app.deps as deps_mod
        original_get_neo4j = deps_mod.get_neo4j
        deps_mod.get_neo4j = lambda: neo4j_driver

        try:
            with TestClient(test_app) as client:
                r2 = client.get(f"/wiki/contradictions/{finding.finding_id}")
        finally:
            deps_mod.get_neo4j = original_get_neo4j

        assert r2.status_code == 200, (
            f"GET /wiki/contradictions/{{id}} HTTP {r2.status_code}: {r2.text[:300]}"
        )
        item = r2.json()

        # Verify documented shape fields are all present
        required_fields = {
            "finding_id", "claim_a_id", "claim_b_id",
            "claim_a_text", "claim_b_text",
            "entity_slug", "severity", "detected_at",
            "query_ctx_id", "source_artifacts",
        }
        missing = required_fields - set(item.keys())
        assert not missing, f"Response missing documented fields: {missing}"

        assert item["finding_id"] == finding.finding_id
        assert item["severity"] == "high"
        assert item["entity_slug"] == _ENTITY_SLUG
        assert "artifact-w4-001" in item["source_artifacts"]

        # 404 for nonexistent
        import app.deps as deps_mod
        original_get_neo4j = deps_mod.get_neo4j
        deps_mod.get_neo4j = lambda: neo4j_driver

        try:
            with TestClient(test_app) as client:
                r_miss = client.get("/wiki/contradictions/does-not-exist-abc")
        finally:
            deps_mod.get_neo4j = original_get_neo4j

        assert r_miss.status_code == 404

    finally:
        # -- Teardown: clean up synthetic nodes --------------------------------
        _cleanup_finding(
            neo4j_driver,
            finding.finding_id,
            finding.claim_a_id,
            finding.claim_b_id,
        )
