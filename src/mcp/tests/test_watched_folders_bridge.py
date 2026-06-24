# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.watched_folders_bridge import (
    folder_record_to_source,
    get_folder_source,
    list_folder_sources,
    strip_folder_prefix,
)

_REC = {
    "id": "abc123",
    "path": "/data/notes",
    "label": "Notes",
    "enabled": True,
    "domain_override": None,
    "exclude_patterns": [".git"],
    "search_enabled": True,
    "is_vault": False,
    "vault_config": None,
    "last_scanned_at": "2026-06-20T10:00:00Z",
    "stats": {"ingested": 12, "skipped": 1, "errored": 0},
    "created_at": "2026-06-01T00:00:00Z",
}


def test_maps_record_to_source_shape():
    s = folder_record_to_source(_REC)
    assert s["id"] == "folder:abc123"
    assert s["kind"] == "folder"
    assert s["display_name"] == "Notes"
    assert s["status"] == "connected"
    assert s["config"]["path"] == "/data/notes"
    assert s["total_artifacts"] == 12
    assert s["last_sync_at"] == "2026-06-20T10:00:00Z"


def test_disabled_folder_is_paused():
    rec = {**_REC, "enabled": False}
    assert folder_record_to_source(rec)["status"] == "paused"


def test_label_falls_back_to_path():
    rec = {**_REC, "label": ""}
    assert folder_record_to_source(rec)["display_name"] == "/data/notes"


def test_strip_prefix():
    assert strip_folder_prefix("folder:abc123") == "abc123"
    assert strip_folder_prefix("abc123") == "abc123"


def test_list_projects_all_records():
    with patch("app.routers.watched_folders._list_folder_ids", return_value=["abc123"]), \
         patch("app.routers.watched_folders._load_folder", return_value=_REC):
        out = list_folder_sources(redis=object())
    assert len(out) == 1
    assert out[0]["id"] == "folder:abc123"


def test_get_resolves_prefixed_id():
    with patch("app.routers.watched_folders._load_folder", return_value=_REC) as load:
        s = get_folder_source(redis=object(), source_id="folder:abc123")
    load.assert_called_once()
    assert load.call_args.args[1] == "abc123"  # prefix stripped before lookup
    assert s["id"] == "folder:abc123"


def test_get_missing_returns_none():
    with patch("app.routers.watched_folders._load_folder", return_value=None):
        assert get_folder_source(redis=object(), source_id="folder:nope") is None


# ---------------------------------------------------------------------------
# update_folder_source (C2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_folder_source_maps_config_to_update():
    """update_folder_source maps config fields to WatchedFolderUpdate and returns projected record."""
    from unittest.mock import AsyncMock
    from unittest.mock import patch as _patch

    updated_rec = {**_REC, "label": "Updated Notes", "exclude_patterns": [".git", "__pycache__"]}

    with _patch(
        "app.routers.watched_folders.update_watched_folder",
        new=AsyncMock(return_value=updated_rec),
    ) as mock_update:
        from app.services.watched_folders_bridge import update_folder_source

        result = await update_folder_source(
            redis=object(),
            source_id="folder:abc123",
            config={
                "label": "Updated Notes",
                "exclude_patterns": [".git", "__pycache__"],
                "search_enabled": True,
                "is_vault": False,
                "vault_config": None,
                "domain_override": None,
            },
        )

    mock_update.assert_awaited_once()
    _, kwargs_body = mock_update.call_args[0][0], mock_update.call_args[0][1]
    assert kwargs_body.label == "Updated Notes"
    assert kwargs_body.exclude_patterns == [".git", "__pycache__"]
    assert result["id"] == "folder:abc123"
    assert result["kind"] == "folder"
    assert result["config"]["exclude_patterns"] == [".git", "__pycache__"]


@pytest.mark.asyncio
async def test_update_folder_source_ignores_path():
    """update_folder_source must NOT pass path to WatchedFolderUpdate (path is immutable)."""
    from unittest.mock import AsyncMock
    from unittest.mock import patch as _patch

    updated_rec = {**_REC}

    with _patch(
        "app.routers.watched_folders.update_watched_folder",
        new=AsyncMock(return_value=updated_rec),
    ) as mock_update:
        from app.services.watched_folders_bridge import update_folder_source

        await update_folder_source(
            redis=object(),
            source_id="folder:abc123",
            config={"label": "Keep", "path": "/some/new/path"},
        )

    _, update_body = mock_update.call_args[0][0], mock_update.call_args[0][1]
    # WatchedFolderUpdate has no path field; confirm it didn't receive one
    assert not hasattr(update_body, "path") or getattr(update_body, "path", "SENTINEL") == "SENTINEL"


@pytest.mark.asyncio
async def test_update_folder_source_strips_prefix_for_lookup():
    """folder: prefix is stripped before calling update_watched_folder."""
    from unittest.mock import AsyncMock
    from unittest.mock import patch as _patch

    updated_rec = {**_REC}

    with _patch(
        "app.routers.watched_folders.update_watched_folder",
        new=AsyncMock(return_value=updated_rec),
    ) as mock_update:
        from app.services.watched_folders_bridge import update_folder_source

        await update_folder_source(
            redis=object(),
            source_id="folder:abc123",
            config={"label": "X"},
        )

    folder_id_arg = mock_update.call_args[0][0]
    assert folder_id_arg == "abc123"
