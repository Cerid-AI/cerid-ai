# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Contract: /setup/models/status exposes provider per model so the banner
hides itself when providers are remote (e.g., Quenchforge serves reranker)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.setup import router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.mark.integration
def test_models_status_includes_provider_field(monkeypatch, client):
    monkeypatch.setenv("RERANK_PROVIDER", "quenchforge")
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "quenchforge")
    resp = client.get("/setup/models/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "reranker" in body and "embedder" in body
    assert "provider" in body["reranker"], "Provider field missing"
    assert body["reranker"]["provider"] == "quenchforge"
    assert "needs_local_cache" in body["reranker"]
    assert body["reranker"]["needs_local_cache"] is False


@pytest.mark.integration
def test_models_preload_honest_about_no_op_for_remote_provider(monkeypatch, client):
    monkeypatch.setenv("RERANK_PROVIDER", "quenchforge")
    resp = client.post("/setup/models/preload")
    body = resp.json()
    # When provider is remote, must NOT claim "loaded"
    assert body["reranker_status"] in ("remote_provider", "no_op_remote", "skipped_remote")
