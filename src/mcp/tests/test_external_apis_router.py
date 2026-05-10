# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Router tests for /external-apis (Phase API.1 + API.2).

Verifies:
* GET /external-apis returns all 8 entries.
* GET /external-apis/{slug}/health calls health_check() (mocked).
* POST /external-apis/{slug}/enabled round-trips Redis state (fakeredis).
* 404 on unknown slug for both health and enabled endpoints.

Does NOT make real network calls — health_check() is patched.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.external_apis import router
from app.services.external_apis import registry as _registry

# ---------------------------------------------------------------------------
# Test app setup
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_redis():
    """Return a fakeredis instance for in-memory state testing."""
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture()
def app():
    _app = FastAPI()
    _app.include_router(router)
    return _app


@pytest.fixture()
def client(app: FastAPI, fake_redis):
    """TestClient with Redis and auth middleware patched out."""
    with patch("app.routers.external_apis._get_redis_client", return_value=fake_redis):
        yield TestClient(app)


# ---------------------------------------------------------------------------
# GET /external-apis
# ---------------------------------------------------------------------------


class TestListAdapters:
    def test_returns_all_eight_adapters(self, client: TestClient, fake_redis):
        resp = client.get("/external-apis")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 8
        slugs = {a["slug"] for a in data["adapters"]}
        expected = {"wikipedia", "wikidata", "openlibrary", "stackexchange", "arxiv", "github", "packages", "osm"}
        assert slugs == expected

    def test_each_entry_has_required_fields(self, client: TestClient):
        resp = client.get("/external-apis")
        assert resp.status_code == 200
        for entry in resp.json()["adapters"]:
            assert "slug" in entry
            assert "display_name" in entry
            assert "enabled" in entry
            assert "requires_key" in entry
            assert "key_configured" in entry

    def test_keyless_adapters_are_enabled_by_default(self, client: TestClient):
        resp = client.get("/external-apis")
        # All 8 are keyless (except github which works without a key)
        # github has requires_key=False so it should also be enabled by default
        for entry in resp.json()["adapters"]:
            if not entry["requires_key"]:
                assert entry["enabled"] is True, f"{entry['slug']} should be enabled by default"


# ---------------------------------------------------------------------------
# GET /external-apis/{slug}/health
# ---------------------------------------------------------------------------


class TestAdapterHealth:
    def test_health_ok(self, client: TestClient):
        with patch.object(_registry.get_adapter("wikipedia"), "health_check", new=AsyncMock(return_value=True)):
            resp = client.get("/external-apis/wikipedia/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["slug"] == "wikipedia"
        assert body["status"] == "ok"

    def test_health_error_when_check_returns_false(self, client: TestClient):
        with patch.object(_registry.get_adapter("arxiv"), "health_check", new=AsyncMock(return_value=False)):
            resp = client.get("/external-apis/arxiv/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "error"

    def test_health_error_when_check_raises(self, client: TestClient):
        with patch.object(
            _registry.get_adapter("osm"),
            "health_check",
            new=AsyncMock(side_effect=Exception("network error")),
        ), patch("app.routers.external_apis.log_swallowed_error"):
            resp = client.get("/external-apis/osm/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "error"
        assert body["detail"] is not None

    def test_health_unknown_slug_returns_404(self, client: TestClient):
        resp = client.get("/external-apis/nonexistent/health")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /external-apis/{slug}/enabled
# ---------------------------------------------------------------------------


class TestSetEnabled:
    def test_enable_adapter(self, client: TestClient, fake_redis):
        resp = client.post("/external-apis/wikipedia/enabled", json={"enabled": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["slug"] == "wikipedia"
        assert body["enabled"] is True
        # Verify Redis state
        assert fake_redis.get("cerid:external_apis:wikipedia:enabled") == "1"

    def test_disable_adapter(self, client: TestClient, fake_redis):
        resp = client.post("/external-apis/github/enabled", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        assert fake_redis.get("cerid:external_apis:github:enabled") == "0"

    def test_round_trip_enabled_state(self, client: TestClient, fake_redis):
        # Disable
        resp = client.post("/external-apis/osm/enabled", json={"enabled": False})
        assert resp.status_code == 200
        # Re-enable
        resp = client.post("/external-apis/osm/enabled", json={"enabled": True})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True
        # List should reflect the state
        list_resp = client.get("/external-apis")
        osm_entry = next(e for e in list_resp.json()["adapters"] if e["slug"] == "osm")
        assert osm_entry["enabled"] is True

    def test_unknown_slug_returns_404(self, client: TestClient):
        resp = client.post("/external-apis/nonexistent/enabled", json={"enabled": True})
        assert resp.status_code == 404

    def test_503_when_redis_unavailable(self, app: FastAPI):
        with patch("app.routers.external_apis._get_redis_client", return_value=None):
            with TestClient(app) as c:
                resp = c.post("/external-apis/wikipedia/enabled", json={"enabled": True})
        assert resp.status_code == 503
