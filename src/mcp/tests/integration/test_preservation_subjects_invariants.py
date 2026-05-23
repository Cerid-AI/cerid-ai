# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Subjects pane preservation invariants — Phase A Day 13.

Locks the contract the Subjects pane consolidation depends on:

  * /graph/neighborhood — Atlas data API mounted, responds with
    documented shape, rejects invalid hops, handles missing entity.
  * /graph/health — visualization-enabled flag exposed.
  * /atlas/views/health — saved-views router mounted, Redis reachable.
  * /atlas/views — CRUD endpoints respond with expected status codes.
  * Response budgets — keep /graph/health and /atlas/views/health
    under 200ms p95 (these are zero-work probes; a regression here
    means the FastAPI pipeline picked up unwanted middleware cost).

Run inside the integration harness against a live stack. Skipped
gracefully when the stack isn't booted.
"""
from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.preservation


def test_graph_health_endpoint_exists(http_client):
    r = http_client.get("/graph/health")
    assert r.status_code == 200, f"/graph/health {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert "neo4j_available" in body
    assert "cache_ttl_seconds" in body
    assert "max_node_degree" in body
    assert "max_hops" in body
    assert "visualization_enabled" in body


def test_graph_neighborhood_rejects_invalid_hops(http_client):
    # hops > 3 must 422 (Pydantic constraint le=3)
    r = http_client.get("/graph/neighborhood", params={"entity": "x", "hops": 5})
    assert r.status_code == 422, f"expected 422, got {r.status_code}: {r.text[:200]}"


def test_graph_neighborhood_missing_entity_returns_4xx(http_client):
    # Missing required entity param → 422
    r = http_client.get("/graph/neighborhood")
    assert r.status_code == 422


def test_atlas_views_health_endpoint_exists(http_client):
    r = http_client.get("/atlas/views/health")
    assert r.status_code == 200, f"/atlas/views/health {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert "redis_available" in body
    assert "max_views_per_user" in body
    assert isinstance(body["max_views_per_user"], int)
    assert body["max_views_per_user"] > 0


def test_atlas_views_list_endpoint_responds(http_client):
    r = http_client.get("/atlas/views")
    # 200 with a (possibly empty) views list, or 503 if Redis is down
    # for this run. Both are valid contract responses.
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        body = r.json()
        assert "views" in body
        assert isinstance(body["views"], list)


def test_atlas_views_delete_is_idempotent(http_client):
    # Delete a definitely-nonexistent view → 204 (per router contract)
    r = http_client.delete("/atlas/views/nonexistent_id_xyz")
    assert r.status_code in (204, 503)


# ---------------------------------------------------------------------------
# Response budgets — these probes do zero real work, so any regression
# above 200ms p95 indicates middleware/instrumentation overhead crept in.
# ---------------------------------------------------------------------------


def _measure_p95(client, path: str, samples: int = 10) -> float:
    times: list[float] = []
    for _ in range(samples):
        t0 = time.perf_counter()
        r = client.get(path)
        elapsed = (time.perf_counter() - t0) * 1000
        if r.status_code in (200, 503):
            times.append(elapsed)
    times.sort()
    if not times:
        pytest.skip(f"{path}: no successful samples")
    idx = max(0, int(len(times) * 0.95) - 1)
    return times[idx]


def test_graph_health_p95_under_200ms(http_client):
    p95 = _measure_p95(http_client, "/graph/health")
    assert p95 < 200, f"/graph/health p95 {p95:.1f}ms — middleware regression?"


def test_atlas_views_health_p95_under_200ms(http_client):
    p95 = _measure_p95(http_client, "/atlas/views/health")
    assert p95 < 200, f"/atlas/views/health p95 {p95:.1f}ms — middleware regression?"


# ---------------------------------------------------------------------------
# Phase B — Constellation 3D endpoint contract
# ---------------------------------------------------------------------------


def test_embeddings_3d_endpoint_responds(http_client):
    """The /graph/embeddings/3d endpoint is mounted and returns the
    documented shape. Empty payload is acceptable — the compute_umap_3d
    job may not have run on this stack yet."""
    r = http_client.get("/graph/embeddings/3d")
    # 200 with envelope, or 503 if Neo4j is down. Both are valid contract responses.
    assert r.status_code in (200, 503), f"/graph/embeddings/3d {r.status_code}: {r.text[:200]}"
    if r.status_code == 200:
        body = r.json()
        assert "count" in body
        assert "entities" in body
        assert "cached" in body
        assert isinstance(body["entities"], list)
        for entity in body["entities"]:
            for field in ("id", "name", "x", "y", "z", "projection"):
                assert field in entity, f"entity missing {field}: {entity}"


def test_embeddings_3d_filter_threads_through(http_client):
    r = http_client.get("/graph/embeddings/3d", params={"filter": "Person"})
    assert r.status_code in (200, 503)


def test_embeddings_3d_p95_under_500ms_when_cached(http_client):
    """Cached fast-path budget — 500ms ceiling accounts for the first
    fetch warming the cache; subsequent hits should be well below."""
    # Warm the cache
    http_client.get("/graph/embeddings/3d")
    p95 = _measure_p95(http_client, "/graph/embeddings/3d", samples=8)
    assert p95 < 500, f"/graph/embeddings/3d cached p95 {p95:.1f}ms"
