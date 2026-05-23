# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for /connectors REST surface (Phase F.2 deferred cleanup)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app() -> FastAPI:
    from app.routers.connectors import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture()
def client(monkeypatch):
    # Privacy-default: clear env so missing_env paths surface
    for var in ("CERID_CONNECTORS_BEARER", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    return TestClient(_make_app())


class TestListConnectors:
    def test_lists_all_six(self, client):
        resp = client.get("/connectors")
        assert resp.status_code == 200
        slugs = [c["slug"] for c in resp.json()["connectors"]]
        assert "gmail" in slugs
        assert "google_calendar" in slugs
        assert "outlook" in slugs
        assert "outlook_calendar" in slugs
        assert "apple_calendar" in slugs
        assert "apple_photos" in slugs

    def test_each_carries_required_fields(self, client):
        connectors = client.get("/connectors").json()["connectors"]
        for c in connectors:
            assert "feature_flag" in c
            assert "auth_kind" in c
            assert "missing_env" in c
            assert "instruction_doc" in c

    def test_missing_env_reported_for_gmail(self, client):
        connectors = client.get("/connectors").json()["connectors"]
        gmail = next(c for c in connectors if c["slug"] == "gmail")
        assert "CERID_CONNECTORS_BEARER" in gmail["missing_env"]
        assert "GOOGLE_OAUTH_CLIENT_ID" in gmail["missing_env"]
        assert gmail["env_complete"] is False

    def test_apple_connectors_have_no_env_requirements(self, client):
        connectors = client.get("/connectors").json()["connectors"]
        apple_cal = next(c for c in connectors if c["slug"] == "apple_calendar")
        assert apple_cal["missing_env"] == []
        assert apple_cal["env_complete"] is True
        assert apple_cal["auth_kind"] == "tcc_only"


class TestGetConnector:
    def test_unknown_slug_returns_404(self, client):
        resp = client.get("/connectors/nonexistent")
        assert resp.status_code == 404

    def test_known_slug_returns_status(self, client):
        resp = client.get("/connectors/gmail")
        assert resp.status_code == 200
        body = resp.json()
        assert body["slug"] == "gmail"
        assert body["feature_flag"] == "gmail_connector"


class TestStartAuth:
    def test_google_returns_browser_url(self, client):
        resp = client.post("/connectors/gmail/auth/start")
        assert resp.status_code == 200
        body = resp.json()
        assert body["auth_kind"] == "google_oauth"
        assert "http" in body["auth_url"].lower()

    def test_microsoft_returns_device_code_instructions(self, client):
        resp = client.post("/connectors/outlook/auth/start")
        body = resp.json()
        assert body["auth_kind"] == "msal_device_code"
        assert "devicelogin" in body["instructions"]

    def test_apple_returns_system_settings_link(self, client):
        resp = client.post("/connectors/apple_calendar/auth/start")
        body = resp.json()
        assert body["auth_kind"] == "tcc_only"
        assert body["settings_url"].startswith("x-apple.systempreferences:")

    def test_unknown_slug_404s(self, client):
        resp = client.post("/connectors/nope/auth/start")
        assert resp.status_code == 404


class TestAuthStatus:
    def test_microsoft_incomplete_when_env_missing(self, client):
        resp = client.get("/connectors/outlook/auth/status")
        body = resp.json()
        assert body["slug"] == "outlook"
        assert body["completed"] is False
        assert "env" in body["detail"].lower() or "missing" in body["detail"].lower()

    def test_apple_complete_when_data_source_configured(self, client):
        # Stub the registry so it returns a "configured" data source.
        from unittest.mock import MagicMock

        from config.features import is_feature_enabled  # noqa: F401

        mock_ds = MagicMock()
        mock_ds.is_configured.return_value = True

        with (
            patch("app.data_sources.registry.get", return_value=mock_ds),
            patch("config.features.is_feature_enabled", return_value=True),
        ):
            resp = client.get("/connectors/apple_calendar/auth/status")
        body = resp.json()
        assert body["slug"] == "apple_calendar"
        assert body["completed"] is True


class TestDisconnect:
    def test_google_returns_docker_exec_instruction(self, client):
        resp = client.post("/connectors/gmail/disconnect")
        body = resp.json()
        assert body["cleared"] is False
        assert "docker compose" in body["detail"]

    def test_microsoft_returns_ms365_logout_instruction(self, client):
        resp = client.post("/connectors/outlook/disconnect")
        body = resp.json()
        assert "ms365-mcp logout" in body["detail"]

    def test_apple_returns_settings_revocation(self, client):
        resp = client.post("/connectors/apple_calendar/disconnect")
        body = resp.json()
        assert "System Settings" in body["detail"]
