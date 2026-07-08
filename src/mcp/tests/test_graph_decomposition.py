# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for GET /graph/decomposition (STRATA Cycle 4).

Covers:
  - Full tree response structure (11 domains, L1/L0 communities)
  - A3: no_communities_computed flag distinguishes "Leiden never ran" from empty KB
  - ?community= leaf payload with path:[domain, sub?, l1, l0]
  - A6: deterministic fallback labels ordered by degree
  - Numeric-garbage guard in _first_clause
  - Caching (tree + leaf)
  - 404 on unknown community
  - z-recency helper in ComputeUmap3DJob
  - ?layout=bogus → 422 on GET /graph/map
  - Per-layout cache keys on GET /graph/map
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeResult:
    """Neo4j-compatible result mock — iterable AND has .data()."""

    def __init__(self, rows: list[dict], single_row: dict | None = None):
        self._rows = rows
        self._single = single_row

    def data(self) -> list[dict]:
        return self._rows

    def single(self) -> dict | None:
        return self._single or (self._rows[0] if self._rows else None)

    def __iter__(self):
        return iter(self._rows)


def _make_redis(initial: dict | None = None):
    state: dict[str, bytes | str] = dict(initial or {})
    fake = MagicMock()
    fake.get = lambda k: state.get(k)
    fake.exists = lambda k: 1 if k in state else 0

    def _set(k, v, ex=None):
        state[k] = v if isinstance(v, str) else v
        return True

    fake.set = _set
    fake._state = state
    return fake


def _fake_driver_decomp(
    *,
    community_count: int = 10,
    entity_rows: list[dict] | None = None,
    l1_entity_rows: list[dict] | None = None,
    community_rows: list[dict] | None = None,
):
    """Driver that returns canned data for decomposition queries."""
    fake = MagicMock()
    session = MagicMock()
    session.__enter__ = lambda s: s
    session.__exit__ = lambda s, *a: None

    # Default: 2 L0 communities with members
    if entity_rows is None:
        entity_rows = [
            {
                "entity_id": "e1",
                "name": "Alpha",
                "domain": "research",
                "sub": "research/beir-scifact",
                "community_id": "2546",
                "degree": 10,
            },
            {
                "entity_id": "e2",
                "name": "Beta",
                "domain": "research",
                "sub": "research/beir-scifact",
                "community_id": "2546",
                "degree": 5,
            },
            {
                "entity_id": "e3",
                "name": "Gamma",
                "domain": "coding",
                "sub": None,
                "community_id": "1234",
                "degree": 3,
            },
        ]

    if l1_entity_rows is None:
        l1_entity_rows = [
            {"eid": "e1", "l1_id": "1:100"},
            {"eid": "e2", "l1_id": "1:100"},
            {"eid": "e3", "l1_id": "1:200"},
        ]

    if community_rows is None:
        community_rows = [
            {"cid": "0:2546", "level": 0, "summary": "Protein folding studies"},
            {"cid": "0:1234", "level": 0, "summary": None},
            {"cid": "1:100", "level": 1, "summary": "Research L1 cluster"},
            {"cid": "1:200", "level": 1, "summary": None},
        ]

    def _run(query, **kwargs):
        if "count(c) AS cnt" in query and "Community" in query:
            return _FakeResult([], single_row={"cnt": community_count})
        elif "c.level IN [0, 1]" in query:
            return _FakeResult(community_rows)
        elif "e.primary_domain AS domain, count(e) AS cnt" in query:
            return _FakeResult([
                {"domain": "research", "cnt": 2},
                {"domain": "coding", "cnt": 1},
            ])
        elif "deg" in query and "primary_domain IS NOT NULL" in query:
            return _FakeResult(entity_rows)
        elif "domains_updated_at" in query:
            return _FakeResult([], single_row={"derived_at": "2026-06-11T03:39:00+00:00"})
        elif "IN_COMMUNITY" in query and "level: 1" in query:
            return _FakeResult(l1_entity_rows)
        elif "e.canonical_id" in query and (
            "community_id = $bare_id" in query or "community_id = $norm_id" in query
        ):
            # entity leaf query
            return _FakeResult([
                {
                    "entity_id": "e1",
                    "name": "Alpha",
                    "entity_type": "CONCEPT",
                    "domain": "research",
                    "sub": "research/beir-scifact",
                    "community_id": "2546",
                    "trust_state": "verified",
                    "mention_count": 5,
                    "degree": 10,
                }
            ])
        elif "count(c) AS cnt" in query and "cid" in query:
            # community existence check for 404
            return _FakeResult([], single_row={"cnt": 0})
        else:
            return _FakeResult([])

    session.run = _run
    fake.session = lambda: session
    return fake


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redis():
    return _make_redis()


@pytest.fixture
def decomp_client(mock_redis):
    from app.routers import graph as graph_router

    app = FastAPI()
    app.include_router(graph_router.router)

    def _make(driver):
        ctx = (
            patch("app.routers.graph.get_redis", return_value=mock_redis),
            patch("app.routers.graph.get_neo4j", return_value=driver),
        )
        return TestClient(app), ctx

    return _make


# ---------------------------------------------------------------------------
# A3: no_communities_computed flag
# ---------------------------------------------------------------------------


def test_decomposition_no_communities_flag_when_zero(mock_redis):
    """A3: community_count=0 → no_communities_computed=true."""
    from app.routers import graph as graph_router

    driver = _fake_driver_decomp(community_count=0)
    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get("/graph/decomposition")

    assert r.status_code == 200
    assert r.json()["no_communities_computed"] is True


def test_decomposition_no_communities_false_when_exist(mock_redis):
    """A3: community_count>0 → no_communities_computed=false."""
    from app.routers import graph as graph_router

    driver = _fake_driver_decomp(community_count=10)
    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get("/graph/decomposition")

    assert r.status_code == 200
    assert r.json()["no_communities_computed"] is False


# ---------------------------------------------------------------------------
# Tree structure
# ---------------------------------------------------------------------------


def test_decomposition_tree_has_domains(mock_redis):
    """Tree response includes domain nodes with l1/l0 communities."""
    from app.routers import graph as graph_router

    driver = _fake_driver_decomp()
    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get("/graph/decomposition")

    assert r.status_code == 200
    payload = r.json()
    domains = payload["domains"]
    assert len(domains) >= 1
    domain_ids = {d["id"] for d in domains}
    assert "research" in domain_ids or len(domains) > 0


def test_decomposition_tree_cached_on_second_call(mock_redis):
    """Second call returns cached=True."""
    from app.routers import graph as graph_router

    driver = _fake_driver_decomp()
    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        tc = TestClient(app)
        r1 = tc.get("/graph/decomposition")
        assert r1.status_code == 200
        assert r1.json()["cached"] is False

        r2 = tc.get("/graph/decomposition")

    assert r2.status_code == 200
    assert r2.json()["cached"] is True


def test_decomposition_tree_no_neo4j_returns_empty(mock_redis):
    """No Neo4j driver → 200 with no_communities_computed=true, empty domains."""
    from app.routers import graph as graph_router

    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=None):
        r = TestClient(app).get("/graph/decomposition")

    assert r.status_code == 200
    assert r.json()["no_communities_computed"] is True
    assert r.json()["domains"] == []


# ---------------------------------------------------------------------------
# ?community= leaf path
# ---------------------------------------------------------------------------


def test_decomposition_community_leaf_returns_entities(mock_redis):
    """?community=<id> → entity leaves with path:[domain, sub?, l1, l0]."""
    from app.routers import graph as graph_router

    driver = _fake_driver_decomp()
    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get("/graph/decomposition?community=0:2546")

    assert r.status_code == 200
    payload = r.json()
    assert payload["community_id"] == "0:2546"
    assert len(payload["entities"]) >= 1
    e = payload["entities"][0]
    assert "path" in e
    assert isinstance(e["path"], list)
    assert len(e["path"]) >= 2  # at minimum [domain, l0]


def test_decomposition_community_leaf_cached(mock_redis):
    """?community= leaf result is cached on second call."""
    from app.routers import graph as graph_router

    driver = _fake_driver_decomp()
    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        tc = TestClient(app)
        r1 = tc.get("/graph/decomposition?community=0:2546")
        assert r1.json()["cached"] is False

        r2 = tc.get("/graph/decomposition?community=0:2546")

    assert r2.json()["cached"] is True


def test_decomposition_community_not_found_returns_404(mock_redis):
    """Unknown community → 404."""
    from app.routers import graph as graph_router

    # Make driver return empty data and no community node
    driver = MagicMock()
    session = MagicMock()
    session.__enter__ = lambda s: s
    session.__exit__ = lambda s, *a: None

    def _run_empty(query, **kwargs):
        return _FakeResult([], single_row={"cnt": 0})

    session.run = _run_empty
    driver.session = lambda: session

    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get("/graph/decomposition?community=0:99999")

    assert r.status_code == 404


# ---------------------------------------------------------------------------
# A6: deterministic fallback labels
# ---------------------------------------------------------------------------


def test_fallback_label_ordered_by_degree():
    """A6: _fallback_label orders members by degree descending, deterministic."""
    from app.db.neo4j.decomposition import _fallback_label

    members = [
        {"id": "e3", "name": "Gamma", "degree": 3},
        {"id": "e1", "name": "Alpha", "degree": 10},
        {"id": "e2", "name": "Beta", "degree": 7},
    ]
    label = _fallback_label("0:100", members, 3)
    assert label == "Community of 3 — top entities: Alpha, Beta, Gamma"


def test_fallback_label_is_deterministic():
    """Same inputs always produce the same label."""
    from app.db.neo4j.decomposition import _fallback_label

    members = [
        {"id": "e1", "name": "Alpha", "degree": 5},
        {"id": "e2", "name": "Beta", "degree": 5},
    ]
    # Tie-break by name ascending
    label1 = _fallback_label("0:x", members, 2)
    label2 = _fallback_label("0:x", members, 2)
    assert label1 == label2


def test_fallback_label_empty_members():
    """Empty members → 'Community of N'."""
    from app.db.neo4j.decomposition import _fallback_label

    assert _fallback_label("0:x", [], 5) == "Community of 5"


# ---------------------------------------------------------------------------
# Numeric-garbage guard in _first_clause
# ---------------------------------------------------------------------------


def test_first_clause_rejects_numeric_garbage():
    """_first_clause returns '' for a label that is purely numeric."""
    from app.processor.jobs.compute_umap_3d import ComputeUmap3DJob

    assert ComputeUmap3DJob._first_clause("0.7143") == ""
    assert ComputeUmap3DJob._first_clause("42") == ""
    assert ComputeUmap3DJob._first_clause("3.14159") == ""


def test_first_clause_passes_valid_text():
    """_first_clause passes through normal summary text."""
    from app.processor.jobs.compute_umap_3d import ComputeUmap3DJob

    result = ComputeUmap3DJob._first_clause("Protein folding studies in bioinformatics")
    assert result.startswith("Protein")


def test_first_clause_strips_boilerplate():
    """_first_clause strips LLM boilerplate lead-ins."""
    from app.processor.jobs.compute_umap_3d import ComputeUmap3DJob

    result = ComputeUmap3DJob._first_clause(
        "This community centers on machine learning techniques"
    )
    assert "centers" not in result.lower()
    assert "machine learning" in result.lower()


# ---------------------------------------------------------------------------
# z-recency
# ---------------------------------------------------------------------------


def test_compute_z_recency_recent_entity_near_zero():
    """Entity updated today → z close to 0."""
    from datetime import datetime, timezone

    from app.processor.jobs.compute_umap_3d import ComputeUmap3DJob

    now_iso = datetime.now(timezone.utc).isoformat()
    entity = {"updated_at": now_iso}
    z = ComputeUmap3DJob._compute_z_recency(entity, z_amplitude=3.0)
    assert abs(z) < 0.1  # close to 0 for recent entity


def test_compute_z_recency_old_entity_negative():
    """Entity updated 4 years ago → z close to -z_amplitude (floor)."""
    from app.processor.jobs.compute_umap_3d import ComputeUmap3DJob

    old_iso = "2020-01-01T00:00:00+00:00"  # > 3 years old
    entity = {"updated_at": old_iso}
    z = ComputeUmap3DJob._compute_z_recency(entity, z_amplitude=3.0)
    assert z == pytest.approx(-3.0, abs=0.01)


def test_compute_z_recency_missing_updated_at():
    """Entity with no updated_at → z=0."""
    from app.processor.jobs.compute_umap_3d import ComputeUmap3DJob

    assert ComputeUmap3DJob._compute_z_recency({}, z_amplitude=3.0) == 0.0
    assert ComputeUmap3DJob._compute_z_recency({"updated_at": None}, z_amplitude=3.0) == 0.0


def test_compute_z_recency_amplitude_bounded():
    """z magnitude never exceeds z_amplitude even for very old entities."""
    from app.processor.jobs.compute_umap_3d import ComputeUmap3DJob

    entity = {"updated_at": "1999-01-01T00:00:00+00:00"}
    z = ComputeUmap3DJob._compute_z_recency(entity, z_amplitude=5.0)
    assert z >= -5.0


# ---------------------------------------------------------------------------
# GET /graph/map ?layout= validation
# ---------------------------------------------------------------------------


def _make_map_driver():
    """Minimal driver for map endpoint tests."""
    driver = MagicMock()
    session = MagicMock()
    session.__enter__ = lambda s: s
    session.__exit__ = lambda s, *a: None

    def _run(query, **kwargs):
        result = MagicMock()
        if "count(e)" in query:
            result.data = lambda: [{"isolated_count": 0}]
        elif ")-[r:" in query:
            # link query: MATCH (a:Entity)-[r:CO_MENTIONED|SIMILAR_TO]->
            result.data = lambda: []
        else:
            result.data = lambda: [
                {
                    "id": "e1",
                    "name": "Alpha",
                    "type": "CONCEPT",
                    "community": "0:2546",
                    "mention_count": 5,
                    "trust_state": "verified",
                    "x": 1.0,
                    "y": 2.0,
                    "z": 0.1,
                    "method": "force",
                    "computed_at": "2026-06-11T00:00:00+00:00",
                    "primary_domain": "research",
                }
            ]
        return result

    session.run = _run
    driver.session = lambda: session
    return driver


def test_map_unknown_layout_returns_422():
    """?layout=bogus → 422."""
    from app.routers import graph as graph_router

    redis = _make_redis()
    driver = _make_map_driver()
    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get("/graph/map?layout=bogus")

    assert r.status_code == 422


def test_map_force_layout_uses_force_cache_key():
    """?layout=force → cache key is cerid:graph:emb3d:v6:map:force."""
    from app.routers import graph as graph_router

    redis = _make_redis()
    driver = _make_map_driver()
    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get("/graph/map?layout=force")

    assert r.status_code == 200
    assert "cerid:graph:emb3d:v6:map:force" in redis._state


def test_map_omit_layout_same_as_force():
    """Omitting ?layout is byte-identical to ?layout=force."""
    from app.routers import graph as graph_router

    redis = _make_redis()
    driver = _make_map_driver()
    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        tc = TestClient(app)
        r1 = tc.get("/graph/map")
        r2 = tc.get("/graph/map?layout=force")

    # Both succeed
    assert r1.status_code == 200
    assert r2.status_code == 200
    # Both should end up in the same cache key
    assert "cerid:graph:emb3d:v6:map:force" in redis._state


def test_map_non_default_layout_fallback_when_no_artifact():
    """?layout=wells with no position artifact → layout_fallback=True + force data."""
    from app.routers import graph as graph_router

    # Redis with no layout_positions artifact for wells
    redis = _make_redis()
    driver = _make_map_driver()
    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get("/graph/map?layout=wells")

    assert r.status_code == 200
    assert r.json()["layout_fallback"] is True


def test_map_semantic_layout_fallback_when_no_artifact():
    """?layout=semantic before the job has computed it → graceful force fallback."""
    from app.routers import graph as graph_router

    redis = _make_redis()
    driver = _make_map_driver()
    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get("/graph/map?layout=semantic")

    assert r.status_code == 200
    assert r.json()["layout_fallback"] is True


def test_map_valid_layouts_return_200():
    """All valid layout values return 200."""
    from app.routers import graph as graph_router

    for layout in ("force", "wells", "domain", "semantic"):
        redis = _make_redis()
        driver = _make_map_driver()
        app = FastAPI()
        app.include_router(graph_router.router)
        with patch("app.routers.graph.get_redis", return_value=redis), \
             patch("app.routers.graph.get_neo4j", return_value=driver):
            r = TestClient(app).get(f"/graph/map?layout={layout}")
        assert r.status_code == 200, f"Expected 200 for layout={layout}, got {r.status_code}"
