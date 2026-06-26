# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for pure-numpy layout helpers in compute_umap_3d.

Tests:
  - _convex_hull: Andrew's monotone chain on known input
  - _chaikin: output has more points and stays within original bbox
  - _procrustes_align: recovers known rotation + translation
  - noverlap: separates two coincident points
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

# ---- convex hull -----------------------------------------------------------


def test_convex_hull_square_and_inner_point():
    """Square with one interior point — hull should be the 4 corners."""
    from app.processor.jobs.compute_umap_3d import _convex_hull

    pts = np.array([
        [0.0, 0.0],
        [1.0, 0.0],
        [1.0, 1.0],
        [0.0, 1.0],
        [0.5, 0.5],  # interior — must NOT appear in hull
    ])
    hull = _convex_hull(pts)

    # Must be exactly 4 points.
    assert hull.shape == (4, 2), f"Expected 4 hull points, got {hull.shape[0]}"

    # All hull vertices must be corners of the square.
    hull_set = {(round(p[0], 6), round(p[1], 6)) for p in hull}
    expected = {(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)}
    assert hull_set == expected, f"Hull corners wrong: {hull_set}"


def test_convex_hull_collinear_points():
    """Three collinear points — hull should still return at least 2 pts."""
    from app.processor.jobs.compute_umap_3d import _convex_hull

    pts = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]])
    hull = _convex_hull(pts)
    # Monotone chain may return 2 extreme points for collinear input.
    assert len(hull) >= 2


def test_convex_hull_two_points():
    """Degenerate: fewer than 3 unique points returns as-is (no crash)."""
    from app.processor.jobs.compute_umap_3d import _convex_hull

    pts = np.array([[0.0, 0.0], [1.0, 0.0]])
    hull = _convex_hull(pts)
    assert len(hull) >= 1


# ---- Chaikin smoothing -----------------------------------------------------


def test_chaikin_increases_point_count():
    """One round of Chaikin doubles the vertex count."""
    from app.processor.jobs.compute_umap_3d import _chaikin

    pts = np.array([
        [0.0, 0.0],
        [2.0, 0.0],
        [2.0, 2.0],
        [0.0, 2.0],
    ])
    smoothed = _chaikin(pts, rounds=1)
    # Each of 4 vertices produces 2 new ones per round.
    assert len(smoothed) == 8


def test_chaikin_stays_within_bbox():
    """Chaikin output must not exceed the bounding box of the input."""
    from app.processor.jobs.compute_umap_3d import _chaikin

    pts = np.array([
        [-1.0, -1.0],
        [3.0, 0.0],
        [2.0, 4.0],
        [-0.5, 3.0],
    ])
    smoothed = _chaikin(pts, rounds=3)
    xmin, ymin = pts.min(axis=0)
    xmax, ymax = pts.max(axis=0)
    # Allow a tiny floating-point margin.
    eps = 1e-9
    assert float(smoothed[:, 0].min()) >= xmin - eps
    assert float(smoothed[:, 0].max()) <= xmax + eps
    assert float(smoothed[:, 1].min()) >= ymin - eps
    assert float(smoothed[:, 1].max()) <= ymax + eps


def test_chaikin_two_rounds_more_points_than_one():
    """Two rounds produce more points than one round."""
    from app.processor.jobs.compute_umap_3d import _chaikin

    pts = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
    r1 = _chaikin(pts, rounds=1)
    r2 = _chaikin(pts, rounds=2)
    assert len(r2) > len(r1)


def test_chaikin_degenerate_passthrough():
    """Fewer than 3 points returns the input unchanged (no crash)."""
    from app.processor.jobs.compute_umap_3d import _chaikin

    pts = np.array([[0.0, 0.0], [1.0, 0.0]])
    result = _chaikin(pts, rounds=2)
    assert len(result) == 2


# ---- Procrustes alignment --------------------------------------------------


def _make_rotation_matrix(theta: float) -> np.ndarray:
    c, s = math.cos(theta), math.sin(theta)
    return np.array([[c, -s], [s, c]])


def test_procrustes_recovers_rotation():
    """Apply a known rotation + translation, then align — should recover near-identity."""
    from app.processor.jobs.compute_umap_3d import ComputeUmap3DJob

    rng = np.random.default_rng(42)
    n = 20
    new_pos = rng.standard_normal((n, 2)) * 5.0

    # Transform: 45° rotation + translation (2, -3).
    theta = math.pi / 4
    R = _make_rotation_matrix(theta)
    old_pos = new_pos @ R.T + np.array([2.0, -3.0])

    old_x = np.array([old_pos[i, 0] for i in range(n)], dtype=object)
    old_y = np.array([old_pos[i, 1] for i in range(n)], dtype=object)

    aligned = ComputeUmap3DJob._procrustes_align(new_pos.copy(), old_x, old_y)

    # Aligned positions should be close to old_pos.
    diff = np.abs(aligned - old_pos)
    assert float(diff.max()) < 1e-6, f"Procrustes residual too large: {diff.max()}"


def test_procrustes_skipped_when_too_few_anchors():
    """Fewer than _PROCRUSTES_MIN_ANCHORS valid rows → positions unchanged."""
    from app.processor.jobs.compute_umap_3d import _PROCRUSTES_MIN_ANCHORS, ComputeUmap3DJob

    n = _PROCRUSTES_MIN_ANCHORS - 1
    new_pos = np.eye(n, 2)
    old_x = np.array([None] * n, dtype=object)
    old_y = np.array([None] * n, dtype=object)

    result = ComputeUmap3DJob._procrustes_align(new_pos.copy(), old_x, old_y)
    assert np.allclose(result, new_pos)


def test_procrustes_handles_mixed_none_anchors():
    """Only rows with non-None old_x/old_y are used as anchors; result still n rows."""
    from app.processor.jobs.compute_umap_3d import _PROCRUSTES_MIN_ANCHORS, ComputeUmap3DJob

    n = _PROCRUSTES_MIN_ANCHORS + 5
    rng = np.random.default_rng(7)
    new_pos = rng.standard_normal((n, 2))

    # Give half the rows valid old coords (identity transform → aligned ≈ new_pos).
    old_x: list = []
    old_y: list = []
    for i in range(n):
        if i % 2 == 0:
            old_x.append(float(new_pos[i, 0]))
            old_y.append(float(new_pos[i, 1]))
        else:
            old_x.append(None)
            old_y.append(None)

    result = ComputeUmap3DJob._procrustes_align(
        new_pos.copy(),
        np.array(old_x, dtype=object),
        np.array(old_y, dtype=object),
    )
    assert result.shape == new_pos.shape


# ---- noverlap --------------------------------------------------------------


def test_noverlap_separates_coincident_points():
    """Two coincident nodes must be pushed apart after the noverlap pass."""
    from app.processor.jobs.compute_umap_3d import ComputeUmap3DJob

    pos = np.array([[0.0, 0.0], [0.0, 0.0]], dtype=np.float64)
    degree = np.array([0.0, 0.0])

    result = ComputeUmap3DJob._noverlap(pos, degree)
    dist = float(np.sqrt(((result[0] - result[1]) ** 2).sum()))
    min_dist = 2 * (_NOVERLAP_BASE_RADIUS + _NOVERLAP_DEGREE_COEFF * math.sqrt(0))
    assert dist >= min_dist * 0.9, (
        f"Nodes still overlap: dist={dist:.6f} < min_dist={min_dist:.6f}"
    )


def test_noverlap_leaves_well_separated_nodes_unchanged():
    """Nodes already far apart should not move significantly."""
    from app.processor.jobs.compute_umap_3d import ComputeUmap3DJob

    pos = np.array([[0.0, 0.0], [100.0, 0.0]], dtype=np.float64)
    degree = np.array([1.0, 1.0])

    result = ComputeUmap3DJob._noverlap(pos.copy(), degree)
    assert float(abs(result[0, 0] - 0.0)) < 0.01
    assert float(abs(result[1, 0] - 100.0)) < 0.01


def test_noverlap_high_degree_hub_has_larger_effective_radius():
    """A hub node (high degree) should push a zero-degree node further."""
    from app.processor.jobs.compute_umap_3d import ComputeUmap3DJob

    pos_lo = np.array([[0.0, 0.0], [0.01, 0.0]], dtype=np.float64)
    pos_hi = np.array([[0.0, 0.0], [0.01, 0.0]], dtype=np.float64)
    deg_lo = np.array([0.0, 0.0])
    deg_hi = np.array([100.0, 0.0])

    result_lo = ComputeUmap3DJob._noverlap(pos_lo, deg_lo)
    result_hi = ComputeUmap3DJob._noverlap(pos_hi, deg_hi)

    dist_lo = float(np.sqrt(((result_lo[0] - result_lo[1]) ** 2).sum()))
    dist_hi = float(np.sqrt(((result_hi[0] - result_hi[1]) ** 2).sum()))
    # Hub should push further because its radius is larger.
    assert dist_hi > dist_lo


# ---- density argmax --------------------------------------------------------


def test_density_argmax_cluster():
    """Points clustered in one corner → anchor should be near that corner."""
    from app.processor.jobs.compute_umap_3d import _density_argmax

    pts = np.array([
        [0.1, 0.1], [0.12, 0.09], [0.11, 0.12],
        [10.0, 10.0],  # one outlier in opposite corner
    ])
    anchor = _density_argmax(pts, bins=10)
    # The dense cluster is near (0.1, 0.1); anchor should be in that region.
    assert float(anchor[0]) < 2.0
    assert float(anchor[1]) < 2.0


# ---- silhouette score ------------------------------------------------------


def test_centroid_silhouette_two_separated_clusters():
    """Well-separated clusters should yield a positive silhouette score."""
    from app.processor.jobs.compute_umap_3d import _centroid_silhouette

    rng = np.random.default_rng(0)
    # Two tight, well-separated clusters.
    c1 = rng.standard_normal((20, 2)) * 0.5 + np.array([0.0, 0.0])
    c2 = rng.standard_normal((20, 2)) * 0.5 + np.array([50.0, 0.0])
    pos2d = np.vstack([c1, c2])

    entities = [{"id": f"e{i}"} for i in range(40)]
    by_community = {"A": list(range(20)), "B": list(range(20, 40))}

    score = _centroid_silhouette(entities, pos2d, by_community, max_sample=800)
    assert score > 0.5, f"Expected positive silhouette, got {score}"


def test_centroid_silhouette_single_community_returns_zero():
    """Single community → can't compute b → returns 0."""
    from app.processor.jobs.compute_umap_3d import _centroid_silhouette

    pos2d = np.random.default_rng(1).standard_normal((10, 2))
    entities = [{"id": f"e{i}"} for i in range(10)]
    by_community = {"A": list(range(10))}

    score = _centroid_silhouette(entities, pos2d, by_community)
    assert score == 0.0


# ---- hash01 ----------------------------------------------------------------


def test_hash01_range_and_determinism():
    """_hash01 must return a value in [0, 1) and be deterministic."""
    from app.processor.jobs.compute_umap_3d import ComputeUmap3DJob

    v1 = ComputeUmap3DJob._hash01("test-community")
    v2 = ComputeUmap3DJob._hash01("test-community")
    assert v1 == v2
    assert 0.0 <= v1 < 1.0


# ---- _fetch_edges: SIMILAR_TO edges included and down-weighted ---------------


class _MockRow(dict):
    """Minimal stand-in for a Neo4j result row."""


def _make_mock_driver(rows: list[dict]) -> Any:
    """Build a mock Neo4j driver whose session().run().data() returns *rows*."""
    from unittest.mock import MagicMock

    row_objects = [_MockRow(r) for r in rows]
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.run.return_value.data.return_value = row_objects

    mock_driver = MagicMock()
    mock_driver.session.return_value = mock_session
    return mock_driver


def test_fetch_edges_includes_similar_to_edges():
    """_fetch_edges must return SIMILAR_TO edges in addition to CO_MENTIONED."""
    from app.processor.jobs.compute_umap_3d import ComputeUmap3DJob

    rows = [
        {"s": "entity-a", "t": "entity-b", "w": 3.0, "rel_type": "CO_MENTIONED"},
        {"s": "entity-c", "t": "entity-d", "w": 0.9, "rel_type": "SIMILAR_TO"},
    ]
    driver = _make_mock_driver(rows)
    job = ComputeUmap3DJob()
    edges = job._fetch_edges(driver)

    ids = {(s, t) for s, t, _ in edges}
    assert ("entity-a", "entity-b") in ids, "CO_MENTIONED edge missing"
    assert ("entity-c", "entity-d") in ids, "SIMILAR_TO edge missing"


def test_fetch_edges_similar_to_downweighted():
    """SIMILAR_TO edge weight must be scaled by SEMANTIC_EDGE_SPRING_SCALE (default 0.6)."""
    import config
    from app.processor.jobs.compute_umap_3d import ComputeUmap3DJob

    scale = config.SEMANTIC_EDGE_SPRING_SCALE
    raw_score = 0.9
    rows = [
        {"s": "entity-c", "t": "entity-d", "w": raw_score, "rel_type": "SIMILAR_TO"},
    ]
    driver = _make_mock_driver(rows)
    job = ComputeUmap3DJob()
    edges = job._fetch_edges(driver)

    assert len(edges) == 1
    s, t, w = edges[0]
    assert s == "entity-c"
    assert t == "entity-d"
    expected = raw_score * scale
    assert abs(w - expected) < 1e-9, f"Expected weight {expected}, got {w}"


def test_fetch_edges_co_mentioned_not_downweighted():
    """CO_MENTIONED edge weight must NOT be scaled — only SIMILAR_TO edges are."""
    from app.processor.jobs.compute_umap_3d import ComputeUmap3DJob

    raw_weight = 5.0
    rows = [
        {"s": "entity-a", "t": "entity-b", "w": raw_weight, "rel_type": "CO_MENTIONED"},
    ]
    driver = _make_mock_driver(rows)
    job = ComputeUmap3DJob()
    edges = job._fetch_edges(driver)

    assert len(edges) == 1
    _, _, w = edges[0]
    assert abs(w - raw_weight) < 1e-9, f"CO_MENTIONED weight altered: expected {raw_weight}, got {w}"


# ---- module-level import needed for test ----
from app.processor.jobs.compute_umap_3d import (  # noqa: E402
    _NOVERLAP_BASE_RADIUS,
    _NOVERLAP_DEGREE_COEFF,
)
