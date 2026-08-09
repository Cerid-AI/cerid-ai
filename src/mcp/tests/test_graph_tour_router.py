# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Unit tests for /graph/tour/* (Phase B Day 7)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def mock_neo4j_with_rows():
    rows: list[list[dict]] = [[]]

    fake_driver = MagicMock()
    fake_session = MagicMock()
    fake_session.__enter__ = lambda self: self
    fake_session.__exit__ = lambda self, exc_type, exc, tb: None

    def _run(*_args, **_kwargs):
        result = MagicMock()
        result.data = lambda: rows[0]
        return result

    fake_session.run = _run
    fake_driver.session = lambda: fake_session

    def set_rows(new_rows):
        rows[0] = new_rows

    return fake_driver, set_rows


@pytest.fixture
def pro_enabled_client(mock_neo4j_with_rows):
    from app.routers import graph_tour

    driver, _ = mock_neo4j_with_rows
    app = FastAPI()
    app.include_router(graph_tour.router)
    with patch("app.routers.graph_tour._is_pro_enabled", return_value=True), \
         patch("app.routers.graph_tour.get_neo4j", return_value=driver):
        yield TestClient(app)


@pytest.fixture
def pro_disabled_client(mock_neo4j_with_rows):
    from app.routers import graph_tour

    driver, _ = mock_neo4j_with_rows
    app = FastAPI()
    app.include_router(graph_tour.router)
    with patch("app.routers.graph_tour._is_pro_enabled", return_value=False), \
         patch("app.routers.graph_tour.get_neo4j", return_value=driver):
        yield TestClient(app)


def _make_row(entity_id: str, mention_count: int = 10, has_coords: bool = True) -> dict:
    return {
        "id": entity_id,
        "name": f"Entity {entity_id}",
        "mention_count": mention_count,
        "x": 1.0 if has_coords else None,
        "y": 2.0 if has_coords else None,
        "z": 3.0 if has_coords else None,
    }


def test_health_endpoint_exposes_feature_flag(pro_enabled_client):
    r = pro_enabled_client.get("/graph/tour/health")
    assert r.status_code == 200
    body = r.json()
    assert "pro_visualization_tour_enabled" in body
    assert "max_stops" in body
    assert "default_duration_s" in body


def test_generate_rejects_without_pro(pro_disabled_client):
    r = pro_disabled_client.post("/graph/tour/generate", json={})
    assert r.status_code == 403
    assert "pro" in r.json()["detail"].lower()


def test_generate_returns_404_when_no_entities(pro_enabled_client, mock_neo4j_with_rows):
    _, set_rows = mock_neo4j_with_rows
    set_rows([])
    r = pro_enabled_client.post("/graph/tour/generate", json={"max_stops": 4})
    assert r.status_code == 404


def test_generate_returns_tour_arc(pro_enabled_client, mock_neo4j_with_rows):
    _, set_rows = mock_neo4j_with_rows
    set_rows([
        _make_row("a", mention_count=100),
        _make_row("b", mention_count=50),
        _make_row("c", mention_count=20),
    ])
    r = pro_enabled_client.post("/graph/tour/generate", json={"max_stops": 3, "duration_s": 30})
    assert r.status_code == 200
    arc = r.json()
    assert len(arc["stops"]) == 3
    for stop in arc["stops"]:
        assert "entity_id" in stop
        assert "entity_name" in stop
        assert len(stop["camera"]) == 3
        assert len(stop["look_at"]) == 3
        assert stop["duration_ms"] > 0
        assert isinstance(stop["narration"], str)
    assert arc["total_duration_ms"] > 0
    assert arc["summary"]


def test_generate_promotes_focal_entity_to_first_stop(pro_enabled_client, mock_neo4j_with_rows):
    _, set_rows = mock_neo4j_with_rows
    set_rows([
        _make_row("a", mention_count=100),
        _make_row("b", mention_count=50),
        _make_row("c", mention_count=20),
    ])
    r = pro_enabled_client.post(
        "/graph/tour/generate",
        json={"focal_entity": "c", "max_stops": 3},
    )
    assert r.status_code == 200
    arc = r.json()
    assert arc["stops"][0]["entity_id"] == "c"


def test_generate_clamps_max_stops(pro_enabled_client):
    r = pro_enabled_client.post("/graph/tour/generate", json={"max_stops": 99})
    assert r.status_code == 422  # Pydantic le=20

    r2 = pro_enabled_client.post("/graph/tour/generate", json={"max_stops": 1})
    assert r2.status_code == 422  # Pydantic ge=2


def test_narration_scales_with_mention_count(pro_enabled_client, mock_neo4j_with_rows):
    _, set_rows = mock_neo4j_with_rows
    set_rows([
        _make_row("popular", mention_count=200),
        _make_row("specialist", mention_count=3),
    ])
    r = pro_enabled_client.post("/graph/tour/generate", json={"max_stops": 2})
    arc = r.json()
    popular_narration = next(s["narration"] for s in arc["stops"] if s["entity_id"] == "popular")
    specialist_narration = next(s["narration"] for s in arc["stops"] if s["entity_id"] == "specialist")
    assert "anchor" in popular_narration.lower() or "200" in popular_narration
    assert popular_narration != specialist_narration


# ── Phase M Day 4: free-tier 15s preview path ──────────────────────


def test_preview_works_without_pro(pro_disabled_client, mock_neo4j_with_rows):
    """Community users can request preview=true and get a clamped
    tour instead of a 403."""
    _, set_rows = mock_neo4j_with_rows
    set_rows([
        _make_row("a", mention_count=100),
        _make_row("b", mention_count=50),
        _make_row("c", mention_count=20),
        _make_row("d", mention_count=10),
    ])
    r = pro_disabled_client.post(
        "/graph/tour/generate",
        json={"preview": True, "max_stops": 8, "duration_s": 90},
    )
    assert r.status_code == 200
    arc = r.json()
    # Preview clamps to 3 stops + 15s total regardless of request body
    assert len(arc["stops"]) <= 3
    assert arc["total_duration_ms"] <= 16_000  # 15s with rounding slack


def test_preview_marks_summary(pro_disabled_client, mock_neo4j_with_rows):
    _, set_rows = mock_neo4j_with_rows
    set_rows([_make_row("a", mention_count=10)])
    r = pro_disabled_client.post(
        "/graph/tour/generate",
        json={"preview": True},
    )
    arc = r.json()
    assert "[Preview]" in arc["summary"]
    assert "Upgrade to Pro" in arc["summary"]


def test_preview_truncates_narration(pro_disabled_client, mock_neo4j_with_rows):
    _, set_rows = mock_neo4j_with_rows
    set_rows([_make_row("a", mention_count=200)])
    r = pro_disabled_client.post(
        "/graph/tour/generate",
        json={"preview": True},
    )
    arc = r.json()
    # Each preview narration is a single sentence (terminated by ".")
    for stop in arc["stops"]:
        assert stop["narration"].count(".") <= 1


def test_pro_user_ignores_preview_flag(pro_enabled_client, mock_neo4j_with_rows):
    """When the Pro flag IS on, preview=true falls through to a full
    Pro tour (no clamping, no preview prefix). The preview path is
    only meaningful for community users."""
    _, set_rows = mock_neo4j_with_rows
    set_rows([
        _make_row("a", mention_count=100),
        _make_row("b", mention_count=50),
        _make_row("c", mention_count=20),
        _make_row("d", mention_count=10),
        _make_row("e", mention_count=5),
    ])
    r = pro_enabled_client.post(
        "/graph/tour/generate",
        json={"preview": True, "max_stops": 5, "duration_s": 75},
    )
    assert r.status_code == 200
    arc = r.json()
    assert len(arc["stops"]) == 5  # not clamped
    assert "[Preview]" not in arc["summary"]
