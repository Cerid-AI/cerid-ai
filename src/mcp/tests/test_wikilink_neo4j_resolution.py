# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""End-to-end tests for wikilink edge writes + PendingArtifact resolution
(RAG Cycle C2.1 Phase C).

These tests need a live Neo4j to exercise the MERGE semantics, the
PendingArtifact lifecycle, and the resolve-on-ingest re-pointing.  They
auto-skip when Neo4j is unreachable (no ``NEO4J_PASSWORD``, no docker
stack, etc.) so the unit-test pass on a developer laptop stays clean.

CI's preservation job + ``docker-compose.ci.yml`` boots Neo4j, at which
point these tests assert the contract.
"""
from __future__ import annotations

import logging
import os
import uuid

import pytest

from app.db.neo4j.artifacts import create_artifact, delete_artifact
from app.db.neo4j.schema import init_schema
from app.db.neo4j.wikilinks import (
    resolve_pending_artifacts,
    write_wikilink_edge,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Live-Neo4j fixture (skip cleanly when stack unavailable)
# ---------------------------------------------------------------------------

NEO4J_URI_DEFAULT = "bolt://ai-companion-neo4j:7687"


@pytest.fixture(scope="module")
def neo4j_driver():
    """Real Neo4j driver. Skips when the database isn't reachable."""
    try:
        from neo4j import GraphDatabase
    except ImportError:
        pytest.skip("neo4j driver not installed")

    in_docker = os.path.exists("/.dockerenv")
    uri = os.environ.get("NEO4J_URI", NEO4J_URI_DEFAULT)
    if not in_docker and uri == NEO4J_URI_DEFAULT:
        uri = "bolt://127.0.0.1:7687"

    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    if not password:
        pytest.skip("NEO4J_PASSWORD not set — live Neo4j assertions unavailable")

    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session() as s:
            s.run("RETURN 1").single()
    except Exception as exc:  # noqa: BLE001 — skip path
        pytest.skip(f"neo4j unreachable ({exc})")

    # Make sure the PendingArtifact constraint exists.
    init_schema(driver)
    yield driver
    driver.close()


def _cleanup(driver, *, artifact_ids: list[str], pending_names: list[str]) -> None:
    """Best-effort teardown — removes nodes/edges created by a test."""
    with driver.session() as session:
        for aid in artifact_ids:
            try:
                delete_artifact(driver, aid)
            except Exception:  # noqa: BLE001 — best-effort teardown; ignore missing nodes
                logger.exception("test cleanup: delete_artifact(%s) skipped", aid)
        for name in pending_names:
            session.run(
                "MATCH (p:PendingArtifact {name: $name}) DETACH DELETE p",
                name=name,
            )


def _count_edges(driver, source_id: str, rel_type: str) -> int:
    with driver.session() as session:
        record = session.run(
            f"MATCH (s:Artifact {{id: $sid}})-[r:{rel_type}]->() "
            "RETURN count(r) AS n",
            sid=source_id,
        ).single()
        return int(record["n"]) if record else 0


def _get_pending(driver, name: str) -> dict | None:
    with driver.session() as session:
        record = session.run(
            "MATCH (p:PendingArtifact {name: $name}) "
            "RETURN p.name AS name, p.created_at AS created_at",
            name=name,
        ).single()
        if not record:
            return None
        return {"name": record["name"], "created_at": record["created_at"]}


def _get_inbound_edge(driver, source_id: str, target_id: str, rel_type: str) -> dict | None:
    """Return the relationship between two real Artifacts, if any."""
    with driver.session() as session:
        record = session.run(
            f"MATCH (s:Artifact {{id: $sid}})-[r:{rel_type}]->(t:Artifact {{id: $tid}}) "
            "RETURN r.pending AS pending, r.source_chunk_id AS chunk_id, "
            "       r.alias AS alias, r.heading AS heading",
            sid=source_id,
            tid=target_id,
        ).single()
        if not record:
            return None
        return {
            "pending": record["pending"],
            "chunk_id": record["chunk_id"],
            "alias": record["alias"],
            "heading": record["heading"],
        }


def _make_artifact(driver, *, filename: str) -> str:
    artifact_id = f"test-{uuid.uuid4().hex}"
    create_artifact(
        driver=driver,
        artifact_id=artifact_id,
        filename=filename,
        domain="coding",
        keywords_json="[]",
        summary="",
        chunk_count=1,
        chunk_ids_json='["chunk_0"]',
        content_hash=artifact_id,
    )
    return artifact_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBrokenWikilink:
    def test_creates_pending_artifact(self, neo4j_driver):
        """Wikilink to an unknown target creates a PendingArtifact with
        a pending=true edge."""
        src_id = _make_artifact(neo4j_driver, filename="src1.md")
        try:
            write_wikilink_edge(
                neo4j_driver,
                source_artifact_id=src_id,
                target_name="MissingTarget",
                is_embed=False,
                source_chunk_id=f"{src_id}_chunk_0",
                alias="MissingTarget",
                heading="",
            )
            pending = _get_pending(neo4j_driver, "MissingTarget")
            assert pending is not None
            assert pending["name"] == "MissingTarget"

            # Edge should exist with pending=true
            with neo4j_driver.session() as session:
                record = session.run(
                    "MATCH (s:Artifact {id: $sid})-[r:WIKILINKS_TO]->(p:PendingArtifact) "
                    "RETURN r.pending AS pending, p.name AS name",
                    sid=src_id,
                ).single()
            assert record is not None
            assert record["pending"] is True
            assert record["name"] == "MissingTarget"
        finally:
            _cleanup(
                neo4j_driver,
                artifact_ids=[src_id],
                pending_names=["MissingTarget"],
            )


class TestResolveOnLaterIngest:
    def test_pending_artifact_promoted_when_target_lands(self, neo4j_driver):
        src_id = _make_artifact(neo4j_driver, filename="src2.md")
        target_id = None
        try:
            # 1. Source links to a not-yet-ingested ``Foo``
            write_wikilink_edge(
                neo4j_driver,
                source_artifact_id=src_id,
                target_name="Foo",
                is_embed=False,
                source_chunk_id=f"{src_id}_chunk_0",
                alias="Foo",
            )
            assert _get_pending(neo4j_driver, "Foo") is not None

            # 2. ``Foo.md`` is ingested
            target_id = _make_artifact(neo4j_driver, filename="Foo.md")
            promoted = resolve_pending_artifacts(
                neo4j_driver,
                artifact_id=target_id,
                filename="Foo.md",
            )
            assert promoted >= 1

            # 3. PendingArtifact gone
            assert _get_pending(neo4j_driver, "Foo") is None

            # 4. Edge now points to the real artifact, pending=false
            edge = _get_inbound_edge(neo4j_driver, src_id, target_id, "WIKILINKS_TO")
            assert edge is not None
            assert edge["pending"] is False
            assert edge["chunk_id"] == f"{src_id}_chunk_0"
        finally:
            ids = [src_id]
            if target_id:
                ids.append(target_id)
            _cleanup(neo4j_driver, artifact_ids=ids, pending_names=["Foo"])

    def test_two_sources_share_pending_target_both_promoted(self, neo4j_driver):
        src_a = _make_artifact(neo4j_driver, filename="srcA.md")
        src_b = _make_artifact(neo4j_driver, filename="srcB.md")
        target_id = None
        try:
            write_wikilink_edge(
                neo4j_driver,
                source_artifact_id=src_a,
                target_name="Shared",
                is_embed=False,
                source_chunk_id=f"{src_a}_chunk_0",
            )
            write_wikilink_edge(
                neo4j_driver,
                source_artifact_id=src_b,
                target_name="Shared",
                is_embed=False,
                source_chunk_id=f"{src_b}_chunk_0",
            )
            assert _get_pending(neo4j_driver, "Shared") is not None

            target_id = _make_artifact(neo4j_driver, filename="Shared.md")
            resolve_pending_artifacts(
                neo4j_driver,
                artifact_id=target_id,
                filename="Shared.md",
            )

            # Both sources should now have real edges, no pending node left.
            assert _get_pending(neo4j_driver, "Shared") is None
            edge_a = _get_inbound_edge(neo4j_driver, src_a, target_id, "WIKILINKS_TO")
            edge_b = _get_inbound_edge(neo4j_driver, src_b, target_id, "WIKILINKS_TO")
            assert edge_a is not None
            assert edge_b is not None
            assert edge_a["pending"] is False
            assert edge_b["pending"] is False
        finally:
            ids = [src_a, src_b]
            if target_id:
                ids.append(target_id)
            _cleanup(neo4j_driver, artifact_ids=ids, pending_names=["Shared"])


class TestResolvedOnFirstIngest:
    def test_existing_target_creates_direct_edge_no_pending(self, neo4j_driver):
        """When the target artifact already exists, no PendingArtifact
        should be created and the edge should be pending=false from the
        start."""
        target_id = _make_artifact(neo4j_driver, filename="Already.md")
        src_id = _make_artifact(neo4j_driver, filename="srcC.md")
        try:
            write_wikilink_edge(
                neo4j_driver,
                source_artifact_id=src_id,
                target_name="Already",
                is_embed=False,
                source_chunk_id=f"{src_id}_chunk_0",
                alias="Already",
            )
            # No pending placeholder
            assert _get_pending(neo4j_driver, "Already") is None
            # Direct edge with pending=false
            edge = _get_inbound_edge(neo4j_driver, src_id, target_id, "WIKILINKS_TO")
            assert edge is not None
            assert edge["pending"] is False
        finally:
            _cleanup(
                neo4j_driver,
                artifact_ids=[src_id, target_id],
                pending_names=["Already"],
            )


class TestEmbedRelationship:
    def test_embed_writes_embeds_edge_type(self, neo4j_driver):
        """``![[…]]`` writes an ``EMBEDS`` edge, not ``WIKILINKS_TO``."""
        target_id = _make_artifact(neo4j_driver, filename="diagram.md")
        src_id = _make_artifact(neo4j_driver, filename="srcD.md")
        try:
            write_wikilink_edge(
                neo4j_driver,
                source_artifact_id=src_id,
                target_name="diagram",
                is_embed=True,
                source_chunk_id=f"{src_id}_chunk_0",
            )
            assert _count_edges(neo4j_driver, src_id, "EMBEDS") == 1
            assert _count_edges(neo4j_driver, src_id, "WIKILINKS_TO") == 0
            edge = _get_inbound_edge(neo4j_driver, src_id, target_id, "EMBEDS")
            assert edge is not None
            assert edge["pending"] is False
        finally:
            _cleanup(
                neo4j_driver,
                artifact_ids=[src_id, target_id],
                pending_names=["diagram"],
            )
