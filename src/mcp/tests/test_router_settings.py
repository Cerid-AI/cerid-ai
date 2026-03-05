# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for GET /settings and PATCH /settings endpoints."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app():
    from routers.settings import router

    app = FastAPI()
    app.include_router(router)
    return app


class TestGetSettings:
    def test_returns_settings(self):
        client = TestClient(_make_app())
        response = client.get("/settings")

        assert response.status_code == 200
        data = response.json()
        assert "categorize_mode" in data
        assert "domains" in data
        assert "version" in data
        assert "taxonomy" in data
        assert "memory_config" in data
        assert "enable_self_rag" in data

    def test_includes_storage_and_sync(self):
        client = TestClient(_make_app())
        data = client.get("/settings").json()

        assert "storage_mode" in data
        assert "sync_backend" in data
        assert "machine_id" in data


class TestPatchSettings:
    def test_update_categorize_mode(self):
        client = TestClient(_make_app())
        response = client.patch("/settings", json={"categorize_mode": "pro"})

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["updated"]["categorize_mode"] == "pro"

    def test_invalid_categorize_mode_rejected(self):
        client = TestClient(_make_app())
        response = client.patch("/settings", json={"categorize_mode": "invalid"})

        assert response.status_code == 400
        assert "Invalid categorize_mode" in response.json()["detail"]

    def test_update_boolean_flag(self):
        client = TestClient(_make_app())
        response = client.patch(
            "/settings", json={"enable_hallucination_check": True}
        )

        assert response.status_code == 200
        assert response.json()["updated"]["enable_hallucination_check"] is True

    def test_update_threshold(self):
        client = TestClient(_make_app())
        response = client.patch(
            "/settings", json={"hallucination_threshold": 0.85}
        )

        assert response.status_code == 200
        assert response.json()["updated"]["hallucination_threshold"] == 0.85

    def test_invalid_cost_sensitivity_rejected(self):
        client = TestClient(_make_app())
        response = client.patch("/settings", json={"cost_sensitivity": "extreme"})

        assert response.status_code == 400
        assert "Invalid cost_sensitivity" in response.json()["detail"]

    def test_invalid_storage_mode_rejected(self):
        client = TestClient(_make_app())
        response = client.patch("/settings", json={"storage_mode": "invalid"})

        assert response.status_code == 400
        assert "Invalid storage_mode" in response.json()["detail"]

    def test_empty_update_rejected(self):
        client = TestClient(_make_app())
        response = client.patch("/settings", json={})

        assert response.status_code == 400
        assert "No valid fields" in response.json()["detail"]

    def test_multiple_fields_updated(self):
        client = TestClient(_make_app())
        response = client.patch(
            "/settings",
            json={
                "enable_feedback_loop": True,
                "enable_self_rag": True,
                "auto_inject_threshold": 0.9,
            },
        )

        assert response.status_code == 200
        updated = response.json()["updated"]
        assert updated["enable_feedback_loop"] is True
        assert updated["enable_self_rag"] is True
        assert updated["auto_inject_threshold"] == 0.9

    def test_auto_inject_threshold_bounds(self):
        client = TestClient(_make_app())

        # Below minimum (0.5)
        response = client.patch("/settings", json={"auto_inject_threshold": 0.1})
        assert response.status_code == 422  # Pydantic validation

        # Above maximum (1.0)
        response = client.patch("/settings", json={"auto_inject_threshold": 1.5})
        assert response.status_code == 422
