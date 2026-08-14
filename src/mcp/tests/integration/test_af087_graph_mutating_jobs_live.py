# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""AF-087 — real Cypher-executing coverage for the graph-mutating jobs and
the privacy wipe.

The unit suites for these modules (``tests/test_community_detection.py``,
``tests/test_config_recommender_job.py``, ``tests/test_derive_domains.py``,
``tests/test_entity_merge.py``, ``tests/test_session_wipe.py``) exercise
every Cypher statement against fakes that either discard the query string
outright (``_FakeNeo4jSession.run`` in the config-recommender tests) or
dispatch on a hand-picked substring (the entity-merge / session-wipe
fakes) or assert on ``inspect.getsource`` text (the community-detection
Cypher-shape tests). All three shapes prove the Python call sequence is
right and say nothing about whether the Cypher itself is syntactically
valid Neo4j or does what its comment claims — Neo4j never parses a single
one of those queries. This module drives the same production code paths
against a real, Cypher-executing Neo4j instance (see
``tests/integration/conftest.py::neo4j_driver``).

Every test below is scoped to synthetic, uniquely-prefixed nodes it
creates and tears down itself in a ``finally`` block — EXCEPT
``TestCommunityDetectionLive``, which is opt-in (see its docstring):
``detect_communities`` has no per-call scoping mechanism and this stack
runs Neo4j Community Edition (verified via ``dbms.components()``), which
has no per-test database isolation, so running it unconditionally would
re-cluster the *entire* live Entity graph as a side effect of an ordinary
``pytest -m preservation`` run.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from .conftest import record_preservation_skip

pytestmark = pytest.mark.preservation


def _uid(label: str) -> str:
    return f"af087:{label}:{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# wipe_conversation_state — app/services/session_wipe.py
# ---------------------------------------------------------------------------


class TestSessionWipeLive:
    def test_wipe_deletes_real_conversation_report_and_memory_nodes(
        self, neo4j_driver, monkeypatch,
    ) -> None:
        from app.services.session_wipe import wipe_conversation_state

        conv_id = _uid("conv")
        mem_id = _uid("mem")

        # Isolate the Chroma side effect — AF-087 is about the Neo4j Cypher
        # (session_wipe._delete_conversation_node / _delete_verification_report_node
        # / _find_verified_memory_ids / _delete_verified_memory's DETACH DELETE),
        # not the Chroma companion-doc delete, which is covered elsewhere.
        fake_collection = MagicMock()
        fake_chroma = MagicMock()
        fake_chroma.get_or_create_collection.return_value = fake_collection
        monkeypatch.setattr("app.deps.get_chroma", lambda: fake_chroma)

        with neo4j_driver.session() as s:
            s.run(
                "CREATE (:Conversation {id: $cid}) "
                "CREATE (r:VerificationReport {conversation_id: $cid}) "
                "CREATE (m:Memory {id: $mid})-[:VERIFIED_BY]->(r)",
                cid=conv_id, mid=mem_id,
            )

        try:
            summary = wipe_conversation_state(
                conv_id, sync_dir=None, neo4j_driver=neo4j_driver,
            )

            assert summary["conversation_node_deleted"] is True
            assert summary["verification_report_deleted"] is True
            assert summary["verified_memories_deleted"] == 1
            assert summary["verified_memories_failed"] == 0
            fake_collection.delete.assert_called_once_with(ids=[f"verified_memory_{mem_id}"])

            with neo4j_driver.session() as s:
                conv_row = s.run(
                    "OPTIONAL MATCH (c:Conversation {id: $cid}) RETURN c", cid=conv_id,
                ).single()
                assert conv_row["c"] is None, "Conversation node must be gone"

                report_row = s.run(
                    "OPTIONAL MATCH (r:VerificationReport {conversation_id: $cid}) RETURN r",
                    cid=conv_id,
                ).single()
                assert report_row["r"] is None, "VerificationReport node must be gone"

                mem_row = s.run(
                    "OPTIONAL MATCH (m:Memory {id: $mid}) RETURN m", mid=mem_id,
                ).single()
                assert mem_row["m"] is None, "Memory node must be gone"
        finally:
            with neo4j_driver.session() as s:
                s.run(
                    "MATCH (n) WHERE n.id = $cid OR n.conversation_id = $cid OR n.id = $mid "
                    "DETACH DELETE n",
                    cid=conv_id, mid=mem_id,
                )


# ---------------------------------------------------------------------------
# config_recommender._corpus_size — app/processor/jobs/config_recommender.py
# ---------------------------------------------------------------------------


class TestConfigRecommenderLive:
    def test_corpus_size_counts_real_artifact_and_excludes_eval_corpus(
        self, neo4j_driver,
    ) -> None:
        from app.processor.jobs.config_recommender import _corpus_size

        art_id = _uid("artifact")
        eval_id = _uid("eval-artifact")

        before = _corpus_size(neo4j_driver)
        try:
            with neo4j_driver.session() as s:
                s.run(
                    "CREATE (:Artifact {id: $aid, sub_category: 'general'}) "
                    "CREATE (:Artifact {id: $eid, sub_category: 'eval-corpus'})",
                    aid=art_id, eid=eval_id,
                )

            after = _corpus_size(neo4j_driver)
            # Only the non-eval-corpus artifact should count — proves the real
            # WHERE coalesce(a.sub_category, '') <> 'eval-corpus' clause parses
            # and filters, not just that _corpus_size returns *some* int.
            assert after == before + 1, (
                f"expected corpus_size to grow by exactly 1 (eval-corpus excluded); "
                f"before={before} after={after}"
            )
        finally:
            with neo4j_driver.session() as s:
                s.run(
                    "MATCH (a:Artifact) WHERE a.id IN [$aid, $eid] DETACH DELETE a",
                    aid=art_id, eid=eval_id,
                )


# ---------------------------------------------------------------------------
# DeriveDomainsJob._write_updates / _write_orphan_removes —
# app/processor/jobs/derive_domains.py
# ---------------------------------------------------------------------------


class TestDeriveDomainsLive:
    def test_write_updates_then_orphan_remove_round_trip(self, neo4j_driver) -> None:
        from app.processor.jobs.derive_domains import DeriveDomainsJob, _fold_distributions

        cid = _uid("entity")
        job = DeriveDomainsJob()

        with neo4j_driver.session() as s:
            s.run(
                "CREATE (:Entity {canonical_id: $cid, name: 'AF-087 Derive Domains', "
                "entity_type: 'TOPIC', mention_count: 3})",
                cid=cid,
            )

        try:
            mention_rows = [
                {"cid": cid, "domain": "coding", "sub": "general", "n": 3,
                 "latest": "2026-06-01", "qsum": 2.0},
            ]
            now = datetime(2026, 6, 13, tzinfo=timezone.utc)
            update_rows, _orphans = _fold_distributions(mention_rows, {cid}, now)
            assert update_rows and update_rows[0]["cid"] == cid

            written = job._write_updates(neo4j_driver, update_rows)
            assert written == 1

            with neo4j_driver.session() as s:
                row = s.run(
                    "MATCH (e:Entity {canonical_id: $cid}) "
                    "RETURN e.primary_domain AS primary_domain, "
                    "       e.domain_mix AS domain_mix, "
                    "       e.domain_salience AS domain_salience",
                    cid=cid,
                ).single()
            assert row["primary_domain"] == "coding"
            assert json.loads(row["domain_mix"]) == {"coding": 3}
            assert "coding" in json.loads(row["domain_salience"])

            removed = job._write_orphan_removes(neo4j_driver, [cid])
            assert removed == 1

            with neo4j_driver.session() as s:
                row2 = s.run(
                    "MATCH (e:Entity {canonical_id: $cid}) "
                    "RETURN e.primary_domain AS primary_domain",
                    cid=cid,
                ).single()
            assert row2["primary_domain"] is None, (
                "primary_domain must be removed by the orphan-remove Cypher"
            )
        finally:
            with neo4j_driver.session() as s:
                s.run("MATCH (e:Entity {canonical_id: $cid}) DETACH DELETE e", cid=cid)


# ---------------------------------------------------------------------------
# merge_entities / unmerge_entity — app/db/neo4j/entity.py
# ---------------------------------------------------------------------------


class TestEntityMergeLive:
    def test_merge_then_unmerge_round_trip_against_real_neo4j(self, neo4j_driver) -> None:
        from app.db.neo4j.entity import merge_entities, unmerge_entity

        survivor_cid = _uid("survivor")
        loser_cid = _uid("loser")
        art_id = _uid("artifact")

        with neo4j_driver.session() as s:
            s.run(
                "CREATE (:Entity {canonical_id: $sid, name: 'Survivor', "
                "entity_type: 'PERSON', mention_count: 2}) "
                "CREATE (loser:Entity {canonical_id: $lid, name: 'Loser', "
                "entity_type: 'PERSON', mention_count: 3}) "
                "CREATE (a:Artifact {id: $aid}) "
                "CREATE (a)-[:MENTIONS {confidence: 0.9, chunk_ids: ['c1'], "
                "created_at: '2026-01-01'}]->(loser)",
                sid=survivor_cid, lid=loser_cid, aid=art_id,
            )

        try:
            result = merge_entities(
                neo4j_driver, survivor_cid, [loser_cid],
                survivor_name="Survivor", entity_type="PERSON",
            )
            assert result["merged"] == [loser_cid]

            with neo4j_driver.session() as s:
                loser_row = s.run(
                    "OPTIONAL MATCH (e:Entity {canonical_id: $lid}) RETURN e", lid=loser_cid,
                ).single()
                assert loser_row["e"] is None, "loser must be gone after merge"

                surv_row = s.run(
                    "MATCH (e:Entity {canonical_id: $sid}) RETURN e.mention_count AS mc",
                    sid=survivor_cid,
                ).single()
                assert surv_row["mc"] == 5, "mention_count must be summed (2 + 3)"

                mention_row = s.run(
                    "MATCH (a:Artifact {id: $aid})-[:MENTIONS]->(e:Entity) "
                    "RETURN e.canonical_id AS cid",
                    aid=art_id,
                ).single()
                assert mention_row["cid"] == survivor_cid, (
                    "artifact's MENTIONS edge must be re-pointed to the survivor"
                )

                tomb_row = s.run(
                    "MATCH (t:MergedEntity {canonical_id: $lid})-[:MERGED_INTO]->"
                    "(s:Entity {canonical_id: $sid}) RETURN t",
                    lid=loser_cid, sid=survivor_cid,
                ).single()
                assert tomb_row is not None, "tombstone + MERGED_INTO edge must be written"

            unmerge_result = unmerge_entity(neo4j_driver, loser_cid)
            assert unmerge_result["status"] == "restored"

            with neo4j_driver.session() as s:
                restored = s.run(
                    "MATCH (e:Entity {canonical_id: $lid}) RETURN e.mention_count AS mc",
                    lid=loser_cid,
                ).single()
                assert restored is not None, "loser must be restored"
                assert restored["mc"] == 3

                surv_after = s.run(
                    "MATCH (e:Entity {canonical_id: $sid}) RETURN e.mention_count AS mc",
                    sid=survivor_cid,
                ).single()
                assert surv_after["mc"] == 2, "survivor mention_count must be decremented back"

                mention_after = s.run(
                    "MATCH (a:Artifact {id: $aid})-[:MENTIONS]->(e:Entity) "
                    "RETURN e.canonical_id AS cid",
                    aid=art_id,
                ).data()
                # unmerge_entity restores the loser's MENTIONS from the
                # tombstone snapshot but does not retract the survivor's
                # merge-time re-point (see its docstring: only identity +
                # MENTIONS are reversed) — so both edges coexist afterward.
                # The contract this test proves is the restoration itself.
                assert loser_cid in {r["cid"] for r in mention_after}, (
                    "MENTIONS edge must be restored onto the loser"
                )

                tomb_after = s.run(
                    "OPTIONAL MATCH (t:MergedEntity {canonical_id: $lid}) RETURN t",
                    lid=loser_cid,
                ).single()
                assert tomb_after["t"] is None, "tombstone must be consumed by unmerge"
        finally:
            with neo4j_driver.session() as s:
                s.run(
                    "MATCH (n) WHERE n.canonical_id IN [$sid, $lid] OR n.id = $aid "
                    "DETACH DELETE n",
                    sid=survivor_cid, lid=loser_cid, aid=art_id,
                )


# ---------------------------------------------------------------------------
# detect_communities — app/db/neo4j/community_detection.py
#
# Opt-in only. Unlike every other test in this module, detect_communities
# cannot be scoped to synthetic nodes: it always operates on `MATCH
# (e:Entity)` — every entity in the graph — and this stack runs Neo4j
# Community Edition (confirmed via `CALL dbms.components()`), which has no
# per-database test isolation. Running it unconditionally here would
# re-cluster the ENTIRE live Entity graph's Community/IN_COMMUNITY
# structure as a side effect of an ordinary `pytest -m preservation` run —
# the same full-corpus work the nightly scheduler already performs, just
# triggered synchronously and unexpectedly from a test suite. Set
# CERID_RUN_FULL_GRAPH_TESTS=1 to opt in.
# ---------------------------------------------------------------------------

_RUN_FULL_GRAPH_TESTS = os.environ.get("CERID_RUN_FULL_GRAPH_TESTS", "") == "1"


class TestCommunityDetectionLive:
    def test_detect_communities_against_real_neo4j_and_gds(self, neo4j_driver, request) -> None:
        if not _RUN_FULL_GRAPH_TESTS:
            record_preservation_skip(
                request, "AF-087",
                "detect_communities re-clusters the ENTIRE live Entity graph "
                "(no per-call scoping, no Neo4j Community-Edition database "
                "isolation) — set CERID_RUN_FULL_GRAPH_TESTS=1 to opt in.",
            )

        from app.db.neo4j.community_detection import detect_communities

        e1 = _uid("comm-e1")
        e2 = _uid("comm-e2")
        e3_isolated = _uid("comm-e3-isolated")
        art_id = _uid("comm-artifact")

        with neo4j_driver.session() as s:
            s.run(
                "CREATE (a:Artifact {id: $aid}) "
                "CREATE (e1:Entity {canonical_id: $e1, name: 'E1', "
                "entity_type: 'TOPIC', mention_count: 1}) "
                "CREATE (e2:Entity {canonical_id: $e2, name: 'E2', "
                "entity_type: 'TOPIC', mention_count: 1}) "
                "CREATE (:Entity {canonical_id: $e3, name: 'E3', "
                "entity_type: 'TOPIC', mention_count: 1}) "
                "CREATE (a)-[:MENTIONS]->(e1) "
                "CREATE (a)-[:MENTIONS]->(e2)",
                aid=art_id, e1=e1, e2=e2, e3=e3_isolated,
            )

        try:
            stats = detect_communities(neo4j_driver, min_community_size=1)
            assert "skipped" not in stats, f"detect_communities unexpectedly skipped: {stats}"

            with neo4j_driver.session() as s:
                rows = s.run(
                    "MATCH (e:Entity) WHERE e.canonical_id IN [$e1, $e2] "
                    "RETURN e.canonical_id AS cid, e.community_id AS community_id",
                    e1=e1, e2=e2,
                ).data()
            by_cid = {r["cid"]: r["community_id"] for r in rows}
            assert by_cid[e1] is not None and by_cid[e1].startswith("0:"), (
                f"co-mentioned entity must get a real Leiden community_id, got {by_cid[e1]!r}"
            )
            assert by_cid[e1] == by_cid[e2], "co-mentioned entities must land in the same community"

            with neo4j_driver.session() as s:
                iso_row = s.run(
                    "MATCH (e:Entity {canonical_id: $eid}) RETURN e.community_id AS community_id",
                    eid=e3_isolated,
                ).single()
            assert iso_row["community_id"] == "isolated", (
                "an entity with zero MENTIONS must get the 'isolated' sentinel"
            )
        finally:
            with neo4j_driver.session() as s:
                s.run(
                    "MATCH (n) WHERE n.canonical_id IN [$e1, $e2, $e3] OR n.id = $aid "
                    "DETACH DELETE n",
                    e1=e1, e2=e2, e3=e3_isolated, aid=art_id,
                )
