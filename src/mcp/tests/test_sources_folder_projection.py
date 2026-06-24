# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client() -> TestClient:
    from app.routers.sources import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


_FOLDER_SOURCE = {
    "id": "folder:abc123", "kind": "folder", "family": "files",
    "display_name": "Notes", "tier": "core", "status": "connected",
    "config": {"path": "/data/notes"}, "sync_cursor": {},
    "total_artifacts": 12, "total_chunks": 0, "total_edges": 0,
    "total_artifacts_24h": 0, "connection_time_ms": None,
    "last_sync_at": None, "created_at": None, "last_error": None,
    "quality_floor": 0.0,
}


def test_list_includes_projected_folders():
    with patch("app.routers.sources.srcdb.list_sources", return_value=[]), \
         patch("app.routers.sources.get_neo4j", return_value=MagicMock()), \
         patch("app.routers.sources.get_redis", return_value=MagicMock()), \
         patch("app.routers.sources.list_folder_sources", return_value=[_FOLDER_SOURCE]):
        r = _client().get("/sources")
    assert r.status_code == 200
    ids = [s["id"] for s in r.json()]
    assert "folder:abc123" in ids


def test_get_folder_resolves_via_bridge():
    with patch("app.routers.sources.get_redis", return_value=MagicMock()), \
         patch("app.routers.sources.get_folder_source", return_value=_FOLDER_SOURCE):
        r = _client().get("/sources/folder:abc123")
    assert r.status_code == 200
    assert r.json()["display_name"] == "Notes"


def test_create_folder_delegates_to_watched_folders():
    created = {**_FOLDER_SOURCE}
    with patch("app.routers.sources.get_redis", return_value=MagicMock()), \
         patch("app.routers.sources.create_folder_source", return_value=created) as mk:
        r = _client().post("/sources", json={
            "kind": "folder", "display_name": "Notes",
            "config": {"path": "/data/notes"},
        })
    assert r.status_code == 201
    mk.assert_called_once()
    assert r.json()["kind"] == "folder"


def test_delete_folder_delegates():
    with patch("app.routers.sources.get_redis", return_value=MagicMock()), \
         patch("app.routers.sources.delete_folder_source") as rm:
        r = _client().delete("/sources/folder:abc123")
    assert r.status_code in (200, 204)
    rm.assert_called_once()


def test_policy_folder_returns_projected_record():
    with patch("app.routers.sources.get_redis", return_value=MagicMock()), \
         patch("app.routers.sources.get_folder_source", return_value=_FOLDER_SOURCE):
        r = _client().post("/sources/folder:abc123/policy", json={})
    assert r.status_code == 200
    assert r.json()["kind"] == "folder"


def test_test_folder_delegates_health():
    with patch("app.routers.sources.get_redis", return_value=MagicMock()), \
         patch("app.routers.sources.folder_health", return_value={"ok": True, "detail": "ok", "last_error": None}) as hk:
        r = _client().post("/sources/folder:abc123/test")
    assert r.status_code == 200
    hk.assert_called_once()
    assert r.json()["ok"] is True
