# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for GET /graph/embeddings/3d (Phase B Day 3)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def mock_redis():
    state: dict[str, str] = {}
    fake = MagicMock()
    fake.get = lambda k: state.get(k)
    def _set(k, v, ex=None, nx=False):
        if nx and k in state:
            return None
        state[k] = v
        return True
    fake.set = _set
    fake._state = state
    return fake


@pytest.fixture
def mock_neo4j_with_rows():
    """Returns (driver, set_rows) — set_rows lets each test inject the
    rows Neo4j should "return" from session.run()."""
    rows: list[list[dict]] = [[]]

    fake_driver = MagicMock()
    fake_session = MagicMock()
    fake_session.__enter__ = lambda self: self
    fake_session.__exit__ = lambda self, exc_type, exc, tb: None

    def _run(query, **_kwargs):
        result = MagicMock()
        if "count(e)" in query:
            # isolated-count subquery — always return 0 for unit tests
            result.data = lambda: [{"isolated_count": 0}]
        elif ")-[r:" in query:
            # link query — return empty for generic fixtures
            result.data = lambda: []
        else:
            result.data = lambda: rows[0]
        return result

    fake_session.run = _run
    fake_driver.session = lambda: fake_session

    def set_rows(new_rows):
        rows[0] = new_rows

    return fake_driver, set_rows


@pytest.fixture
def client(mock_redis, mock_neo4j_with_rows):
    from app.routers import graph as graph_router

    driver, _ = mock_neo4j_with_rows
    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        yield TestClient(app)


def _make_row(entity_id: str, *, has_coords: bool = True, method: str = "fallback") -> dict:
    return {
        "id": entity_id,
        "name": f"Entity {entity_id}",
        "type": "Person",
        "community": "c1",
        "mention_count": 10,
        "trust_state": "verified",
        "x": 1.0 if has_coords else None,
        "y": 2.0 if has_coords else None,
        "z": 3.0 if has_coords else None,
        "method": method,
        "computed_at": "2026-05-21T08:00:00+00:00",
        "created_at": "2026-05-01T00:00:00+00:00",
    }


def test_embeddings_3d_returns_projected_entities(client, mock_neo4j_with_rows):
    _, set_rows = mock_neo4j_with_rows
    set_rows([_make_row("a"), _make_row("b")])

    r = client.get("/graph/embeddings/3d")
    assert r.status_code == 200
    payload = r.json()
    assert payload["count"] == 2
    assert {e["id"] for e in payload["entities"]} == {"a", "b"}
    for e in payload["entities"]:
        assert e["x"] == 1.0 and e["y"] == 2.0 and e["z"] == 3.0
        assert e["projection"] == "fallback"
        # created_at (entity birth timestamp) drives the timebar/timelapse.
        assert e["created_at"] == "2026-05-01T00:00:00+00:00"
    assert payload["cached"] is False
    assert payload["computed_at"] == "2026-05-21T08:00:00+00:00"


def test_embeddings_3d_created_at_null_when_missing(client, mock_neo4j_with_rows):
    """created_at is present but may be null when the Entity has no birth timestamp."""
    _, set_rows = mock_neo4j_with_rows
    row = _make_row("a")
    row["created_at"] = None
    set_rows([row])

    r = client.get("/graph/embeddings/3d")
    assert r.status_code == 200
    entity = r.json()["entities"][0]
    assert "created_at" in entity
    assert entity["created_at"] is None


def test_embeddings_3d_drops_rows_missing_coords(client, mock_neo4j_with_rows):
    _, set_rows = mock_neo4j_with_rows
    set_rows([
        _make_row("a", has_coords=True),
        _make_row("b", has_coords=False),
    ])
    r = client.get("/graph/embeddings/3d")
    payload = r.json()
    assert payload["count"] == 1
    assert payload["entities"][0]["id"] == "a"


def test_embeddings_3d_uses_cache_on_second_call(client, mock_neo4j_with_rows, mock_redis):
    _, set_rows = mock_neo4j_with_rows
    set_rows([_make_row("a")])

    r1 = client.get("/graph/embeddings/3d")
    assert r1.status_code == 200
    assert r1.json()["cached"] is False

    # Mutate rows — second response should still come from cache
    set_rows([])
    r2 = client.get("/graph/embeddings/3d")
    payload = r2.json()
    assert payload["cached"] is True
    assert payload["count"] == 1  # cached payload, not the now-empty rows


def test_embeddings_3d_filter_changes_cache_key(client, mock_neo4j_with_rows):
    _, set_rows = mock_neo4j_with_rows
    set_rows([_make_row("a")])

    client.get("/graph/embeddings/3d")  # warms unfiltered key
    set_rows([_make_row("b")])
    r = client.get("/graph/embeddings/3d?filter=Person")
    payload = r.json()
    assert payload["cached"] is False  # filter changes key → fresh fetch
    assert payload["entities"][0]["id"] == "b"


def test_embeddings_3d_corrupt_cache_falls_through_to_neo4j(client, mock_neo4j_with_rows, mock_redis):
    _, set_rows = mock_neo4j_with_rows
    set_rows([_make_row("a")])
    # Plant corrupt JSON into the cache slot (v6 key — payload carries links + isolated_count)
    mock_redis._state["cerid:graph:emb3d:v6:all"] = "not-json{"

    r = client.get("/graph/embeddings/3d")
    assert r.status_code == 200
    assert r.json()["cached"] is False
    assert r.json()["count"] == 1


def test_embeddings_3d_filter_threads_to_query(client, mock_neo4j_with_rows):
    _, set_rows = mock_neo4j_with_rows
    set_rows([_make_row("a")])
    r = client.get("/graph/embeddings/3d?filter=Person")
    assert r.status_code == 200
    assert r.json()["count"] == 1


def test_embeddings_3d_entity_whitelist(client, mock_neo4j_with_rows):
    _, set_rows = mock_neo4j_with_rows
    set_rows([_make_row("a"), _make_row("b")])
    r = client.get("/graph/embeddings/3d?entities=a,b")
    assert r.status_code == 200
    payload = r.json()
    assert payload["count"] == 2


def test_embeddings_3d_links_are_index_triples(mock_redis):
    """links = [sourceIdx, targetIdx, weight, kind] 4-tuples indexing into entities;
    edges referencing out-of-scope ids and self-loops are dropped."""
    from app.routers import graph as graph_router

    fake_driver = MagicMock()
    fake_session = MagicMock()
    fake_session.__enter__ = lambda self: self
    fake_session.__exit__ = lambda self, exc_type, exc, tb: None

    def _run(query, **_kwargs):
        result = MagicMock()
        if "count(e)" in query:
            # isolated-count subquery
            result.data = lambda: [{"isolated_count": 0}]
        elif ")-[r:" in query:
            # link query: MATCH (a:Entity)-[r:CO_MENTIONED|SIMILAR_TO]->
            result.data = lambda: [
                {"s": "a", "t": "b", "w": 5.0, "kind": "CO_MENTIONED"},
                {"s": "b", "t": "c", "w": 2.0, "kind": "CO_MENTIONED"},
                {"s": "a", "t": "ghost", "w": 9.0, "kind": "CO_MENTIONED"},  # out of scope — dropped
                {"s": "c", "t": "c", "w": 3.0, "kind": "CO_MENTIONED"},      # self-loop — dropped
            ]
        else:
            # entity rows query
            result.data = lambda: [_make_row("a"), _make_row("b"), _make_row("c")]
        return result

    fake_session.run = _run
    fake_driver.session = lambda: fake_session

    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=fake_driver):
        r = TestClient(app).get("/graph/embeddings/3d")

    assert r.status_code == 200
    payload = r.json()
    assert payload["count"] == 3
    assert payload["links"] == [[0, 1, 5.0, "co_mention"], [1, 2, 2.0, "co_mention"]]


def test_embeddings_3d_links_failure_does_not_break_nodes(mock_redis):
    """A failed edge query degrades to links=[] — never a 500."""
    from app.routers import graph as graph_router

    fake_driver = MagicMock()
    fake_session = MagicMock()
    fake_session.__enter__ = lambda self: self
    fake_session.__exit__ = lambda self, exc_type, exc, tb: None

    def _run(query, **_kwargs):
        if "count(e)" in query:
            result = MagicMock()
            result.data = lambda: [{"isolated_count": 0}]
            return result
        if ")-[r:" in query:
            # link query — simulate failure
            raise RuntimeError("edge query down")
        result = MagicMock()
        result.data = lambda: [_make_row("a")]
        return result

    fake_session.run = _run
    fake_driver.session = lambda: fake_session

    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=fake_driver):
        r = TestClient(app).get("/graph/embeddings/3d")

    assert r.status_code == 200
    payload = r.json()
    assert payload["count"] == 1
    assert payload["links"] == []
