# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""S11-3 (audit F19): a report's source edge lands iff the cited source exists
and is not forgotten, and the report says per source what happened.

The graph here is a small in-memory stand-in that answers the writer's and the
backfill's queries. Which labels a link query can reach is read from the query
text, so a link query that stops matching memories stops linking them here too.
"""
from __future__ import annotations

import copy
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.db.neo4j import artifacts
from app.db.neo4j.artifacts import save_verification_report
from app.db.neo4j.migrations import m0001_backfill_verification_edges as m0001

LINK_TYPES = frozenset({"EXTRACTED_FROM", "VERIFIED"})


class FakeGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, str] = {}
        self.reports: dict[str, dict[str, Any]] = {}
        self.edges: set[tuple[str, str, str]] = set()
        self.fail_link: set[str] = set()
        self.forgotten: dict[str, set[str]] = {"artifact": set(), "memory": set()}

    def add_report(self, claims: list[dict], message_id: str | None = "m1", **props: Any) -> str:
        eid = f"4:r:{len(self.reports)}"
        self.reports[eid] = {"cid": "c1", "mid": message_id, "claims": json.dumps(claims), **props}
        return eid

    def state(self) -> tuple:
        return copy.deepcopy(self.reports), set(self.edges)

    def status(self, eid: str) -> dict[str, str]:
        return json.loads(self.reports[eid].get("edge_status") or "null")

    def linked(self, eid: str) -> set[str]:
        by_source: dict[str, set[str]] = {}
        for r, t, s in self.edges:
            if r == eid:
                by_source.setdefault(s, set()).add(t)
        return {s for s, types in by_source.items() if types == LINK_TYPES}

    def run(self, cypher: str, p: dict[str, Any]) -> list[dict[str, Any]]:
        if "MERGE (r:VerificationReport {conversation_id: $cid, message_id: $mid})" in cypher:
            eid = next((e for e, r in self.reports.items() if (r["cid"], r["mid"]) == (p["cid"], p["mid"])), None)
            eid = eid or self.add_report([], message_id=p["mid"], cid=p["cid"])
            self.reports[eid].update(claims=p["claims"], source_urls=p["source_urls"],
                                     methods=p["verification_methods"])
            return [{"eid": eid}]
        if cypher == artifacts.REPORT_EDGES_CYPHER:
            touched = {s for r, _t, s in self.edges if r == p["rid"]}
            return [{"edge_status": self.reports[p["rid"]].get("edge_status"),
                     "linked": sorted(self.linked(p["rid"])), "touched": sorted(touched)}]
        if "MERGE (r)-[:VERIFIED]->(src)" in cypher:
            if p["aid"] in self.fail_link:
                raise RuntimeError("bolt connection reset")
            reachable = {label for label in ("Artifact", "Memory") if f":{label} {{id: $aid}})" in cypher}
            if self.nodes.get(p["aid"]) not in reachable:
                return [{"linked": 0}]
            self.edges |= {(p["rid"], t, p["aid"]) for t in LINK_TYPES}
            return [{"linked": 1}]
        if "DELETE e" in cypher:
            gone = {e for e in self.edges if e[0] == p["rid"] and e[2] == p["aid"]}
            self.edges -= gone
            return [{"removed": len(gone)}]
        if "SET r.edge_status" in cypher:
            self.reports[p["rid"]]["edge_status"] = p["edge_status"]
            return []
        if cypher == m0001.REPORTS_CYPHER:
            return [{"eid": e, "claims": r["claims"], "source_urls": r.get("source_urls"),
                     "methods": r.get("methods")} for e, r in self.reports.items()]
        if cypher == m0001.ANNOTATE_CYPHER:
            self.reports[p["rid"]].update(source_urls=p["urls"], methods=p["methods"])
            return []
        if "CREATE INDEX" in cypher:
            return []
        if "WHERE r.message_id IS NULL SET r.message_id = ''" in cypher:
            unkeyed = [r for r in self.reports.values() if r["mid"] is None]
            for r in unkeyed:
                r["mid"] = ""
            return [{"n": len(unkeyed)}]
        raise AssertionError(f"unexpected query: {cypher}")


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def single(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _Session:
    def __init__(self, graph: FakeGraph) -> None:
        self._graph = graph

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def run(self, cypher: str, **params: Any) -> _Result:
        return _Result(self._graph.run(cypher, params))


@pytest.fixture
def graph(monkeypatch) -> FakeGraph:
    g = FakeGraph()
    monkeypatch.setattr("core.forget.registry.forgotten_ids", lambda kind: frozenset(g.forgotten.get(kind, ())))
    monkeypatch.setattr("app.deps.get_redis", lambda: MagicMock(), raising=False)
    return g


def _driver(graph: FakeGraph) -> MagicMock:
    driver = MagicMock()
    driver.session.side_effect = lambda: _Session(graph)
    return driver


def _save(graph: FakeGraph, *sources: str) -> str:
    claims = [{"claim": f"c{i}", "status": "verified", "source_artifact_id": s} for i, s in enumerate(sources)]
    save_verification_report(_driver(graph), "c1", claims, 0.8, verified=len(claims), total=len(claims),
                             message_id="m1")
    return next(e for e, r in graph.reports.items() if r["mid"] == "m1")


def test_a_cited_source_that_does_not_exist_reads_missing(graph):
    eid = _save(graph, "never-there")
    assert graph.status(eid) == {"never-there": "missing"}
    assert graph.linked(eid) == set()


def test_a_forgotten_source_still_in_the_trash_reads_unverifiable_and_gets_no_edge(graph):
    graph.nodes["mem-trashed"] = "Memory"
    graph.forgotten["memory"].add("mem-trashed")
    eid = _save(graph, "mem-trashed")
    assert graph.status(eid) == {"mem-trashed": "unverifiable"}
    assert graph.edges == set()


def test_a_purged_source_reads_unverifiable_not_missing(graph):
    graph.forgotten["artifact"].add("art-purged")
    eid = _save(graph, "art-purged")
    assert graph.status(eid) == {"art-purged": "unverifiable"}


def test_a_cited_memory_is_linked(graph):
    graph.nodes["mem-1"] = "Memory"
    eid = _save(graph, "mem-1")
    assert graph.status(eid) == {"mem-1": "linked"}
    assert graph.linked(eid) == {"mem-1"}


def test_a_cited_artifact_is_linked(graph):
    graph.nodes["art-1"] = "Artifact"
    eid = _save(graph, "art-1")
    assert graph.status(eid) == {"art-1": "linked"}
    assert graph.linked(eid) == {"art-1"}


def test_an_edge_write_that_raises_reads_failed_and_the_report_is_still_saved(graph):
    graph.nodes["art-1"] = "Artifact"
    graph.fail_link.add("art-1")
    eid = _save(graph, "art-1")
    assert graph.status(eid) == {"art-1": "failed"}


def _seed_existing_reports(graph: FakeGraph) -> dict[str, str]:
    """Reports already in the graph, as the boot finds them: never linked to the
    memories they cite, one written before reports were keyed per message, and
    one still linked to a source forgotten since."""
    graph.nodes.update({"mem-1": "Memory", "art-1": "Artifact", "mem-forgotten": "Memory"})
    graph.forgotten["memory"].add("mem-forgotten")
    ids = {
        "memory": graph.add_report([{"claim": "a", "status": "verified", "source_artifact_id": "mem-1"}]),
        "artifact": graph.add_report([{"claim": "b", "status": "verified", "source_artifact_id": "art-1",
                                       "verification_method": "kb"}], message_id=None),
        "missing": graph.add_report([{"claim": "c", "status": "verified", "source_artifact_id": "gone"}]),
        "forgotten": graph.add_report([{"claim": "d", "status": "verified",
                                        "source_artifact_id": "mem-forgotten"}]),
    }
    graph.edges |= {(ids["forgotten"], t, "mem-forgotten") for t in LINK_TYPES}
    return ids


def test_the_boot_migration_links_reports_already_in_the_graph_and_records_every_reason(graph, monkeypatch):
    from app.services import verification_reports

    ids = _seed_existing_reports(graph)
    monkeypatch.setattr("app.deps.get_neo4j", lambda: _driver(graph))
    monkeypatch.setattr(verification_reports, "migrate_embedded_reports", lambda: {})

    verification_reports.migrate_reports()

    assert graph.linked(ids["memory"]) == {"mem-1"}
    assert graph.linked(ids["artifact"]) == {"art-1"}
    assert graph.status(ids["memory"]) == {"mem-1": "linked"}
    assert graph.status(ids["missing"]) == {"gone": "missing"}
    assert graph.status(ids["forgotten"]) == {"mem-forgotten": "unverifiable"}
    assert graph.linked(ids["forgotten"]) == set()
    assert graph.reports[ids["artifact"]]["methods"] == ["kb"]


def test_a_second_backfill_changes_nothing(graph):
    _seed_existing_reports(graph)
    first = m0001.run(_driver(graph))
    after_first = graph.state()
    second = m0001.run(_driver(graph))

    assert first["edges_added"] == 2 and first["edges_removed"] == 2 and first["statuses_written"] == 4
    assert second == {"reports_scanned": 4, "edges_added": 0, "edges_removed": 0,
                      "statuses_written": 0, "reports_annotated": 0}
    assert graph.state() == after_first


def test_a_stored_report_reads_back_its_edge_status():
    from app.db.neo4j.artifacts import get_verification_report

    driver, session = MagicMock(), MagicMock()
    driver.session.return_value.__enter__.return_value = session
    session.run.return_value.single.return_value = {
        "id": "r1", "conversation_id": "c-read", "message_id": "m1", "claims": "[]", "overall_score": 1.0,
        "verified": 0, "agreed": None, "unverified": 0, "uncertain": 0, "total": 0, "created_at": "",
        "edge_status": json.dumps({"gone": "missing"}),
    }
    assert get_verification_report(driver, "c-read", "m1")["edge_status"] == {"gone": "missing"}
