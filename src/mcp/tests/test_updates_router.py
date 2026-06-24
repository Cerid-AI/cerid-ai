# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for /updates/check — app-version update check (ST10).

Mocks the GitHub releases API so no real network calls are made.
Verifies:
* ``running`` always reflects ``get_version()``.
* Newer tag → ``update_available: true`` + ``release_url`` populated.
* Same/older tag → ``update_available: false``.
* Fetch failure / timeout → ``update_available: false`` + ``error`` set, NOT 500.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app() -> FastAPI:
    from app.routers.updates import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture()
def client():
    return TestClient(_make_app())


_FETCH_TARGET = "app.routers.updates._cached_fetch_latest_release"
_FETCH_LIVE_TARGET = "app.routers.updates._fetch_latest_release"
_VERSION_TARGET = "app.routers.updates.get_version"


# ── running version always from get_version() ──────────────────────────────

class TestRunningVersion:
    def test_running_reflects_get_version(self, client):
        with (
            patch(_VERSION_TARGET, return_value="1.2.3"),
            patch(_FETCH_TARGET, new=AsyncMock(return_value=None)),
        ):
            resp = client.get("/updates/check")
        assert resp.status_code == 200
        assert resp.json()["running"] == "1.2.3"


# ── update_available: true when newer tag ──────────────────────────────────

class TestNewerTag:
    def test_update_available_true_with_release_url(self, client):
        release_url = "https://github.com/Cerid-AI/cerid-ai/releases/tag/v99.0.0"
        with (
            patch(_VERSION_TARGET, return_value="1.0.0"),
            patch(_FETCH_TARGET, new=AsyncMock(return_value={"latest": "99.0.0", "release_url": release_url})),
        ):
            resp = client.get("/updates/check")
        assert resp.status_code == 200
        body = resp.json()
        assert body["update_available"] is True
        assert body["latest"] == "99.0.0"
        assert body["release_url"] == release_url

    def test_latest_with_v_prefix_stripped(self, client):
        with (
            patch(_VERSION_TARGET, return_value="1.0.0"),
            patch(_FETCH_TARGET, new=AsyncMock(return_value={"latest": "2.0.0", "release_url": "https://example.com/releases/v2.0.0"})),
        ):
            resp = client.get("/updates/check")
        body = resp.json()
        assert body["latest"] == "2.0.0"  # no leading 'v'
        assert body["update_available"] is True


# ── update_available: false when same or older tag ─────────────────────────

class TestSameOrOlderTag:
    def test_same_version_not_available(self, client):
        with (
            patch(_VERSION_TARGET, return_value="1.5.0"),
            patch(_FETCH_TARGET, new=AsyncMock(return_value={"latest": "1.5.0", "release_url": "https://example.com"})),
        ):
            resp = client.get("/updates/check")
        assert resp.status_code == 200
        body = resp.json()
        assert body["update_available"] is False

    def test_older_tag_not_available(self, client):
        with (
            patch(_VERSION_TARGET, return_value="2.0.0"),
            patch(_FETCH_TARGET, new=AsyncMock(return_value={"latest": "1.9.9", "release_url": "https://example.com"})),
        ):
            resp = client.get("/updates/check")
        assert resp.status_code == 200
        assert resp.json()["update_available"] is False


# ── graceful degradation on fetch failure ─────────────────────────────────

class TestFetchFailure:
    def test_network_error_returns_200_with_error_field(self, client):
        with (
            patch(_VERSION_TARGET, return_value="1.0.0"),
            patch(_FETCH_TARGET, new=AsyncMock(side_effect=Exception("connection refused"))),
        ):
            resp = client.get("/updates/check")
        assert resp.status_code == 200
        body = resp.json()
        assert body["update_available"] is False
        assert body["latest"] is None
        assert "error" in body
        assert body["error"]  # non-empty

    def test_timeout_returns_200_with_error_field(self, client):
        import httpx

        with (
            patch(_VERSION_TARGET, return_value="1.0.0"),
            patch(_FETCH_TARGET, new=AsyncMock(side_effect=httpx.TimeoutException("timed out"))),
        ):
            resp = client.get("/updates/check")
        assert resp.status_code == 200
        body = resp.json()
        assert body["update_available"] is False
        assert body["error"]

    def test_none_result_means_couldnt_check(self, client):
        """_cached_fetch_latest_release returning None (rate-limited etc.) → graceful."""
        with (
            patch(_VERSION_TARGET, return_value="1.0.0"),
            patch(_FETCH_TARGET, new=AsyncMock(return_value=None)),
        ):
            resp = client.get("/updates/check")
        assert resp.status_code == 200
        body = resp.json()
        assert body["update_available"] is False
        assert body["latest"] is None
        assert body.get("error")  # set to explain couldn't check


# ── force=true bypasses the cache ─────────────────────────────────────────────

class TestForceBypass:
    """ST10: ?force=true must bypass the in-memory cache and call _fetch_latest_release."""

    def test_force_fetches_live_not_cached(self, client):
        """With a warm cache, force=true should still call _fetch_latest_release."""
        # Prime the cache with a stale result
        import time

        import app.routers.updates as updates_mod
        updates_mod._cache["result"] = {"latest": "0.0.1", "release_url": "https://example.com"}
        updates_mod._cache["expires_at"] = time.monotonic() + 3600.0

        fresh_release = {"latest": "99.9.9", "release_url": "https://github.com/Cerid-AI/cerid-ai/releases/tag/v99.9.9"}

        with (
            patch(_VERSION_TARGET, return_value="1.0.0"),
            patch(_FETCH_LIVE_TARGET, new=AsyncMock(return_value=fresh_release)) as mock_live,
        ):
            resp = client.get("/updates/check?force=true")

        assert resp.status_code == 200
        mock_live.assert_called_once()
        body = resp.json()
        assert body["latest"] == "99.9.9"
        assert body["update_available"] is True

    def test_force_updates_cache_for_subsequent_calls(self, client):
        """After a forced fetch, subsequent non-forced calls see the fresh result."""
        import time

        import app.routers.updates as updates_mod

        # Start with an expired cache so we can tell what got written
        updates_mod._cache["result"] = None
        updates_mod._cache["expires_at"] = 0.0

        fresh_release = {"latest": "5.0.0", "release_url": "https://example.com/v5"}

        with (
            patch(_VERSION_TARGET, return_value="1.0.0"),
            patch(_FETCH_LIVE_TARGET, new=AsyncMock(return_value=fresh_release)),
        ):
            client.get("/updates/check?force=true")

        # Cache should now hold the fresh result
        assert updates_mod._cache["result"] == fresh_release
        assert updates_mod._cache["expires_at"] > time.monotonic()

    def test_non_forced_uses_cache_path(self, client):
        """Without force=true, the cached fetch path (_cached_fetch_latest_release) is used."""
        with (
            patch(_VERSION_TARGET, return_value="1.0.0"),
            patch(_FETCH_TARGET, new=AsyncMock(return_value={"latest": "2.0.0", "release_url": ""})) as mock_cached,
            patch(_FETCH_LIVE_TARGET, new=AsyncMock()) as mock_live,
        ):
            resp = client.get("/updates/check")

        assert resp.status_code == 200
        mock_cached.assert_called_once()
        mock_live.assert_not_called()
