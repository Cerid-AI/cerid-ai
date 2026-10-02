# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""/sdk/v1/ingest and /ingest/file write only into the consumer's own domains.

/ingest/upload already enforced this; the text and file-path routes took any
domain the caller named, so a consumer scoped to one domain could write into
another.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.sdk import router as sdk_router

FINANCE = {"X-Client-ID": "cerid-finance"}  # registry: allowed_domains ["finance"]


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(sdk_router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _private_mode_off(monkeypatch):
    monkeypatch.setattr("app.services.private_mode.get_private_mode_level", lambda: 0)


@pytest.mark.parametrize("route,body", [
    ("/sdk/v1/ingest", {"content": "hello", "domain": "personal"}),
    ("/sdk/v1/ingest/file", {"file_path": "/tmp/x.txt", "domain": "personal"}),
])
def test_a_restricted_consumer_cannot_write_elsewhere(client, route, body):
    with patch("app.routers.sdk.ingest_content") as content, patch("app.routers.sdk.ingest_file") as file:
        resp = client.post(route, json=body, headers=FINANCE)
    assert resp.status_code == 403
    assert resp.json()["detail"]["retrieval_reason"] == "consumer_domain_restricted"
    content.assert_not_called()
    file.assert_not_called()


def test_a_restricted_consumer_defaults_to_its_only_domain(client):
    with patch("app.routers.sdk.ingest_content", MagicMock(return_value={"status": "ok"})) as spy:
        resp = client.post("/sdk/v1/ingest", json={"content": "hello"}, headers=FINANCE)
    assert resp.status_code == 200, resp.text
    assert spy.call_args.kwargs["domain"] == "finance"


def test_an_unrestricted_consumer_keeps_the_general_default(client):
    with patch("app.routers.sdk.ingest_content", MagicMock(return_value={"status": "ok"})) as spy:
        resp = client.post("/sdk/v1/ingest", json={"content": "hello"})
    assert resp.status_code == 200, resp.text
    assert spy.call_args.kwargs["domain"] == "general"


def test_an_unrestricted_file_ingest_still_auto_categorizes(client):
    with patch("app.routers.sdk.ingest_file", MagicMock(return_value={"status": "ok"})) as spy:
        resp = client.post("/sdk/v1/ingest/file", json={"file_path": "/tmp/x.txt"})
    assert resp.status_code == 200, resp.text
    assert spy.call_args.kwargs["domain"] == ""
