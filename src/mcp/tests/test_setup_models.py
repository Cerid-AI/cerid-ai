# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for /setup/models/{status,preload} endpoints (Phase E.6.2)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.routers.setup import _model_cache_status, router


@pytest.fixture
def client():
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# _model_cache_status helper
# ---------------------------------------------------------------------------


def test_model_cache_status_returns_uncached_when_no_files(monkeypatch):
    """try_to_load_from_cache returning None → cached=False, file paths None."""
    monkeypatch.setattr(
        "huggingface_hub.try_to_load_from_cache",
        lambda **kwargs: None,
    )
    result = _model_cache_status(
        repo_id="some/repo",
        filenames=("onnx/model.onnx", "tokenizer.json"),
        cache_dir=None,
    )
    assert result["repo"] == "some/repo"
    assert result["cached"] is False
    assert result["files"]["onnx/model.onnx"] is None
    assert result["files"]["tokenizer.json"] is None


def test_model_cache_status_returns_cached_when_all_files_present(monkeypatch):
    """All files in cache → cached=True, file paths populated."""
    def fake_path_for(**kwargs):
        return f"/cache/{kwargs['filename']}"
    monkeypatch.setattr("huggingface_hub.try_to_load_from_cache", fake_path_for)
    result = _model_cache_status(
        repo_id="some/repo",
        filenames=("onnx/model.onnx", "tokenizer.json"),
        cache_dir="/some/cache",
    )
    assert result["cached"] is True
    assert result["files"]["onnx/model.onnx"] == "/cache/onnx/model.onnx"


def test_model_cache_status_partial_means_uncached(monkeypatch):
    """If even one file is missing the model isn't usable → cached=False."""
    def _maybe(**kwargs):
        if kwargs["filename"] == "tokenizer.json":
            return "/cache/tokenizer.json"
        return None
    monkeypatch.setattr("huggingface_hub.try_to_load_from_cache", _maybe)
    result = _model_cache_status(
        repo_id="some/repo",
        filenames=("onnx/model.onnx", "tokenizer.json"),
        cache_dir=None,
    )
    assert result["cached"] is False
    assert result["files"]["onnx/model.onnx"] is None
    assert result["files"]["tokenizer.json"] == "/cache/tokenizer.json"


def test_model_cache_status_handles_probe_exception(monkeypatch):
    """If huggingface_hub raises (e.g. corrupt cache), report uncached
    rather than 500-ing the endpoint."""
    def _raises(**kwargs):
        raise RuntimeError("simulated cache corruption")
    monkeypatch.setattr("huggingface_hub.try_to_load_from_cache", _raises)
    result = _model_cache_status(
        repo_id="some/repo",
        filenames=("onnx/model.onnx",),
        cache_dir=None,
    )
    assert result["cached"] is False
    assert result["files"]["onnx/model.onnx"] is None


# ---------------------------------------------------------------------------
# GET /setup/models/status
# ---------------------------------------------------------------------------


def test_get_models_status_returns_both_models(client, monkeypatch):
    monkeypatch.setattr(
        "huggingface_hub.try_to_load_from_cache",
        lambda **kwargs: None,  # all uncached
    )
    response = client.get("/setup/models/status")
    assert response.status_code == 200
    body = response.json()
    assert "reranker" in body
    assert "embedder" in body
    assert body["reranker"]["cached"] is False
    assert body["embedder"]["cached"] is False


def test_get_models_status_reports_cached_when_files_present(client, monkeypatch):
    monkeypatch.setattr(
        "huggingface_hub.try_to_load_from_cache",
        lambda **kwargs: f"/cache/{kwargs['repo_id']}/{kwargs['filename']}",
    )
    response = client.get("/setup/models/status")
    assert response.status_code == 200
    body = response.json()
    assert body["reranker"]["cached"] is True
    assert body["embedder"]["cached"] is True


def test_get_models_status_includes_loading_field_default_false(client, monkeypatch):
    """Phase E.6.6: every status response carries `loading` per model.
    With both modules idle, it's False — the GUI banner uses this to
    distinguish "downloading right now" from "not cached, waiting"."""
    monkeypatch.setattr(
        "huggingface_hub.try_to_load_from_cache",
        lambda **kwargs: None,
    )
    response = client.get("/setup/models/status")
    assert response.status_code == 200
    body = response.json()
    assert body["reranker"]["loading"] is False
    assert body["embedder"]["loading"] is False


def test_get_models_status_loading_field_reflects_inflight_download(client, monkeypatch):
    """When reranker._lock is held + session is None, loading=True surfaces."""
    monkeypatch.setattr(
        "huggingface_hub.try_to_load_from_cache",
        lambda **kwargs: None,
    )

    # Pretend the reranker is mid-download
    from core.retrieval import reranker as rr

    # Save originals to restore in finally
    held_lock = rr._lock
    held_session = rr._session
    try:
        rr._session = None
        held_lock.acquire()
        try:
            response = client.get("/setup/models/status")
        finally:
            held_lock.release()
    finally:
        rr._session = held_session

    body = response.json()
    assert body["reranker"]["loading"] is True
    # Embedder remained idle
    assert body["embedder"]["loading"] is False


def test_is_loading_probe_returns_false_on_import_error():
    """If a module raises while reporting status, the probe must
    swallow + return False so /status keeps responding."""
    from unittest.mock import patch

    from app.routers.setup import _is_loading

    # Force the dynamic import to fail
    with patch("core.retrieval.reranker.is_loading",
               side_effect=RuntimeError("boom")):
        assert _is_loading("reranker") is False


# ---------------------------------------------------------------------------
# POST /setup/models/preload
# ---------------------------------------------------------------------------


def test_post_models_preload_loads_both_when_successful(client):
    """Happy path: both loaders return cleanly → status=ok with timings."""
    def fake_load(*_args, **_kwargs):
        return None
    fake_ef = type("FakeEF", (), {"_load": fake_load})()

    with patch("core.retrieval.reranker._load_model", fake_load), \
         patch("core.utils.embeddings.get_embedding_function",
               return_value=fake_ef):
        response = client.post("/setup/models/preload")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["reranker_status"] == "loaded"
    assert body["embedder_status"] == "loaded"
    assert "total_ms" in body
    assert "reranker_ms" in body
    assert "embedder_ms" in body


def test_post_models_preload_skips_embedder_when_server_side(client):
    """When EMBEDDING_MODEL == ChromaDB server default,
    get_embedding_function returns None — that's a valid skip,
    not a failure."""
    def fake_load(*_args, **_kwargs):
        return None
    with patch("core.retrieval.reranker._load_model", fake_load), \
         patch("core.utils.embeddings.get_embedding_function", return_value=None):
        response = client.post("/setup/models/preload")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["embedder_status"] == "skipped_server_side"
    assert body["embedder_ms"] == 0.0


def test_post_models_preload_partial_when_reranker_fails(client):
    """A failed reranker download shouldn't crash the endpoint —
    it should report partial success so the GUI can show a
    targeted error."""
    def _raise_load():
        raise ConnectionError("simulated HuggingFace outage")

    def _fake_ef_load(*_args, **_kwargs):
        return None
    fake_ef = type("FakeEF", (), {"_load": _fake_ef_load})()
    with patch("core.retrieval.reranker._load_model", side_effect=_raise_load), \
         patch("core.utils.embeddings.get_embedding_function",
               return_value=fake_ef):
        response = client.post("/setup/models/preload")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial"
    assert body["reranker_status"] == "failed"
    assert "simulated HuggingFace outage" in body.get("reranker_error", "")
    # Embedder still loads even when reranker fails — independent path
    assert body["embedder_status"] == "loaded"


def test_post_models_preload_partial_when_embedder_fails(client):
    def fake_load(*_args, **_kwargs):
        return None
    def _raise_ef():
        raise ConnectionError("simulated embedder outage")
    with patch("core.retrieval.reranker._load_model", fake_load), \
         patch("core.utils.embeddings.get_embedding_function",
               side_effect=_raise_ef):
        response = client.post("/setup/models/preload")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial"
    assert body["reranker_status"] == "loaded"
    assert body["embedder_status"] == "failed"
