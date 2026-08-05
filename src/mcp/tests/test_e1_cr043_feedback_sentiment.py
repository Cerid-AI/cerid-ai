# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 CR-043 — /ingest/feedback sentiment validation + empty body 422."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    import config
    from app.routers import ingestion

    monkeypatch.setattr(config, "ENABLE_FEEDBACK_LOOP", True, raising=False)
    app = FastAPI()
    app.include_router(ingestion.router)
    with patch.object(ingestion, "get_redis", return_value=MagicMock()):
        yield TestClient(app, raise_server_exceptions=False)


def test_sentiment_up_returns_202(client: TestClient) -> None:
    with patch("core.utils.cache.log_conversation_sentiment") as log:
        res = client.post(
            "/ingest/feedback",
            json={
                "conversation_id": "c1",
                "message_id": "m1",
                "sentiment": "up",
            },
        )
    assert res.status_code == 202
    log.assert_called_once()


def test_invalid_sentiment_returns_422(client: TestClient) -> None:
    res = client.post(
        "/ingest/feedback",
        json={"conversation_id": "c1", "message_id": "m1", "sentiment": "meh"},
    )
    assert res.status_code == 422


def test_empty_feedback_body_returns_422(client: TestClient) -> None:
    res = client.post("/ingest/feedback", json={})
    assert res.status_code == 422
