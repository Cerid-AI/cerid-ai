# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for GET /graph/map (Cartographer Phase 0)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---- helpers ---------------------------------------------------------------

_SAMPLE_ARTIFACT = {
    "communities": [
        {
            "id": "comm1",
            "count": 5,
            "hull": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
            "anchor": [0.5, 0.5],
            "label": "Top Entity",
            "top_hubs": [{"id": "a", "name": "Top Entity", "degree": 12}],
            "trust_mix": {"verified": 3, "partial": 1, "unverified": 0, "unknown": 1},
        }
    ],
    "silhouette": 0.42,
    "computed_at": "2026-06-09T00:00:00+00:00",
}


def _make_row(eid: str, *, has_coords: bool = True) -> dict:
    return {
        "id": eid,
        "name": f"Entity {eid}",
        "type": "Person",
        "community": "comm1",
        "mention_count": 5,
        "trust_state": "verified",
        "x": 1.0 if has_coords else None,
        "y": 2.0 if has_coords else None,
        "z": 0.5 if has_coords else None,
        "method": "force",
        "computed_at": "2026-06-09T00:00:00+00:00",
    }


# ---- fixtures --------------------------------------------------------------


@pytest.fixture
def mock_redis():
    state: dict[str, bytes | str] = {}
    fake = MagicMock()
    fake.get = lambda k: state.get(k)

    def _set(k, v, ex=None):
        state[k] = v
        return True

    fake.set = _set
    fake._state = state
    return fake


@pytest.fixture
def fake_driver_factory():
    """Return a factory that creates a fake Neo4j driver dispatching on query content."""

    def _make(entity_rows: list[dict], edge_rows: list[dict] | None = None):
        fake_driver = MagicMock()
        fake_session = MagicMock()
        fake_session.__enter__ = lambda self: self
        fake_session.__exit__ = lambda self, exc_type, exc, tb: None

        def _run(query, **_kwargs):
            result = MagicMock()
            if "count(e)" in query:
                # isolated_count subquery
                result.data = lambda: [{"isolated_count": 0}]
            elif ")-[r:" in query:
                # link query: MATCH (a:Entity)-[r:CO_MENTIONED|SIMILAR_TO]->
                result.data = lambda: edge_rows or []
            else:
                # entity rows query: MATCH (e:Entity)
                result.data = lambda: entity_rows
            return result

        fake_session.run = _run
        fake_driver.session = lambda: fake_session
        return fake_driver

    return _make


@pytest.fixture
def client_factory(mock_redis):
    from app.routers import graph as graph_router

    def _make(driver):
        app = FastAPI()
        app.include_router(graph_router.router)
        ctx = patch("app.routers.graph.get_redis", return_value=mock_redis), \
              patch("app.routers.graph.get_neo4j", return_value=driver)
        return TestClient(app), ctx

    return _make


# ---- tests -----------------------------------------------------------------


def test_map_returns_200_with_entities_links_communities(mock_redis, fake_driver_factory):
    """Basic smoke: 200, payload structure, community artifacts decoded."""
    from app.routers import graph as graph_router

    driver = fake_driver_factory(
        entity_rows=[_make_row("a"), _make_row("b")],
        edge_rows=[{"s": "a", "t": "b", "w": 3.0}],
    )
    # Pre-seed community artifact.
    mock_redis._state["cerid:graph:map:communities"] = json.dumps(_SAMPLE_ARTIFACT)

    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get("/graph/map")

    assert r.status_code == 200
    payload = r.json()
    assert payload["count"] == 2
    assert {e["id"] for e in payload["entities"]} == {"a", "b"}
    assert payload["links"] == [[0, 1, 3.0, "co_mention"]]
    assert len(payload["communities"]) == 1
    comm = payload["communities"][0]
    assert comm["id"] == "comm1"
    assert comm["label"] == "Top Entity"
    assert comm["silhouette"] if "silhouette" in comm else payload["silhouette"] == pytest.approx(0.42)
    assert payload["cached"] is False


def test_map_missing_artifact_degrades_to_empty_communities(mock_redis, fake_driver_factory):
    """No community artifact in Redis → communities=[], silhouette=None, 200."""
    from app.routers import graph as graph_router

    driver = fake_driver_factory(entity_rows=[_make_row("x")])
    # Do NOT seed the community artifact key.

    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get("/graph/map")

    assert r.status_code == 200
    payload = r.json()
    assert payload["communities"] == []
    assert payload["silhouette"] is None


def test_map_second_call_is_cached(mock_redis, fake_driver_factory):
    """Second identical call is served from Redis with cached=True."""
    from app.routers import graph as graph_router

    driver = fake_driver_factory(entity_rows=[_make_row("a")])

    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        tc = TestClient(app)
        r1 = tc.get("/graph/map")
        assert r1.json()["cached"] is False

        # Mutate neo4j rows — second call should still get cached payload.
        driver.session().__enter__().run = lambda *a, **k: MagicMock(data=lambda: [])
        r2 = tc.get("/graph/map")

    assert r2.status_code == 200
    assert r2.json()["cached"] is True
    assert r2.json()["count"] == 1  # from cache, not empty rows


def test_map_corrupt_artifact_json_degrades_gracefully(mock_redis, fake_driver_factory):
    """Corrupt community JSON → communities=[], not 500."""
    from app.routers import graph as graph_router

    driver = fake_driver_factory(entity_rows=[_make_row("a")])
    mock_redis._state["cerid:graph:map:communities"] = "not-json{"

    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get("/graph/map")

    assert r.status_code == 200
    assert r.json()["communities"] == []


def test_map_corrupt_cache_falls_through_to_neo4j(mock_redis, fake_driver_factory):
    """Corrupt response cache → fall through to Neo4j fetch, return fresh data."""
    from app.routers import graph as graph_router

    driver = fake_driver_factory(entity_rows=[_make_row("z")])
    # Plant corrupt response in the map cache slot.
    mock_redis._state["cerid:graph:emb3d:v2:map"] = "not-json{"

    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get("/graph/map")

    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert r.json()["cached"] is False
