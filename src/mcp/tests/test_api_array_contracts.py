# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests: list endpoints must return arrays, never null, under their envelope key.

Guards against the t.map bug class: a backend returning null where the
frontend expects an array. Each test hits the live endpoint via TestClient.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


def test_pro_automations_list_returns_array(client: TestClient) -> None:
    resp = client.get("/settings/pro-automations")
    assert resp.status_code == 200
    body = resp.json()
    assert "automations" in body
    assert isinstance(body["automations"], list), (
        f"Expected list, got {type(body['automations'])}"
    )


def test_whisper_models_list_returns_array(client: TestClient) -> None:
    resp = client.get("/settings/whisper/models")
    assert resp.status_code == 200
    body = resp.json()
    assert "models" in body
    assert isinstance(body["models"], list)


@pytest.mark.integration
def test_watched_folders_list_returns_array(client: TestClient) -> None:
    resp = client.get("/watched-folders")
    assert resp.status_code == 200
    body = resp.json()
    assert "folders" in body
    assert isinstance(body["folders"], list)
