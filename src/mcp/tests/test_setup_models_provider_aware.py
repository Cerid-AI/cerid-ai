# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Contract: /setup/models/status names the configured provider per model and
says whether the local ONNX weights are the primary path or the fallback.

The banner used to hide itself whenever a remote provider was configured. That
is the state where the in-process fallback answers every request the GPU lane
drops, so the weights still have to be on disk — the card reports the provider
AND the role rather than treating "remote" as "nothing to cache"."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.setup import router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_models_status_includes_provider_field(monkeypatch, client):
    monkeypatch.setenv("RERANK_PROVIDER", "quenchforge")
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "quenchforge")
    with patch("huggingface_hub.try_to_load_from_cache", return_value=None):
        resp = client.get("/setup/models/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "reranker" in body and "embedder" in body
    assert "provider" in body["reranker"], "Provider field missing"
    assert body["reranker"]["provider"] == "quenchforge"
    # The fallback runs in-process whatever the provider is, so the weights
    # are still required — the role is what distinguishes the two cases.
    assert body["reranker"]["needs_local_cache"] is True
    assert body["reranker"]["role"] == "fallback"


def test_models_preload_warms_the_fallback_for_a_remote_provider(monkeypatch, client):
    monkeypatch.setenv("RERANK_PROVIDER", "quenchforge")
    load_reranker = MagicMock()
    with (
        patch("core.retrieval.reranker._load_model", load_reranker),
        patch("core.utils.embeddings.get_embedding_function", return_value=None),
    ):
        resp = client.post("/setup/models/preload")
    body = resp.json()
    assert load_reranker.called
    assert body["reranker_status"] == "loaded"
    assert body["reranker_provider"] == "quenchforge"
    assert body["reranker_role"] == "fallback"
