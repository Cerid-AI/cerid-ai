# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for GET /settings and PATCH /settings endpoints."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app():
    from app.routers.settings import router

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


class TestRAGPipelineSettings:
    """Tests for Advanced RAG pipeline settings (Phase 35)."""

    def test_get_includes_rag_pipeline_fields(self):
        client = TestClient(_make_app())
        data = client.get("/settings").json()

        rag_fields = [
            "enable_contextual_chunks",
            "enable_adaptive_retrieval",
            "adaptive_retrieval_light_top_k",
            "enable_query_decomposition",
            "query_decomposition_max_subqueries",
            "enable_mmr_diversity",
            "mmr_lambda",
            "enable_intelligent_assembly",
            "enable_late_interaction",
            "late_interaction_top_n",
            "late_interaction_blend_weight",
            "enable_semantic_cache",
            "semantic_cache_threshold",
        ]
        for field in rag_fields:
            assert field in data, f"Missing field: {field}"

    def test_patch_rag_toggle(self):
        client = TestClient(_make_app())
        response = client.patch(
            "/settings", json={"enable_mmr_diversity": True}
        )
        assert response.status_code == 200
        assert response.json()["updated"]["enable_mmr_diversity"] is True

    def test_patch_rag_parameter(self):
        client = TestClient(_make_app())
        response = client.patch("/settings", json={"mmr_lambda": 0.5})
        assert response.status_code == 200
        assert response.json()["updated"]["mmr_lambda"] == 0.5

    def test_rag_parameter_validation(self):
        client = TestClient(_make_app())
        # mmr_lambda out of range
        response = client.patch("/settings", json={"mmr_lambda": 1.5})
        assert response.status_code == 422

    def test_patch_multiple_rag_fields(self):
        client = TestClient(_make_app())
        response = client.patch(
            "/settings",
            json={
                "enable_adaptive_retrieval": True,
                "adaptive_retrieval_light_top_k": 5,
                "enable_semantic_cache": True,
                "semantic_cache_threshold": 0.95,
            },
        )
        assert response.status_code == 200
        updated = response.json()["updated"]
        assert updated["enable_adaptive_retrieval"] is True
        assert updated["adaptive_retrieval_light_top_k"] == 5
        assert updated["enable_semantic_cache"] is True
        assert updated["semantic_cache_threshold"] == 0.95

    def test_dual_mutation_propagates(self):
        """Verify that PATCH updates both config and config.features."""
        import config
        import config.features as features_mod

        client = TestClient(_make_app())
        client.patch("/settings", json={"enable_mmr_diversity": True})

        assert features_mod.ENABLE_MMR_DIVERSITY is True
        assert config.ENABLE_MMR_DIVERSITY is True

        # Clean up
        features_mod.ENABLE_MMR_DIVERSITY = False
        config.ENABLE_MMR_DIVERSITY = False  # type: ignore[assignment]
