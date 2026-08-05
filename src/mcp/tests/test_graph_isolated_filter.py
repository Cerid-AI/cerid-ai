# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""TDD tests for the include_isolated toggle on /graph/* endpoints.

Fixture: 3 connected entities + 2 isolated entities (degree 0).
- include_isolated=False (default): returns 3 entities, isolated_count==2
- include_isolated=True: returns 5 entities, isolated_count==2 (still computed)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
    fake.exists = lambda k: k in state
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
        from app.routers.graph import router  # noqa: PLC0415
        app = FastAPI()
        app.include_router(router)
        yield TestClient(app, raise_server_exceptions=False), mock_redis, fake_session


# ---------------------------------------------------------------------------
# 3D-embedding fixture helpers
# ---------------------------------------------------------------------------


def _make_emb3d_rows(
    connected_count: int = 3,
    isolated_count: int = 2,
    include_isolated: bool = True,
) -> list[dict]:
    """Return fake _query_embeddings_3d rows.

    Connected entities have degree >= 1 (they would pass the
    (e)-[:CO_MENTIONED|SIMILAR_TO]-() predicate).  Isolated entities have
    degree 0 and are only included when include_isolated=True.
    """
    rows = []
    for i in range(connected_count):
        rows.append({
            "id": f"entity_connected_{i}",
            "name": f"Connected {i}",
            "type": "Person",
            "community": "c1",
            "mention_count": 5 + i,
            "trust_state": "verified",
            "x": float(i),
            "y": float(i),
            "z": float(i),
            "method": "umap",
            "computed_at": "2026-06-01",
            "primary_domain": None,
            "degree": 2,  # connected
        })
    if include_isolated:
        for j in range(isolated_count):
            rows.append({
                "id": f"entity_isolated_{j}",
                "name": f"Isolated {j}",
                "type": "Topic",
                "community": "c2",
                "mention_count": 1,
                "trust_state": "unknown",
                "x": float(100 + j),
                "y": float(100 + j),
                "z": float(100 + j),
                "method": "fallback",
                "computed_at": "2026-06-01",
                "primary_domain": None,
                "degree": 0,  # isolated
            })
    return rows


# ---------------------------------------------------------------------------
# /graph/embeddings/3d — include_isolated toggle
# ---------------------------------------------------------------------------


def test_emb3d_default_excludes_isolated(client):
    """GET /graph/embeddings/3d without include_isolated returns only connected
    entities (degree > 0) and reports isolated_count.

    TDD RED → GREEN gate for Task 1.2a."""
    tc, _, fake_session = client
    # Simulate Neo4j returning only connected entities (filter applied in Cypher)
    fake_session.run.return_value.data.return_value = _make_emb3d_rows(
        connected_count=3, isolated_count=2, include_isolated=False,
    )
    # Second run call for the isolated_count subquery → returns 2
    fake_session.run.side_effect = [
        _make_side_effect_run(_make_emb3d_rows(
            connected_count=3, isolated_count=2, include_isolated=False,
        )),
        _make_side_effect_run([{"isolated_count": 2}]),
        # links query
        _make_side_effect_run([]),
    ]

    res = tc.get("/graph/embeddings/3d")
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 3, f"expected 3 connected entities, got {body['count']}"
    assert body["isolated_count"] == 2, f"expected isolated_count=2, got {body.get('isolated_count')}"


def test_emb3d_include_isolated_true_returns_all(client):
    """GET /graph/embeddings/3d?include_isolated=true returns all 5 entities."""
    tc, _, fake_session = client
    fake_session.run.side_effect = [
        _make_side_effect_run(_make_emb3d_rows(
            connected_count=3, isolated_count=2, include_isolated=True,
        )),
        _make_side_effect_run([{"isolated_count": 2}]),
        # links query
        _make_side_effect_run([]),
    ]

    res = tc.get("/graph/embeddings/3d?include_isolated=true")
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 5, f"expected 5 entities with include_isolated=true, got {body['count']}"
    assert body["isolated_count"] == 2


def test_emb3d_response_has_isolated_count_field(client):
    """Response must always include isolated_count (even when 0)."""
    tc, _, fake_session = client
    fake_session.run.side_effect = [
        _make_side_effect_run(_make_emb3d_rows(
            connected_count=3, isolated_count=0, include_isolated=False,
        )),
        _make_side_effect_run([{"isolated_count": 0}]),
        _make_side_effect_run([]),
    ]
    res = tc.get("/graph/embeddings/3d")
    assert res.status_code == 200
    body = res.json()
    assert "isolated_count" in body


def test_emb3d_include_isolated_changes_cache_key(client):
    """include_isolated=true must produce a different cache key than the default."""
    tc, redis, fake_session = client
    fake_session.run.side_effect = [
        _make_side_effect_run(_make_emb3d_rows(
            connected_count=3, isolated_count=2, include_isolated=False,
        )),
        _make_side_effect_run([{"isolated_count": 2}]),
        _make_side_effect_run([]),
        # second request
        _make_side_effect_run(_make_emb3d_rows(
            connected_count=3, isolated_count=2, include_isolated=True,
        )),
        _make_side_effect_run([{"isolated_count": 2}]),
        _make_side_effect_run([]),
    ]

    tc.get("/graph/embeddings/3d?include_isolated=false")
    tc.get("/graph/embeddings/3d?include_isolated=true")
    # Both responses were fresh (not cached), so cache must have two distinct keys
    cached_keys = [k for k in redis._state if "emb3d" in k]
    assert len(cached_keys) >= 2, (
        "include_isolated should produce distinct cache keys; "
        f"found only: {cached_keys}"
    )


# ---------------------------------------------------------------------------
# /graph/neighborhood — isolated_count field exists
# ---------------------------------------------------------------------------


def test_neighborhood_has_isolated_count_field(client):
    """NeighborhoodResponse must include isolated_count field."""
    tc, _, fake_session = client
    fake_session.run.return_value.data.return_value = [
        {
            "id": "alex",
            "name": "Alex",
            "type": "Person",
            "community": "c1",
            "mention_count": 5,
            "trust_state": "verified",
            "recency_score": 0.9,
            "primary_domain": None,
            "edges": [],
            "truncated": False,
        },
    ]
    res = tc.get("/graph/neighborhood?entity=alex&hops=1")
    assert res.status_code == 200
    body = res.json()
    assert "isolated_count" in body, "NeighborhoodResponse must include isolated_count"


# ---------------------------------------------------------------------------
# Helper: wrap list rows into a Neo4j run() return value mock
# ---------------------------------------------------------------------------


def _make_side_effect_run(rows: list[dict]):
    """Create a MagicMock that behaves like session.run(...)."""
    mock = MagicMock()
    mock.data.return_value = rows
    return mock


# ---------------------------------------------------------------------------
# Task 3.3 — SIMILAR_TO edges served with kind tag
# ---------------------------------------------------------------------------

def _make_entity_rows_for_link_test() -> list[dict]:
    """Two entities with UMAP coords so the link query can index them."""
    return [
        {
            "id": "entity_a",
            "name": "A",
            "type": "Person",
            "community": "c1",
            "mention_count": 3,
            "trust_state": "verified",
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
            "method": "umap",
            "computed_at": "2026-06-01",
            "primary_domain": None,
            "degree": 2,
        },
        {
            "id": "entity_b",
            "name": "B",
            "type": "Person",
            "community": "c1",
            "mention_count": 2,
            "trust_state": "verified",
            "x": 1.0,
            "y": 1.0,
            "z": 1.0,
            "method": "umap",
            "computed_at": "2026-06-01",
            "primary_domain": None,
            "degree": 2,
        },
    ]


def test_emb3d_links_include_co_mention_and_similar_to_with_kind(client):
    """Task 3.3 TDD gate: /graph/embeddings/3d links must contain both
    CO_MENTIONED (kind='co_mention') and SIMILAR_TO (kind='similar') edges,
    each 4-tuple [src_idx, tgt_idx, weight, kind].
    """
    tc, _, fake_session = client

    # Build two raw link rows — one per relationship type
    link_rows = [
        {"s": "entity_a", "t": "entity_b", "w": 2.5, "kind": "CO_MENTIONED"},
        {"s": "entity_b", "t": "entity_a", "w": 0.85, "kind": "SIMILAR_TO"},
    ]

    fake_session.run.side_effect = [
        # entity rows for _query_embeddings_3d
        _make_side_effect_run(_make_entity_rows_for_link_test()),
        # isolated_count subquery
        _make_side_effect_run([{"isolated_count": 0}]),
        # link rows for _query_embeddings_3d_links
        _make_side_effect_run(link_rows),
    ]

    res = tc.get("/graph/embeddings/3d")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
    body = res.json()

    links = body.get("links", [])
    assert len(links) >= 2, f"Expected at least 2 links, got {links}"

    # Each link must be a 4-tuple [src_idx, tgt_idx, weight, kind]
    for lnk in links:
        assert len(lnk) == 4, f"Link must be a 4-tuple, got: {lnk}"
        assert isinstance(lnk[3], str), f"4th element (kind) must be str, got: {lnk[3]}"

    kinds = {lnk[3] for lnk in links}
    assert "co_mention" in kinds, f"'co_mention' kind missing from links: {links}"
    assert "similar" in kinds, f"'similar' kind missing from links: {links}"
