# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""I3 — Verification round-trip produces a provenanced VerificationReport.

Preservation invariant: the /agent/hallucination → /verification/save
pair must persist a :VerificationReport in Neo4j with ONE OF three
provenance channels populated:

  * [:VERIFIED]/[:EXTRACTED_FROM] edges to :Artifact nodes (kb_nli path)
  * source_urls array (web_search path)
  * verification_methods array (cross_model and any path)

This is the writer contract fixed in v0.84.1 (commit 9725835 —
recognizing flat source_artifact_id) and the smoke harness fix that
surfaced a production silent-orphan bug. Regression here means every
saved VerificationReport becomes unlinkable from its evidence.

Sprint C will fold the two calls into one (/agent/hallucination
with persist=true). This test stays portable — when Sprint C lands,
the assertions remain valid because the data path doesn't change.
"""
from __future__ import annotations

import time
import uuid

_RESPONSE_TEXT = (
    "The speed of light in a vacuum is approximately 299,792 kilometres per second. "
    "Water freezes at 0 degrees Celsius at standard atmospheric pressure."
)


def _hallucination_check(http_client, conv_id: str) -> dict:
    r = http_client.post(
        "/agent/hallucination",
        json={
            "response_text": _RESPONSE_TEXT,
            "conversation_id": conv_id,
            "user_query": "What is the speed of light?",
        },
    )
    assert r.status_code in (200, 202), (
        f"/agent/hallucination HTTP {r.status_code}: {r.text[:300]}"
    )
    return r.json()


def _save_verification(http_client, conv_id: str, check_result: dict) -> dict:
    r = http_client.post(
        "/verification/save",
        json={
            "conversation_id": conv_id,
            "claims": check_result.get("claims", []),
            "overall_score": check_result.get("overall_score", 1.0),
            "verified": check_result.get("verified", 0),
            "unverified": check_result.get("unverified", 0),
            "uncertain": check_result.get("uncertain", 0),
            "total": check_result.get(
                "total", len(check_result.get("claims", []))
            ),
        },
    )
    assert r.status_code in (200, 202), (
        f"/verification/save HTTP {r.status_code}: {r.text[:300]}"
    )
    return r.json()


def _poll_for_report(neo4j_driver, conv_id: str, timeout_s: float = 10.0) -> dict:
    """Returns {node_found, edges, urls, methods} or raises TimeoutError."""
    deadline = time.monotonic() + timeout_s
    with neo4j_driver.session() as s:
        while time.monotonic() < deadline:
            row = s.run(
                """
                MATCH (v:VerificationReport {conversation_id: $cid})
                OPTIONAL MATCH (v)-[:VERIFIED]->(a)
                RETURN
                    count(DISTINCT v) AS n,
                    count(a) AS edges,
                    coalesce(size(v.source_urls), 0) AS urls,
                    coalesce(size(v.verification_methods), 0) AS methods
                """,
                cid=conv_id,
            ).single()
            if row and row["n"] > 0:
                return {
                    "node_found": True,
                    "edges": int(row["edges"] or 0),
                    "urls": int(row["urls"] or 0),
                    "methods": int(row["methods"] or 0),
                }
            time.sleep(0.5)
    return {"node_found": False, "edges": 0, "urls": 0, "methods": 0}


def test_verification_round_trip_returns_200(http_client):
    conv_id = str(uuid.uuid4())
    check = _hallucination_check(http_client, conv_id)
    assert "claims" in check, f"/agent/hallucination missing claims: {check}"

    save = _save_verification(http_client, conv_id, check)
    assert save.get("status") == "saved", (
        f"/verification/save status!=saved: {save}"
    )
    assert "report_id" in save, f"/verification/save missing report_id: {save}"


def test_verification_lands_with_provenance(http_client, neo4j_driver):
    conv_id = str(uuid.uuid4())
    check = _hallucination_check(http_client, conv_id)
    _save_verification(http_client, conv_id, check)

    state = _poll_for_report(neo4j_driver, conv_id, timeout_s=10.0)
    assert state["node_found"], (
        f"VerificationReport not found for conversation_id={conv_id} "
        "within 10s of /verification/save return"
    )
    # Writer contract: one of three provenance channels MUST be populated.
    has_provenance = (
        state["edges"] > 0 or state["urls"] > 0 or state["methods"] > 0
    )
    assert has_provenance, (
        f"VerificationReport for {conv_id} has ZERO provenance: "
        f"edges={state['edges']}, urls={state['urls']}, methods={state['methods']}. "
        "This is the exact pre-v0.84.1 orphan bug (P1.4 in "
        "tasks/2026-04-19-consolidation-program.md)."
    )


def test_single_call_auto_persist_leaves_provenanced_report(http_client, neo4j_driver):
    """Sprint C invariant: /agent/hallucination with default persist=True
    is enough on its own — no follow-up /verification/save required.

    Before Sprint C, this test would have failed because the endpoint
    returned claim verdicts but never wrote a full :VerificationReport.
    The FE had to call /verification/save separately. Auto-persist
    collapses that into one call."""
    conv_id = str(uuid.uuid4())
    check = _hallucination_check(http_client, conv_id)
    assert check.get("persisted") is True, (
        f"auto-persist expected; got persisted={check.get('persisted')} — "
        f"skipped={check.get('skipped')}, claims={len(check.get('claims', []))}"
    )
    # No /verification/save call. The report should already be
    # provenanced thanks to the endpoint's inline save.
    state = _poll_for_report(neo4j_driver, conv_id, timeout_s=10.0)
    assert state["node_found"], (
        f"auto-persisted VerificationReport missing for {conv_id}"
    )
    has_provenance = (
        state["edges"] > 0 or state["urls"] > 0 or state["methods"] > 0
    )
    assert has_provenance, (
        f"auto-persist landed a report with ZERO provenance: {state}"
    )


def test_opt_out_persist_false_returns_claims_without_writing(http_client, neo4j_driver):
    """External SDK consumers can set persist=False to manage their
    own storage. The endpoint must still return claim verdicts, and
    MUST NOT create a :VerificationReport for that conversation_id."""
    conv_id = str(uuid.uuid4())
    r = http_client.post(
        "/agent/hallucination",
        json={
            "response_text": _RESPONSE_TEXT,
            "conversation_id": conv_id,
            "user_query": "What is the speed of light?",
            "persist": False,
        },
    )
    assert r.status_code in (200, 202), f"HTTP {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert body.get("persisted") is False
    assert "claims" in body
    # No report should land for this conversation id.
    with neo4j_driver.session() as s:
        row = s.run(
            "MATCH (v:VerificationReport {conversation_id: $cid}) RETURN count(v) AS n",
            cid=conv_id,
        ).single()
        assert row["n"] == 0, (
            f"persist=False still wrote a :VerificationReport for {conv_id}"
        )


def test_verification_does_not_create_new_orphans(http_client, neo4j_driver):
    """Every round-trip must leave verification_report_orphans at 0.

    Counts orphans before + after the round-trip. Delta must be 0 —
    anything else means the writer regressed."""
    r = http_client.get("/health")
    before = r.json()["invariants"]["verification_report_orphans"]
    assert before == 0, (
        f"Preconditions dirty: {before} orphans already exist before test"
    )

    conv_id = str(uuid.uuid4())
    check = _hallucination_check(http_client, conv_id)
    _save_verification(http_client, conv_id, check)
    # Give the write a moment to settle.
    time.sleep(0.5)

    r = http_client.get("/health")
    after = r.json()["invariants"]["verification_report_orphans"]
    assert after == 0, (
        f"Round-trip leaked {after - before} orphan(s). Investigate "
        "save_verification_report claim-shape handling."
    )
