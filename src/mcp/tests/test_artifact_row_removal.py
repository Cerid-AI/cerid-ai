# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Removing or replacing an artifact removes every Chroma row it owns.

With parent-child retrieval on, an artifact's Neo4j ``chunk_ids`` lists only its
child chunks. Its parent chunks sit beside them in the same collection, and its
HyPE questions sit in the ``_hype`` companion collection; neither is listed
anywhere on the node. A removal that walks ``chunk_ids`` alone leaves both
behind, and the parent chunk still answers queries about deleted content.

Chroma here is a real in-process client, so every ``where`` clause and delete is
evaluated by Chroma itself. Neo4j, Redis and the lexical indexes are stand-ins:
they are not where this behaviour lives.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any
from unittest.mock import MagicMock, patch

import chromadb
import pytest

import app.services.ingestion as ingestion
import config
import utils.chunker as chunker
from app.db.neo4j.artifacts import delete_artifact
from app.routers import artifacts, kb_admin, memories
from app.services import content_lifecycle
from core.agents.maintenance import purge_artifacts
from core.agents.rectify import find_orphaned_chunks, rectify, resolve_duplicates
from core.retrieval import bm25, sparse_index
from tests.helpers.fake_neo4j import _FakeNeo4jDriver

A = "a" * 64
B = "b" * 64
C = "c" * 64
MEMORY = "m" * 36


class _WordEmbedding(chromadb.EmbeddingFunction):
    """Deterministic bag-of-words vectors; no model download."""

    def __init__(self) -> None:
        pass

    @staticmethod
    def name() -> str:
        return "artifact_row_removal_test_words"

    def get_config(self) -> dict[str, Any]:
        return {}

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> _WordEmbedding:
        return _WordEmbedding()

    def __call__(self, input: Any) -> Any:  # noqa: A002 — Chroma's parameter name
        vectors = []
        for text in input:
            vec = [0.0] * 16
            for word in str(text).lower().split():
                vec[int(hashlib.sha256(word.encode()).hexdigest(), 16) % 16] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            vectors.append([v / norm for v in vec])
        return vectors


class _Store:
    """The in-process Chroma client, serving its collections with a model-free
    embedding function. Collection names are the production ones."""

    def __init__(self) -> None:
        self._real = chromadb.EphemeralClient()

    def get_or_create_collection(self, name: str, **_: Any) -> Any:
        return self._real.get_or_create_collection(name, embedding_function=_WordEmbedding())

    def get_collection(self, name: str, **_: Any) -> Any:
        return self._real.get_collection(name, embedding_function=_WordEmbedding())

    def list_collections(self) -> list[Any]:
        return list(self._real.list_collections())

    def delete_collection(self, name: str) -> None:
        self._real.delete_collection(name)

    def names(self) -> list[str]:
        return sorted(c.name for c in self.list_collections() if c.name.startswith("domain_"))

    def clear(self) -> None:
        for name in self.names():
            self.delete_collection(name)


@pytest.fixture
def store():
    """The client is shared by the whole test process; these tests own every
    ``domain_*`` collection in it while they run."""
    s = _Store()
    s.clear()
    yield s
    s.clear()


@pytest.fixture(autouse=True)
def quiet_side_stores(monkeypatch):
    """The lexical indexes and the caches are not under test here; keep them
    off disk and off Redis."""

    for module in (bm25, sparse_index):
        monkeypatch.setattr(module, "remove_chunks", lambda domain, ids: 0)
        monkeypatch.setattr(module, "index_chunks", lambda domain, ids, texts: None)
    monkeypatch.setattr("app.services.content_lifecycle.invalidate_caches", lambda **_: None)
    monkeypatch.setattr("utils.query_cache.invalidate_query_caches", lambda *a, **k: None)
    monkeypatch.setattr("utils.query_cache.invalidate_query_caches_threaded", lambda *a, **k: None)


def _coll(store: _Store, domain: str) -> Any:
    return store.get_or_create_collection(config.collection_name(domain))


def _hype(store: _Store, domain: str) -> Any:
    return store.get_or_create_collection(config.collection_name(domain) + "_hype")


def _seed(col: Any, aid: str, *, parents: int = 1, children: int = 2, **meta: Any) -> list[str]:
    """Write one artifact the way a parent-child ingest does; return the child
    ids, which are all its Neo4j node records."""
    ids, docs, metas = [], [], []
    for p in range(parents):
        pid = f"{aid}_parent_{p}"
        ids.append(pid)
        docs.append(f"parent {p} text of {aid[:4]}")
        metas.append({"artifact_id": aid, "chunk_level": "parent", "parent_chunk_id": "", **meta})
        for c in range(children):
            ids.append(f"{aid}_child_{p}_{c}")
            docs.append(f"child {p} {c} text of {aid[:4]}")
            metas.append({"artifact_id": aid, "chunk_level": "child", "parent_chunk_id": pid, **meta})
    col.add(ids=ids, documents=docs, metadatas=metas)
    return [i for i in ids if "_child_" in i]


def _seed_hype(col: Any, aid: str, chunk_ids: list[str]) -> None:
    col.add(
        ids=[f"{cid}_hype_0" for cid in chunk_ids],
        documents=[f"what does {cid} say?" for cid in chunk_ids],
        metadatas=[{"source_artifact_id": aid, "source_chunk_id": cid} for cid in chunk_ids],
    )


def _ids(col: Any) -> set[str]:
    return set(col.get()["ids"])


def _rows_of(col: Any, aid: str) -> set[str]:
    return {i for i in _ids(col) if i.startswith(f"{aid}_")}


# ── delete ───────────────────────────────────────────────────────────────────


def test_deleting_an_artifact_leaves_none_of_its_rows(store):

    col, hype = _coll(store, "general"), _hype(store, "general")
    a_children = _seed(col, A)
    b_children = _seed(col, B)
    _seed_hype(hype, A, a_children)
    _seed_hype(hype, B, b_children)
    b_rows, b_hype = _rows_of(col, B), _rows_of(hype, B)
    neo4j = _FakeNeo4jDriver().add_artifact(A, chunk_ids=a_children, domain="general")

    result = content_lifecycle.remove_content(A, neo4j=neo4j, chroma=store, redis=MagicMock())

    assert result.found
    assert col.get(where={"artifact_id": A})["ids"] == []
    assert hype.get(where={"source_artifact_id": A})["ids"] == []
    assert _ids(col) == b_rows
    assert _ids(hype) == b_hype


def test_a_row_that_names_the_artifact_but_belongs_to_another_survives(store):
    """Caller metadata can carry an ``artifact_id`` key; every id an ingest
    writes starts with the artifact's own id, so that is what decides
    ownership."""

    col = _coll(store, "general")
    a_children = _seed(col, A)
    col.add(ids=[f"{B}_child_0_0"], documents=["other"], metadatas=[{"artifact_id": A}])
    neo4j = _FakeNeo4jDriver().add_artifact(A, chunk_ids=a_children, domain="general")

    content_lifecycle.remove_content(A, neo4j=neo4j, chroma=store, redis=MagicMock())

    assert _ids(col) == {f"{B}_child_0_0"}


def test_deleting_an_email_removes_its_attachments_parent_chunks(store, monkeypatch):

    col = _coll(store, "general")
    a_children = _seed(col, A)
    c_children = _seed(col, C)
    _seed(col, B)
    b_rows = _rows_of(col, B)
    monkeypatch.setattr(
        "app.db.neo4j.artifacts.delete_artifact",
        lambda driver, aid: {
            "deleted": True, "artifact_id": aid, "domain": "general", "filename": "mail.eml",
            "chunk_ids": a_children + c_children, "attachment_ids": [C],
        },
    )

    content_lifecycle.remove_content(A, neo4j=object(), chroma=store, redis=MagicMock())

    assert _ids(col) == b_rows


def test_delete_artifact_reports_the_attachment_ids_it_cascades_to():

    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__ = MagicMock(return_value=session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    session.run.return_value.single.return_value = {
        "chunk_ids": '["p1"]', "domain": "mail", "filename": None,
        "child_chunk_ids": ['["c1"]'], "attachment_ids": ["att-1"],
    }

    result = delete_artifact(driver, "email-1")

    assert result["attachment_ids"] == ["att-1"]
    assert "attachment_ids" in " ".join(session.run.call_args_list[0].args[0].split())


# ── replace ──────────────────────────────────────────────────────────────────


@pytest.fixture
def reingest(store, monkeypatch):

    monkeypatch.setattr(chunker, "PARENT_CHILD_ENABLED", True)
    monkeypatch.setattr(config, "ENABLE_CONTEXTUAL_CHUNKS", False, raising=False)
    monkeypatch.setattr(ingestion, "get_chroma", lambda: store)
    monkeypatch.setattr(ingestion, "get_neo4j", lambda: object())
    monkeypatch.setattr(ingestion, "get_redis", lambda: object())
    monkeypatch.setattr(ingestion, "_enqueue_entity_extraction_if_enabled", lambda **_: None)

    def run(prev: dict[str, Any], content: str, content_hash: str) -> dict[str, Any]:
        with patch("app.services.ingestion.graph"):
            return ingestion._reingest_artifact(
                prev, content, "general", {"filename": "note.md"}, content_hash,
            )

    return run


def test_reingest_leaves_no_row_of_the_old_content(store, reingest):

    col, hype = _coll(store, "general"), _hype(store, "general")
    old_children = _seed(col, A, parents=3)
    _seed_hype(hype, A, old_children)
    b_children = _seed(col, B)
    _seed_hype(hype, B, b_children)
    b_rows, b_hype = _rows_of(col, B), _rows_of(hype, B)
    prev = {"id": A, "content_hash": "old", "chunk_ids": json.dumps(old_children)}
    new_content = "a short revised note"

    result = reingest(prev, new_content, "new")

    assert result["status"] == "updated"
    expected = {c["chunk_id"] for c in chunker.chunk_with_parents(new_content, artifact_id=A)}
    assert _rows_of(col, A) == expected
    assert f"{A}_parent_2" not in _ids(col)
    assert hype.get(where={"source_artifact_id": A})["ids"] == []
    assert _rows_of(col, B) == b_rows
    assert _ids(hype) == b_hype


def test_force_reindex_of_unchanged_text_keeps_its_hype_questions(store, reingest):
    """The same text yields the same chunk ids, so its questions still hold and
    nothing on this path would generate them again."""
    col, hype = _coll(store, "general"), _hype(store, "general")
    old_children = _seed(col, A)
    _seed_hype(hype, A, old_children)
    a_hype = _rows_of(hype, A)
    prev = {"id": A, "content_hash": "same", "chunk_ids": json.dumps(old_children)}

    reingest(prev, "the same note", "same")

    assert _rows_of(hype, A) == a_hype


# ── the other removal paths ──────────────────────────────────────────────────


class _Graph:
    """Answers each query shape the core removal paths issue, by substring."""

    def __init__(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def session(self) -> _Graph:
        return self

    def __enter__(self) -> _Graph:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def run(self, query: str, **params: Any) -> Any:
        self.queries.append(query)
        for key, rows in self.rows.items():
            if key in query:
                result = MagicMock()
                result.__iter__ = lambda _self, rows=rows: iter(rows)
                result.single.return_value = rows[0] if rows else None
                return result
        result = MagicMock()
        result.__iter__ = lambda _self: iter([])
        result.single.return_value = None
        return result


def test_maintenance_purge_leaves_none_of_the_artifacts_rows(store):

    col, hype = _coll(store, "general"), _hype(store, "general")
    a_children = _seed(col, A)
    _seed_hype(hype, A, a_children)
    _seed(col, B)
    b_rows = _rows_of(col, B)
    graph = _Graph({"a.chunk_ids AS chunk_ids": [
        {"id": A, "filename": "a.md", "domain": "general", "chunk_ids": json.dumps(a_children)},
    ]})

    result = purge_artifacts(graph, store, [A])

    assert result["purged_count"] == 1
    assert _ids(col) == b_rows
    assert _ids(hype) == set()


def test_duplicate_resolution_leaves_none_of_the_removed_artifacts_rows(store):

    col, hype = _coll(store, "general"), _hype(store, "general")
    a_children = _seed(col, A)
    _seed_hype(hype, A, a_children)
    b_children = _seed(col, B)
    b_rows = _rows_of(col, B)
    graph = _Graph({"content_hash: $hash": [
        {"id": A, "filename": "a.md", "domain": "general", "chunk_ids": json.dumps(a_children)},
        {"id": B, "filename": "b.md", "domain": "general", "chunk_ids": json.dumps(b_children)},
    ]})

    result = resolve_duplicates(graph, store, "hash", keep_artifact_id=B)

    assert result["removed_count"] == 1
    assert _ids(col) == b_rows
    assert _ids(hype) == set()


def test_deleting_a_memory_leaves_none_of_its_rows(store, monkeypatch, tmp_path):
    """The pane's delete runs through the forget engine into remove_content."""
    from tests.helpers.forget import isolate_forget

    isolate_forget(monkeypatch, tmp_path)
    col, hype = _coll(store, "conversations"), _hype(store, "conversations")
    a_children = _seed(col, A)
    _seed_hype(hype, A, a_children)
    _seed(col, B)
    b_rows = _rows_of(col, B)
    graph = _Graph({
        "a.chunk_ids AS chunk_ids": [
            {"id": A, "chunk_ids": json.dumps(a_children), "filename": "memory_a", "domain": "conversations"},
        ],
        "STARTS WITH 'memory_'": [{"id": A, "filename": "memory_a"}],
    })
    monkeypatch.setattr(memories, "get_neo4j", lambda: graph)
    monkeypatch.setattr(memories, "get_redis", lambda: None)
    monkeypatch.setattr("app.deps.get_neo4j", lambda: graph)
    monkeypatch.setattr("app.deps.get_chroma", lambda: store)
    monkeypatch.setattr("app.deps.get_redis", lambda: MagicMock())

    asyncio.run(memories.delete_memory(A))

    assert _ids(col) == b_rows
    assert _ids(hype) == set()


def test_recategorizing_moves_the_parent_chunks_with_the_children(store, monkeypatch):

    old, hype = _coll(store, "general"), _hype(store, "general")
    a_children = _seed(old, A)
    _seed_hype(hype, A, a_children)
    a_rows = _rows_of(old, A)
    _seed(old, B)
    b_rows = _rows_of(old, B)
    fake_graph = MagicMock()
    fake_graph.get_artifact.return_value = {
        "domain": "general", "chunk_ids": json.dumps(a_children), "filename": "a.md",
    }
    monkeypatch.setattr(artifacts, "graph", fake_graph)
    monkeypatch.setattr(artifacts, "get_neo4j", lambda: object())
    monkeypatch.setattr(artifacts, "get_chroma", lambda: store)
    monkeypatch.setattr(artifacts, "get_redis", lambda: None)

    artifacts.recategorize(A, "finance")

    assert _ids(old) == b_rows
    assert _rows_of(_coll(store, "finance"), A) == a_rows
    assert hype.get(where={"source_artifact_id": A})["ids"] == []


def test_clearing_a_domain_drops_its_hype_collection_too(store, monkeypatch):

    _seed(_coll(store, "general"), A)
    _seed_hype(_hype(store, "general"), A, [f"{A}_child_0_0"])
    monkeypatch.setattr(kb_admin, "get_neo4j", lambda: object())
    monkeypatch.setattr(kb_admin, "get_chroma", lambda: store)
    monkeypatch.setattr(
        kb_admin, "delete_artifacts_by_domain", lambda driver, domain: {"deleted": 1, "chunks": 2},
    )
    monkeypatch.setattr(kb_admin, "_invalidate_scoped_safe", lambda *a, **k: None)
    monkeypatch.setattr(kb_admin.audit_log, "audit", lambda *a, **k: None)

    asyncio.run(kb_admin.clear_domain("general", kb_admin.ClearDomainRequest(confirm=True)))

    assert config.collection_name("general") not in store.names()
    assert config.collection_name("general") + "_hype" not in store.names()


# ── the repair tool ──────────────────────────────────────────────────────────


@pytest.fixture
def leftovers(store):
    """What the live stack held on 2026-10-01: a deleted artifact's parent chunk
    and HyPE questions, beside a live artifact and a verified memory."""
    general, general_hype = _coll(store, "general"), _hype(store, "general")
    general.add(
        ids=[f"{A}_parent_0"], documents=["deleted parent"],
        metadatas=[{"artifact_id": A, "chunk_level": "parent", "filename": "gone.md"}],
    )
    _seed_hype(general_hype, A, [f"{A}_child_0_0"])
    b_children = _seed(general, B)
    _seed_hype(general_hype, B, b_children)
    conversations = _coll(store, "conversations")
    conversations.add(
        ids=[f"verified_memory_{MEMORY}"], documents=["a verified claim"],
        metadatas=[{
            "artifact_id": MEMORY, "memory_type": "empirical",
            "memory_source_type": "verification", "domain": "conversations",
        }],
    )
    _seed_hype(_hype(store, "conversations"), MEMORY, [f"verified_memory_{MEMORY}"])
    return _Graph({"MATCH (a:Artifact) RETURN a.id AS id": [{"id": B}]})


ORPHANS = {f"{A}_parent_0", f"{A}_child_0_0_hype_0"}


def test_the_orphan_finder_lists_exactly_the_deleted_artifacts_rows(store, leftovers):

    found = find_orphaned_chunks(leftovers, store)

    assert {c["chunk_id"] for rows in found.values() for c in rows} == ORPHANS


def test_the_rectify_dry_run_reports_the_orphans_and_deletes_nothing(store, leftovers):

    before = {n: _ids(store.get_collection(n)) for n in store.names()}

    report = asyncio.run(rectify(leftovers, store, checks=["orphans"], auto_fix=False))

    assert report["findings"]["orphans"]["count"] == len(ORPHANS)
    assert {c["chunk_id"] for c in report["findings"]["orphans"]["chunks"]} == ORPHANS
    assert {n: _ids(store.get_collection(n)) for n in store.names()} == before


def test_the_rectify_apply_deletes_only_the_orphans(store, leftovers):

    before = {n: _ids(store.get_collection(n)) for n in store.names()}

    asyncio.run(rectify(leftovers, store, checks=["orphans"], auto_fix=True))

    after = {n: _ids(store.get_collection(n)) for n in store.names()}
    assert {n: before[n] - ORPHANS for n in before} == after
