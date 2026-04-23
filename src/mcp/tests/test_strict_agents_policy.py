# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sprint 1C — STRICT_AGENTS_ONLY kill switch.

Verifies that:

* :func:`is_strict_mode` parses the env var correctly across truthy /
  falsy / unset / unknown values, and reads it per-call (no module-
  level capture — protects against the 2026-04-22 stale-key bug).
* :func:`enforce_strict_mode` raises ``HTTPException(403)`` with a
  clear operator-facing message when on; returns silently when off.
* The dependency is wired on the custom-agents router so EVERY
  endpoint — including the no-Neo4j ``/templates`` one — denies with
  403 when the flag is on.
* Default deployment posture (no env var set) leaves the surface open
  — Sprint 1C must not break existing custom-agents users.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.routers.custom_agents import router as custom_agents_router
from app.services.strict_agents_policy import (
    ENV_STRICT,
    enforce_strict_mode,
    is_strict_mode,
)

# No file-level pytestmark — pytest-asyncio's ``asyncio_mode = "auto"``
# (pyproject.toml) auto-discovers async tests; the sync ``TestClient``
# tests below would get a spurious warning under a forced asyncio
# marker because TestClient itself is synchronous.


# ---------------------------------------------------------------------------
# is_strict_mode — env parsing
# ---------------------------------------------------------------------------


class TestIsStrictMode:
    async def test_default_is_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENV_STRICT, raising=False)
        assert is_strict_mode() is False

    async def test_empty_string_is_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_STRICT, "")
        assert is_strict_mode() is False

    @pytest.mark.parametrize("val", ["true", "1", "yes", "on", "TRUE", "Yes", "  on  "])
    async def test_truthy_values(
        self, monkeypatch: pytest.MonkeyPatch, val: str,
    ) -> None:
        monkeypatch.setenv(ENV_STRICT, val)
        assert is_strict_mode() is True

    @pytest.mark.parametrize("val", ["false", "0", "no", "off", "FALSE", "anything"])
    async def test_falsy_or_unknown_values(
        self, monkeypatch: pytest.MonkeyPatch, val: str,
    ) -> None:
        monkeypatch.setenv(ENV_STRICT, val)
        assert is_strict_mode() is False

    async def test_read_per_call_not_at_import(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Per-call read — operator can flip without restart. Regression
        guard against the lessons.md module-level capture bug."""
        monkeypatch.setenv(ENV_STRICT, "false")
        assert is_strict_mode() is False
        monkeypatch.setenv(ENV_STRICT, "true")
        assert is_strict_mode() is True
        monkeypatch.setenv(ENV_STRICT, "false")
        assert is_strict_mode() is False


# ---------------------------------------------------------------------------
# enforce_strict_mode — dependency behavior in isolation
# ---------------------------------------------------------------------------


class TestEnforceStrictMode:
    async def test_returns_silently_when_off(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(ENV_STRICT, raising=False)
        # Must not raise — caller (FastAPI) treats no-exception as "permit"
        enforce_strict_mode()

    async def test_raises_403_when_on(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(ENV_STRICT, "true")
        with pytest.raises(HTTPException) as ei:
            enforce_strict_mode()
        assert ei.value.status_code == 403
        assert "STRICT_AGENTS_ONLY" in ei.value.detail
        assert "disabled" in ei.value.detail.lower()


# ---------------------------------------------------------------------------
# Router-level integration — every endpoint gated when on, free when off
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    """Minimal FastAPI app wrapping just the custom-agents router."""
    app = FastAPI()
    app.include_router(custom_agents_router)
    return TestClient(app)


class TestRouterGate:
    def test_off_mode_lets_request_reach_endpoint(
        self, monkeypatch: pytest.MonkeyPatch, client: TestClient,
    ) -> None:
        """In off mode the gate must NOT short-circuit. ``/templates`` reads
        a static in-memory list — no Neo4j needed — so a 200 here proves
        the dependency yielded and the body executed."""
        monkeypatch.delenv(ENV_STRICT, raising=False)
        response = client.get("/custom-agents/templates")
        assert response.status_code == 200
        body = response.json()
        assert "templates" in body

    def test_on_mode_denies_templates_endpoint(
        self, monkeypatch: pytest.MonkeyPatch, client: TestClient,
    ) -> None:
        """When the flag is on, even the no-side-effect ``/templates`` GET
        returns 403 — clean lockdown, no grey area for compliance."""
        monkeypatch.setenv(ENV_STRICT, "true")
        response = client.get("/custom-agents/templates")
        assert response.status_code == 403
        assert "STRICT_AGENTS_ONLY" in response.json()["detail"]

    def test_on_mode_denies_create_without_touching_neo4j(
        self, monkeypatch: pytest.MonkeyPatch, client: TestClient,
    ) -> None:
        """The 403 fires before the request body reaches the endpoint, so
        the test passes even though no Neo4j is configured. This is the
        compliance value of router-level dependency gating."""
        monkeypatch.setenv(ENV_STRICT, "true")
        response = client.post(
            "/custom-agents",
            json={
                "name": "evil-agent",
                "description": "x",
                "system_prompt": "y",
            },
        )
        assert response.status_code == 403

    def test_on_mode_denies_runtime_query(
        self, monkeypatch: pytest.MonkeyPatch, client: TestClient,
    ) -> None:
        """The runtime path — POST /custom-agents/{id}/query — is the
        most security-critical surface (it actually executes user-defined
        agent behavior). Must 403 before any agent loads."""
        monkeypatch.setenv(ENV_STRICT, "true")
        response = client.post(
            "/custom-agents/some-id/query",
            json={"query": "anything"},
        )
        assert response.status_code == 403

    @pytest.mark.parametrize("method,path", [
        ("GET", "/custom-agents"),
        ("GET", "/custom-agents/some-id"),
        ("PATCH", "/custom-agents/some-id"),
        ("DELETE", "/custom-agents/some-id"),
        ("POST", "/custom-agents/from-template/research-assistant"),
    ])
    def test_on_mode_denies_every_endpoint(
        self,
        monkeypatch: pytest.MonkeyPatch,
        client: TestClient,
        method: str,
        path: str,
    ) -> None:
        """Sweep the rest of the surface to prove the router-level
        dependency catches every endpoint in one place."""
        monkeypatch.setenv(ENV_STRICT, "true")
        response = client.request(method, path, json={})
        assert response.status_code == 403, (
            f"{method} {path} returned {response.status_code} in strict mode"
        )


# ---------------------------------------------------------------------------
# Module surface — single source of truth for the env var name
# ---------------------------------------------------------------------------


async def test_env_var_constant_is_canonical() -> None:
    """Hardcoded env var name — protect against accidental rename that
    would silently disable the kill switch. Operators will set the
    string literal in their .env / compose; if we rename, their
    existing config silently stops working."""
    assert ENV_STRICT == "STRICT_AGENTS_ONLY"
