# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Tests for GET /graph/community-hierarchy."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _hierarchy_rows():
    # Two level-0 communities, one level-1 parent.
    return [
        {"community_id": "0:1", "level": 0, "parent_id": "1:7", "member_count": 12, "summary": "Infra", "top_terms": ["kubernetes", "cluster", "helm"]},
        {"community_id": "0:2", "level": 0, "parent_id": "1:7", "member_count": 8, "summary": "Models", "top_terms": None},
        {"community_id": "1:7", "level": 1, "parent_id": None, "member_count": 20, "summary": "Platform", "top_terms": ["platform"]},
    ]


def _make_driver(rows):
    driver = MagicMock()
    session = MagicMock()
    session.__enter__ = lambda self: self
    session.__exit__ = lambda self, exc_type, exc, tb: None
    session.run = lambda *a, **k: MagicMock(__iter__=lambda self: iter([dict(r) for r in rows]))
    driver.session = lambda: session
    return driver


def _make_redis():
    state: dict[str, str] = {}
    fake = MagicMock()
    fake.get = lambda k: state.get(k)

    def _set(k, v, ex=None):
        state[k] = v
        return True

    fake.set = _set
    fake._state = state
    return fake


def test_community_hierarchy_200_shape():
    from app.routers import graph as graph_router

    app = FastAPI()
    app.include_router(graph_router.router)
    redis = _make_redis()
    driver = _make_driver(_hierarchy_rows())
    with patch("app.routers.graph.get_redis", return_value=redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get("/graph/community-hierarchy")

    assert r.status_code == 200
    payload = r.json()
    assert payload["levels"] >= 2
    ids = {n["community_id"]: n for n in payload["nodes"]}
    assert ids["0:1"]["parent_id"] == "1:7"
    assert ids["1:7"]["parent_id"] is None
    assert ids["0:1"]["member_count"] == 12
    # A3: c-TF-IDF top_terms surface additively (None when not yet computed).
    assert ids["0:1"]["top_terms"] == ["kubernetes", "cluster", "helm"]
    assert ids["0:2"]["top_terms"] is None
    # Cache key carries the v2 suffix (schema gained top_terms).
    assert "cerid:graph:community-hierarchy:v2" in redis._state


def test_community_hierarchy_served_from_cache_on_second_call():
    from app.routers import graph as graph_router

    app = FastAPI()
    app.include_router(graph_router.router)
    redis = _make_redis()
    redis._state["cerid:graph:community-hierarchy:v2"] = json.dumps(
        {"levels": 1, "nodes": [{"community_id": "0:9", "level": 0, "parent_id": None, "member_count": 3, "summary": None}]}
    )
    driver = _make_driver([])  # would yield nothing if hit
    with patch("app.routers.graph.get_redis", return_value=redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get("/graph/community-hierarchy")

    assert r.status_code == 200
    assert r.json()["nodes"][0]["community_id"] == "0:9"
