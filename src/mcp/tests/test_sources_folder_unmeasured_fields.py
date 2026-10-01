# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""F090: a folder source reports the figures nobody measures as null.

Artifacts ingested by a folder scan carry no link back to the folder, so
its chunk, edge and last-24h counts cannot be read from the stores, and a
folder has no quality floor. The /sources contract used to state all four
as 0.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import sources

_UNMEASURED = ("total_chunks", "total_edges", "total_artifacts_24h", "quality_floor")

_FOLDER = {
    "id": "abc123",
    "path": "/data/notes",
    "label": "Notes",
    "enabled": True,
    "stats": {"ingested": 12, "skipped": 1, "errored": 0},
    "last_scanned_at": "2026-09-01T03:00:00+00:00",
    "created_at": "2026-08-01T00:00:00+00:00",
}


@pytest.fixture
def client(monkeypatch) -> TestClient:
    redis = MagicMock()
    redis.smembers.return_value = {_FOLDER["id"]}
    redis.get.return_value = json.dumps(_FOLDER)
    monkeypatch.setattr(sources, "get_redis", lambda: redis)

    neo4j = MagicMock()
    neo4j.session.return_value.__enter__.return_value.run.return_value = []
    monkeypatch.setattr(sources, "get_neo4j", lambda: neo4j)

    app = FastAPI()
    app.include_router(sources.router)
    return TestClient(app)


def test_folder_source_reports_unmeasured_figures_as_null(client):
    body = client.get("/sources/folder:abc123").json()

    for field in _UNMEASURED:
        assert body[field] is None, field
    # What the scan did count is still reported.
    assert body["total_artifacts"] == 12


def test_folder_in_the_source_list_reports_unmeasured_figures_as_null(client):
    rows = client.get("/sources", params={"kind": "folder"}).json()

    folder = next(r for r in rows if r["id"] == "folder:abc123")
    for field in _UNMEASURED:
        assert folder[field] is None, field
