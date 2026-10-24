# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""S11-3 (audit F19) against a real Neo4j.

Verification cites recalled memories by their ``:Memory`` id as well as
documents by their ``:Artifact`` id. The writer linked only artifacts, so 374
of 507 cited sources on the Studio graph got no edge, and a report whose edge
did not land said nothing about why.

These need Neo4j, not the rest of the stack, so they open their own driver
from ``NEO4J_URI``/``NEO4J_PASSWORD`` rather than ``graph_driver``, which is
gated on the MCP server answering. The first test runs the linker inside a
transaction that is rolled back, so it writes nothing to whatever graph it
reaches. The second runs the boot migration, which rewrites every report in
the graph, so it is opt-in.
"""
from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from app.db.neo4j.artifacts import link_cited_sources

from .conftest import NEO4J_URI_DEFAULT, record_preservation_skip

_RUN_FULL_GRAPH_TESTS = os.environ.get("CERID_RUN_FULL_GRAPH_TESTS", "") == "1"


@pytest.fixture(scope="module")
def graph_driver(request: pytest.FixtureRequest) -> Iterator[Any]:
    from neo4j import GraphDatabase

    uri = os.environ.get("NEO4J_URI", NEO4J_URI_DEFAULT)
    if not os.path.exists("/.dockerenv") and uri == NEO4J_URI_DEFAULT:
        uri = "bolt://127.0.0.1:7687"
    password = os.environ.get("NEO4J_PASSWORD", "")
    if not password:
        record_preservation_skip(request, "S11-3", "missing NEO4J_PASSWORD — S11-3 graph checks need Neo4j")
    driver = GraphDatabase.driver(uri, auth=(os.environ.get("NEO4J_USER", "neo4j"), password))
    try:
        driver.verify_connectivity()
    except Exception as exc:  # noqa: BLE001 — reported as a recorded skip
        driver.close()
        record_preservation_skip(request, "S11-3", f"neo4j unreachable at {uri} ({exc})")
    yield driver
    driver.close()


def test_a_report_links_live_sources_and_says_why_the_others_did_not(graph_driver):
    memory_id, artifact_id, gone_id, forgotten_id = (str(uuid.uuid4()) for _ in range(4))
    with graph_driver.session() as session:
        tx = session.begin_transaction()
        try:
            eid = tx.run(
                "CREATE (r:VerificationReport {conversation_id: $cid, message_id: ''}) "
                "CREATE (:Memory {id: $memory_id}) CREATE (:Artifact {id: $artifact_id}) "
                "CREATE (r)-[:VERIFIED]->(f:Memory {id: $forgotten_id}) CREATE (r)-[:EXTRACTED_FROM]->(f) "
                "RETURN elementId(r) AS eid",
                cid=f"f19-{uuid.uuid4()}", memory_id=memory_id, artifact_id=artifact_id, forgotten_id=forgotten_id,
            ).single()["eid"]
            link_cited_sources(tx, eid, {memory_id, artifact_id, gone_id, forgotten_id}, frozenset({forgotten_id}))
            row = tx.run(
                "MATCH (r:VerificationReport) WHERE elementId(r) = $eid "
                "OPTIONAL MATCH (r)-[:VERIFIED]->(s) RETURN r.edge_status AS status, collect(s.id) AS linked",
                eid=eid,
            ).single()
        finally:
            tx.rollback()

    assert json.loads(row["status"]) == {
        memory_id: "linked", artifact_id: "linked", gone_id: "missing", forgotten_id: "unverifiable",
    }
    assert sorted(row["linked"]) == sorted([memory_id, artifact_id])


def _report_audit(driver) -> dict[str, int]:
    """Every report against S11-3: each cited source has an edge and reads
    ``linked``, or has no edge and a reason."""
    from core.agents.hallucination.models import ClaimVerification

    audit = {"reports": 0, "without_edge_status": 0, "sources_linked": 0, "sources_with_reason": 0,
             "sources_with_neither": 0}
    with driver.session() as session:
        rows = session.run(
            "MATCH (r:VerificationReport) OPTIONAL MATCH (r)-[:VERIFIED]->(s) WHERE s:Artifact OR s:Memory "
            "RETURN r.claims AS claims, r.edge_status AS status, collect(s.id) AS linked"
        ).data()
    for row in rows:
        audit["reports"] += 1
        status = json.loads(row["status"]) if row["status"] else None
        if status is None:
            audit["without_edge_status"] += 1
            status = {}
        cited = {aid for raw in json.loads(row["claims"] or "[]")
                 for aid in ClaimVerification.from_legacy_dict(raw).artifact_ids()}
        for aid in cited:
            if aid in row["linked"] and status.get(aid) == "linked":
                audit["sources_linked"] += 1
            elif aid not in row["linked"] and status.get(aid) in {"missing", "unverifiable", "failed"}:
                audit["sources_with_reason"] += 1
            else:
                audit["sources_with_neither"] += 1
    return audit


def test_the_boot_migration_leaves_every_report_with_an_edge_or_a_reason(graph_driver, request, tmp_path, monkeypatch):
    if not _RUN_FULL_GRAPH_TESTS:
        record_preservation_skip(
            request, "S11-3",
            "the boot report migration rewrites edges and edge_status on EVERY report in the graph; "
            "run it only against a disposable Neo4j, with CERID_RUN_FULL_GRAPH_TESTS=1.",
        )

    from app.services import verification_reports
    from core.forget import registry as forget_registry

    tag = f"f19-{uuid.uuid4().hex[:8]}"
    ids = {k: f"{tag}-{k}" for k in ("artifact", "memory", "missing", "trashed", "purged")}

    # The forget registry as a forget leaves it: the trashed memory keeps its
    # node (status forgotten), the purged one has none.
    registry = forget_registry.Registry(tmp_path / "forget", "f19-test", tmp_path / "snapshot.jsonl")
    registry.append([
        forget_registry.Entry(forget_registry.new_forget_id(), forget_registry.Subject("memory", ids[k]), state,
                              "2026-10-21T00:00:00Z", "f19-test", "user")
        for k, state in (("trashed", "trashed"), ("purged", "purged"))
    ])
    monkeypatch.setattr(forget_registry, "_REGISTRY", registry)
    monkeypatch.setattr("app.deps.get_neo4j", lambda: graph_driver)
    monkeypatch.setattr(verification_reports, "migrate_embedded_reports", lambda: {})

    def claim(source: str) -> dict:
        return {"claim": f"cites {source}", "status": "verified", "verification_method": "kb",
                "source_artifact_id": ids[source]}

    with graph_driver.session() as session:
        session.run(
            "CREATE (:Artifact {id: $artifact, f19: $tag}) "
            "CREATE (:Memory {id: $memory, f19: $tag}) "
            "CREATE (t:Memory {id: $trashed, status: 'forgotten', f19: $tag}) "
            # Written as the boot finds them: never linked to the memories they
            # cite, one from before reports were keyed per message, one still
            # linked to a memory forgotten since.
            "CREATE (:VerificationReport {conversation_id: $tag, message_id: 'a', f19: $tag, claims: $c_artifact}) "
            "CREATE (:VerificationReport {conversation_id: $tag, f19: $tag, claims: $c_memory}) "
            "CREATE (:VerificationReport {conversation_id: $tag, message_id: 'c', f19: $tag, claims: $c_missing}) "
            "CREATE (:VerificationReport {conversation_id: $tag, message_id: 'd', f19: $tag, claims: $c_purged}) "
            "CREATE (r:VerificationReport {conversation_id: $tag, message_id: 'e', f19: $tag, claims: $c_trashed}) "
            "CREATE (r)-[:VERIFIED]->(t) CREATE (r)-[:EXTRACTED_FROM]->(t)",
            tag=tag, **ids, **{f"c_{k}": json.dumps([claim(k)]) for k in ids},
        )
    try:
        before = _report_audit(graph_driver)
        first = verification_reports.migrate_reports()
        after_first = _report_audit(graph_driver)
        second = verification_reports.migrate_reports()
        after_second = _report_audit(graph_driver)
        with graph_driver.session() as session:
            seeded = {
                r["mid"]: (json.loads(r["status"]), sorted(r["linked"]))
                for r in session.run(
                    "MATCH (r:VerificationReport {f19: $tag}) OPTIONAL MATCH (r)-[:VERIFIED]->(s) "
                    "RETURN r.message_id AS mid, r.edge_status AS status, collect(s.id) AS linked",
                    tag=tag,
                )
            }
    finally:
        with graph_driver.session() as session:
            session.run("MATCH (n {f19: $tag}) DETACH DELETE n", tag=tag)

    print(f"\nS11-3 audit before={before}\n  run 1={first} -> {after_first}\n  run 2={second} -> {after_second}")
    assert before["sources_with_neither"] >= 5
    for audit in (after_first, after_second):
        assert audit["without_edge_status"] == 0 and audit["sources_with_neither"] == 0
    assert first["edges_added"] >= 2 and first["edges_removed"] >= 2 and first["reports_keyed"] >= 1
    assert {k: v for k, v in second.items() if k != "reports_scanned"} == {k: 0 for k in second if k != "reports_scanned"}
    assert seeded == {
        "a": ({ids["artifact"]: "linked"}, [ids["artifact"]]),
        "": ({ids["memory"]: "linked"}, [ids["memory"]]),
        "c": ({ids["missing"]: "missing"}, []),
        "d": ({ids["purged"]: "unverifiable"}, []),
        "e": ({ids["trashed"]: "unverifiable"}, []),
    }
