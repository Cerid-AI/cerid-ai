# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Scheduled folder scan — store-driven paths + ``import_mode="once"``.

The scan job must walk the enabled watched folders from the Redis store
(the latent bug had it scanning ``config.SCAN_PATHS`` instead), persist
per-folder stats, disable one-time-import folders after their scan, and
only fall back to the legacy SCAN_PATHS behavior when the store is empty.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest


class _FakeRedis:
    """Just enough of the redis surface for the watched-folders helpers."""

    def __init__(self, records):
        self._store = {
            f"cerid:watched_folders:{r['id']}": json.dumps(r) for r in records
        }
        self._index = {r["id"] for r in records}

    def get(self, key):
        return self._store.get(key)

    def set(self, key, value):
        self._store[key] = value

    def smembers(self, key):
        return set(self._index)

    def record(self, folder_id):
        return json.loads(self._store[f"cerid:watched_folders:{folder_id}"])


def _fake_scan_results(statuses):
    class _R:
        def __init__(self, status):
            self.status = status

    async def _gen():
        for s in statuses:
            yield _R(s)

    return _gen()


_FOLDER = {
    "id": "f1",
    "path": "",  # filled per-test with tmp_path
    "label": "Notes",
    "enabled": True,
    "domain_override": None,
    "exclude_patterns": [".git"],
    "search_enabled": True,
    "is_vault": False,
    "vault_config": None,
    "import_mode": "watch",
    "last_scanned_at": None,
    "stats": {"ingested": 0, "skipped": 0, "errored": 0},
    "created_at": "2026-07-01T00:00:00Z",
}


@pytest.mark.asyncio
async def test_scan_uses_watched_folder_paths_not_scan_paths(tmp_path, monkeypatch):
    import config
    from app.scheduler import _run_folder_scan

    # A configured legacy path must be IGNORED when store folders exist.
    monkeypatch.setattr(config, "SCAN_PATHS", "/legacy/scan/path", raising=False)

    rec = {**_FOLDER, "path": str(tmp_path)}
    fake = _FakeRedis([rec])
    calls: list[tuple[str, dict]] = []

    def fake_scan_folder(path, **kwargs):
        calls.append((path, kwargs))
        return _fake_scan_results(["ingested", "skipped"])

    with patch("app.scheduler.get_redis", return_value=fake), \
         patch("app.services.folder_scanner.scan_folder", side_effect=fake_scan_folder):
        await _run_folder_scan()

    assert [c[0] for c in calls] == [str(tmp_path)]
    assert calls[0][1]["exclude_patterns"] == {".git"}
    saved = fake.record("f1")
    assert saved["stats"] == {"ingested": 1, "skipped": 1, "errored": 0}
    assert saved["enabled"] is True  # watch mode stays enabled
    assert saved["last_scanned_at"]


@pytest.mark.asyncio
async def test_watched_folder_scan_threads_config_quality_and_size_caps(tmp_path, monkeypatch):
    """AF-048: the watched-folders branch must pass the operator's
    SCAN_MIN_QUALITY / SCAN_MAX_FILE_SIZE_MB into scan_folder, not the
    hardcoded scan_folder defaults (0.4 / 50)."""
    import config
    from app.scheduler import _run_folder_scan

    monkeypatch.setattr(config, "SCAN_MIN_QUALITY", 0.72, raising=False)
    monkeypatch.setattr(config, "SCAN_MAX_FILE_SIZE_MB", 7, raising=False)

    rec = {**_FOLDER, "path": str(tmp_path)}
    fake = _FakeRedis([rec])
    captured: dict = {}

    def fake_scan_folder(path, **kwargs):
        captured.update(kwargs)
        return _fake_scan_results(["ingested"])

    with patch("app.scheduler.get_redis", return_value=fake), \
         patch("app.services.folder_scanner.scan_folder", side_effect=fake_scan_folder):
        await _run_folder_scan()

    assert captured.get("min_quality") == 0.72
    assert captured.get("max_file_size_mb") == 7


@pytest.mark.asyncio
async def test_once_mode_folder_scans_then_disables(tmp_path):
    from app.scheduler import _run_folder_scan

    rec = {**_FOLDER, "path": str(tmp_path), "import_mode": "once"}
    fake = _FakeRedis([rec])

    with patch("app.scheduler.get_redis", return_value=fake), \
         patch(
             "app.services.folder_scanner.scan_folder",
             side_effect=lambda path, **kw: _fake_scan_results(["ingested"]),
         ):
        await _run_folder_scan()

    saved = fake.record("f1")
    assert saved["stats"]["ingested"] == 1
    assert saved["enabled"] is False  # one-time import → disabled after scan


@pytest.mark.asyncio
async def test_disabled_folder_is_skipped(tmp_path):
    from app.scheduler import _run_folder_scan

    rec = {**_FOLDER, "path": str(tmp_path), "enabled": False}
    fake = _FakeRedis([rec])
    calls: list[str] = []

    with patch("app.scheduler.get_redis", return_value=fake), \
         patch(
             "app.services.folder_scanner.scan_folder",
             side_effect=lambda path, **kw: calls.append(path) or _fake_scan_results([]),
         ):
        await _run_folder_scan()

    assert calls == []
    # Disabled + store non-empty must NOT fall back to legacy scanning either.
    assert fake.record("f1")["stats"] == {"ingested": 0, "skipped": 0, "errored": 0}


@pytest.mark.asyncio
async def test_vault_folder_uses_scan_vault(tmp_path):
    from app.scheduler import _run_folder_scan

    rec = {
        **_FOLDER,
        "path": str(tmp_path),
        "is_vault": True,
        "vault_config": {"default_domain": "notes"},
    }
    fake = _FakeRedis([rec])
    vault_calls: list[tuple[str, dict | None]] = []

    def fake_scan_vault(path, ui_config, **kwargs):
        vault_calls.append((path, ui_config))
        return _fake_scan_results(["ingested"])

    with patch("app.scheduler.get_redis", return_value=fake), \
         patch("app.services.folder_scanner.scan_vault", side_effect=fake_scan_vault), \
         patch("app.services.folder_scanner.scan_folder") as flat_scan:
        await _run_folder_scan()

    assert vault_calls == [(str(tmp_path), {"default_domain": "notes"})]
    flat_scan.assert_not_called()


@pytest.mark.asyncio
async def test_empty_store_falls_back_to_legacy_scan_paths(tmp_path, monkeypatch):
    import config
    from app.scheduler import _run_folder_scan

    monkeypatch.setattr(config, "SCAN_PATHS", str(tmp_path), raising=False)
    fake = _FakeRedis([])
    calls: list[tuple[str, dict]] = []

    def fake_scan_folder(path, **kwargs):
        calls.append((path, kwargs))
        return _fake_scan_results(["ingested"])

    with patch("app.scheduler.get_redis", return_value=fake), \
         patch("app.services.folder_scanner.scan_folder", side_effect=fake_scan_folder):
        await _run_folder_scan()

    assert [c[0] for c in calls] == [str(tmp_path)]
    # Legacy call shape (min_quality / max_file_size_mb) preserved.
    assert "min_quality" in calls[0][1]
    assert "max_file_size_mb" in calls[0][1]


# ---------------------------------------------------------------------------
# Router create — import_mode persisted on the stored record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_watched_folder_stores_import_mode(tmp_path):
    from app.routers import watched_folders as wf

    fake = _FakeRedis([])
    fake.sadd = lambda key, member: fake._index.add(member)  # index add used by create

    with patch.object(wf, "_ALLOWED_ROOTS", [tmp_path.resolve()]), \
         patch("deps.get_redis", return_value=fake):
        detail = await wf.create_watched_folder(
            wf.WatchedFolderCreate(path=str(tmp_path), label="X", import_mode="once")
        )

    assert detail["import_mode"] == "once"
    stored = fake.record(detail["id"])
    assert stored["import_mode"] == "once"


@pytest.mark.asyncio
async def test_create_watched_folder_defaults_to_watch(tmp_path):
    from app.routers import watched_folders as wf

    fake = _FakeRedis([])
    fake.sadd = lambda key, member: fake._index.add(member)

    with patch.object(wf, "_ALLOWED_ROOTS", [tmp_path.resolve()]), \
         patch("deps.get_redis", return_value=fake):
        detail = await wf.create_watched_folder(
            wf.WatchedFolderCreate(path=str(tmp_path), label="X")
        )

    assert detail["import_mode"] == "watch"
