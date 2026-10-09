# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The preview for documents, passages and memories picked from search, and its routes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.forget.registry import Entry, Subject
from core.retrieval.chunk_ids import ChunkIdAssigner
from tests.helpers.fake_chroma import FakeChromaClient, FakeChromaCollection
from tests.helpers.forget import isolate_forget

DOC, NOTE, MEM = "d" * 64, "e" * 64, "f" * 64
VERIFIED = "11111111-2222-3333-4444-555555555555"


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


class Graph:
    def __init__(self, nodes: dict[str, dict[str, Any]]) -> None:
        self.nodes = nodes

    def session(self) -> Graph:
        return self

    def __enter__(self) -> Graph:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def run(self, query: str, **p: Any) -> _Result:
        if query.startswith("UNWIND $ids AS i MATCH (a:Artifact {id: i})"):
            return _Result([{"id": i, **self.nodes[i]} for i in p["ids"] if i in self.nodes])
        if "MATCH (m:Memory)" in query:
            return _Result([{"id": i, "text": "verified fact"} for i in p["ids"] if i == VERIFIED])
        if "[:FACT]->(f:Fact)" in query:
            return _Result([{"n": 2 if p["aids"] else 0}])
        raise AssertionError(query)


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    reg = isolate_forget(monkeypatch, tmp_path)
    col = FakeChromaCollection("domain_general")
    ids = ChunkIdAssigner(NOTE)
    passages = [ids.assign("child", t) for t in ("first passage", "second passage")]
    for i, (cid, text) in enumerate(zip(passages, ("first passage", "second passage"))):
        col.upsert(ids=[cid], documents=[f"Source: note.md | Domain: general\n\n{text}"], embeddings=[[1.0, i]],
                   metadatas=[{"artifact_id": NOTE, "chunk_level": "child", "parent_chunk_id": ""}])
    doc_chunk = ChunkIdAssigner(DOC).assign("child", "doc text")
    col.upsert(ids=[doc_chunk], documents=["doc text"], embeddings=[[0.0, 1.0]],
               metadatas=[{"artifact_id": DOC, "chunk_level": "child", "parent_chunk_id": ""}])
    graph = Graph({
        DOC: {"filename": "report.pdf", "summary": "", "domain": "general",
              "chunk_ids": json.dumps([doc_chunk]), "chunk_count": 1},
        NOTE: {"filename": "note.md", "summary": "", "domain": "general",
               "chunk_ids": json.dumps(passages), "chunk_count": 2},
        MEM: {"filename": "memory_fact_x", "summary": "Prefers tea", "domain": "conversations",
              "chunk_ids": "[]", "chunk_count": 1},
    })
    monkeypatch.setattr("app.deps.get_chroma", lambda: FakeChromaClient([col]))
    monkeypatch.setattr("app.deps.get_neo4j", lambda: graph)
    return {"reg": reg, "passages": passages, "doc_chunk": doc_chunk}


def _groups(out: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {g["key"]: g["items"] for g in out["groups"]}


def test_groups_documents_passages_and_memories(env):
    from app.services.forget.preview import preview_items

    out = preview_items([
        Subject("artifact", DOC), Subject("chunk", env["passages"][0]),
        Subject("artifact", MEM), Subject("memory", VERIFIED),
    ])
    groups = _groups(out)
    assert [(i["id"], i["label"], i["passages"]) for i in groups["documents"]] == [(DOC, "report.pdf", 1)]
    assert [(i["kind"], i["label"], i["document"]) for i in groups["passages"]] == [
        ("chunk", "first passage", "note.md"),
    ]
    assert [(i["kind"], i["label"]) for i in groups["memories"]] == [
        ("artifact", "Prefers tea"), ("memory", "verified fact"),
    ]
    assert out["subject"] is None and out["derived_facts"] == 2 and out["notes"] == []


def test_a_passage_of_a_selected_document_folds_into_it(env):
    from app.services.forget.preview import preview_items

    out = preview_items([Subject("artifact", DOC), Subject("chunk", env["doc_chunk"])])
    assert [i["id"] for i in _groups(out)["documents"]] == [DOC]
    assert _groups(out)["passages"] == []


def test_every_passage_of_a_document_gets_a_note(env):
    from app.services.forget.preview import preview_items

    out = preview_items([Subject("chunk", c) for c in env["passages"]])
    assert out["notes"] == [
        "These are all 2 passages of note.md; forgetting the document instead also removes the facts drawn from it."
    ]


def test_already_forgotten_items_are_left_out(env):
    from app.services.forget.preview import preview_items

    env["reg"].append([Entry("fg_1", Subject("chunk", env["passages"][0]), "trashed",
                             "2026-10-09T10:00:00Z", "m1", "ui")])
    out = preview_items([Subject("chunk", env["passages"][0])])
    assert _groups(out)["passages"] == []


@pytest.fixture
def client(env):
    from app.routers import forget as forget_router

    app = FastAPI()
    app.include_router(forget_router.router)
    return TestClient(app)


def test_route_previews_a_selection(client, env):
    resp = client.post("/forget/preview", json={"subjects": [{"kind": "chunk", "id": env["passages"][1]}]})
    assert resp.status_code == 200, resp.text
    assert [i["label"] for i in _groups(resp.json())["passages"]] == ["second passage"]


def test_route_refuses_a_malformed_chunk_id(client):
    for bad in ("short", "x" * 8, "../../etc/passwd"):
        resp = client.post("/forget/preview", json={"subjects": [{"kind": "chunk", "id": bad}]})
        assert resp.status_code == 400, bad
        assert client.post("/forget", json={"subjects": [{"kind": "chunk", "id": bad}]}).status_code == 400


def test_route_refuses_a_conversation_in_a_selection(client):
    resp = client.post("/forget/preview", json={"subjects": [{"kind": "conversation", "id": "c1"}]})
    assert resp.status_code == 400


def test_route_takes_one_form_and_at_most_200_subjects(client):
    assert client.post("/forget/preview", json={}).status_code == 422
    both = {"kind": "conversation", "id": "c1", "subjects": [{"kind": "artifact", "id": DOC}]}
    assert client.post("/forget/preview", json=both).status_code == 422
    many = {"subjects": [{"kind": "artifact", "id": DOC}] * 201}
    assert client.post("/forget/preview", json=many).status_code == 422


def test_trashing_a_passage_records_a_chunk_subject(client, env):
    resp = client.post("/forget", json={"subjects": [{"kind": "chunk", "id": env["passages"][0]}]})
    assert resp.status_code == 200, resp.text
    assert env["reg"].state_of("chunk", env["passages"][0]) == "trashed"


@pytest.fixture
def family(tmp_path: Path, monkeypatch):
    """A parent passage with two children in one document."""
    reg = isolate_forget(monkeypatch, tmp_path)
    col = FakeChromaCollection("domain_general")
    ids = ChunkIdAssigner(NOTE)
    parent = ids.assign("parent", "alpha beta gamma delta")
    kids = [ids.assign("child", "alpha beta"), ids.assign("child", "gamma delta")]
    col.upsert(ids=[parent], documents=["alpha beta gamma delta"], embeddings=[[1.0, 0.0]],
               metadatas=[{"artifact_id": NOTE, "chunk_level": "parent", "parent_chunk_id": ""}])
    for i, k in enumerate(kids):
        col.upsert(ids=[k], documents=["x"], embeddings=[[0.0, float(i)]],
                   metadatas=[{"artifact_id": NOTE, "chunk_level": "child", "parent_chunk_id": parent}])
    graph = Graph({NOTE: {"filename": "note.md", "summary": "", "domain": "general",
                          "chunk_ids": json.dumps(kids), "chunk_count": 2}})
    monkeypatch.setattr("app.deps.get_chroma", lambda: FakeChromaClient([col]))
    monkeypatch.setattr("app.deps.get_neo4j", lambda: graph)
    return {"reg": reg, "parent": parent, "kids": kids}


def test_a_child_stands_for_its_parent(family):
    from app.services.forget.preview import passage_ids

    p, (a, b) = family["parent"], family["kids"]
    assert passage_ids([a, b, p]) == [p]
    unknown = f"{NOTE}_ffffffffffffffff"
    assert passage_ids([unknown]) == [unknown]


def test_forgetting_a_child_records_its_parent(family):
    from app.routers import forget as forget_router

    app = FastAPI()
    app.include_router(forget_router.router)
    resp = TestClient(app).post("/forget", json={"subjects": [{"kind": "chunk", "id": family["kids"][1]}]})
    assert resp.status_code == 200, resp.text
    assert family["reg"].state_of("chunk", family["parent"]) == "trashed"
    assert family["reg"].state_of("chunk", family["kids"][1]) is None


def test_the_preview_of_a_child_shows_its_parent(family):
    from app.services.forget.preview import preview_items

    out = preview_items([Subject("chunk", family["kids"][0])])
    passages = _groups(out)["passages"]
    assert [(i["id"], i["children"]) for i in passages] == [(family["parent"], 2)]
