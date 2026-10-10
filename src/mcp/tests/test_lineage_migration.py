# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The phase 5 migration: lineages from the old ``superseded_by`` forest, and
facts split into one version per source memory."""
from __future__ import annotations

from typing import Any

import pytest

import config
from app.services import lineage_migration as lm
from tests.helpers.fake_chroma import FakeChromaClient, FakeChromaCollection

_NAME = config.collection_name("conversations")


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


class _Rec(dict):
    def __getitem__(self, key: str) -> Any:
        return dict.get(self, key)


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = [_Rec(r) for r in rows]

    def single(self) -> _Rec | None:
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _Graph:
    def __init__(self, chained: list[dict[str, Any]], facts: list[dict[str, Any]] | None = None) -> None:
        self.chained = chained
        self.facts = {f["props"]["uid"]: f for f in facts or []}
        self.writes: dict[str, list[Any]] = {}

    def session(self) -> _Graph:
        return self

    def __enter__(self) -> _Graph:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute_write(self, fn: Any, *args: Any) -> Any:
        return fn(self, *args)

    def run(self, cypher: str, **p: Any) -> _Result:
        if cypher == lm._PENDING:
            return _Result([{"n": len(self.chained)}])
        if cypher == lm._CHAINED:
            return _Result(self.chained)
        if cypher == lm._FACTS_PENDING:
            uids = sorted(u for u in self.facts if u > p["after"])[: p["limit"]]
            return _Result([{"uid": u} for u in uids])
        if cypher == lm._FACT_SOURCES:
            return _Result([self.facts[u] for u in p["uids"]])
        if cypher == lm._CLOSED_VERSIONS:
            return _Result([])
        self.writes.setdefault(cypher, []).append(p)
        return _Result([])


def _node(nid: str, t: str, sup: str | None = None, **kw: Any) -> dict[str, Any]:
    return {"id": nid, "sup": sup, "until": None, "valid_to": None, "lineage": None, "t": t, **kw}


def _chroma(*rows: tuple[str, dict[str, Any]]) -> FakeChromaClient:
    col = FakeChromaCollection(_NAME)
    for i, (aid, meta) in enumerate(rows):
        col.upsert(ids=[f"r{i}"], documents=[aid], embeddings=[[1.0, 0.0]], metadatas=[{"artifact_id": aid, **meta}])
    return FakeChromaClient([col])


def _rows(graph: _Graph, cypher: str) -> list[dict[str, Any]]:
    return [r for call in graph.writes.get(cypher, []) for r in call.get("rows", call.get("ids", []))]


def test_each_tree_becomes_a_lineage_numbered_by_ingest_time(monkeypatch):
    monkeypatch.setattr(lm.m0007_lineage, "run", lambda _d: {})
    graph = _Graph([
        _node("b", "2026-02", sup="c", until="2026-03-05T00:00:00Z"),
        _node("a", "2026-01", sup="b", until="2026-02-05T00:00:00Z"),
        _node("c", "2026-03"),
        _node("x", "2026-01", sup="y", until="2026-04-01T00:00:00Z"),
        _node("w", "2026-01", sup="y"),  # replaced into the same memory as x
        _node("y", "2026-04"),
    ])
    chroma = _chroma(("a", {"valid_to": "2026-02-01"}), ("b", {}), ("c", {"valid_to": "stale"}))
    counts = lm.migrate_lineages(neo4j=graph, chroma=chroma)

    assert counts["lineages"] == 2 and counts["versions"] == 6 and counts["reopened"] == 0
    rows = {r["id"]: r for r in _rows(graph, lm._WRITE_VERSIONS)}
    assert [(rows[i]["lineage_id"], rows[i]["version"]) for i in "abc"] == [("a", 1), ("a", 2), ("a", 3)]
    assert rows["a"]["valid_to"] == "2026-02-01"           # the world time its rows recorded
    assert rows["b"]["valid_to"] == "2026-03-05T00:00:00Z"  # else the old valid_until
    assert rows["c"]["valid_to"] is None                     # the root is current
    assert {rows[i]["lineage_id"] for i in "wxy"} == {"w"} and rows["y"]["version"] == 3
    col = chroma.get_or_create_collection(name=_NAME)
    assert col.metadata_of("r2")["valid_to"] == ""          # a current version's rows are open
    assert col.metadata_of("r0")["superseded_by"] == "b" and col.metadata_of("r0")["lineage_id"] == "a"


def test_a_version_whose_successor_is_gone_is_current_again(monkeypatch):
    monkeypatch.setattr(lm.m0007_lineage, "run", lambda _d: {})
    graph = _Graph([_node("o", "2026-01", sup="purged-long-ago", until="2026-02-01")])
    chroma = _chroma(("o", {"valid_to": "2026-02-01", "superseded_by": "purged-long-ago"}))
    counts = lm.migrate_lineages(neo4j=graph, chroma=chroma)
    assert counts["reopened"] == 1
    assert _rows(graph, lm._REOPEN_ORPHANS) == ["o"]
    meta = chroma.get_or_create_collection(name=_NAME).metadata_of("r0")
    assert meta["valid_to"] == "" and meta["superseded_by"] == ""


def test_a_dry_run_counts_without_writing(monkeypatch):
    monkeypatch.setattr(lm.m0007_lineage, "run", lambda _d: (_ for _ in ()).throw(AssertionError("schema write")))
    graph = _Graph([_node("a", "2026-01", sup="b"), _node("b", "2026-02")])
    counts = lm.migrate_lineages(neo4j=graph, chroma=_chroma(), dry_run=True)
    assert counts["lineages"] == 1 and graph.writes == {}


def test_a_tree_the_writer_already_manages_keeps_its_lineage_and_is_settled(monkeypatch):
    """A legacy version synced in beside a lineage the writer made joins it;
    nothing is relabelled, and the writer decides what is current."""
    monkeypatch.setattr(lm.m0007_lineage, "run", lambda _d: {})
    settled: list[str] = []
    monkeypatch.setattr(lm, "settle_lineage", lambda _d, _c, lineage, forgotten: settled.append(lineage))
    graph = _Graph([
        _node("legacy", "2026-01", sup="b", until="2026-02-01"),
        _node("b", "2026-02", sup="c", lineage="lin-b", valid_to="2026-03-01"),
        _node("c", "2026-03", lineage="lin-b"),
        _node("o", "2026-01", sup="purged", lineage="lin-o"),  # successor purged: the writer reconnects it
    ])
    counts = lm.migrate_lineages(neo4j=graph, chroma=_chroma())
    assert counts["lineages"] == 0 and counts["reopened"] == 0 and counts["settled"] == 2
    assert _rows(graph, lm._JOIN_LINEAGE) == [{"id": "legacy", "lineage_id": "lin-b"}]
    assert graph.writes.get(lm._WRITE_VERSIONS) is None and sorted(settled) == ["lin-b", "lin-o"]


def _fact(uid: str, predicate: str, sources: list[dict[str, Any]], **props: Any) -> dict[str, Any]:
    return {"props": {"uid": uid, "predicate": predicate, "subject_id": "person:u", **props},
            "sources": sources, "objects": []}


def test_facts_split_into_one_version_per_source_memory(monkeypatch):
    monkeypatch.setattr(lm.m0007_lineage, "run", lambda _d: {})
    graph = _Graph([], facts=[
        _fact("person:u|preference", "preference", [
            {"id": "old", "superseded_by": "new", "valid_to": "2026-02-01", "lineage_id": "old", "summary": "tea"},
            {"id": "new", "superseded_by": None, "valid_to": None, "lineage_id": "old", "summary": "coffee"},
        ], valid_to="2026-02-01", invalid_at="2026-02-02"),  # the old node, wrongly closed for both
        _fact("person:u|conversational|2026-01-03", "conversational", [
            {"id": "old", "superseded_by": "new", "valid_to": "2026-02-01", "lineage_id": "old", "summary": "met"},
        ]),
        _fact("other:gone|empirical", "empirical", []),
    ])
    counts = lm.migrate_lineages(neo4j=graph, chroma=_chroma())
    assert counts == {"lineages": 0, "versions": 0, "reopened": 0, "settled": 0, "flagged_versions": 0,
                      "facts_split": 2, "fact_versions": 3, "orphan_facts_removed": 1}
    versions = {r["uid"]: r["props"] for r in _rows(graph, lm._WRITE_FACT_VERSIONS)}
    old = versions["person:u|preference|old"]
    assert old["valid_to"] == "2026-02-01" and old["closed_by"] == "new" and old["value"] == "tea"
    new = versions["person:u|preference|new"]
    assert "valid_to" not in new and "invalid_at" not in new and new["lineage_id"] == "old"
    assert "valid_to" not in versions["person:u|conversational|2026-01-03|old"]  # events never close
    assert sorted(u for c in graph.writes[lm._DROP_SPLIT] for u in c["uids"]) == [
        "person:u|conversational|2026-01-03", "person:u|preference"]
    assert [u for c in graph.writes[lm._DROP_SOURCELESS] for u in c["uids"]] == ["other:gone|empirical"]
