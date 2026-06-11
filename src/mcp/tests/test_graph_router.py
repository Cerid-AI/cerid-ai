# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the /graph/* router (Phase A — Atlas data API)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def mock_redis():
    """In-memory fake redis with .get / .set semantics needed by the cache."""
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
def mock_neo4j():
    """Mock Neo4j driver with session context manager."""
    fake_driver = MagicMock()
    fake_session = MagicMock()
    fake_session.__enter__ = lambda self: self
    fake_session.__exit__ = lambda self, exc_type, exc, tb: None
    fake_driver.session = lambda: fake_session
    return fake_driver, fake_session


@pytest.fixture
def client(mock_redis, mock_neo4j):
    """FastAPI client mounting just the /graph router."""
    fake_driver, fake_session = mock_neo4j

    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=fake_driver):
        from app.routers.graph import router

        app = FastAPI()
        app.include_router(router)
        yield TestClient(app, raise_server_exceptions=False), mock_redis, fake_session


# ---------------------------------------------------------------------------
# /graph/health
# ---------------------------------------------------------------------------


def test_health_reports_config(client):
    tc, _, _ = client
    res = tc.get("/graph/health")
    assert res.status_code == 200
    body = res.json()
    assert body["neo4j_available"] is True
    assert body["cache_ttl_seconds"] >= 0
    assert body["max_node_degree"] > 0
    assert body["max_hops"] == 3


# ---------------------------------------------------------------------------
# /graph/neighborhood — error paths
# ---------------------------------------------------------------------------


def test_neighborhood_missing_entity_returns_422(client):
    """FastAPI's required-query enforcement returns 422 (not 400) when
    `entity` query param is absent."""
    tc, _, _ = client
    res = tc.get("/graph/neighborhood")
    assert res.status_code == 422


def test_neighborhood_hops_out_of_range_returns_422(client):
    tc, _, _ = client
    res = tc.get("/graph/neighborhood?entity=alex&hops=5")
    assert res.status_code == 422


def test_neighborhood_unknown_entity_returns_404(client):
    """Empty Neo4j result → 404 (not 500)."""
    tc, _, fake_session = client
    fake_session.run.return_value.data.return_value = []
    res = tc.get("/graph/neighborhood?entity=nope&hops=2")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# /graph/neighborhood — success
# ---------------------------------------------------------------------------


def _stub_rows():
    """Two-node, one-edge fake graph."""
    return [
        {
            "id": "alex",
            "name": "Alex Chen",
            "type": "Person",
            "community": "c1",
            "mention_count": 47,
            "trust_state": "verified",
            "recency_score": 0.92,
            "edges": [{
                "from": "alex",
                "to": "api_redesign",
                "type": "works_on",
                "weight": 1.4,
                "attestation": "attested",
                "contradiction": False,
            }],
        },
        {
            "id": "api_redesign",
            "name": "API Redesign",
            "type": "Project",
            "community": "c1",
            "mention_count": 23,
            "trust_state": "verified",
            "recency_score": 0.85,
            "edges": [{
                "from": "alex",
                "to": "api_redesign",
                "type": "works_on",
                "weight": 1.4,
                "attestation": "attested",
                "contradiction": False,
            }],
        },
    ]


def test_neighborhood_happy_path_returns_nodes_and_edges(client):
    tc, redis, fake_session = client
    fake_session.run.return_value.data.return_value = _stub_rows()
    res = tc.get("/graph/neighborhood?entity=alex&hops=2")
    assert res.status_code == 200
    body = res.json()
    assert body["focal_entity"] == "alex"
    assert len(body["nodes"]) == 2
    assert {n["id"] for n in body["nodes"]} == {"alex", "api_redesign"}
    # Edges should dedupe across rows — bidirectional same-type pair = 1 edge
    assert len(body["edges"]) == 1
    edge = body["edges"][0]
    assert edge["type"] == "works_on"
    assert edge["attestation"] == "attested"
    # Focal entity has focused=True
    focal = next(n for n in body["nodes"] if n["id"] == "alex")
    assert focal["focused"] is True
    other = next(n for n in body["nodes"] if n["id"] == "api_redesign")
    assert other["focused"] is False
    assert body["cached"] is False


def test_neighborhood_second_call_hits_cache(client):
    """Second request with same params returns cached=true without re-querying Neo4j."""
    tc, redis, fake_session = client
    fake_session.run.return_value.data.return_value = _stub_rows()

    res1 = tc.get("/graph/neighborhood?entity=alex&hops=2")
    assert res1.status_code == 200
    assert res1.json()["cached"] is False

    # Second call — should NOT hit Neo4j again
    fake_session.run.reset_mock()
    res2 = tc.get("/graph/neighborhood?entity=alex&hops=2")
    assert res2.status_code == 200
    assert res2.json()["cached"] is True
    # Confirm Neo4j was not consulted
    fake_session.run.assert_not_called()


def test_neighborhood_filter_changes_cache_key(client):
    tc, redis, fake_session = client
    fake_session.run.return_value.data.return_value = _stub_rows()

    tc.get("/graph/neighborhood?entity=alex&hops=2")
    fake_session.run.reset_mock()
    # Same entity + hops but different filter → different cache key → cache miss
    tc.get("/graph/neighborhood?entity=alex&hops=2&filter=Person")
    fake_session.run.assert_called_once()


def test_neighborhood_cache_key_format(client):
    tc, redis, fake_session = client
    fake_session.run.return_value.data.return_value = _stub_rows()
    tc.get("/graph/neighborhood?entity=alex&hops=2")
    # The cache key shape "cerid:graph:nbhd:alex:2" should exist (no filter)
    assert "cerid:graph:nbhd:alex:2" in redis._state


def test_neighborhood_response_shape_matches_atlas_contract(client):
    """The response shape is what sigma.js + graphology adapter consume.

    This contract test guards against breaking the frontend renderer when
    backend fields are renamed.
    """
    tc, _, fake_session = client
    fake_session.run.return_value.data.return_value = _stub_rows()
    res = tc.get("/graph/neighborhood?entity=alex&hops=1")
    body = res.json()
    assert set(body.keys()) >= {"focal_entity", "nodes", "edges", "truncated", "cached"}
    for node in body["nodes"]:
        assert set(node.keys()) == {
            "id", "name", "type", "community", "mention_count",
            "trust_state", "recency_score", "focused",
        }
    for edge in body["edges"]:
        assert set(edge.keys()) == {
            "source", "target", "type", "weight",
            "attestation", "contradiction",
        }


def test_neighborhood_corrupt_cache_falls_through_to_neo4j(client):
    """If the cache returns invalid JSON, fall through gracefully."""
    tc, redis, fake_session = client
    # Pre-populate cache with garbage
    redis._state["cerid:graph:nbhd:alex:2"] = "not json {{{"
    fake_session.run.return_value.data.return_value = _stub_rows()
    res = tc.get("/graph/neighborhood?entity=alex&hops=2")
    assert res.status_code == 200
    # Should have fallen through to Neo4j (not served the garbage)
    fake_session.run.assert_called_once()


# ---------------------------------------------------------------------------
# Typed Cypher traversal (fix/subjects-eval-round2 hardening)
# ---------------------------------------------------------------------------


def test_neighborhood_cypher_uses_co_mentioned_type(client):
    """Expansion Cypher must restrict to CO_MENTIONED — prevents hub-node blowup.

    Verifies that the Cypher sent to Neo4j contains [:CO_MENTIONED*1..
    and NOT the untyped -[*1..- pattern that routes through Community/Artifact
    hubs and inflates hop-2/3 result sets to O(community size).
    """
    tc, _, fake_session = client
    fake_session.run.return_value.data.return_value = _stub_rows()
    tc.get("/graph/neighborhood?entity=alex&hops=2")
    fake_session.run.assert_called_once()
    cypher_text = fake_session.run.call_args[0][0]
    assert "[:CO_MENTIONED*1.." in cypher_text, (
        "Neighborhood Cypher must use typed [:CO_MENTIONED*1..N] traversal"
    )
    # The old untyped form must not appear.
    assert "-[*1.." not in cypher_text, (
        "Neighborhood Cypher must not use untyped -[*1..N]- (hub traversal)"
    )


def test_neighborhood_cypher_drops_rel_lists(client):
    """The unused rel_lists collect must not appear in the Cypher — it materialises
    all paths combinatorially at hops=3 over hub nodes."""
    tc, _, fake_session = client
    fake_session.run.return_value.data.return_value = _stub_rows()
    tc.get("/graph/neighborhood?entity=alex&hops=3")
    cypher_text = fake_session.run.call_args[0][0]
    assert "rel_lists" not in cypher_text, (
        "rel_lists collect is never used and must be removed from the Cypher"
    )


def test_neighborhood_response_shape_preserved_after_cypher_change(client):
    """Response shape must remain identical after the typed-traversal refactor.

    Guards the frontend contract: sigma.js + graphology adapter consume
    exactly these fields.
    """
    tc, _, fake_session = client
    fake_session.run.return_value.data.return_value = _stub_rows()
    res = tc.get("/graph/neighborhood?entity=alex&hops=2")
    assert res.status_code == 200
    body = res.json()
    # Top-level keys
    assert set(body.keys()) >= {"focal_entity", "nodes", "edges", "truncated", "cached"}
    # Node fields
    for node in body["nodes"]:
        assert set(node.keys()) == {
            "id", "name", "type", "community", "mention_count",
            "trust_state", "recency_score", "focused",
        }
    # Edge fields
    for edge in body["edges"]:
        assert set(edge.keys()) == {
            "source", "target", "type", "weight",
            "attestation", "contradiction",
        }
    assert body["focal_entity"] == "alex"


def test_neighborhood_type_filter_uses_entity_type_field(client):
    """When filter= is provided the Cypher must reference entity_type not type."""
    tc, _, fake_session = client
    fake_session.run.return_value.data.return_value = _stub_rows()
    tc.get("/graph/neighborhood?entity=alex&hops=1&filter=Person")
    cypher_text = fake_session.run.call_args[0][0]
    assert "entity_type" in cypher_text, (
        "Type filter must reference e.entity_type, not the old e.type field"
    )
