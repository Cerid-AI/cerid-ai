# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""``/graph/map`` and ``/graph/embeddings/3d`` must scope links in Cypher.

The 2026-07-29 GA audit reproduced this against the live KB: the endpoint
returned 4,923 nodes and exactly 25,000 links — the ``_EMBEDDINGS_3D_MAX_LINKS``
cap, saturated against 32,271 real edges. The link query took a *global*
top-N-by-weight slice and only afterwards discarded the rows whose endpoints
were not in the returned node set, so the budget was spent on edges that could
never render. 583 nodes (11.84%) shipped with no links at all while the same
response body reported ``isolated_count: 435`` — the endpoint contradicting
itself, and the origin of the long-standing "~12% of the graph is orphaned"
folklore that had been filed as a data-hygiene chore rather than a code defect.

The pre-existing link test (``test_graph_isolated_filter.py``) mocks
``session.run`` and asserts only on the decoded payload, so it passes with
either query. These tests assert the **wire contract** — what Cypher is actually
sent — which is the only thing that distinguishes the two implementations.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.routers.graph as graph_mod


@pytest.fixture
def captured_session():
    """Neo4j session mock that records every (cypher, params) pair."""
    calls: list[tuple[str, dict]] = []
    fake_session = MagicMock()
    fake_session.__enter__ = lambda self: self
    fake_session.__exit__ = lambda self, exc_type, exc, tb: None

    def _run(cypher, **params):
        calls.append((cypher, params))
        result = MagicMock()
        if "isolated_count" in cypher:
            result.data.return_value = [{"isolated_count": 0}]
        elif "CO_MENTIONED|SIMILAR_TO]->" in cypher:
            result.data.return_value = [
                {"s": "e0", "t": "e1", "w": 2.5, "kind": "CO_MENTIONED"},
            ]
        else:
            result.data.return_value = [
                {
                    "id": f"e{i}", "name": f"Entity {i}", "type": "concept",
                    "community": None, "mention_count": 3,
                    "trust_state": "verified", "x": 1.0, "y": 1.0, "z": 1.0,
                    "method": "umap", "computed_at": "2026-07-30",
                    "primary_domain": None, "degree": 2,
                }
                for i in range(3)
            ]
        return result

    fake_session.run = _run
    fake_session._calls = calls

    fake_driver = MagicMock()
    fake_driver.session = lambda: fake_session
    return fake_driver, fake_session, calls


@pytest.fixture
def client(captured_session):
    fake_driver, _, _ = captured_session
    redis_state: dict[str, str] = {}
    fake_redis = MagicMock()
    fake_redis.get = lambda k: redis_state.get(k)
    fake_redis.set = lambda k, v, ex=None, nx=False: redis_state.setdefault(k, v)

    with patch("app.routers.graph.get_redis", return_value=fake_redis), \
         patch("app.routers.graph.get_neo4j", return_value=fake_driver):
        app = FastAPI()
        app.include_router(graph_mod.router)
        yield TestClient(app, raise_server_exceptions=False)


def _link_call(calls: list[tuple[str, dict]]) -> tuple[str, dict]:
    for cypher, params in calls:
        if "CO_MENTIONED|SIMILAR_TO]->" in cypher:
            return cypher, params
    raise AssertionError(f"no link query was issued; saw {len(calls)} queries")


@pytest.mark.parametrize("path", ["/graph/map", "/graph/embeddings/3d"])
def test_link_query_is_scoped_to_the_returned_nodes(client, captured_session, path):
    """The cap must be spent on renderable edges, not the whole graph."""
    _, _, calls = captured_session
    res = client.get(path)
    assert res.status_code == 200, res.text

    cypher, params = _link_call(calls)

    assert "scope_ids" in params, (
        "link query received no scope_ids — it is still selecting edges "
        "globally and discarding out-of-scope rows in Python, which is the "
        "false-orphan defect"
    )
    assert "$scope_ids" in cypher, (
        f"link Cypher does not reference $scope_ids:\n{cypher}"
    )
    # Both endpoints of every candidate edge must be constrained, or the cap is
    # still reachable by edges with an out-of-scope target.
    assert cypher.count("$scope_ids") >= 2, (
        "only one endpoint of the edge is scoped — edges pointing out of the "
        f"returned node set can still consume the cap:\n{cypher}"
    )
    assert params["scope_ids"], "scope_ids was empty despite nodes being returned"


def test_cap_saturation_is_reported_not_silent(captured_session, monkeypatch):
    """A cap-saturated edge set must set links_truncated."""
    import asyncio

    fake_driver, fake_session, _ = captured_session
    monkeypatch.setattr(graph_mod, "_EMBEDDINGS_3D_MAX_LINKS", 2)

    def _saturated(cypher, **params):
        result = MagicMock()
        result.data.return_value = [
            {"s": "e0", "t": "e1", "w": 2.0, "kind": "CO_MENTIONED"},
            {"s": "e1", "t": "e2", "w": 1.0, "kind": "SIMILAR_TO"},
        ]
        return result

    fake_session.run = _saturated

    with patch("app.routers.graph.get_neo4j", return_value=fake_driver):
        links, truncated = asyncio.run(
            graph_mod._query_embeddings_3d_links(["e0", "e1", "e2"])
        )

    assert len(links) == 2
    assert truncated is True, (
        "the edge set exactly filled the cap but truncated was False — callers "
        "cannot tell a capped graph from a sparse one"
    )


def test_uncapped_result_is_not_flagged_truncated(captured_session):
    """Below the cap, nothing is hidden — do not cry wolf."""
    import asyncio

    fake_driver, _, _ = captured_session
    with patch("app.routers.graph.get_neo4j", return_value=fake_driver):
        links, truncated = asyncio.run(
            graph_mod._query_embeddings_3d_links(["e0", "e1", "e2"])
        )

    assert links, "expected the single fixture edge to survive scoping"
    assert truncated is False


def test_capped_out_nodes_are_rescued_with_their_strongest_edge(captured_session):
    """A connected node must never render as an orphan.

    Measured on the live graph 2026-07-30: 32,271 candidate edges against the
    25,000 cap left 583 entities holding none of their real edges. Scoping alone
    does not help — the node cap is 10,000 against ~5,370 entities, so every
    entity is already in scope and the in-scope edge set *is* the global one.
    The cap is the cause, so the rescue pass is what closes it.
    """
    import asyncio

    fake_driver, fake_session, _ = captured_session
    monkey_cap = 1

    calls: list[str] = []

    def _run(cypher, **params):
        calls.append(cypher)
        result = MagicMock()
        if "UNWIND $missing AS mid" in cypher:
            # Rescue pass: e2 was capped out; hand back its strongest edge.
            result.data.return_value = [
                {"s": "e2", "t": "e0", "w": 0.3, "kind": "SIMILAR_TO"},
            ]
        else:
            # Primary pass saturates the cap with a single e0<->e1 edge.
            result.data.return_value = [
                {"s": "e0", "t": "e1", "w": 9.0, "kind": "CO_MENTIONED"},
            ]
        return result

    fake_session.run = _run

    with patch("app.routers.graph.get_neo4j", return_value=fake_driver), \
         patch.object(graph_mod, "_EMBEDDINGS_3D_MAX_LINKS", monkey_cap):
        links, truncated = asyncio.run(
            graph_mod._query_embeddings_3d_links(["e0", "e1", "e2"])
        )

    assert truncated is True
    assert any("UNWIND $missing AS mid" in c for c in calls), (
        "no rescue query was issued despite the cap being saturated — nodes "
        "whose edges lost the weight ranking still render as false orphans"
    )
    linked_indices = {i for lnk in links for i in (lnk[0], lnk[1])}
    assert 2 in linked_indices, (
        f"capped-out node e2 was left with no edge: {links}"
    )


def test_no_rescue_query_when_nothing_was_capped(captured_session):
    """Below the cap there are no false orphans — do not pay for a second query."""
    import asyncio

    fake_driver, _, calls = captured_session
    with patch("app.routers.graph.get_neo4j", return_value=fake_driver):
        _links, truncated = asyncio.run(
            graph_mod._query_embeddings_3d_links(["e0", "e1", "e2"])
        )

    assert truncated is False
    assert not any("UNWIND $missing AS mid" in c for c, _ in calls), (
        "rescue query ran even though the cap was never reached"
    )


def test_response_exposes_links_truncated(client):
    """The disclosure must reach the client, not just the helper."""
    for path in ("/graph/map", "/graph/embeddings/3d"):
        body = client.get(path).json()
        assert "links_truncated" in body, (
            f"{path} does not expose links_truncated; a capped payload is "
            "indistinguishable from a sparse graph"
        )
