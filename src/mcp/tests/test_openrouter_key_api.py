# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the write-only OpenRouter key API (settings_secrets.py).

Security invariant: the raw API key value MUST NOT appear in any
response body JSON for any of the three endpoints.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def _make_app() -> FastAPI:
    """Build a minimal FastAPI app with only the settings_secrets router."""
    from app.routers.settings_secrets import register_redacted_validation_handler, router

    app = FastAPI()
    app.include_router(router)
    # Register the R4-1-safe 422 handler so validation errors never echo
    # the user-supplied key value in responses.
    register_redacted_validation_handler(app)
    return app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with CERID_ENV_FILE pointing to a temp .env so writes are safe."""
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("CERID_ENV_FILE", str(env_file))
    # Reset OPENROUTER_API_KEY so each test starts clean
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    app = _make_app()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Test 1: GET with no key set → configured=False
# ---------------------------------------------------------------------------

class TestGetStatus:
    def test_no_key_returns_not_configured(self, client):
        resp = client.get("/settings/openrouter-key")
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is False
        assert body["last4"] is None
        assert body["updated_at"] is None

    def test_with_key_returns_configured_and_last4(self, client, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-testabcd")  # pragma: allowlist secret
        resp = client.get("/settings/openrouter-key")
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is True
        assert body["last4"] == "abcd"
        # updated_at may be None if the key was set before meta tracking existed
        assert "updated_at" in body


# ---------------------------------------------------------------------------
# Test 3 & 4: PUT writes key + response never contains the raw key
# ---------------------------------------------------------------------------

class TestPutKey:
    TEST_KEY = "sk-or-v1-0000000011111111abcdefgh"  # pragma: allowlist secret

    def test_put_writes_to_os_environ(self, client, monkeypatch):
        with patch("app.routers.settings_secrets._update_env_file") as mock_write:
            resp = client.put(
                "/settings/openrouter-key",
                json={"api_key": self.TEST_KEY},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is True
        assert body["last4"] == self.TEST_KEY[-4:]
        # Confirm _update_env_file was called with the key
        mock_write.assert_called_once_with({"OPENROUTER_API_KEY": self.TEST_KEY})
        # os.environ should now hold the key (client runs in-process)
        assert os.environ.get("OPENROUTER_API_KEY") == self.TEST_KEY

    def test_put_response_body_never_contains_raw_key(self, client):
        """Security invariant: the raw key value must NOT appear anywhere in the JSON."""
        with patch("app.routers.settings_secrets._update_env_file"):
            resp = client.put(
                "/settings/openrouter-key",
                json={"api_key": self.TEST_KEY},
            )
        assert resp.status_code == 200
        raw_json = resp.text
        # The full key must not appear anywhere in the serialised response
        assert self.TEST_KEY not in raw_json
        # Also check the parsed dict — no field should equal the key
        body = resp.json()
        for _field, value in body.items():
            assert value != self.TEST_KEY, (
                f"Field '{_field}' leaked the raw API key in the response"
            )

    def test_put_short_key_does_not_echo_input_in_422(self, client):
        """R4-1 security invariant: 422 response on validation failure must
        NOT echo the user-supplied key value (even a wrong one)."""
        # Use a recognisable short-but-realistic-looking string so we can
        # grep the response for it unambiguously.
        short_key = "sk-fake-abc"
        resp = client.put(
            "/settings/openrouter-key",
            json={"api_key": short_key},
        )
        assert resp.status_code == 422
        body_text = resp.text
        # The full input must not appear anywhere in the response body.
        assert short_key not in body_text, (
            f"422 response echoed the user-supplied key input; "
            f"body was: {body_text}"
        )
        # Defence in depth: no field in the parsed body should carry the input.
        def _walk(obj):
            if isinstance(obj, str):
                yield obj
            elif isinstance(obj, dict):
                for v in obj.values():
                    yield from _walk(v)
            elif isinstance(obj, list):
                for item in obj:
                    yield from _walk(item)
        for s in _walk(resp.json()):
            assert short_key not in s, (
                f"422 response contained the key via a nested string: {s!r}"
            )


# ---------------------------------------------------------------------------
# Tests 5–7: POST /test endpoint
# ---------------------------------------------------------------------------

class TestTestEndpoint:
    TEST_KEY = "sk-or-v1-validkey00000000deadbeef"  # pragma: allowlist secret

    def _mock_httpx_200(self, credits=12.45):
        """Build an httpx mock that returns 200 with limit_remaining."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"limit_remaining": credits}}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)
        return mock_client

    def _mock_httpx_401(self):
        """Build an httpx mock that returns 401."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)
        return mock_client

    def test_test_with_body_key_valid(self, client):
        with patch("app.routers.settings_secrets.httpx.AsyncClient",
                   return_value=self._mock_httpx_200(12.45)):
            resp = client.post(
                "/settings/openrouter-key/test",
                json={"api_key": self.TEST_KEY},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["credits_remaining"] == pytest.approx(12.45)
        assert body["error"] is None

    def test_test_with_body_key_invalid_401(self, client):
        with patch("app.routers.settings_secrets.httpx.AsyncClient",
                   return_value=self._mock_httpx_401()):
            resp = client.post(
                "/settings/openrouter-key/test",
                json={"api_key": self.TEST_KEY},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert "401" in body["error"]

    def test_test_no_body_uses_stored_key(self, client, monkeypatch):
        """When no api_key in body, the endpoint must test the stored key."""
        monkeypatch.setenv("OPENROUTER_API_KEY", self.TEST_KEY)
        with patch("app.routers.settings_secrets.httpx.AsyncClient",
                   return_value=self._mock_httpx_200(5.00)):
            resp = client.post(
                "/settings/openrouter-key/test",
                json={},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["credits_remaining"] == pytest.approx(5.00)
