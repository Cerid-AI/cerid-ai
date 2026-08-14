# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Routers must signal failure via HTTP status codes, not a 200 + {"error"}
envelope (FE then shows success while nothing changed)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

_EMAIL_CFG = {
    "host": "imap.example.com",
    "port": 993,
    "user": "u",
    "password": "p",  # pragma: allowlist secret
    "folder": "INBOX",
    "poll_interval": 15,
}


def _client(router) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_configure_email_unreachable_host_returns_422() -> None:
    """A bad host/port/credentials (validation failure) is user input → 422,
    not a 500, and it must not trip the email-imap circuit breaker."""
    from app.routers.data_sources import router

    with patch(
        "app.data_sources.email_imap.save_email_config",
        new=AsyncMock(side_effect=OSError("[Errno 8] nodename nor servname provided")),
    ):
        resp = _client(router).post("/data-sources/email/configure", json=_EMAIL_CFG)
    assert resp.status_code == 422
    assert "IMAP" in resp.json()["detail"]


def test_configure_email_success_returns_configured() -> None:
    from app.routers.data_sources import router

    with patch("app.data_sources.email_imap.save_email_config", new=AsyncMock(return_value=None)):
        resp = _client(router).post("/data-sources/email/configure", json=_EMAIL_CFG)
    assert resp.status_code == 200
    assert resp.json()["status"] == "configured"


# ── data-sources: not-found → 404 (not 200 + error) ─────────────────────────

def test_enable_unknown_source_returns_404() -> None:
    from app.routers.data_sources import router

    resp = _client(router).post("/data-sources/__no_such_source__/enable")
    assert resp.status_code == 404


def test_disable_unknown_source_returns_404() -> None:
    from app.routers.data_sources import router

    resp = _client(router).post("/data-sources/__no_such_source__/disable")
    assert resp.status_code == 404


# ── custom-agents: unimplemented stream → 501 (not silent no-op) ─────────────

def test_custom_agent_query_rejects_streaming() -> None:
    from app.routers import custom_agents

    with (
        patch.object(custom_agents, "get_neo4j", return_value=object()),
        patch("app.db.neo4j.agents.get_agent", return_value={"agent_id": "a1", "domains": []}),
    ):
        resp = _client(custom_agents.router).post(
            "/custom-agents/a1/query",
            json={"query": "hello there", "stream": True},
        )
    assert resp.status_code == 501


def test_custom_agent_query_missing_agent_returns_404() -> None:
    from app.routers import custom_agents

    with (
        patch.object(custom_agents, "get_neo4j", return_value=object()),
        patch("app.db.neo4j.agents.get_agent", return_value=None),
    ):
        resp = _client(custom_agents.router).post(
            "/custom-agents/ghost/query",
            json={"query": "hello there", "stream": False},
        )
    assert resp.status_code == 404
