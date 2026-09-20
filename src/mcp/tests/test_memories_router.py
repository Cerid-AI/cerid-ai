# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""GET /memories must show every memory the system holds, and only the live ones.

Two defects covered here, both of which made the Memories pane disagree with
the rest of the product:

* the listing read ``memory_``-prefixed ``:Artifact`` rows only, so the
  ``:Memory`` nodes that ``/observability/knowledge-stats`` counts and
  ``/sync/status`` backs up had no listing path at all;
* the listing carried no ``superseded_by`` predicate, so a memory that
  ``/memories/dedup`` had retired — and that recall already drops — kept
  rendering as a live row.

The double below is not a Cypher engine: it understands exactly the
predicates this router issues, and *enforces* them, so a query that omits
one returns the rows it should have excluded.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeResult:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def single(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    def __init__(self, graph: "FakeMemoryGraph"):
        self._graph = graph

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **params):
        return self._graph.run(query, **params)


class FakeMemoryGraph:
    """Behavioural double for the two memory stores the pane must merge.

    ``artifacts`` are ``memory_``-prefixed ``:Artifact`` rows in the
    conversations domain; ``memories`` are ``:Memory`` nodes. Each stored
    row carries the properties the predicates key off, and ``run`` applies
    a predicate only when the query actually asks for it.
    """

    def __init__(self, artifacts: list[dict] | None = None, memories: list[dict] | None = None):
        self.artifacts = artifacts or []
        self.memories = memories or []
        self.queries: list[str] = []

    def session(self):
        return _FakeSession(self)

    # -- predicate application -------------------------------------------
    def _matching_artifacts(self, query: str, params: dict) -> list[dict]:
        rows = [a for a in self.artifacts if (a.get("filename") or "").startswith("memory_")]
        if "a.superseded_by IS NULL" in query:
            rows = [a for a in rows if a.get("superseded_by") is None]
        if "$memory_type_prefix" in query:
            rows = [a for a in rows if (a["filename"] or "").startswith(params["memory_type_prefix"])]
        if "$convo_prefix" in query and "a.filename CONTAINS" in query:
            rows = [a for a in rows if params["convo_prefix"] in (a["filename"] or "")]
        rows.sort(key=lambda a: a.get("ingested_at") or "", reverse=True)
        return rows

    def _matching_memories(self, query: str, params: dict) -> list[dict]:
        rows = list(self.memories)
        if "m.status" in query:
            rows = [m for m in rows if (m.get("status") or "active") == "active"]
        if "SUPERSEDES" in query:
            rows = [m for m in rows if not m.get("superseded")]
        if "$memory_type" in query and "m.memory_type = $memory_type" in query:
            rows = [m for m in rows if m.get("memory_type") == params["memory_type"]]
        if "c.id STARTS WITH $convo_prefix" in query:
            rows = [
                m for m in rows
                if (m.get("conversation_id") or "").startswith(params["convo_prefix"])
            ]
        rows.sort(key=lambda m: m.get("created_at") or "", reverse=True)
        return rows

    def run(self, query, **params):
        self.queries.append(query)
        if "$memory_id" in query:
            mid = params["memory_id"]
            if "(m:Memory" in query:
                return _FakeResult([{"id": m["id"]} for m in self.memories if m["id"] == mid])
            return _FakeResult([
                {"id": a["id"], "chunk_ids": a.get("chunk_ids"), "filename": a.get("filename")}
                for a in self.artifacts if a["id"] == mid
            ])
        limit = params.get("fetch") or params.get("limit") or 1000
        if "(m:Memory)" in query:
            rows = self._matching_memories(query, params)
            if "count(" in query:
                return _FakeResult([{"total": len(rows)}])
            return _FakeResult([
                {
                    "id": m["id"],
                    "text": m.get("text"),
                    "memory_type": m.get("memory_type"),
                    "created_at": m.get("created_at"),
                    "conversation_id": m.get("conversation_id"),
                }
                for m in rows[:limit]
            ])
        rows = self._matching_artifacts(query, params)
        if "count(" in query:
            return _FakeResult([{"total": len(rows)}])
        return _FakeResult([
            {
                "id": a["id"],
                "filename": a.get("filename"),
                "domain": "conversations",
                "summary": a.get("summary"),
                "created_at": a.get("ingested_at"),
                "chunk_ids": a.get("chunk_ids"),
            }
            for a in rows[:limit]
        ])


def _artifact(aid: str, *, mtype="fact", convo="conv1234", ts="2026-09-01T10:00:00Z", superseded=None):
    return {
        "id": aid,
        "filename": f"memory_{mtype}_{convo}_{ts}_0",
        "summary": f"summary for {aid}",
        "ingested_at": ts,
        "superseded_by": superseded,
    }


def _memory(mid: str, *, mtype="decision", convo="conv9999", ts="2026-09-02T10:00:00Z", **kw):
    row = {
        "id": mid,
        "text": f"verified text for {mid}",
        "memory_type": mtype,
        "created_at": ts,
        "conversation_id": convo,
        "status": "active",
        "superseded": False,
    }
    row.update(kw)
    return row


@pytest.fixture()
def client_for():
    def _make(graph: FakeMemoryGraph) -> TestClient:
        from app.routers import memories as memories_router

        app = FastAPI()
        app.include_router(memories_router.router)
        return TestClient(app)

    return _make


def test_verified_memory_nodes_are_listed(client_for, monkeypatch):
    """A :Memory node is a memory the user owns; it must have a listing path."""
    graph = FakeMemoryGraph(
        artifacts=[_artifact("art-1")],
        memories=[_memory("mem-1"), _memory("mem-2", ts="2026-09-03T10:00:00Z")],
    )
    monkeypatch.setattr("app.routers.memories.get_neo4j", lambda: graph)
    resp = client_for(graph).get("/memories")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {m["id"] for m in body["memories"]}
    assert ids == {"art-1", "mem-1", "mem-2"}
    assert body["total"] == 3
    by_id = {m["id"]: m for m in body["memories"]}
    assert by_id["mem-1"]["content"] == "verified text for mem-1"
    assert by_id["mem-1"]["type"] == "decision"
    assert by_id["mem-1"]["source"] == "verified"
    assert by_id["art-1"]["source"] == "conversation"


def test_superseded_memories_are_not_listed(client_for, monkeypatch):
    """Recall drops superseded memories; the pane must not keep showing them."""
    graph = FakeMemoryGraph(
        artifacts=[
            _artifact("art-live"),
            _artifact("art-dead", ts="2026-08-01T10:00:00Z", superseded="art-live"),
        ],
        memories=[
            _memory("mem-live"),
            _memory("mem-dead", ts="2026-08-01T10:00:00Z", superseded=True),
            _memory("mem-merged", ts="2026-08-02T10:00:00Z", status="merged"),
        ],
    )
    monkeypatch.setattr("app.routers.memories.get_neo4j", lambda: graph)
    resp = client_for(graph).get("/memories")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {m["id"] for m in body["memories"]}
    assert ids == {"art-live", "mem-live"}
    assert body["total"] == 2


def test_type_filter_applies_to_both_stores(client_for, monkeypatch):
    graph = FakeMemoryGraph(
        artifacts=[_artifact("art-fact", mtype="fact"), _artifact("art-dec", mtype="decision")],
        memories=[_memory("mem-dec", mtype="decision"), _memory("mem-pref", mtype="preference")],
    )
    monkeypatch.setattr("app.routers.memories.get_neo4j", lambda: graph)
    resp = client_for(graph).get("/memories?type=decisions")

    assert resp.status_code == 200, resp.text
    ids = {m["id"] for m in resp.json()["memories"]}
    assert ids == {"art-dec", "mem-dec"}


def test_pagination_spans_the_merged_list(client_for, monkeypatch):
    graph = FakeMemoryGraph(
        artifacts=[_artifact("art-old", ts="2026-09-01T00:00:00Z")],
        memories=[
            _memory("mem-new", ts="2026-09-03T00:00:00Z"),
            _memory("mem-mid", ts="2026-09-02T00:00:00Z"),
        ],
    )
    monkeypatch.setattr("app.routers.memories.get_neo4j", lambda: graph)
    client = client_for(graph)

    first = client.get("/memories?limit=2&offset=0").json()
    second = client.get("/memories?limit=2&offset=2").json()

    assert [m["id"] for m in first["memories"]] == ["mem-new", "mem-mid"]
    assert [m["id"] for m in second["memories"]] == ["art-old"]
    assert first["total"] == second["total"] == 3


def test_delete_falls_through_to_a_verified_memory_node(monkeypatch):
    """A listed :Memory row must be deletable from the same pane that shows it."""
    from app.routers import memories as memories_router

    class _NoArtifactGraph(FakeMemoryGraph):
        pass

    graph = _NoArtifactGraph(artifacts=[], memories=[_memory("mem-1")])
    deleted: list[str] = []
    monkeypatch.setattr(memories_router, "get_neo4j", lambda: graph)
    monkeypatch.setattr(memories_router, "get_chroma", lambda: object())
    monkeypatch.setattr(memories_router, "get_redis", lambda: None)
    monkeypatch.setattr(
        "app.services.session_wipe._delete_verified_memory",
        lambda driver, memory_id: deleted.append(memory_id),
    )

    app = FastAPI()
    app.include_router(memories_router.router)
    resp = TestClient(app).delete("/memories/mem-1")

    assert resp.status_code == 200, resp.text
    assert deleted == ["mem-1"]


def test_delete_of_an_unknown_id_is_still_a_404(monkeypatch):
    from app.routers import memories as memories_router

    graph = FakeMemoryGraph(artifacts=[], memories=[_memory("mem-1")])
    monkeypatch.setattr(memories_router, "get_neo4j", lambda: graph)
    monkeypatch.setattr(memories_router, "get_chroma", lambda: object())

    app = FastAPI()
    app.include_router(memories_router.router)
    assert TestClient(app).delete("/memories/nope").status_code == 404
