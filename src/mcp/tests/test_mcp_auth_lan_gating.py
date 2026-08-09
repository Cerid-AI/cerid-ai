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


def test_compose_forwards_bind_addr_into_the_mcp_container():
    """The middleware can only gate on a value it actually receives.

    Every test above monkeypatches ``CERID_BIND_ADDR`` straight into the test
    process, so they passed for a week while the deployed container never saw
    the variable at all: ``docker-compose.yml`` interpolated it host-side for
    the port binding, and ``scripts/start-cerid.sh`` exported it into the shell
    for LAN mode, but it was absent from the service's ``environment:`` block
    and never written to ``.env``. Inside the container
    ``os.getenv("CERID_BIND_ADDR", "127.0.0.1")`` therefore returned the
    default, ``_is_loopback_bind()`` returned True, and the whole LAN gate was
    inert on exactly the deployment it exists for.

    This asserts the plumbing rather than the logic — it is the half no
    monkeypatch can stand in for.
    """
    import re
    from pathlib import Path

    compose = Path(__file__).resolve().parents[3] / "docker-compose.yml"
    text = compose.read_text()

    # Isolate the mcp-server service block (up to the next top-level service).
    m = re.search(r"^  mcp-server:\n(.*?)(?=^  \S+:\n)", text, re.S | re.M)
    assert m, "mcp-server service not found in docker-compose.yml"
    block = m.group(1)

    env_section = block.split("environment:", 1)
    assert len(env_section) == 2, "mcp-server has no environment: block"

    # Parse actual entries, not substrings. The first draft of this test
    # asserted `"CERID_BIND_ADDR" in env_section[1]` and passed with the
    # variable deleted — the explanatory COMMENT beside it satisfied the
    # check. A gate a comment can satisfy is not a gate.
    entries = [
        ln.strip().lstrip("-").strip()
        for ln in env_section[1].splitlines()
        if ln.strip().startswith("-")
    ]
    names = {e.split("=", 1)[0].split(":", 1)[0].strip() for e in entries}

    assert "CERID_BIND_ADDR" in names, (
        "docker-compose.yml does not pass CERID_BIND_ADDR into mcp-server as an "
        "environment entry; the LAN auth gate in app/middleware/auth.py reads it "
        "from the container's own environment and silently falls back to the "
        "loopback default. A ports: interpolation does not reach the container."
    )
