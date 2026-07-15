# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase C — C2 bi-temporal :Fact writer (app/db/neo4j/facts.py).

An in-memory fake models MERGE-by-uid so the behavioural guarantees are proven
directly: same uid twice = one node, every :Fact has an inbound HAS_FACT edge
(zero-orphan invariant), provenance + binary FACT_OBJECT edges, source-flag
propagation, and chunked writes. Cypher-shape assertions prove node + edge land
in ONE transaction.
"""
from __future__ import annotations

from app.db.neo4j.facts import (
    _WRITE_FACTS_CYPHER,
    FACT_WRITE_CHUNK_SIZE,
    _build_rows,
    write_facts,
)
from core.agents.fact_derivation import DerivedFact, fact_uid

# ---------------------------------------------------------------------------
# In-memory graph fake modelling exactly _WRITE_FACTS_CYPHER's MERGE semantics.
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, row: dict):
        self._row = row

    def single(self):
        return self._row


class _FakeGraph:
    """Applies the writer's UNWIND-MERGE semantics against in-memory state."""

    def __init__(self, existing_artifacts: set[str] | None = None):
        self.entities: set[str] = set()
        self.facts: dict[str, dict] = {}          # uid -> props (MERGE by uid)
        self.edges: set[tuple] = set()            # ((label,key), rel, (label,key))
        self.artifacts: set[str] = set(existing_artifacts or set())
        self.calls: list[tuple[str, dict]] = []   # (cypher, params) — call capture

    # session context-manager protocol
    def session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, cypher: str, **params):
        self.calls.append((cypher, params))
        written: set[str] = set()
        matched_closed: set[str] = set()
        for row in params.get("rows", []):
            subj = ("Entity", row["subject_id"])
            self.entities.add(row["subject_id"])
            uid = row["uid"]
            fact = ("Fact", uid)
            if uid not in self.facts:  # MERGE (f:Fact {uid}) ON CREATE
                self.facts[uid] = {
                    k: row[k]
                    for k in (
                        "subject_id", "object_id", "predicate", "fact_key",
                        "event_date", "valid_from", "valid_to", "invalid_at",
                        "created_at", "source",
                    )
                }
            elif self.facts[uid].get("invalid_at") is not None:
                # MATCH (not ON CREATE) of a node Phase-D closure already
                # closed — this writer never touches invalid_at on a match.
                matched_closed.add(uid)
            self.edges.add((subj, "HAS_FACT", fact))
            if row["source_artifact_id"] in self.artifacts:  # OPTIONAL MATCH
                self.edges.add((("Artifact", row["source_artifact_id"]), "FACT", fact))
            if row["object_id"]:  # binary
                self.entities.add(row["object_id"])
                self.edges.add((fact, "FACT_OBJECT", ("Entity", row["object_id"])))
            written.add(uid)
        return _FakeResult({
            "facts_written": len(written),
            "facts_matched_closed": len(matched_closed),
        })

    # assertions helpers
    def has_orphan_fact(self) -> bool:
        for uid in self.facts:
            fact = ("Fact", uid)
            if not any(rel == "HAS_FACT" and dst == fact for _, rel, dst in self.edges):
                return True
        return False


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
    return DerivedFact(
        subject_id=subject_id,
        predicate=predicate,
        object_id=object_id,
        fact_key=fact_key,
        valid_from=valid_from,
        event_date=event_date,
        is_state=is_state,
        source=source,
    )


# ---------------------------------------------------------------------------
# Orphan-safety: node + inbound HAS_FACT in ONE transaction
# ---------------------------------------------------------------------------


def test_cypher_merges_node_and_has_fact_in_one_statement():
    # The single write statement contains BOTH the :Fact MERGE and the inbound
    # HAS_FACT MERGE — proving they commit in the same transaction (no window
    # where a :Fact exists without its HAS_FACT edge).
    assert "MERGE (f:Fact {uid: row.uid})" in _WRITE_FACTS_CYPHER
    assert "MERGE (subj)-[:HAS_FACT]->(f)" in _WRITE_FACTS_CYPHER
    # And they are in the same string handed to a single session.run.
    node_idx = _WRITE_FACTS_CYPHER.index("MERGE (f:Fact")
    edge_idx = _WRITE_FACTS_CYPHER.index("MERGE (subj)-[:HAS_FACT]")
    assert edge_idx > node_idx


def test_no_orphan_fact_after_write():
    g = _FakeGraph(existing_artifacts={"art-1"})
    write_facts(g, [_fact()], source_artifact_id="art-1")
    assert g.facts  # a fact was written
    assert not g.has_orphan_fact()


# ---------------------------------------------------------------------------
# MERGE dedup: same uid twice = one node
# ---------------------------------------------------------------------------


def test_same_uid_twice_one_node():
    g = _FakeGraph(existing_artifacts={"art-1"})
    write_facts(g, [_fact()], source_artifact_id="art-1")
    write_facts(g, [_fact()], source_artifact_id="art-1")  # re-extraction
    assert len(g.facts) == 1


def test_build_rows_dedups_identical_facts():
    rows = _build_rows([_fact(), _fact()], source_artifact_id="art-1", created_at="now")
    assert len(rows) == 1


def test_distinct_dates_distinct_nodes():
    g = _FakeGraph(existing_artifacts={"art-1"})
    facts = [
        _fact(fact_key="conversational|2026-03-01", valid_from="2026-03-01", event_date="2026-03-01"),
        _fact(fact_key="conversational|2026-03-08", valid_from="2026-03-08", event_date="2026-03-08"),
    ]
    result = write_facts(g, facts, source_artifact_id="art-1")
    assert len(g.facts) == 2
    assert result["facts_written"] == 2


# ---------------------------------------------------------------------------
# uid = "{subject_id}|{fact_key}"
# ---------------------------------------------------------------------------


def test_uid_is_subject_pipe_fact_key():
    rows = _build_rows([_fact()], source_artifact_id="art-1", created_at="now")
    assert rows[0]["uid"] == fact_uid("other:yoga-class", "conversational|2026-03-01")
    assert rows[0]["uid"] == "other:yoga-class|conversational|2026-03-01"


# ---------------------------------------------------------------------------
# Provenance edge + interval stamps + source flag
# ---------------------------------------------------------------------------


def test_provenance_edge_to_source_artifact():
    g = _FakeGraph(existing_artifacts={"art-1"})
    write_facts(g, [_fact()], source_artifact_id="art-1")
    uid = "other:yoga-class|conversational|2026-03-01"
    assert (("Artifact", "art-1"), "FACT", ("Fact", uid)) in g.edges


def test_missing_source_artifact_still_writes_fact():
    # OPTIONAL MATCH: a missing source artifact must not abort the fact/edge.
    g = _FakeGraph(existing_artifacts=set())  # art-1 does not exist
    write_facts(g, [_fact()], source_artifact_id="art-1")
    assert len(g.facts) == 1
    assert not g.has_orphan_fact()  # HAS_FACT still present


def test_interval_stamps_open_and_active():
    rows = _build_rows([_fact()], source_artifact_id="art-1", created_at="2026-07-14T00:00:00Z")
    row = rows[0]
    assert row["valid_to"] is None      # open (still true)
    assert row["invalid_at"] is None    # active belief
    assert row["created_at"] == "2026-07-14T00:00:00Z"
    assert row["valid_from"] == "2026-03-01"


def test_source_flag_propagated():
    rows = _build_rows(
        [_fact(source="verification")], source_artifact_id="art-1", created_at="now"
    )
    assert rows[0]["source"] == "verification"


# ---------------------------------------------------------------------------
# Binary fact -> FACT_OBJECT edge
# ---------------------------------------------------------------------------


def test_binary_fact_writes_fact_object_edge():
    g = _FakeGraph(existing_artifacts={"art-1"})
    binary = _fact(
        subject_id="person:user",
        predicate="attended",
        object_id="other:yoga-class",
        fact_key="attended|other:yoga-class|2026-03-01",
    )
    write_facts(g, [binary], source_artifact_id="art-1")
    uid = fact_uid("person:user", "attended|other:yoga-class|2026-03-01")
    assert (("Fact", uid), "FACT_OBJECT", ("Entity", "other:yoga-class")) in g.edges


def test_cypher_supports_fact_object_and_provenance():
    assert "FACT_OBJECT" in _WRITE_FACTS_CYPHER
    assert "MERGE (a)-[:FACT]->(f)" in _WRITE_FACTS_CYPHER
    assert "OPTIONAL MATCH (a:Artifact {id: row.source_artifact_id})" in _WRITE_FACTS_CYPHER


# ---------------------------------------------------------------------------
# Chunking + empty
# ---------------------------------------------------------------------------


def test_chunked_writes_split_into_batches():
    g = _FakeGraph(existing_artifacts={"art-1"})
    facts = [
        _fact(
            subject_id=f"other:e{i}",
            fact_key=f"conversational|2026-03-{i:02d}",
        )
        for i in range(1, 6)  # 5 distinct facts
    ]
    result = write_facts(g, facts, source_artifact_id="art-1", chunk_size=2)
    assert result["facts_written"] == 5
    assert result["chunks"] == 3          # ceil(5/2)
    assert len(g.calls) == 3


def test_default_chunk_size_is_bounded():
    assert 0 < FACT_WRITE_CHUNK_SIZE <= 10_000


def test_empty_facts_no_run():
    g = _FakeGraph()
    result = write_facts(g, [], source_artifact_id="art-1")
    assert result == {"facts_written": 0, "facts_matched_closed": 0, "chunks": 0}
    assert g.calls == []


# ---------------------------------------------------------------------------
# Closed-node MERGE telemetry (Phase D contract: no silent re-open)
# ---------------------------------------------------------------------------


def test_closed_fact_matched_not_reopened():
    g = _FakeGraph(existing_artifacts={"art-1", "art-2"})
    write_facts(g, [_fact()], source_artifact_id="art-1")
    uid = "other:yoga-class|conversational|2026-03-01"
    # Simulate Phase-D closure code having invalidated this fact out-of-band.
    g.facts[uid]["invalid_at"] = "2026-06-01T00:00:00Z"

    result = write_facts(g, [_fact()], source_artifact_id="art-2")

    assert result["facts_matched_closed"] == 1
    assert result["facts_written"] == 1
    # Closed stays closed — this writer never re-opens it.
    assert g.facts[uid]["invalid_at"] == "2026-06-01T00:00:00Z"


def test_closed_fact_still_gets_provenance_edge():
    g = _FakeGraph(existing_artifacts={"art-1", "art-2"})
    write_facts(g, [_fact()], source_artifact_id="art-1")
    uid = "other:yoga-class|conversational|2026-03-01"
    g.facts[uid]["invalid_at"] = "2026-06-01T00:00:00Z"

    write_facts(g, [_fact()], source_artifact_id="art-2")

    # The new artifact genuinely references the (closed) subject — provenance
    # still MERGEs even though belief state stays closed.
    assert (("Artifact", "art-2"), "FACT", ("Fact", uid)) in g.edges


def test_open_fact_matched_is_not_counted_closed():
    g = _FakeGraph(existing_artifacts={"art-1", "art-2"})
    write_facts(g, [_fact()], source_artifact_id="art-1")
    result = write_facts(g, [_fact()], source_artifact_id="art-2")
    assert result["facts_matched_closed"] == 0


def test_facts_matched_closed_key_present_on_create():
    g = _FakeGraph(existing_artifacts={"art-1"})
    result = write_facts(g, [_fact()], source_artifact_id="art-1")
    assert "facts_matched_closed" in result
    assert result["facts_matched_closed"] == 0  # freshly created, not matched-closed


def test_cypher_returns_facts_matched_closed():
    assert "facts_matched_closed" in _WRITE_FACTS_CYPHER
    assert "f.invalid_at IS NOT NULL" in _WRITE_FACTS_CYPHER
