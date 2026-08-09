# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Tests for topology metric on GET /graph/health (Task 0.1)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures — mirror the pattern in test_graph_router.py
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redis():
    """In-memory fake redis with .get / .setex semantics."""
    state: dict[str, str] = {}
    fake = MagicMock()
    fake.get = lambda k: state.get(k)

    def _setex(k, ttl, v):
        state[k] = v
        return True

    fake.setex = _setex
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


def _topology_result(
    *,
    total: int = 5,
    edges: int = 3,
    orphan_count: int = 2,
    sum_degree: float = 6.0,
    connected_count: int = 3,
    community_count: int = 2,
    single_mention_count: int = 1,
):
    """Build the fake record returned by the single-pass Cypher aggregate."""
    return {
        "total": total,
        "edges": edges,
        "orphan_count": orphan_count,
        "sum_degree": sum_degree,
        "connected_count": connected_count,
        "community_count": community_count,
        "single_mention_count": single_mention_count,
    }


@pytest.fixture
def client_with_topology(mock_redis, mock_neo4j):
    """Client where Neo4j returns a well-formed topology aggregate."""
    fake_driver, fake_session = mock_neo4j
    fake_session.run.return_value = [_topology_result()]

    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=fake_driver):
        from app.routers.graph import router

        app = FastAPI()
        app.include_router(router)
        yield TestClient(app, raise_server_exceptions=False), mock_redis, fake_session


@pytest.fixture
def client_no_redis(mock_neo4j):
    """Client where Redis is unavailable (topology computed fresh every call)."""
    fake_driver, fake_session = mock_neo4j
    fake_session.run.return_value = [_topology_result()]

    with patch("app.routers.graph.get_redis", return_value=None), \
         patch("app.routers.graph.get_neo4j", return_value=fake_driver):
        from app.routers.graph import router

        app = FastAPI()
        app.include_router(router)
        yield TestClient(app, raise_server_exceptions=False), fake_session


@pytest.fixture
def client_neo4j_fails(mock_redis, mock_neo4j):
    """Client where the Neo4j topology query raises an exception."""
    fake_driver, fake_session = mock_neo4j
    fake_session.run.side_effect = RuntimeError("neo4j down")

    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=fake_driver):
        from app.routers.graph import router

        app = FastAPI()
        app.include_router(router)
        yield TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_health_topology_orphan_counts(client_with_topology):
    """Core requirement: orphan_count == 2, orphan_pct == 40.0 for 5-node/2-orphan graph."""
    tc, _, _ = client_with_topology
    res = tc.get("/graph/health")
    assert res.status_code == 200
    body = res.json()
    topo = body["topology"]
    assert topo is not None
    assert topo["nodes"] == 5
    assert topo["edges"] == 3
    assert topo["orphan_count"] == 2
    assert topo["orphan_pct"] == pytest.approx(40.0)


def test_health_topology_connected_mean_degree_positive(client_with_topology):
    """connected_mean_degree must be > 0 when there are connected nodes."""
    tc, _, _ = client_with_topology
    res = tc.get("/graph/health")
    topo = res.json()["topology"]
    assert topo["connected_mean_degree"] > 0


def test_health_topology_community_count(client_with_topology):
    """community_count and nodes_per_community are computed."""
    tc, _, _ = client_with_topology
    res = tc.get("/graph/health")
    topo = res.json()["topology"]
    assert topo["community_count"] == 2
    # 5 nodes / 2 communities → nodes_per_community ~= 2.5
    assert topo["nodes_per_community"] == pytest.approx(2.5)


def test_health_topology_single_mention_pct(client_with_topology):
    """single_mention_pct = single_mention_count / total * 100."""
    tc, _, _ = client_with_topology
    res = tc.get("/graph/health")
    topo = res.json()["topology"]
    # 1 single-mention out of 5 total → 20.0 %
    assert topo["single_mention_pct"] == pytest.approx(20.0)


def test_health_still_returns_existing_fields(client_with_topology):
    """Existing health fields must not be broken by the topology addition."""
    tc, _, _ = client_with_topology
    res = tc.get("/graph/health")
    assert res.status_code == 200
    body = res.json()
    assert "neo4j_available" in body
    assert "cache_ttl_seconds" in body
    assert "max_node_degree" in body
    assert "max_hops" in body
    assert "visualization_enabled" in body


def test_health_topology_cached_on_second_call(client_with_topology):
    """Second call returns cached topology (Redis) without re-querying Neo4j."""
    tc, _, fake_session = client_with_topology
    tc.get("/graph/health")
    call_count_after_first = fake_session.run.call_count

    tc.get("/graph/health")
    # Neo4j should not have been called again for topology
    assert fake_session.run.call_count == call_count_after_first


def test_health_topology_null_on_neo4j_failure(client_neo4j_fails):
    """When Neo4j topology query fails, topology is null — existing payload is unbroken."""
    tc = client_neo4j_fails
    res = tc.get("/graph/health")
    assert res.status_code == 200
    body = res.json()
    assert body["topology"] is None
    # Other fields still present
    assert "neo4j_available" in body


def test_health_topology_works_without_redis(client_no_redis):
    """topology is computed even when Redis is unavailable."""
    tc, _ = client_no_redis
    res = tc.get("/graph/health")
    assert res.status_code == 200
    topo = res.json()["topology"]
    assert topo is not None
    assert topo["orphan_count"] == 2


def test_health_topology_zero_nodes_edge_case(mock_redis, mock_neo4j):
    """Empty graph → orphan_pct=0.0, connected_mean_degree=0.0, nodes_per_community=0.0."""
    fake_driver, fake_session = mock_neo4j
    fake_session.run.return_value = [_topology_result(
        total=0, edges=0, orphan_count=0,
        sum_degree=0.0, connected_count=0,
        community_count=0, single_mention_count=0,
    )]

    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=fake_driver):
        from app.routers.graph import router

        app = FastAPI()
        app.include_router(router)
        tc = TestClient(app, raise_server_exceptions=False)
        res = tc.get("/graph/health")

    assert res.status_code == 200
    topo = res.json()["topology"]
    assert topo["orphan_pct"] == 0.0
    assert topo["connected_mean_degree"] == 0.0
    assert topo["nodes_per_community"] == 0.0
    assert topo["single_mention_pct"] == 0.0
