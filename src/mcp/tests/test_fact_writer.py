# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The bi-temporal :Fact writer (app/db/neo4j/facts.py), one version per source memory.

An in-memory fake models the two statements the writer runs: the version write
(MERGE by uid, ON CREATE stamps, written closed when the source memory is
already superseded) and the predecessor closure (STATE facts of superseded
versions in the source's lineage close; EVENT facts never do). The Cypher
itself runs against Neo4j in tests/integration/test_preservation_lineage.py.
"""
from __future__ import annotations

from app.db.neo4j.facts import (
    _CLOSE_PREDECESSOR_FACTS,
    _WRITE_FACTS_CYPHER,
    FACT_VALUE_MAX,
    FACT_WRITE_CHUNK_SIZE,
    _build_rows,
    write_facts,
)
from core.agents.fact_derivation import DerivedFact, fact_uid

_NOW = "2026-10-09T00:00:00Z"


class _FakeResult:
    def __init__(self, row: dict):
        self._row = row

    def single(self):
        return self._row


class _FakeGraph:
    """Artifacts are ``{id: {lineage_id, superseded_by, valid_to}}``."""

    def __init__(self, artifacts: dict[str, dict] | None = None):
        self.artifacts = {k: {"lineage_id": None, "superseded_by": None, "valid_to": None, **v}
                          for k, v in (artifacts or {}).items()}
        self.facts: dict[str, dict] = {}
        self.edges: set[tuple] = set()
        self.calls: list[tuple[str, dict]] = []

    def session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, cypher: str, **params):
        self.calls.append((cypher, params))
        if cypher == _CLOSE_PREDECESSOR_FACTS:
            return _FakeResult({"closed": self._close(params["aid"], set(params["state"]), params["now"])})
        written: set[str] = set()
        matched_closed: set[str] = set()
        for row in params.get("rows", []):
            src = self.artifacts.get(row["source_artifact_id"])
            if src is None:  # MATCH on the source: a forgotten memory writes nothing
                continue
            closed_at = None
            if src is not None and (src["superseded_by"] or src["valid_to"]):
                closed_at = src["valid_to"] or params["now"]
            uid = row["uid"]
            if uid not in self.facts:
                self.facts[uid] = {
                    **{k: row[k] for k in ("subject_id", "object_id", "predicate", "fact_key",
                                           "event_date", "valid_from", "created_at", "source",
                                           "source_artifact_id", "value")},
                    "valid_to": closed_at,
                    "invalid_at": params["now"] if closed_at else None,
                    "closed_by": src["superseded_by"] if closed_at and src else None,
                    "lineage_id": (src["lineage_id"] or row["source_artifact_id"]) if src
                    else row["source_artifact_id"],
                }
            if self.facts[uid]["invalid_at"] is not None:
                matched_closed.add(uid)
            self.edges.add((("Entity", row["subject_id"]), "HAS_FACT", ("Fact", uid)))
            if src is not None:
                self.edges.add((("Artifact", row["source_artifact_id"]), "FACT", ("Fact", uid)))
            if row["object_id"]:
                self.edges.add((("Fact", uid), "FACT_OBJECT", ("Entity", row["object_id"])))
            written.add(uid)
        return _FakeResult({"facts_written": len(written), "facts_matched_closed": len(matched_closed)})

    def _close(self, aid: str, state: set[str], now: str) -> int:
        b = self.artifacts.get(aid)
        if not b or not b["lineage_id"] or b["superseded_by"] or b["valid_to"]:
            return 0
        closed = 0
        for pid, p in self.artifacts.items():
            if pid == aid or p["lineage_id"] != b["lineage_id"] or not (p["superseded_by"] or p["valid_to"]):
                continue
            for f in self.facts.values():
                if f["source_artifact_id"] == pid and f["valid_to"] is None and f["predicate"] in state:
                    f.update(valid_to=p["valid_to"] or now, invalid_at=now, closed_by=p["superseded_by"])
                    closed += 1
        return closed

    def has_orphan_fact(self) -> bool:
        return any(
            not any(rel == "HAS_FACT" and dst == ("Fact", uid) for _, rel, dst in self.edges)
            for uid in self.facts
        )


def _fact(
    subject_id="other:yoga-class",
    predicate="conversational",
    object_id=None,
    fact_key="conversational|2026-03-01",
    valid_from="2026-03-01",
    event_date="2026-03-01",
    is_state=False,
    source="extraction",
) -> DerivedFact:
    return DerivedFact(subject_id=subject_id, predicate=predicate, object_id=object_id, fact_key=fact_key,
                       valid_from=valid_from, event_date=event_date, is_state=is_state, source=source)


def _state(subject_id="person:user") -> DerivedFact:
    return _fact(subject_id=subject_id, predicate="preference", fact_key="preference",
                 event_date="", is_state=True)


def _write(g, facts, aid, **kw):
    return write_facts(g, facts, source_artifact_id=aid, **kw)


# ---------------------------------------------------------------------------
# Orphan safety: node + inbound HAS_FACT in ONE statement
# ---------------------------------------------------------------------------


def test_cypher_merges_node_and_has_fact_in_one_statement():
    node_idx = _WRITE_FACTS_CYPHER.index("MERGE (f:Fact {uid: row.uid})")
    edge_idx = _WRITE_FACTS_CYPHER.index("MERGE (subj)-[:HAS_FACT]->(f)")
    assert edge_idx > node_idx


def test_no_orphan_fact_after_write():
    g = _FakeGraph({"art-1": {}})
    _write(g, [_fact()], "art-1")
    assert g.facts and not g.has_orphan_fact()


# ---------------------------------------------------------------------------
# Identity: one version per source memory
# ---------------------------------------------------------------------------


def test_uid_ends_in_the_source_memory():
    rows = _build_rows([_fact()], source_artifact_id="art-1", created_at="now")
    assert rows[0]["uid"] == fact_uid("other:yoga-class", "conversational|2026-03-01", "art-1")
    assert rows[0]["uid"] == "other:yoga-class|conversational|2026-03-01|art-1"


def test_re_extracting_one_memory_is_one_node():
    g = _FakeGraph({"art-1": {}})
    _write(g, [_fact()], "art-1")
    _write(g, [_fact()], "art-1")
    assert len(g.facts) == 1


def test_two_memories_asserting_one_fact_are_two_versions():
    g = _FakeGraph({"art-1": {}, "art-2": {}})
    _write(g, [_fact()], "art-1")
    _write(g, [_fact()], "art-2")
    assert len(g.facts) == 2
    assert {f["fact_key"] for f in g.facts.values()} == {"conversational|2026-03-01"}


def test_build_rows_dedups_identical_facts():
    assert len(_build_rows([_fact(), _fact()], source_artifact_id="art-1", created_at="now")) == 1


def test_distinct_dates_distinct_nodes():
    g = _FakeGraph({"art-1": {}})
    result = _write(g, [_fact(), _fact(fact_key="conversational|2026-03-08", valid_from="2026-03-08",
                                       event_date="2026-03-08")], "art-1")
    assert len(g.facts) == 2 and result["facts_written"] == 2


def test_the_memory_text_is_the_value_capped():
    rows = _build_rows([_fact()], source_artifact_id="art-1", created_at="now", value="x" * (FACT_VALUE_MAX + 50))
    assert rows[0]["value"] == "x" * FACT_VALUE_MAX


# ---------------------------------------------------------------------------
# Provenance, interval stamps, source flag, FACT_OBJECT
# ---------------------------------------------------------------------------


def test_provenance_edge_to_source_artifact():
    g = _FakeGraph({"art-1": {}})
    _write(g, [_fact()], "art-1")
    uid = fact_uid("other:yoga-class", "conversational|2026-03-01", "art-1")
    assert (("Artifact", "art-1"), "FACT", ("Fact", uid)) in g.edges


def test_a_forgotten_source_memory_writes_no_fact():
    # The fact would hold the memory's text after the memory itself is gone.
    g = _FakeGraph()
    result = _write(g, [_fact()], "art-1")
    assert g.facts == {} and result["facts_written"] == 0


def test_a_current_memorys_facts_are_open():
    g = _FakeGraph({"art-1": {}})
    _write(g, [_fact()], "art-1")
    (f,) = g.facts.values()
    assert f["valid_to"] is None and f["invalid_at"] is None and f["valid_from"] == "2026-03-01"


def test_source_flag_propagated():
    rows = _build_rows([_fact(source="verification")], source_artifact_id="art-1", created_at="now")
    assert rows[0]["source"] == "verification"


def test_binary_fact_writes_fact_object_edge():
    g = _FakeGraph({"art-1": {}})
    binary = _fact(subject_id="person:user", predicate="attended", object_id="other:yoga-class",
                   fact_key="attended|other:yoga-class|2026-03-01")
    _write(g, [binary], "art-1")
    uid = fact_uid("person:user", "attended|other:yoga-class|2026-03-01", "art-1")
    assert (("Fact", uid), "FACT_OBJECT", ("Entity", "other:yoga-class")) in g.edges


def test_cypher_supports_fact_object_and_provenance():
    assert "FACT_OBJECT" in _WRITE_FACTS_CYPHER
    assert "MERGE (a)-[:FACT]->(f)" in _WRITE_FACTS_CYPHER
    assert "\nMATCH (a:Artifact {id: row.source_artifact_id})" in _WRITE_FACTS_CYPHER
    assert "OPTIONAL MATCH (a:Artifact" not in _WRITE_FACTS_CYPHER


# ---------------------------------------------------------------------------
# Closure follows the memory's lineage (spec §7)
# ---------------------------------------------------------------------------


def test_writing_the_successor_closes_the_predecessors_state_facts():
    g = _FakeGraph({
        "old": {"lineage_id": "old", "superseded_by": "new", "valid_to": "2026-05-01"},
        "new": {"lineage_id": "old"},
    })
    _write(g, [_state()], "old")
    old_uid = fact_uid("person:user", "preference", "old")
    assert g.facts[old_uid]["valid_to"] == "2026-05-01"  # a late extraction of old is written closed
    g.facts[old_uid].update(valid_to=None, invalid_at=None)  # as if extracted before old was superseded

    result = _write(g, [_state()], "new")

    assert result["facts_closed"] == 1
    assert g.facts[old_uid]["valid_to"] == "2026-05-01" and g.facts[old_uid]["closed_by"] == "new"
    new = g.facts[fact_uid("person:user", "preference", "new")]
    assert new["valid_to"] is None  # the successor's late extraction never lands on the closed node


def test_a_successor_with_no_facts_still_closes_the_predecessors():
    g = _FakeGraph({
        "old": {"lineage_id": "old", "superseded_by": "new", "valid_to": "2026-05-01"},
        "new": {"lineage_id": "old"},
    })
    g.facts["seed"] = {"source_artifact_id": "old", "predicate": "preference", "valid_to": None,
                       "invalid_at": None, "closed_by": None, "subject_id": "person:user"}
    result = _write(g, [], "new")
    assert result["facts_written"] == 0 and result["facts_closed"] == 1
    assert g.facts["seed"]["closed_by"] == "new"


def test_event_facts_never_close():
    g = _FakeGraph({
        "old": {"lineage_id": "old", "superseded_by": "new", "valid_to": "2026-05-01"},
        "new": {"lineage_id": "old"},
    })
    g.facts["evt"] = {"source_artifact_id": "old", "predicate": "conversational", "valid_to": None,
                      "invalid_at": None, "closed_by": None, "subject_id": "x"}
    _write(g, [_state()], "new")
    assert g.facts["evt"]["valid_to"] is None


def test_a_memory_in_another_lineage_is_untouched():
    g = _FakeGraph({
        "other": {"lineage_id": "other", "superseded_by": "z", "valid_to": "2026-04-01"},
        "new": {"lineage_id": "mine"},
    })
    g.facts["f"] = {"source_artifact_id": "other", "predicate": "preference", "valid_to": None,
                    "invalid_at": None, "closed_by": None, "subject_id": "person:user"}
    _write(g, [_state()], "new")
    assert g.facts["f"]["valid_to"] is None


def test_a_closed_version_is_not_reopened_by_re_extraction():
    g = _FakeGraph({"art-1": {}})
    _write(g, [_fact()], "art-1")
    uid = fact_uid("other:yoga-class", "conversational|2026-03-01", "art-1")
    g.facts[uid]["invalid_at"] = "2026-06-01T00:00:00Z"
    result = _write(g, [_fact()], "art-1")
    assert result["facts_matched_closed"] == 1
    assert g.facts[uid]["invalid_at"] == "2026-06-01T00:00:00Z"


def test_cypher_writes_a_superseded_sources_facts_closed():
    assert "a.superseded_by IS NOT NULL OR a.valid_to IS NOT NULL" in _WRITE_FACTS_CYPHER
    assert "f.predicate IN $state" in _CLOSE_PREDECESSOR_FACTS


# ---------------------------------------------------------------------------
# Chunking + empty
# ---------------------------------------------------------------------------


def test_chunked_writes_split_into_batches():
    g = _FakeGraph({"art-1": {}})
    facts = [_fact(subject_id=f"other:e{i}", fact_key=f"conversational|2026-03-{i:02d}") for i in range(1, 6)]
    result = _write(g, facts, "art-1", chunk_size=2)
    assert result["facts_written"] == 5 and result["chunks"] == 3
    assert [c for c, _ in g.calls].count(_WRITE_FACTS_CYPHER) == 3


def test_default_chunk_size_is_bounded():
    assert 0 < FACT_WRITE_CHUNK_SIZE <= 10_000


def test_empty_facts_write_nothing_but_still_close():
    g = _FakeGraph()
    result = _write(g, [], "art-1")
    assert result == {"facts_written": 0, "facts_matched_closed": 0, "facts_closed": 0, "chunks": 0}
    assert [c for c, _ in g.calls] == [_CLOSE_PREDECESSOR_FACTS]
