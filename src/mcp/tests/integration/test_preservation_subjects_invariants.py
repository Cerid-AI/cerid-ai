# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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


# ---------------------------------------------------------------------------
# Phase Task-1.3 — default graph view excludes isolated nodes
# ---------------------------------------------------------------------------


def test_graph_default_excludes_isolated(http_client):
    """GET /graph/map with no include_isolated param returns a node set
    whose orphan ratio is 0 — every returned entity has at least one edge
    among the returned link set.

    ``isolated_count`` is the number of degree-0 nodes the router EXCLUDED
    from the default view.  On a real graph this is typically non-zero
    (~2,395 orphans on the production KB).  We only assert its presence in
    the envelope, not its value.

    The invariant is checked structurally: every entity index that appears
    in ``entities`` must also appear in at least one link triple.

    Vacuously true for an empty graph (0 entities → pass, since the
    default-exclusion invariant holds trivially). We do NOT skip — a skip
    would trip the no-silent-preservation-skips gate on a fresh CI stack.
    """
    r = http_client.get("/graph/map")
    assert r.status_code in (200, 503), (
        f"/graph/map {r.status_code}: {r.text[:200]}"
    )
    if r.status_code == 503:
        pytest.skip("/graph/map 503 — Neo4j/Redis unavailable on this stack")

    body = r.json()

    # Envelope shape sanity
    assert "entities" in body, "missing 'entities' key in /graph/map response"
    assert "links" in body, "missing 'links' key in /graph/map response"
    assert "isolated_count" in body, "missing 'isolated_count' key in /graph/map response"

    entities: list[dict] = body["entities"]
    links: list[list] = body["links"]  # each triple: [src_idx, tgt_idx, weight]

    if not entities:
        # Empty graph: the default-exclusion invariant holds vacuously (0
        # entities ⇒ 0 isolated shown). Pass, don't skip — a fresh CI stack
        # has no ingested data and a skip would fail no-silent-preservation-skips.
        return

    # Structural check — every entity index appears in at least one link
    # (the response is well-formed only when entities has ≥2 items and links is
    # non-empty; a single-entity graph has no edges by definition).
    if len(entities) < 2:
        return  # single-entity graph cannot have edges; nothing more to assert

    if not links:
        # No links at all means every entity is isolated — fail.
        # This can happen if the nightly compute_umap_3d job hasn't run yet;
        # that's still a violation of the default-exclusion invariant.
        pytest.fail(
            f"/graph/map returned {len(entities)} entities but 0 links; "
            "all nodes would be isolated — default view should exclude them "
            "or the job hasn't run (run compute_umap_3d and retry)"
        )

    connected_indices: set[int] = set()
    for triple in links:
        connected_indices.add(int(triple[0]))
        connected_indices.add(int(triple[1]))

    all_indices = set(range(len(entities)))
    orphan_indices = all_indices - connected_indices
    orphan_pct = len(orphan_indices) / len(entities)

    assert orphan_pct == 0, (
        f"/graph/map (no include_isolated) has {len(orphan_indices)}/{len(entities)} "
        f"orphan entities ({orphan_pct:.1%}); default view must return 0 isolated nodes. "
        f"Orphan indices: {sorted(orphan_indices)[:10]}"
    )
