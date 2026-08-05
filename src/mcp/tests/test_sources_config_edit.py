# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Tests for POST /sources/{id}/config (Stage C1/C2 — config editing for all source kinds)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client() -> TestClient:
    from app.routers.sources import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


_RSS_SOURCE = {
    "id": "src-rss-001",
    "kind": "rss",
    "family": "web",
    "display_name": "My RSS Feed",
    "tier": "core",
    "status": "connected",
    "config": {"url": "https://old.example.com/feed.xml", "api_key": "real-secret"},  # pragma: allowlist secret
    "sync_cursor": {},
    "total_artifacts": 5,
    "total_chunks": 0,
    "total_edges": 0,
    "total_artifacts_24h": 0,
    "connection_time_ms": 120,
    "last_sync_at": None,
    "created_at": None,
    "last_error": None,
    "quality_floor": 0.0,
}

_FOLDER_SOURCE = {
    "id": "folder:abc123",
    "kind": "folder",
    "family": "files",
    "display_name": "Notes",
    "tier": "core",
    "status": "connected",
    "config": {"path": "/data/notes"},
    "sync_cursor": {},
    "total_artifacts": 0,
    "total_chunks": 0,
    "total_edges": 0,
    "total_artifacts_24h": 0,
    "connection_time_ms": None,
    "last_sync_at": None,
    "created_at": None,
    "last_error": None,
    "quality_floor": 0.0,
}


def _make_connector(connect_result_config: dict | None = None) -> MagicMock:
    """Return a mock connector whose connect() returns the merged config."""
    connector = MagicMock()
    # connect() is async in the real connector protocol
    result = MagicMock()
    result.config = connect_result_config or {}
    connector.connect = AsyncMock(return_value=result)
    return connector


def test_config_edit_reruns_connector_and_persists():
    """Editing a URL on an rss source re-validates via connect() and persists."""
    updated_source = {
        **_RSS_SOURCE,
        "config": {"url": "https://new.example.com/feed.xml", "api_key": "real-secret"},  # pragma: allowlist secret
    }
    connector = _make_connector({"url": "https://new.example.com/feed.xml", "api_key": "real-secret"})  # pragma: allowlist secret

    with patch("app.routers.sources.get_neo4j", return_value=MagicMock()), \
         patch("app.routers.sources.srcdb.get_source", return_value=_RSS_SOURCE), \
         patch("app.routers.sources.srcdb.update_source_config", return_value=updated_source) as mock_update, \
         patch("app.routers.sources.get_connector", return_value=connector):
        r = _client().post(
            "/sources/src-rss-001/config",
            json={"config": {"url": "https://new.example.com/feed.xml"}},
        )

    assert r.status_code == 200
    body = r.json()
    assert body["config"]["url"] == "https://new.example.com/feed.xml"
    # connect() must have been called with the merged config
    connector.connect.assert_awaited_once()
    # persist must have been called
    mock_update.assert_called_once()


def test_config_edit_drops_redaction_mask():
    """A field equal to '***redacted***' is dropped; stored secret is preserved."""
    # The caller sends back the redaction mask for api_key — it must NOT overwrite
    # the stored real secret. Only the url change should be merged.
    merged_config = {"url": "https://new.example.com/feed.xml", "api_key": "real-secret"}  # pragma: allowlist secret
    connector = _make_connector(merged_config)
    updated_source = {**_RSS_SOURCE, "config": merged_config}

    with patch("app.routers.sources.get_neo4j", return_value=MagicMock()), \
         patch("app.routers.sources.srcdb.get_source", return_value=_RSS_SOURCE), \
         patch("app.routers.sources.srcdb.update_source_config", return_value=updated_source) as mock_update, \
         patch("app.routers.sources.get_connector", return_value=connector):
        r = _client().post(
            "/sources/src-rss-001/config",
            json={"config": {"url": "https://new.example.com/feed.xml", "api_key": "***redacted***"}},
        )

    assert r.status_code == 200
    # The connect() call must receive the real secret, NOT the mask
    called_config = connector.connect.call_args[0][0]
    assert called_config.get("api_key") == "real-secret"
    assert called_config.get("url") == "https://new.example.com/feed.xml"
    mock_update.assert_called_once()


def test_config_edit_404_on_missing_source():
    """Returns 404 when the source does not exist."""
    with patch("app.routers.sources.get_neo4j", return_value=MagicMock()), \
         patch("app.routers.sources.srcdb.get_source", return_value=None):
        r = _client().post(
            "/sources/nonexistent-id/config",
            json={"config": {"url": "https://example.com"}},
        )

    assert r.status_code == 404


def test_config_edit_422_on_connector_validation_failure():
    """Returns 422 when the connector's connect() raises ValueError."""
    connector = MagicMock()
    connector.connect = AsyncMock(side_effect=ValueError("invalid feed URL"))

    with patch("app.routers.sources.get_neo4j", return_value=MagicMock()), \
         patch("app.routers.sources.srcdb.get_source", return_value=_RSS_SOURCE), \
         patch("app.routers.sources.get_connector", return_value=connector):
        r = _client().post(
            "/sources/src-rss-001/config",
            json={"config": {"url": "not-a-url"}},
        )

    assert r.status_code == 422
    assert "invalid feed URL" in r.json()["detail"]


def test_config_edit_folder_returns_updated_record():
    """Folder config edit (C2) delegates to update_folder_source and returns the updated projected record."""
    updated_folder = {
        **_FOLDER_SOURCE,
        "display_name": "Updated Notes",
        "config": {**_FOLDER_SOURCE["config"], "exclude_patterns": [".git", "__pycache__"]},
    }

    with patch("app.routers.sources.get_redis", return_value=MagicMock()), \
         patch("app.routers.sources.update_folder_source", new=AsyncMock(return_value=updated_folder)):
        r = _client().post(
            "/sources/folder:abc123/config",
            json={"config": {"label": "Updated Notes", "exclude_patterns": [".git", "__pycache__"]}},
        )

    assert r.status_code == 200
    body = r.json()
    assert body["display_name"] == "Updated Notes"


def test_config_edit_folder_path_in_patch_is_ignored():
    """A path field in the folder config patch is passed to bridge but bridge ignores it (path immutable)."""
    returned_folder = {**_FOLDER_SOURCE}

    with patch("app.routers.sources.get_redis", return_value=MagicMock()), \
         patch("app.routers.sources.update_folder_source", new=AsyncMock(return_value=returned_folder)) as mock_bridge:
        r = _client().post(
            "/sources/folder:abc123/config",
            json={"config": {"path": "/evil/new/path", "label": "Safe"}},
        )

    assert r.status_code == 200
    # The bridge was called with the raw config (path included); the bridge is responsible for ignoring path
    called_config = mock_bridge.call_args[1]["config"] if mock_bridge.call_args[1] else mock_bridge.call_args[0][2]
    assert "path" in called_config  # router passes it through; bridge drops it


def test_config_edit_folder_404_propagates():
    """When update_folder_source raises 404 (folder not found), the endpoint returns 404."""
    from fastapi import HTTPException

    with patch("app.routers.sources.get_redis", return_value=MagicMock()), \
         patch("app.routers.sources.update_folder_source",
               new=AsyncMock(side_effect=HTTPException(status_code=404, detail="Source not found"))):
        r = _client().post(
            "/sources/folder:no-such/config",
            json={"config": {"label": "x"}},
        )

    assert r.status_code == 404
