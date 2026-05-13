# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Settings router PATCH/GET coverage for C3.2 sparse + fusion fields."""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.settings import router


@pytest.fixture
def client(monkeypatch):
    # Disable sync-dir persistence so we don't touch real disk in unit tests.
    import config

    monkeypatch.setattr(config, "SYNC_DIR", "")
    monkeypatch.delenv("RETRIEVAL_SPARSE_ENABLED", raising=False)
    monkeypatch.setattr(config, "HYBRID_FUSION_MODE", "weighted_sum", raising=False)
    monkeypatch.setattr(config, "HYBRID_RRF_SPARSE_WEIGHT", 1.0, raising=False)

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_get_settings_includes_sparse_fields(client):
    r = client.get("/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["enable_sparse_retrieval"] is False
    assert body["hybrid_fusion_mode"] == "weighted_sum"
    assert body["hybrid_rrf_sparse_weight"] == 1.0


def test_patch_enables_sparse_retrieval(client):
    r = client.patch("/settings", json={"enable_sparse_retrieval": True})
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] == {"enable_sparse_retrieval": True}
    assert os.environ.get("RETRIEVAL_SPARSE_ENABLED", "").lower() in {"true", "1"}


def test_patch_accepts_tri_rrf_mode(client):
    r = client.patch("/settings", json={"hybrid_fusion_mode": "tri_rrf"})
    assert r.status_code == 200
    import config
    assert config.HYBRID_FUSION_MODE == "tri_rrf"


def test_patch_rejects_invalid_fusion_mode(client):
    r = client.patch("/settings", json={"hybrid_fusion_mode": "magic_sum"})
    assert r.status_code == 400


def test_patch_accepts_sparse_weight(client):
    r = client.patch("/settings", json={"hybrid_rrf_sparse_weight": 2.5})
    assert r.status_code == 200
    import config
    assert config.HYBRID_RRF_SPARSE_WEIGHT == 2.5


def test_patch_rejects_out_of_range_weight(client):
    r = client.patch("/settings", json={"hybrid_rrf_sparse_weight": 100.0})
    # Pydantic rejects out-of-range field; FastAPI surfaces it as 422.
    assert r.status_code == 422


def test_patch_combo_enable_and_mode(client):
    r = client.patch("/settings", json={
        "enable_sparse_retrieval": True,
        "hybrid_fusion_mode": "tri_rrf",
        "hybrid_rrf_sparse_weight": 1.5,
    })
    assert r.status_code == 200
    body = r.json()
    assert set(body["updated"].keys()) == {
        "enable_sparse_retrieval",
        "hybrid_fusion_mode",
        "hybrid_rrf_sparse_weight",
    }


# ---------------------------------------------------------------------------
# v0.93.8 — per-workload GPU routing fields
# ---------------------------------------------------------------------------

def test_patch_embeddings_provider_quenchforge(client, monkeypatch):
    import os
    monkeypatch.delenv("EMBEDDINGS_PROVIDER", raising=False)
    r = client.patch("/settings", json={"embeddings_provider": "quenchforge"})
    assert r.status_code == 200
    assert r.json()["updated"]["embeddings_provider"] == "quenchforge"
    assert os.environ["EMBEDDINGS_PROVIDER"] == "quenchforge"


def test_patch_rerank_provider_quenchforge(client, monkeypatch):
    import os
    monkeypatch.delenv("RERANK_PROVIDER", raising=False)
    r = client.patch("/settings", json={"rerank_provider": "quenchforge"})
    assert r.status_code == 200
    assert os.environ["RERANK_PROVIDER"] == "quenchforge"


def test_patch_rejects_invalid_provider(client):
    r = client.patch("/settings", json={"embeddings_provider": "fake_gpu"})
    assert r.status_code == 400


def test_patch_quenchforge_models(client, monkeypatch):
    import os
    monkeypatch.delenv("QUENCHFORGE_EMBED_MODEL", raising=False)
    monkeypatch.delenv("QUENCHFORGE_RERANK_MODEL", raising=False)
    r = client.patch("/settings", json={
        "quenchforge_embed_model": "nomic-embed-text-v1.5",
        "quenchforge_rerank_model": "bge-reranker-v2-m3",
    })
    assert r.status_code == 200
    assert os.environ["QUENCHFORGE_EMBED_MODEL"] == "nomic-embed-text-v1.5"
    assert os.environ["QUENCHFORGE_RERANK_MODEL"] == "bge-reranker-v2-m3"


def test_get_surfaces_gpu_routing_fields(client):
    r = client.get("/settings")
    body = r.json()
    assert "embeddings_provider" in body
    assert "rerank_provider" in body
    assert "quenchforge_embed_model" in body
    assert "quenchforge_rerank_model" in body


def test_patch_all_three_gpu_flags_together(client, monkeypatch):
    monkeypatch.delenv("EMBEDDINGS_PROVIDER", raising=False)
    monkeypatch.delenv("RERANK_PROVIDER", raising=False)
    monkeypatch.delenv("QUENCHFORGE_EMBED_MODEL", raising=False)
    r = client.patch("/settings", json={
        "embeddings_provider": "quenchforge",
        "rerank_provider": "quenchforge",
        "quenchforge_embed_model": "nomic-embed-text-v1.5",
    })
    assert r.status_code == 200
    assert set(r.json()["updated"]) >= {
        "embeddings_provider", "rerank_provider", "quenchforge_embed_model",
    }
