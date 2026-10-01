# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The /graph/neighborhood type filter must narrow the neighbours.

The filter used to read ``e.entity_type = $filter OR n.entity_type = $filter``.
``n`` is the focal entity, the same node on every row, so whenever the focal
entity had the filtered type the predicate was true for every neighbour and
the filter selected nothing.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def session(monkeypatch):
    fake_session = MagicMock()
    fake_session.__enter__ = lambda self: self
    fake_session.__exit__ = lambda self, exc_type, exc, tb: None
    fake_session.run.return_value.data.return_value = [
        {
            "id": "asset:sol",
            "name": "SOL",
            "type": "ASSET",
            "community": None,
            "mention_count": 3,
            "trust_state": None,
            "recency_score": None,
            "primary_domain": None,
            "edges": [],
            "truncated": False,
            "degree_co": 2,
        },
    ]
    fake_driver = MagicMock()
    fake_driver.session = lambda: fake_session

    import app.routers.graph as graph_router

    monkeypatch.setattr(graph_router, "get_neo4j", lambda: fake_driver)
    monkeypatch.setattr(graph_router, "get_redis", lambda: None)
    return fake_session


def _filter_predicates(cypher: str) -> list[str]:
    return [line.strip() for line in cypher.splitlines() if "$filter" in line]


def test_type_filter_does_not_test_the_focal_entity(session):
    from app.routers.graph import router

    app = FastAPI()
    app.include_router(router)
    res = TestClient(app).get(
        "/graph/neighborhood?entity=asset:sol&hops=1&filter=ASSET",
    )
    assert res.status_code == 200

    cypher = session.run.call_args.args[0]
    assert session.run.call_args.kwargs["filter"] == "ASSET"
    predicates = _filter_predicates(cypher)
    assert predicates, "the filter was requested but never reached the query"
    for predicate in predicates:
        assert "e.entity_type = $filter" in predicate
        assert "n.entity_type" not in predicate, (
            "the filter compares the focal entity's own type, which is the "
            f"same on every row: {predicate}"
        )
