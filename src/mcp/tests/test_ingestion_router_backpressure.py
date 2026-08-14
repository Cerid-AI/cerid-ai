# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Router-boundary test for ingest backpressure (AF-042).

``/ingest_file`` and ``/ingest_batch`` each carry a catch-all
``except Exception: raise HTTPException(500, ...)`` that would otherwise
downgrade a ``StorageLimitExceededError`` (http_status=507) to a generic
500 before it ever reaches the app-wide ``CeridError`` handler. This test
drives the endpoints through a real FastAPI app (with that handler
registered, exactly as app/main.py wires it) to confirm 507 — not 500 —
is what a caller actually sees.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from errors import StorageLimitExceededError


def _make_app() -> FastAPI:
    from app.error_handlers import register_cerid_error_handler
    from app.routers.ingestion import router

    app = FastAPI()
    app.include_router(router)
    register_cerid_error_handler(app)
    return app


@pytest.fixture()
def client():
    return TestClient(_make_app(), raise_server_exceptions=False)


class TestIngestFileBackpressureStatus:
    def test_storage_limit_exceeded_returns_507_not_500(self, client):
        with patch("app.routers.ingestion.ingest_file") as mock_ingest_file:
            mock_ingest_file.side_effect = StorageLimitExceededError(
                "Corpus storage at 92% of 2048MB (critical threshold 80%) — ingest rejected",
            )
            resp = client.post("/ingest_file", json={"file_path": "/archive/x/note.txt"})

        assert resp.status_code == 507
        assert resp.json()["error_code"] == "STORAGE_ERROR"


class TestIngestBatchBackpressureStatus:
    def test_storage_limit_exceeded_returns_507_not_500(self, client):
        with patch("app.routers.ingestion.ingest_batch") as mock_ingest_batch:
            mock_ingest_batch.side_effect = StorageLimitExceededError(
                "Corpus storage at 92% of 2048MB (critical threshold 80%) — ingest rejected",
            )
            resp = client.post(
                "/ingest_batch",
                json={"items": [{"content": "one"}, {"content": "two"}]},
            )

        assert resp.status_code == 507
        assert resp.json()["error_code"] == "STORAGE_ERROR"
