# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The ingest-history stream had no writer, so its endpoint was always empty.

``/admin/ingest-history`` answered an empty page on every deployment however
much had been ingested. The ingestion ledger is the audit log behind
``/ingest_log``; the empty endpoint is gone rather than left to mislead.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client() -> TestClient:
    from app.routers import system_monitor

    app = FastAPI()
    app.include_router(system_monitor.router)
    return TestClient(app)


def test_the_always_empty_history_endpoint_is_not_served():
    assert _client().get("/admin/ingest-history").status_code == 404


def test_the_storage_report_is_still_served(monkeypatch):
    from app.routers import system_monitor

    monkeypatch.setattr(system_monitor, "get_storage_report", lambda: {"status": "healthy"})

    response = _client().get("/system/storage")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}
