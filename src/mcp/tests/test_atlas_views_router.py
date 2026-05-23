# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the /atlas/views router (Phase A Day 12)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def mock_redis_hash():
    """In-memory fake redis with hash-shaped operations."""
    state: dict[str, dict[str, str]] = {}
    fake = MagicMock()

    def hgetall(key):
        return dict(state.get(key, {}))

    def hget(key, field):
        return state.get(key, {}).get(field)

    def hset(key, field, value):
        bucket = state.setdefault(key, {})
        is_new = field not in bucket
        bucket[field] = value
        return 1 if is_new else 0

    def hdel(key, *fields):
        bucket = state.get(key)
        if not bucket:
            return 0
        removed = 0
        for f in fields:
            if f in bucket:
                del bucket[f]
                removed += 1
        return removed

    def hlen(key):
        return len(state.get(key, {}))

    fake.hgetall = hgetall
    fake.hget = hget
    fake.hset = hset
    fake.hdel = hdel
    fake.hlen = hlen
    fake._state = state
    return fake


@pytest.fixture
def client(mock_redis_hash):
    from app.routers import atlas_views

    app = FastAPI()
    app.include_router(atlas_views.router)
    with patch("app.routers.atlas_views.get_redis", return_value=mock_redis_hash):
        yield TestClient(app)


def _body():
    return {
        "name": "Alex deep dive",
        "entity": "alex",
        "hops": 2,
        "filter": None,
        "mode": "atlas",
        "lenses": ["quality", "contradiction"],
        "camera": {"x": 0.1, "y": -0.2, "ratio": 1.5, "angle": 0.0},
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health_reports_redis(client, mock_redis_hash):
    r = client.get("/atlas/views/health")
    assert r.status_code == 200
    payload = r.json()
    assert payload["redis_available"] is True
    assert "max_views_per_user" in payload


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_create_returns_201_with_view(client):
    r = client.post("/atlas/views", json=_body())
    assert r.status_code == 201
    view = r.json()
    assert view["name"] == "Alex deep dive"
    assert view["entity"] == "alex"
    assert view["hops"] == 2
    assert "view_id" in view
    assert "created_at" in view
    assert "updated_at" in view
    assert view["created_at"] == view["updated_at"]
    assert view["camera"]["ratio"] == 1.5


def test_list_returns_created_views(client):
    body = _body()
    client.post("/atlas/views", json=body)
    body2 = dict(body)
    body2["name"] = "Other"
    client.post("/atlas/views", json=body2)

    r = client.get("/atlas/views")
    assert r.status_code == 200
    payload = r.json()
    names = [v["name"] for v in payload["views"]]
    assert set(names) == {"Alex deep dive", "Other"}


def test_create_rejects_missing_required_fields(client):
    # Missing entity
    r = client.post("/atlas/views", json={"name": "X", "hops": 2})
    assert r.status_code == 422


def test_create_clamps_hops_to_1_3(client):
    body = _body()
    body["hops"] = 5
    r = client.post("/atlas/views", json=body)
    assert r.status_code == 422


def test_update_replaces_view_in_place(client):
    create = client.post("/atlas/views", json=_body()).json()
    vid = create["view_id"]

    body = _body()
    body["name"] = "Updated name"
    body["hops"] = 3
    r = client.patch(f"/atlas/views/{vid}", json=body)
    assert r.status_code == 200
    out = r.json()
    assert out["view_id"] == vid
    assert out["name"] == "Updated name"
    assert out["hops"] == 3
    # created_at preserved, updated_at refreshed
    assert out["created_at"] == create["created_at"]
    assert out["updated_at"] >= create["updated_at"]


def test_update_missing_returns_404(client):
    r = client.patch("/atlas/views/nonexistent", json=_body())
    assert r.status_code == 404


def test_delete_is_idempotent(client):
    create = client.post("/atlas/views", json=_body()).json()
    vid = create["view_id"]
    # First delete: succeeds
    r1 = client.delete(f"/atlas/views/{vid}")
    assert r1.status_code == 204
    # Second delete: still 204 (idempotent — caller doesn't need to track)
    r2 = client.delete(f"/atlas/views/{vid}")
    assert r2.status_code == 204
    # List is empty
    listed = client.get("/atlas/views").json()
    assert listed["views"] == []


def test_corrupt_row_skipped_in_list(client, mock_redis_hash):
    # Insert a valid view + a corrupt row
    client.post("/atlas/views", json=_body())
    mock_redis_hash._state["cerid:atlas:views:default"]["badrow"] = "not-json{"
    r = client.get("/atlas/views")
    assert r.status_code == 200
    payload = r.json()
    # Valid view still returned, corrupt row dropped silently
    assert len(payload["views"]) == 1


# ---------------------------------------------------------------------------
# Quota
# ---------------------------------------------------------------------------


def test_per_user_cap_enforced(client, monkeypatch):
    from app.routers import atlas_views
    monkeypatch.setattr(atlas_views, "_MAX_VIEWS_PER_USER", 2)
    # Force Pro tier so the free-tier cap doesn't shadow the hard cap.
    monkeypatch.setattr(atlas_views, "_is_pro_unlocked", lambda: True)
    client.post("/atlas/views", json=_body())
    client.post("/atlas/views", json=_body())
    # 3rd should be rejected
    r = client.post("/atlas/views", json=_body())
    assert r.status_code == 429
    assert "limit" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Phase M Day 6 — mode validation + free tier cap + mode filter
# ---------------------------------------------------------------------------


def test_create_accepts_supported_modes(client, monkeypatch):
    from app.routers import atlas_views
    monkeypatch.setattr(atlas_views, "_is_pro_unlocked", lambda: True)
    for mode in ("atlas", "constellation", "timeline", "wiki"):
        body = _body()
        body["name"] = f"view-{mode}"
        body["mode"] = mode
        r = client.post("/atlas/views", json=body)
        assert r.status_code == 201, f"{mode} rejected: {r.text}"
        assert r.json()["mode"] == mode


def test_create_rejects_unknown_mode(client):
    body = _body()
    body["mode"] = "bogus"
    r = client.post("/atlas/views", json=body)
    assert r.status_code == 422


def test_list_filters_by_mode(client, monkeypatch):
    from app.routers import atlas_views
    monkeypatch.setattr(atlas_views, "_is_pro_unlocked", lambda: True)
    for mode in ("atlas", "timeline", "wiki"):
        body = _body()
        body["name"] = f"v-{mode}"
        body["mode"] = mode
        client.post("/atlas/views", json=body)

    r = client.get("/atlas/views?mode=timeline")
    assert r.status_code == 200
    views = r.json()["views"]
    assert len(views) == 1
    assert views[0]["mode"] == "timeline"


def test_list_unknown_mode_returns_empty(client, monkeypatch):
    from app.routers import atlas_views
    monkeypatch.setattr(atlas_views, "_is_pro_unlocked", lambda: True)
    client.post("/atlas/views", json=_body())
    # Mode validator rejects on POST but list accepts arbitrary strings
    # so the frontend can stay loose.
    r = client.get("/atlas/views?mode=zzz")
    assert r.status_code == 200
    assert r.json()["views"] == []


def test_free_tier_cap_at_three(client, monkeypatch):
    from app.routers import atlas_views
    monkeypatch.setattr(atlas_views, "_is_pro_unlocked", lambda: False)
    monkeypatch.setattr(atlas_views, "_FREE_TIER_MAX_VIEWS", 3)
    for i in range(3):
        body = _body()
        body["name"] = f"v{i}"
        assert client.post("/atlas/views", json=body).status_code == 201
    # 4th rejected with 402 Payment Required, surfacing upgrade-to-Pro
    r = client.post("/atlas/views", json=_body())
    assert r.status_code == 402
    assert "Pro" in r.json()["detail"]


def test_pro_unlocks_unlimited(client, monkeypatch):
    from app.routers import atlas_views
    monkeypatch.setattr(atlas_views, "_is_pro_unlocked", lambda: True)
    monkeypatch.setattr(atlas_views, "_FREE_TIER_MAX_VIEWS", 1)
    # Three creates should all succeed since Pro is on
    for i in range(3):
        body = _body()
        body["name"] = f"v{i}"
        assert client.post("/atlas/views", json=body).status_code == 201


def test_health_exposes_phase_m_fields(client):
    r = client.get("/atlas/views/health")
    body = r.json()
    assert "free_tier_max_views" in body
    assert "supported_modes" in body
    assert "pro_unlocked" in body
    assert set(body["supported_modes"]) == {"atlas", "constellation", "timeline", "wiki"}
