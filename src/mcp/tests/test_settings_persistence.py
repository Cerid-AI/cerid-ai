# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for settings persistence to sync directory on PATCH /settings."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app():
    from app.routers.settings import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture()
def sync_dir(tmp_path: Path) -> Path:
    """Return a temporary sync directory."""
    return tmp_path / "sync"


@pytest.fixture()
def client(sync_dir: Path) -> TestClient:
    """TestClient with config.SYNC_DIR patched to the temp sync directory."""
    with patch("config.SYNC_DIR", str(sync_dir)):
        yield TestClient(_make_app())


def _read_settings(sync_dir: Path) -> dict:
    path = sync_dir / "user" / "settings.json"
    return json.loads(path.read_text(encoding="utf-8"))


class TestSettingsPersistence:
    def test_patch_writes_to_sync_dir(self, client: TestClient, sync_dir: Path):
        """PATCH /settings should persist changed values to user/settings.json."""
        resp = client.patch("/settings", json={"enable_feedback_loop": True})

        assert resp.status_code == 200
        data = _read_settings(sync_dir)
        assert data["enable_feedback_loop"] is True
        assert "updated_at" in data
        assert "machine_id" in data

    def test_multiple_patches_merge(self, client: TestClient, sync_dir: Path):
        """Successive PATCHes should merge into the same settings file."""
        client.patch("/settings", json={"enable_feedback_loop": True})
        client.patch("/settings", json={"enable_self_rag": True})

        data = _read_settings(sync_dir)
        assert data["enable_feedback_loop"] is True
        assert data["enable_self_rag"] is True

    def test_skips_when_no_sync_dir(self):
        """PATCH should still succeed when SYNC_DIR is empty (no sync configured)."""
        with patch("config.SYNC_DIR", ""):
            app_client = TestClient(_make_app())
            resp = app_client.patch("/settings", json={"enable_feedback_loop": True})

        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
