# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The health surface must report observed fact, not configured intent.

Covers the health-truth regressions: an empty payload rendered as
"healthy" (F013), a degraded inference lane invisible to the overall
verdict (F344), optimistic capability flags from a dead degradation
manager (F014), a hardcoded circuit-breaker name (F169), config-intent
pipeline providers (F351) and the always-empty ``features`` block on the
"detailed" endpoint (F354).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app():
    from app.routers.health import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture(autouse=True)
def _reset_health_cache():
    import app.routers.health as h

    h._health_payload_cache.value = {}
    h._health_payload_cache.updated_at = 0.0
    yield
    h._health_payload_cache.value = {}
    h._health_payload_cache.updated_at = 0.0


class TestColdCacheFailure:
    """F013 — ``all()`` over an empty services dict is True."""

    def test_build_failure_is_not_reported_as_healthy(self):
        with patch(
            "app.routers.health._health_payload_cache._build",
            side_effect=RuntimeError("probe exploded"),
        ):
            response = TestClient(_make_app()).get("/health")

        assert response.status_code == 503, (
            "a total health-build failure returned HTTP 200 to every uptime monitor"
        )
        body = response.json()
        assert body["status"] == "degraded"
        assert body["services"] == {}


class TestDegradedInferenceLane:
    """F344 — a degraded rerank lane must not report ``healthy``."""

    PAYLOAD = {
        "status": "healthy",
        "services": {"chromadb": "connected", "redis": "connected", "neo4j": "connected"},
        "invariants": {"healthy_invariants": True},
        "inference_routing": {
            "llm": {"provider": "quenchforge", "serving": "quenchforge", "degraded": False},
            "rerank": {
                "provider": "quenchforge",
                "serving": "onnx",
                "degraded": True,
                "fallback_count": 44,
            },
        },
    }

    def test_degraded_lane_flips_overall_status(self):
        with patch(
            "app.routers.health._health_payload_cache._build",
            return_value=dict(self.PAYLOAD),
        ):
            response = TestClient(_make_app()).get("/health")

        body = response.json()
        # Transports are fine — the container must stay up.
        assert response.status_code == 200
        assert body["status"] == "degraded", (
            "rerank served by the ONNX fallback but /health still said healthy"
        )
        assert body["degraded_lanes"] == ["rerank"]

    def test_healthy_lanes_leave_status_alone(self):
        payload = dict(self.PAYLOAD)
        payload["inference_routing"] = {
            "rerank": {"provider": "quenchforge", "serving": "quenchforge", "degraded": False},
        }
        with patch("app.routers.health._health_payload_cache._build", return_value=payload):
            response = TestClient(_make_app()).get("/health")

        body = response.json()
        assert response.status_code == 200
        assert body["status"] == "healthy"
        assert "degraded_lanes" not in body


class TestDegradationManagerDead:
    """F014 — capability flags must not default to True."""

    @patch("app.routers.health.health_check", return_value={})
    def test_capabilities_are_unknown_when_manager_fails(self, _mock_health, monkeypatch):
        import utils.degradation as degradation

        def _boom(*_args, **_kwargs):
            raise RuntimeError("degradation manager unavailable")

        monkeypatch.setattr(degradation, "DegradationManager", _boom)
        from app.routers.health import degradation_status

        payload = degradation_status()

        assert payload["degradation_tier"] == "unknown"
        assert payload["can_retrieve"] is None, (
            "a dead degradation manager claimed retrieval works"
        )
        assert payload["can_verify"] is None
        assert payload["can_generate"] is None


class TestCircuitBreakerSurface:
    """F169 — report the breakers that exist, not a hardcoded pair."""

    @patch("app.routers.health.get_neo4j", return_value=None)
    @patch("app.routers.health.get_redis")
    @patch("app.routers.health.get_chroma")
    def test_registered_breakers_are_surfaced(self, _chroma, _redis, _neo4j, monkeypatch):
        from core.utils.circuit_breaker import _BREAKER_REGISTRY, AsyncCircuitBreaker

        monkeypatch.setitem(
            _BREAKER_REGISTRY,
            "quenchforge-rerank",
            AsyncCircuitBreaker("quenchforge-rerank", failure_threshold=3, recovery_timeout=60),
        )
        from app.routers.health import health_check

        breakers = health_check()["circuit_breakers"]

        assert "quenchforge-rerank" in breakers, (
            "the breaker that gates rerank on quenchforge installs is invisible"
        )
        assert "openrouter" in breakers


class TestPipelineProvidersTruth:
    """F351 — pipeline_providers must not contradict inference_routing."""

    @patch("app.routers.health.health_check", return_value={})
    def test_reranking_reports_the_provider_that_actually_served(
        self, _mock_health, monkeypatch
    ):
        from core.routing import provider_state
        from core.utils import inference_health

        monkeypatch.setattr(provider_state, "active_provider", lambda: "quenchforge")
        inference_health.reset()
        inference_health.record_fallback(
            "rerank", configured="quenchforge", served_by="onnx", detail="503"
        )
        try:
            from app.routers.health import degradation_status

            payload = degradation_status()
        finally:
            inference_health.reset()

        assert payload["pipeline_providers"]["reranking"] == "onnx", (
            "pipeline_providers advertised quenchforge for a lane served by CPU ONNX"
        )
        assert payload["pipeline_providers_configured"]["reranking"] == "quenchforge"
        # Lanes that are not degraded keep reporting their provider.
        assert payload["pipeline_providers"]["chat_generation"] == "quenchforge"


class TestDetailedFeatures:
    """F354 — /sdk/v1/health/detailed returned features: {} forever."""

    @patch("app.routers.health.health_check", return_value={})
    def test_features_are_populated(self, _mock_health):
        from app.routers.health import degradation_status

        features = degradation_status()["features"]

        assert features, "the 'detailed' endpoint returned fewer features than the plain one"
        assert set(features) == {
            "enable_hallucination_check",
            "enable_feedback_loop",
            "enable_self_rag",
            "enable_memory_extraction",
        }
