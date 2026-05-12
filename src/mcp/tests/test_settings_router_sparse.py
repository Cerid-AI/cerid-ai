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
