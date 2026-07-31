"""`/mcp/*` auth exemption is conditional on a loopback bind.

Regression guard for the 2026-07-29 GA audit finding S1: `/mcp/*` was
unconditionally exempt from `APIKeyMiddleware`, so in LAN mode
(`CERID_BIND_ADDR=0.0.0.0`) every MCP tool — including deletes and purges —
was reachable with no credential, contradicting `docs/LAN_REMOTE_ACCESS.md`.

Both halves matter:
  - LAN bind  → `/mcp/*` MUST require the key (closes the hole)
  - loopback  → `/mcp/*` MUST stay exempt (headerless local MCP clients,
                including this repo's own `.mcp.json`, keep working)
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.auth import APIKeyMiddleware

API_KEY = "test-key-abc123"  # pragma: allowlist secret


def _app(monkeypatch, bind_addr: str) -> TestClient:
    monkeypatch.setenv("CERID_BIND_ADDR", bind_addr)
    app = FastAPI()

    @app.get("/mcp/probe")
    async def mcp_probe():
        return {"ok": True}

    @app.get("/api/probe")
    async def api_probe():
        return {"ok": True}

    @app.get("/health/ping")
    async def health_probe():
        return {"ok": True}

    app.add_middleware(APIKeyMiddleware, api_key=API_KEY)
    return TestClient(app)


@pytest.mark.parametrize("bind", ["127.0.0.1", "::1", "localhost"])
def test_loopback_bind_keeps_mcp_exempt(monkeypatch, bind):
    """Local MCP clients send no X-API-Key — they must keep working."""
    client = _app(monkeypatch, bind)
    assert client.get("/mcp/probe").status_code == 200


@pytest.mark.parametrize("bind", ["0.0.0.0", "192.168.1.50"])
def test_non_loopback_bind_requires_key_on_mcp(monkeypatch, bind):
    """LAN mode: the documented X-API-Key promise must actually hold."""
    client = _app(monkeypatch, bind)
    assert client.get("/mcp/probe").status_code == 401
    assert client.get(
        "/mcp/probe", headers={"X-API-Key": API_KEY}
    ).status_code == 200


def test_api_routes_always_require_key(monkeypatch):
    client = _app(monkeypatch, "127.0.0.1")
    assert client.get("/api/probe").status_code == 401
    assert client.get("/api/probe", headers={"X-API-Key": API_KEY}).status_code == 200


def test_health_probes_stay_exempt_on_lan(monkeypatch):
    """Docker healthchecks must not need the key, loopback or not."""
    client = _app(monkeypatch, "0.0.0.0")
    assert client.get("/health/ping").status_code == 200
