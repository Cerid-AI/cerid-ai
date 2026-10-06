# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""``POST /sources/{id}/webhook-url`` — the one place the webhook token is
shown in cleartext. It answers POST only, is never cacheable, and builds the
URL from the configured external host rather than the request's Host header."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import sources as sources_mod

_WEBHOOK_SOURCE = {
    "id": "src-hook",
    "kind": "webhook",
    "family": "webhook",
    "display_name": "Hook",
    "tier": "core",
    "status": "connected",
    "config": {"token": "tok_abc"},  # pragma: allowlist secret
    "sync_cursor": {},
}


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(sources_mod.router)
    return TestClient(app)


@pytest.fixture
def webhook_source(monkeypatch):
    monkeypatch.setattr(sources_mod.config, "MCP_EXTERNAL_HOST", "kb.example.test:8888")
    with (
        patch("app.routers.sources.srcdb.get_source", return_value=dict(_WEBHOOK_SOURCE)),
        patch("app.routers.sources.get_neo4j", return_value=MagicMock()),
    ):
        yield


def test_get_is_not_allowed(webhook_source):
    assert _client().get("/sources/src-hook/webhook-url").status_code == 405


def test_post_builds_url_from_the_configured_host_not_the_host_header(webhook_source):
    r = _client().post("/sources/src-hook/webhook-url", headers={"Host": "evil.example"})
    assert r.status_code == 200
    body = r.json()
    assert body["url"] == "http://kb.example.test:8888/sdk/v1/ingest/webhook/tok_abc"
    assert "evil.example" not in body["curl_example"]
    assert body["url"] in body["curl_example"]


def test_configured_host_may_carry_its_own_scheme(webhook_source, monkeypatch):
    monkeypatch.setattr(sources_mod.config, "MCP_EXTERNAL_HOST", "https://kb.example.test/")
    body = _client().post("/sources/src-hook/webhook-url").json()
    assert body["url"] == "https://kb.example.test/sdk/v1/ingest/webhook/tok_abc"


def test_post_is_no_store(webhook_source):
    r = _client().post("/sources/src-hook/webhook-url")
    assert r.headers["cache-control"] == "no-store"


def test_existing_source_without_a_secret_keeps_reporting_no_hmac(webhook_source):
    """Sources created before the HMAC default keep their value; reading the
    URL never rewrites the stored config."""
    with patch("app.routers.sources.srcdb.update_source_config") as update:
        body = _client().post("/sources/src-hook/webhook-url").json()
    assert body["require_hmac"] is False
    update.assert_not_called()
