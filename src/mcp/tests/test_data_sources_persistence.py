# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Persistence tests for /data-sources enable/disable (beta triage P0-C.4).

Verifies the Redis-backed enable-state wiring in ``app.data_sources``:

* POST /data-sources/{name}/enable|disable writes through to Redis.
* Persisted flags survive a simulated restart (fresh registry hydrated
  from the same fakeredis instance).
* Redis-less deployments keep the pre-persistence in-memory behaviour
  (toggle still succeeds, no 503).
* Response shapes and status codes are unchanged (backward-compatible).
"""
from __future__ import annotations

from unittest.mock import patch

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.data_sources as ds
from app.data_sources import hydrate_enabled_state, persist_enabled_state, registry
from app.data_sources.base import DataSource, DataSourceRegistry, DataSourceResult
from app.routers.data_sources import router


class _StubSource(DataSource):
    """Minimal concrete DataSource for registry tests."""

    def __init__(self, name: str, enabled: bool = True):
        self.name = name
        self.description = f"Stub {name}"
        self.requires_api_key = False
        self.domains: list[str] = []
        self.enabled = enabled

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        return []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_redis():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def _reset_hydration_and_state():
    """Isolate hydration flag + wikipedia enabled state across tests."""
    ds._hydrated = False
    wiki = registry.get("wikipedia")
    original = wiki.enabled if wiki is not None else True
    yield
    ds._hydrated = False
    if wiki is not None:
        wiki.enabled = original


@pytest.fixture()
def client(fake_redis):
    _app = FastAPI()
    _app.include_router(router)
    with patch("app.data_sources._get_redis_client", return_value=fake_redis):
        yield TestClient(_app)


# ---------------------------------------------------------------------------
# Write-through
# ---------------------------------------------------------------------------


class TestWriteThrough:
    def test_disable_persists_to_redis(self, client: TestClient, fake_redis):
        resp = client.post("/data-sources/wikipedia/disable")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "disabled"
        assert body["name"] == "wikipedia"
        assert fake_redis.get("cerid:data_sources:wikipedia:enabled") == "0"

    def test_enable_persists_to_redis(self, client: TestClient, fake_redis):
        resp = client.post("/data-sources/wikipedia/enable")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "enabled"
        assert body["name"] == "wikipedia"
        assert fake_redis.get("cerid:data_sources:wikipedia:enabled") == "1"

    def test_unknown_source_returns_404(self, client: TestClient):
        resp = client.post("/data-sources/nonexistent/enable")
        assert resp.status_code == 404

    def test_list_reflects_toggle(self, client: TestClient):
        client.post("/data-sources/wikipedia/disable")
        listed = client.get("/data-sources").json()
        wiki = next(s for s in listed["sources"] if s["name"] == "wikipedia")
        assert wiki["enabled"] is False


# ---------------------------------------------------------------------------
# Simulated restart — fresh registry + same Redis
# ---------------------------------------------------------------------------


class TestRestartSurvival:
    def test_persisted_disable_survives_restart(self, fake_redis):
        """A fresh registry (new process) hydrates the persisted flag."""
        persist_enabled_state("stub_a", False, redis_client=fake_redis)

        fresh = DataSourceRegistry()
        fresh.register(_StubSource("stub_a", enabled=True))  # code default: on
        fresh.register(_StubSource("stub_b", enabled=True))

        assert hydrate_enabled_state(fake_redis, reg=fresh, force=True) is True
        assert fresh.get("stub_a").enabled is False
        # No persisted key → code default untouched
        assert fresh.get("stub_b").enabled is True

    def test_persisted_reenable_survives_restart(self, fake_redis):
        persist_enabled_state("stub_a", False, redis_client=fake_redis)
        persist_enabled_state("stub_a", True, redis_client=fake_redis)

        fresh = DataSourceRegistry()
        fresh.register(_StubSource("stub_a", enabled=False))  # code default: off

        hydrate_enabled_state(fake_redis, reg=fresh, force=True)
        assert fresh.get("stub_a").enabled is True

    def test_router_toggle_survives_router_level_restart(self, client: TestClient, fake_redis):
        """Full loop through the REST surface: toggle → wipe in-memory →
        re-hydrate (as a fresh process would) → list shows persisted state."""
        client.post("/data-sources/wikipedia/disable")

        # Simulate restart: reset in-memory state + hydration flag
        registry.get("wikipedia").enabled = True
        ds._hydrated = False

        listed = client.get("/data-sources").json()
        wiki = next(s for s in listed["sources"] if s["name"] == "wikipedia")
        assert wiki["enabled"] is False


# ---------------------------------------------------------------------------
# Redis-less fallback (backward compatibility)
# ---------------------------------------------------------------------------


class TestRedisUnavailable:
    def test_toggle_still_succeeds_without_redis(self):
        _app = FastAPI()
        _app.include_router(router)
        with patch("app.data_sources._get_redis_client", return_value=None):
            with TestClient(_app) as c:
                resp = c.post("/data-sources/wikipedia/disable")
                assert resp.status_code == 200
                listed = c.get("/data-sources").json()
                wiki = next(s for s in listed["sources"] if s["name"] == "wikipedia")
                assert wiki["enabled"] is False

    def test_hydrate_returns_false_and_retries_later(self, fake_redis):
        with patch("app.data_sources._get_redis_client", return_value=None):
            ds._hydrated = False
            assert hydrate_enabled_state() is False
            assert ds._hydrated is False  # not marked — retried on next access
        # Redis comes back → hydration succeeds and marks the process
        assert hydrate_enabled_state(fake_redis) is True
        assert ds._hydrated is True

    def test_persist_returns_false_without_redis(self):
        with patch("app.data_sources._get_redis_client", return_value=None):
            assert persist_enabled_state("wikipedia", True) is False


# ---------------------------------------------------------------------------
# Hydration semantics
# ---------------------------------------------------------------------------


class TestHydration:
    def test_hydration_is_idempotent(self, fake_redis):
        fake_redis.set("cerid:data_sources:wikipedia:enabled", "0")
        assert hydrate_enabled_state(fake_redis) is True
        # Manual re-enable after hydration must not be clobbered by a
        # second (skipped) hydration pass.
        registry.get("wikipedia").enabled = True
        assert hydrate_enabled_state(fake_redis) is True
        assert registry.get("wikipedia").enabled is True

    def test_force_rehydrates(self, fake_redis):
        fake_redis.set("cerid:data_sources:wikipedia:enabled", "0")
        hydrate_enabled_state(fake_redis)
        registry.get("wikipedia").enabled = True
        hydrate_enabled_state(fake_redis, force=True)
        assert registry.get("wikipedia").enabled is False
