# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Deleting a conversation deletes the transcript artifacts it produced.

Every chat turn is ingested as its own artifact in the ``conversations``
domain (``filename=chat_{cid[:8]}_{ts}``, ``conversation_id`` on each chunk).
``DELETE /user-state/conversations/{id}`` used to unlink only the sync file,
leaving those turns retrievable. It now goes through the content lifecycle:
Neo4j node, Chroma rows (parents included), HyPE questions, lexical postings
and a tombstone, idempotent when the artifacts are already gone.

Chroma is a real in-process client; Neo4j and Redis are stand-ins.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from app.routers.user_state import router
from app.sync.user_state import list_conversation_ids
from core.retrieval import bm25, sparse_index
from tests.helpers.fake_neo4j import _FakeNeo4jDriver
from tests.test_artifact_row_removal import _seed, _seed_hype, _Store

CONV = "conv-abcdef0123"
OTHER_CONV = "conv-other"
TURN_A = "a" * 64
TURN_B = "b" * 64
MEMORY = "d" * 64
OTHER_TURN = "e" * 64


@pytest.fixture
def store():
    s = _Store()
    s.clear()
    yield s
    s.clear()


@pytest.fixture
def lexical(monkeypatch) -> dict[str, list[str]]:
    """Record what the lexical indexes are asked to drop; keep caches off Redis."""
    removed: dict[str, list[str]] = {"bm25": [], "sparse": []}
    monkeypatch.setattr(bm25, "remove_chunks", lambda domain, ids: removed["bm25"].extend(ids) or len(ids))
    monkeypatch.setattr(
        sparse_index, "remove_chunks", lambda domain, ids: removed["sparse"].extend(ids) or len(ids),
    )
    monkeypatch.setattr("app.services.content_lifecycle.invalidate_caches", lambda **_: None)
    return removed


@pytest.fixture
def neo4j() -> _FakeNeo4jDriver:
    return _FakeNeo4jDriver()


@pytest.fixture
def client(tmp_path: Path, store: _Store, neo4j: _FakeNeo4jDriver, lexical) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    redis = MagicMock()
    redis.get.return_value = None
    with patch("app.routers.user_state._sync_dir", return_value=str(tmp_path)), \
         patch("app.deps.get_redis", return_value=redis), \
         patch("app.deps.get_chroma", return_value=store), \
         patch("app.deps.get_neo4j", return_value=neo4j), \
         patch("app.services.private_mode.get_private_mode_level", lambda: 0):
        yield TestClient(app)


def _collections(store: _Store) -> tuple[Any, Any]:
    name = config.collection_name("conversations")
    return store.get_or_create_collection(name), store.get_or_create_collection(name + "_hype")


def _seed_conversation(store: _Store, neo4j: _FakeNeo4jDriver) -> dict[str, list[str]]:
    """Two turns of CONV, one memory extracted from it, and a turn of another
    conversation, written the way their ingest jobs write them."""
    col, hype = _collections(store)
    children: dict[str, list[str]] = {}
    for aid, cid, filename in (
        (TURN_A, CONV, f"chat_{CONV[:8]}_20261005_120000"),
        (TURN_B, CONV, f"chat_{CONV[:8]}_20261005_120100"),
        (MEMORY, CONV, f"memory_fact_{CONV[:8]}_20261005_120200_0"),
        (OTHER_TURN, OTHER_CONV, f"chat_{OTHER_CONV[:8]}_20261005_130000"),
    ):
        children[aid] = _seed(col, aid, conversation_id=cid, filename=filename)
        _seed_hype(hype, aid, children[aid])
        neo4j.add_artifact(aid, chunk_ids=children[aid], domain="conversations", filename=filename)
    return children


def _rows(col: Any, aid: str) -> list[str]:
    return col.get(where={"artifact_id": aid})["ids"]


def test_delete_removes_every_transcript_artifact_of_the_conversation(
    client: TestClient, store: _Store, neo4j: _FakeNeo4jDriver, lexical, tmp_path: Path,
):
    children = _seed_conversation(store, neo4j)
    col, hype = _collections(store)
    client.post("/user-state/conversations", json={"id": CONV, "title": "Figures"})

    resp = client.delete(f"/user-state/conversations/{CONV}")

    assert resp.status_code == 200
    assert resp.json() == {"deleted": CONV}
    assert list_conversation_ids(str(tmp_path)) == []

    for aid in (TURN_A, TURN_B):
        assert aid not in neo4j.nodes
        assert _rows(col, aid) == []
        assert hype.get(where={"source_artifact_id": aid})["ids"] == []
        assert set(children[aid]) <= set(lexical["bm25"])
        assert set(children[aid]) <= set(lexical["sparse"])
    assert col.get(where={"conversation_id": CONV})["ids"] == _rows(col, MEMORY)

    tombstoned = [
        json.loads(line)["artifact_id"]
        for line in Path(config.TOMBSTONE_LOG_PATH).read_text(encoding="utf-8").splitlines()
    ]
    assert sorted(tombstoned) == sorted([TURN_A, TURN_B])

    # Memories extracted from the conversation and other conversations' turns stay.
    for aid in (MEMORY, OTHER_TURN):
        assert aid in neo4j.nodes
        assert len(_rows(col, aid)) == 3
        assert len(hype.get(where={"source_artifact_id": aid})["ids"]) == 2


def test_second_delete_is_not_an_error(
    client: TestClient, store: _Store, neo4j: _FakeNeo4jDriver, lexical,
):
    _seed_conversation(store, neo4j)
    client.post("/user-state/conversations", json={"id": CONV, "title": "Figures"})
    assert client.delete(f"/user-state/conversations/{CONV}").status_code == 200

    resp = client.delete(f"/user-state/conversations/{CONV}")

    assert resp.status_code == 200
    assert resp.json() == {"deleted": CONV}
    assert TURN_A not in neo4j.nodes and TURN_B not in neo4j.nodes
    assert OTHER_TURN in neo4j.nodes


def test_delete_of_a_conversation_that_was_never_ingested(client: TestClient, store: _Store):
    client.post("/user-state/conversations", json={"id": CONV, "title": "Never sent"})

    resp = client.delete(f"/user-state/conversations/{CONV}")

    assert resp.status_code == 200
    assert resp.json() == {"deleted": CONV}
