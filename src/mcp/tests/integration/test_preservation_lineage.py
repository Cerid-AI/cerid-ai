# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The lineage writer against Neo4j (forget phase 5, spec §7).

Probe nodes carry unique ids and are removed afterwards. Chroma is the shared
fake, so these exercise the writer's Cypher and its contract with the rows.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from core.lineage import writer
from tests.helpers.fake_chroma import FakeChromaCollection


class _BrokenCollection(FakeChromaCollection):
    def update(self, **_kw: Any) -> None:
        raise RuntimeError("chroma down")


@pytest.fixture
def probe(neo4j_driver):
    tag = f"lineageprobe{uuid.uuid4().hex[:10]}"
    col = FakeChromaCollection("conversations")
    ids: list[str] = []

    def make(name: str, ts: str, *, label: str = "Artifact") -> str:
        nid = f"{tag}-{name}"
        ids.append(nid)
        with neo4j_driver.session() as s:
            s.run(f"CREATE (n:{label} {{id: $id, ingested_at: $ts, created_at: $ts, summary: $name, text: $name}})",
                  id=nid, ts=ts, name=name)
        col.upsert(ids=[f"{nid}_row"], documents=[name], embeddings=[[1.0, 0.0]],
                   metadatas=[{"artifact_id": nid, "memory_type": "preference", "valid_to": ""}])
        return nid

    def node(nid: str) -> dict[str, Any]:
        with neo4j_driver.session() as s:
            rec = s.run("MATCH (n) WHERE n.id = $id RETURN properties(n) AS p", id=nid).single()
        return dict(rec["p"]) if rec else {}

    class _Client:
        def get_or_create_collection(self, **_kw: Any) -> FakeChromaCollection:
            return col

    yield {"make": make, "node": node, "col": col, "chroma": _Client(), "driver": neo4j_driver, "tag": tag}
    with neo4j_driver.session() as s:
        s.run("MATCH (n) WHERE n.id STARTS WITH $tag DETACH DELETE n", tag=tag)
        s.run("MATCH (f:Fact) WHERE f.source_artifact_id STARTS WITH $tag DETACH DELETE f", tag=tag)
        s.run("MATCH (e:Entity) WHERE e.canonical_id STARTS WITH $tag DETACH DELETE e", tag=tag)


def _row(p: dict, nid: str) -> dict[str, Any]:
    return p["col"].metadata_of(f"{nid}_row")


def test_a_chain_grows_one_current_version_at_a_time(probe):
    a = probe["make"]("a", "2026-01-01T00:00:00Z")
    b = probe["make"]("b", "2026-02-01T00:00:00Z")
    c = probe["make"]("c", "2026-03-01T00:00:00Z")
    d, ch = probe["driver"], probe["chroma"]

    assert writer.supersede(d, ch, a, b, valid_to="2026-02-01").ok
    result = writer.supersede(d, ch, b, c, valid_to="2026-03-01")
    assert result.ok and result.lineage_id == a and result.version == 3

    na, nb, nc = probe["node"](a), probe["node"](b), probe["node"](c)
    assert (na["version"], nb["version"], nc["version"]) == (1, 2, 3)
    assert {na["lineage_id"], nb["lineage_id"], nc["lineage_id"]} == {a}
    assert na["superseded_by"] == b and na["valid_to"] == "2026-02-01"
    assert nb["superseded_by"] == c and "valid_to" in nb and "superseded_by" not in nc
    assert _row(probe, b)["superseded_by"] == c and _row(probe, b)["valid_to"] == "2026-03-01"
    assert _row(probe, c)["valid_to"] == "" and _row(probe, c)["version"] == 3

    refused = writer.supersede(d, ch, a, probe["make"]("d", "2026-04-01T00:00:00Z"))
    assert not refused.ok and "already superseded" in refused.reason  # history is never replaced twice
    assert writer.supersede(d, ch, a, b).ok  # the same pair again is a no-op, not an error


def test_every_lineage_change_marks_its_versions_for_the_next_sync(probe):
    """Sync exports what changed since the last run, by ``updated_at``, and the
    newer one wins a conflict; a supersede or a settle must count as a change."""
    a = probe["make"]("a", "2026-01-01T00:00:00Z")
    b = probe["make"]("b", "2026-02-01T00:00:00Z")
    d, ch = probe["driver"], probe["chroma"]
    assert writer.supersede(d, ch, a, b).ok
    first = {probe["node"](a)["updated_at"], probe["node"](b)["updated_at"]}
    assert len(first) == 1 and next(iter(first)) > "2026-02-01"
    with d.session() as session:
        session.run("MATCH (n:Artifact) WHERE n.id IN [$a, $b] SET n.updated_at = '2000-01-01'", a=a, b=b)
    writer.settle_lineage(d, ch, a, forgotten=lambda nid: nid == b)
    assert probe["node"](a)["updated_at"] > "2026"
    assert "valid_to" not in probe["node"](a)  # the settle reopened it, and it will travel


def test_superseding_into_a_version_with_history_merges_the_lineages(probe):
    x0 = probe["make"]("x0", "2026-01-01T00:00:00Z")
    x1 = probe["make"]("x1", "2026-01-15T00:00:00Z")
    y = probe["make"]("y", "2026-02-01T00:00:00Z")
    keeper = probe["make"]("k", "2026-03-01T00:00:00Z")
    d, ch = probe["driver"], probe["chroma"]
    assert writer.supersede(d, ch, x0, x1).ok
    assert writer.supersede(d, ch, y, keeper).ok
    assert writer.supersede(d, ch, x1, keeper).ok
    versions = writer.lineage_versions(d, keeper)
    assert [v["id"] for v in versions] == [keeper, y, x1, x0]
    assert [v["version"] for v in versions] == [4, 3, 2, 1]
    assert sum(1 for v in versions if not v["valid_to"]) == 1


def test_a_failed_row_stamp_puts_the_graph_back(probe):
    a = probe["make"]("a", "2026-01-01T00:00:00Z")
    b = probe["make"]("b", "2026-02-01T00:00:00Z")
    broken = _BrokenCollection("conversations")
    for nid in (a, b):
        broken.upsert(ids=[f"{nid}_row"], documents=[nid], embeddings=[[1.0, 0.0]], metadatas=[{"artifact_id": nid}])

    class _Client:
        def get_or_create_collection(self, **_kw: Any) -> FakeChromaCollection:
            return broken

    result = writer.supersede(probe["driver"], _Client(), a, b)
    assert not result.ok and "not applied" in result.reason
    na = probe["node"](a)
    assert "superseded_by" not in na and "valid_to" not in na and "lineage_id" not in na
    with probe["driver"].session() as s:
        assert s.run("MATCH (:Artifact {id: $b})-[r:SUPERSEDES]->() RETURN count(r) AS n", b=b).single()["n"] == 0


def _chain(probe, *names: str) -> list[str]:
    ids = [probe["make"](n, f"2026-0{k + 1}-01T00:00:00Z") for k, n in enumerate(names)]
    for old, new in zip(ids, ids[1:]):
        assert writer.supersede(probe["driver"], probe["chroma"], old, new, valid_to=f"closed-{old}").ok
    return ids


def _settle(probe, lineage: str, *forgotten: str) -> str:
    return writer.settle_lineage(probe["driver"], probe["chroma"], lineage, forgotten=lambda v: v in forgotten)


def _current(probe, ids: list[str]) -> list[str]:
    return [i for i in ids if not probe["node"](i).get("valid_to") and not probe["node"](i).get("superseded_by")]


def test_forgetting_the_current_version_makes_the_previous_current_and_restore_undoes_it(probe):
    a, b = _chain(probe, "a", "b")
    assert _settle(probe, a, b) == a
    assert "superseded_by" not in probe["node"](a) and _row(probe, a)["valid_to"] == ""
    assert _settle(probe, a, b) == a  # idempotent
    assert _settle(probe, a) == b
    na = probe["node"](a)
    assert na["superseded_by"] == b and na["valid_to"] == f"closed-{a}"  # the time it first stopped being true
    assert _row(probe, a)["superseded_by"] == b


def test_a_lineage_forgotten_whole_changes_nothing(probe):
    a, b = _chain(probe, "a", "b")
    assert _settle(probe, a, a, b) == ""
    assert probe["node"](a)["superseded_by"] == b


def test_forgetting_the_two_newest_versions_makes_the_oldest_current(probe):
    a, b, c = _chain(probe, "a", "b", "c")
    assert _settle(probe, a, b, c) == a
    # forgotten versions are left as they are (hidden); a is the one visible current version
    assert "superseded_by" not in probe["node"](a) and probe["node"](b)["superseded_by"] == c


def test_restores_in_any_order_leave_one_current_version(probe):
    a, b, c = _chain(probe, "a", "b", "c")
    _settle(probe, a, c)          # trash c: b is current
    _settle(probe, a, b, c)       # trash b too: a is current
    assert _settle(probe, a, b) == c  # restore c first
    assert probe["node"](a)["superseded_by"] == c
    assert _settle(probe, a) == c     # then b
    assert [probe["node"](i).get("superseded_by") for i in (a, b)] == [b, c]
    assert _current(probe, [a, b, c]) == [c]


def test_purging_a_middle_version_reconnects_the_history(probe):
    a, b, c = _chain(probe, "a", "b", "c")
    with probe["driver"].session() as s:
        s.run("MATCH (n {id: $b}) DETACH DELETE n", b=b)
    assert _settle(probe, a) == c
    assert probe["node"](a)["superseded_by"] == c and probe["node"](a)["valid_to"] == f"closed-{a}"
    assert [v["version"] for v in writer.lineage_versions(probe["driver"], c)] == [2, 1]


def test_facts_close_when_the_successor_value_is_known(probe):
    from app.db.neo4j.facts import write_facts
    from core.agents.fact_derivation import DerivedFact

    a = probe["make"]("a", "2026-01-01T00:00:00Z")
    b = probe["make"]("b", "2026-02-01T00:00:00Z")
    d, ch = probe["driver"], probe["chroma"]
    subj = f"{probe['tag']}-person"
    pref = DerivedFact(subject_id=subj, predicate="preference", object_id=None, fact_key="preference",
                       valid_from="2026-01-01", event_date="", is_state=True, source="extraction")
    write_facts(d, [pref], source_artifact_id=a, value="likes tea")
    assert writer.supersede(d, ch, a, b, valid_to="2026-02-01").ok

    def fact(src: str) -> dict[str, Any]:
        with d.session() as s:
            rec = s.run("MATCH (f:Fact {source_artifact_id: $src}) RETURN properties(f) AS p", src=src).single()
        return dict(rec["p"]) if rec else {}

    assert "valid_to" not in fact(a)  # b's value is not known yet
    result = write_facts(d, [pref], source_artifact_id=b, value="likes coffee")
    assert result["facts_closed"] == 1
    assert fact(a)["valid_to"] == "2026-02-01" and fact(a)["closed_by"] == b
    assert "valid_to" not in fact(b) and fact(b)["value"] == "likes coffee"

    writer.settle_lineage(d, ch, a, forgotten=lambda v: v == b)  # b forgotten: a's fact is current again
    assert "valid_to" not in fact(a) and fact(b)["closed_by"] == f"trash:{b}"
    writer.settle_lineage(d, ch, a, forgotten=lambda v: False)
    assert fact(a)["valid_to"] == "2026-02-01" and fact(a)["closed_by"] == b and "valid_to" not in fact(b)


def test_purging_a_memory_takes_every_fact_that_holds_its_text(probe):
    from app.db.neo4j.facts import write_facts
    from app.services.forget.adapters import _FACT_SWEEP
    from core.agents.fact_derivation import DerivedFact

    a = probe["make"]("a", "2026-01-01T00:00:00Z")
    d = probe["driver"]
    pref = DerivedFact(subject_id=f"{probe['tag']}-person", predicate="preference", object_id=None,
                       fact_key="preference", valid_from="2026-01-01", event_date="", is_state=True,
                       source="extraction")
    write_facts(d, [pref], source_artifact_id=a, value="the memory's own words")
    with d.session() as s:
        s.run("MATCH (:Artifact {id: $a})-[r:FACT]->() DELETE r", a=a)  # an edge lost to an old bug
        removed = s.run(_FACT_SWEEP, aid=a).single()["n"]
        left = s.run("MATCH (f:Fact) WHERE f.value = $v RETURN count(f) AS n", v="the memory's own words").single()
    assert removed == 1 and left["n"] == 0

    gone = f"{probe['tag']}-gone"
    assert write_facts(d, [pref], source_artifact_id=gone, value="text of a forgotten memory")["facts_written"] == 0
