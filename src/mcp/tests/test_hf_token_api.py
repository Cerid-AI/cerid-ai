# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the write-only HuggingFace token API (Phase E).

Security invariant: the raw token value MUST NOT appear in any
response body JSON for any of the three endpoints.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app() -> FastAPI:
    from app.routers.settings_secrets import register_redacted_validation_handler, router

    app = FastAPI()
    app.include_router(router)
    register_redacted_validation_handler(app)
    return app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("CERID_ENV_FILE", str(env_file))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    app = _make_app()
    return TestClient(app)


class TestGetStatus:
    def test_no_token_returns_not_configured(self, client):
        resp = client.get("/settings/hf-token")
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is False
        assert body["last4"] is None
        assert body["updated_at"] is None

    def test_with_token_returns_configured_and_last4(self, client, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "hf_testabcd0123wxyz")  # pragma: allowlist secret
        resp = client.get("/settings/hf-token")
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is True
        assert body["last4"] == "wxyz"


class TestPutToken:
    TEST_TOKEN = "hf_0000000011111111abcdefghxyzw"  # pragma: allowlist secret

    def test_put_writes_to_os_environ(self, client):
        with patch("app.routers.settings_secrets._update_env_file") as mock_write:
            resp = client.put(
                "/settings/hf-token",
                json={"token": self.TEST_TOKEN},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is True
        assert body["last4"] == self.TEST_TOKEN[-4:]
        mock_write.assert_called_once_with({"HF_TOKEN": self.TEST_TOKEN})
        assert os.environ.get("HF_TOKEN") == self.TEST_TOKEN

    def test_put_response_body_never_contains_raw_token(self, client):
        with patch("app.routers.settings_secrets._update_env_file"):
            resp = client.put(
                "/settings/hf-token",
                json={"token": self.TEST_TOKEN},
            )
        assert resp.status_code == 200
        raw_json = resp.text
        assert self.TEST_TOKEN not in raw_json
        body = resp.json()
        for _field, value in body.items():
            assert value != self.TEST_TOKEN

    def test_put_short_token_does_not_echo_input_in_422(self, client):
        short_token = "hf-fake-ab"
        resp = client.put(
            "/settings/hf-token",
            json={"token": short_token},
        )
        assert resp.status_code == 422
        assert short_token not in resp.text


class TestTestEndpoint:
    TEST_TOKEN = "hf_validtoken000000000deadbeef00"  # pragma: allowlist secret

    @staticmethod
    def _mock_resp(status: int, body: dict | None = None) -> MagicMock:
        m = MagicMock()
        m.status_code = status
        m.json.return_value = body or {}
        return m

    def _mock_httpx(self, whoami_status: int, gated_statuses: dict[str, int]):
        """Builds an AsyncClient mock that returns a whoami response then
        per-model gated-probe responses in the order they're called."""
        whoami = self._mock_resp(whoami_status, {"name": "testuser"})
        gated_responses = [
            self._mock_resp(gated_statuses[model_id])
            for model_id in (
                "pyannote/speaker-diarization-3.1",
                "pyannote/segmentation-3.0",
            )
        ]

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=[whoami, *gated_responses])
        return mock_client

    def test_test_valid_token_all_gates_accepted(self, client):
        with patch(
            "app.routers.settings_secrets.httpx.AsyncClient",
            return_value=self._mock_httpx(
                whoami_status=200,
                gated_statuses={
                    "pyannote/speaker-diarization-3.1": 200,
                    "pyannote/segmentation-3.0": 200,
                },
            ),
        ):
            resp = client.post(
                "/settings/hf-token/test",
                json={"token": self.TEST_TOKEN},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["gated_model_access"] == {
            "pyannote/speaker-diarization-3.1": True,
            "pyannote/segmentation-3.0": True,
        }

    def test_test_valid_token_one_gate_not_accepted(self, client):
        """403 from gated probe means token valid but ToS not accepted."""
        with patch(
            "app.routers.settings_secrets.httpx.AsyncClient",
            return_value=self._mock_httpx(
                whoami_status=200,
                gated_statuses={
                    "pyannote/speaker-diarization-3.1": 403,
                    "pyannote/segmentation-3.0": 200,
                },
            ),
        ):
            resp = client.post(
                "/settings/hf-token/test",
                json={"token": self.TEST_TOKEN},
            )
        body = resp.json()
        assert body["valid"] is True
        assert body["gated_model_access"]["pyannote/speaker-diarization-3.1"] is False
        assert body["gated_model_access"]["pyannote/segmentation-3.0"] is True

    def test_test_invalid_token_401(self, client):
        mock_resp = self._mock_resp(401)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)
        with patch("app.routers.settings_secrets.httpx.AsyncClient", return_value=mock_client):
            resp = client.post(
                "/settings/hf-token/test",
                json={"token": self.TEST_TOKEN},
            )
        body = resp.json()
        assert body["valid"] is False
        assert "401" in body["error"]

    def test_test_no_body_no_stored_token(self, client):
        resp = client.post("/settings/hf-token/test", json={})
        body = resp.json()
        assert body["valid"] is False
        assert "No token" in body["error"]
