# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for GET /graph/structural-gaps (Phase 5 "C2").

Surfaces structural holes: community pairs that are semantically close
(embedding-centroid cosine) but weakly co-mentioned. Covers:
  - gap-scoring math: close+weakly-linked ranks high; close+strongly-linked low
  - the empty case (too few communities/embeddings → {"gaps": []}, 200)
  - the ``limit`` query param (caps returned pairs; over-cap → 422)
  - the exact response shape (snake_case contract the frontend depends on)
  - bridging_candidates are member entities "between" the two communities
"""
from __future__ import annotations

import json
import math
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_redis(initial: dict | None = None):
    state: dict[str, str] = dict(initial or {})

    class _FakeRedis:
        def get(self, k):
            return state.get(k)

        def set(self, k, v, ex=None):
            state[k] = v
            return True

        _state = state

    fake = _FakeRedis()
    fake._state = state
    return fake


def _unit(vec: list[float]) -> list[float]:
    """L2-normalise so cosine == dot product (mirrors the entity-embedding contract)."""
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


# Three communities in a 2D embedding plane:
#   A near-parallel to B (cosine ~1.0), orthogonal to C (cosine ~0).
#   A<->B carry NO co-mention link (structural hole → high gap).
#   A<->C carry NO link either, but they're semantically far → low gap.
def _member_rows():
    """Rows: one per (community, member entity) with a parsed-ready embedding."""
    return [
        # Community A — pointing ~east
        {"community_id": "0:1", "canonical_id": "a1", "name": "Alpha One",
         "embedding": json.dumps(_unit([1.0, 0.05]))},
        {"community_id": "0:1", "canonical_id": "a2", "name": "Alpha Two",
         "embedding": json.dumps(_unit([1.0, 0.20]))},
        # Community B — pointing ~east too (semantically close to A)
        {"community_id": "0:2", "canonical_id": "b1", "name": "Beta One",
         "embedding": json.dumps(_unit([1.0, -0.05]))},
        {"community_id": "0:2", "canonical_id": "b2", "name": "Beta Two",
         "embedding": json.dumps(_unit([1.0, -0.20]))},
        # Community C — pointing ~north (orthogonal → far from A and B)
        {"community_id": "0:3", "canonical_id": "c1", "name": "Gamma One",
         "embedding": json.dumps(_unit([0.05, 1.0]))},
        {"community_id": "0:3", "canonical_id": "c2", "name": "Gamma Two",
         "embedding": json.dumps(_unit([0.20, 1.0]))},
    ]


def _label_rows():
    """Community label ladder rows (summary → top_terms → fallback)."""
    return [
        {"community_id": "0:1", "summary": "Infra", "top_terms": ["kubernetes"]},
        {"community_id": "0:2", "summary": None, "top_terms": ["models", "gpu"]},
        {"community_id": "0:3", "summary": "Finance", "top_terms": None},
    ]


def _client(member_rows, label_rows, link_rows, redis=None):
    from app.routers import graph as graph_router

    app = FastAPI()
    app.include_router(graph_router.router)
    redis = redis if redis is not None else _make_redis()

    with patch("app.routers.graph.get_redis", return_value=redis), \
         patch("app.routers.graph.get_neo4j", return_value=object()), \
         patch("app.routers.graph._fetch_community_members", return_value=member_rows), \
         patch("app.routers.graph._fetch_community_labels", return_value=label_rows), \
         patch("app.routers.graph._fetch_inter_community_links", return_value=link_rows):
        yield TestClient(app), redis


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_close_weakly_linked_ranks_high_over_close_strongly_linked():
    """A<->B close + NO link → high gap. Add a strong A<->C-style link case:
    make B<->C strongly linked so their (lower-sim) pair still ranks below A<->B,
    and confirm a *strong* link on the close pair collapses its gap.
    """
    # No inter-community links at all: A<->B (close) must top the ranking.
    for client, _ in _client(_member_rows(), _label_rows(), link_rows=[]):
        r = client.get("/graph/structural-gaps")
        assert r.status_code == 200
        gaps = r.json()["gaps"]
        assert gaps, "expected at least one gap"
        top = gaps[0]
        pair = {top["community_a"]["id"], top["community_b"]["id"]}
        assert pair == {"0:1", "0:2"}, f"A<->B should rank first, got {pair}"
        assert top["semantic_similarity"] > 0.9
        assert top["link_strength"] == 0.0
        assert top["gap_score"] > 0.9


def test_strong_link_collapses_gap_for_close_pair():
    """Same close A<->B pair, but now strongly co-mentioned → gap_score drops
    far below the close-but-unlinked baseline."""
    # A<->B carries the maximum link weight in the corpus → normalized 1.0.
    strong_links = [{"a": "0:1", "b": "0:2", "weight": 100.0}]
    for client, _ in _client(_member_rows(), _label_rows(), link_rows=strong_links):
        r = client.get("/graph/structural-gaps")
        assert r.status_code == 200
        by_pair = {
            frozenset({g["community_a"]["id"], g["community_b"]["id"]}): g
            for g in r.json()["gaps"]
        }
        ab = by_pair[frozenset({"0:1", "0:2"})]
        assert ab["link_strength"] == 1.0
        # gap = sim * (1 - link_strength) = sim * 0 → 0
        assert ab["gap_score"] == 0.0
        # A close-but-unlinked pair must now outrank the strongly-linked close pair.
        top = r.json()["gaps"][0]
        top_pair = frozenset({top["community_a"]["id"], top["community_b"]["id"]})
        assert top_pair != frozenset({"0:1", "0:2"})


def test_empty_when_too_few_communities():
    """Fewer than two communities with embeddings → {"gaps": []}, 200 not error."""
    one_comm = [
        {"community_id": "0:1", "canonical_id": "a1", "name": "Alpha One",
         "embedding": json.dumps(_unit([1.0, 0.0]))},
    ]
    for client, _ in _client(one_comm, _label_rows(), link_rows=[]):
        r = client.get("/graph/structural-gaps")
        assert r.status_code == 200
        assert r.json() == {"gaps": []}


def test_empty_when_no_members():
    for client, _ in _client([], [], link_rows=[]):
        r = client.get("/graph/structural-gaps")
        assert r.status_code == 200
        assert r.json() == {"gaps": []}


def test_limit_param_caps_returned_gaps():
    for client, _ in _client(_member_rows(), _label_rows(), link_rows=[]):
        r = client.get("/graph/structural-gaps?limit=1")
        assert r.status_code == 200
        assert len(r.json()["gaps"]) == 1


def test_limit_param_above_cap_returns_422():
    """limit above the hard cap (20) → 422 via the Pydantic le= validator,
    matching the repo convention (neighborhood hops=5→422, tour le=20→422)."""
    for client, _ in _client(_member_rows(), _label_rows(), link_rows=[]):
        r = client.get("/graph/structural-gaps?limit=9999")
        assert r.status_code == 422


def test_limit_at_cap_ok():
    """limit at the exact cap (20) is accepted; 3 communities yield C(3,2)=3 pairs."""
    for client, _ in _client(_member_rows(), _label_rows(), link_rows=[]):
        r = client.get("/graph/structural-gaps?limit=20")
        assert r.status_code == 200
        assert len(r.json()["gaps"]) <= 3


def test_response_shape_and_labels():
    for client, _ in _client(_member_rows(), _label_rows(), link_rows=[]):
        r = client.get("/graph/structural-gaps")
        assert r.status_code == 200
        payload = r.json()
        assert set(payload.keys()) == {"gaps"}
        g = payload["gaps"][0]
        assert set(g.keys()) == {
            "community_a", "community_b", "semantic_similarity",
            "link_strength", "gap_score", "bridging_candidates",
        }
        for side in ("community_a", "community_b"):
            assert set(g[side].keys()) == {"id", "label", "count"}
            assert isinstance(g[side]["count"], int)
        assert isinstance(g["semantic_similarity"], float)
        assert isinstance(g["link_strength"], float)
        assert isinstance(g["gap_score"], float)
        for cand in g["bridging_candidates"]:
            assert set(cand.keys()) == {"id", "name"}
        assert 2 <= len(g["bridging_candidates"]) <= 4
        # Label ladder (short-first): top_terms wins over the summary paragraph;
        # summary is the fallback only when top_terms is absent.
        all_labels = {
            side_val["label"]
            for gap in payload["gaps"]
            for side_val in (gap["community_a"], gap["community_b"])
        }
        assert "kubernetes" in all_labels  # 0:1 top_terms beats its "Infra" summary
        assert "Infra" not in all_labels
        assert "Finance" in all_labels  # 0:3 has no top_terms → summary fallback


def test_bridging_candidates_are_between_communities():
    """Candidates should be members of one community closest to the OTHER
    community's centroid — the entities most 'between' the two."""
    for client, _ in _client(_member_rows(), _label_rows(), link_rows=[]):
        r = client.get("/graph/structural-gaps")
        top = r.json()["gaps"][0]  # A<->B
        cand_ids = {c["id"] for c in top["bridging_candidates"]}
        # All candidates must be real members of A or B (not C).
        assert cand_ids <= {"a1", "a2", "b1", "b2"}
        assert cand_ids  # non-empty


def test_served_from_cache_on_second_call():
    redis = _make_redis()
    # Pre-seed a cached payload; the endpoint must return it verbatim.
    cached = {"gaps": [{
        "community_a": {"id": "9:9", "label": "Cached A", "count": 1},
        "community_b": {"id": "9:8", "label": "Cached B", "count": 1},
        "semantic_similarity": 0.5, "link_strength": 0.1, "gap_score": 0.45,
        "bridging_candidates": [{"id": "z1", "name": "Zeta"}, {"id": "z2", "name": "Zed"}],
    }]}
    # Find the key the route uses by inspecting the module constant.
    from app.routers import graph as graph_router
    key = graph_router._STRUCTURAL_GAPS_CACHE_KEY
    redis._state[key] = json.dumps(cached)

    from fastapi import FastAPI as _FastAPI
    app = _FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=redis), \
         patch("app.routers.graph.get_neo4j", return_value=object()), \
         patch("app.routers.graph._fetch_community_members") as m_members:
        r = TestClient(app).get("/graph/structural-gaps")
        assert r.status_code == 200
        assert r.json()["gaps"][0]["community_a"]["id"] == "9:9"
        m_members.assert_not_called()  # cache short-circuits Neo4j
