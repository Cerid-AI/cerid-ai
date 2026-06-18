# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Whisper model download manager (Phase E Day 3)."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app() -> FastAPI:
    from app.routers.whisper_models import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # Clear in-memory download state between tests
    from app.routers import whisper_models
    whisper_models._DOWNLOADS.clear()
    whisper_models._CANCEL_FLAGS.clear()
    return TestClient(_make_app())


class TestListModels:
    def test_list_includes_all_canonical_models(self, client):
        resp = client.get("/settings/whisper/models")
        assert resp.status_code == 200
        body = resp.json()
        ids = {m["id"] for m in body["models"]}
        assert ids == {"tiny", "base", "small", "medium", "medium-q5_0", "large-v3"}

    def test_list_reports_cache_dir(self, client, tmp_path):
        resp = client.get("/settings/whisper/models")
        body = resp.json()
        assert str(tmp_path) in body["cache_dir"]
        assert body["cache_dir"].endswith(".cerid/models/whisper")

    def test_list_reports_uncached_initially(self, client):
        body = client.get("/settings/whisper/models").json()
        for m in body["models"]:
            assert m["cached"] is False
            assert m["cached_size_bytes"] is None

    def test_list_reports_cached_after_file_present(self, client, tmp_path):
        cache_dir = tmp_path / ".cerid" / "models" / "whisper"
        cache_dir.mkdir(parents=True)
        (cache_dir / "ggml-tiny.bin").write_bytes(b"x" * 1000)

        body = client.get("/settings/whisper/models").json()
        tiny = next(m for m in body["models"] if m["id"] == "tiny")
        assert tiny["cached"] is True
        assert tiny["cached_size_bytes"] == 1000

    def test_rtf_estimate_differs_by_platform(self, client):
        """RTF estimate should reflect Apple-Silicon-or-not detection."""
        from app.routers import whisper_models

        with patch.object(whisper_models, "_is_apple_silicon", return_value=True):
            body = client.get("/settings/whisper/models").json()
            tiny_arm = next(m for m in body["models"] if m["id"] == "tiny")
        with patch.object(whisper_models, "_is_apple_silicon", return_value=False):
            body = client.get("/settings/whisper/models").json()
            tiny_cpu = next(m for m in body["models"] if m["id"] == "tiny")
        assert tiny_arm["rtf_estimate"] < tiny_cpu["rtf_estimate"]


class TestDownload:
    def test_start_unknown_model_404(self, client):
        resp = client.post("/settings/whisper/download", json={"model_id": "bogus"})
        assert resp.status_code == 404

    def test_start_returns_download_id(self, client):
        # Stub _do_download to immediately mark complete (no real HTTP)
        from app.routers import whisper_models

        async def _stub(did, _mid):
            whisper_models._DOWNLOADS[did].state = "completed"

        with patch.object(whisper_models, "_do_download", _stub):
            resp = client.post("/settings/whisper/download", json={"model_id": "tiny"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["model_id"] == "tiny"
        assert len(body["download_id"]) == 32  # hex uuid

    def test_status_returns_404_for_unknown(self, client):
        resp = client.get("/settings/whisper/download/deadbeef")
        assert resp.status_code == 404

    def test_status_returns_state_after_start(self, client):
        from app.routers import whisper_models

        async def _stub(did, _mid):
            await asyncio.sleep(0)  # yield once
            whisper_models._DOWNLOADS[did].state = "completed"
            whisper_models._DOWNLOADS[did].bytes_downloaded = 75 * 1024 * 1024

        with patch.object(whisper_models, "_do_download", _stub):
            did = client.post(
                "/settings/whisper/download", json={"model_id": "tiny"}
            ).json()["download_id"]
            # Give the task a tick to flip the state
            import time
            time.sleep(0.1)
            resp = client.get(f"/settings/whisper/download/{did}")
        body = resp.json()
        assert body["download_id"] == did
        assert body["state"] in ("completed", "pending", "downloading")

    def test_cancel_sets_flag(self, client):
        from app.routers import whisper_models

        async def _stub(did, _mid):
            # Wait for cancel
            await whisper_models._CANCEL_FLAGS[did].wait()
            whisper_models._DOWNLOADS[did].state = "cancelled"

        with patch.object(whisper_models, "_do_download", _stub):
            did = client.post(
                "/settings/whisper/download", json={"model_id": "tiny"}
            ).json()["download_id"]
            resp = client.delete(f"/settings/whisper/download/{did}")
        assert resp.status_code == 200
        # Flag must now be set
        assert whisper_models._CANCEL_FLAGS[did].is_set()


class TestDeleteCachedModel:
    def test_delete_unknown_returns_404(self, client):
        resp = client.delete("/settings/whisper/models/bogus")
        assert resp.status_code == 404

    def test_delete_existing_returns_deleted_true(self, client, tmp_path):
        cache_dir = tmp_path / ".cerid" / "models" / "whisper"
        cache_dir.mkdir(parents=True)
        f = cache_dir / "ggml-tiny.bin"
        f.write_bytes(b"x")
        resp = client.delete("/settings/whisper/models/tiny")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": True}
        assert not f.exists()

    def test_delete_uncached_returns_deleted_false(self, client):
        resp = client.delete("/settings/whisper/models/tiny")
        assert resp.json() == {"deleted": False}
