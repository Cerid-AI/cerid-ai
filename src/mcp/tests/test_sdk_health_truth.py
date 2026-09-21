# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""F355 / F349 — the two /sdk/v1 health endpoints must agree with each other
and with the backend actually serving the request.

``/sdk/v1/health`` overwrote the application version with the SDK contract
version while ``/sdk/v1/health/detailed`` passed the application version
through, so a consumer doing version negotiation got 1.1.0 or 1.0.3 depending
on which endpoint it happened to call. And ``internal_llm.model`` was an
import-time copy of a config string, so it advertised a model the backend does
not serve.
"""
from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.routers.sdk import router
from app.routers.sdk_version import SDK_VERSION


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


_SNAPSHOT = {
    "llm": {"provider": "ollama", "url": "http://localhost:11434", "model": "qwen2.5-7b-instruct-q4_k_m"},
    "embed": {"provider": "in-process"},
    "rerank": {"provider": "in-process"},
    "sparse": {"provider": "in-process"},
    "nli": {"provider": "in-process"},
}


def test_both_health_endpoints_report_the_same_versions():
    with (
        patch("app.routers.sdk.health_check", return_value={"status": "healthy", "version": "1.0.3", "services": {}}),
        patch("app.routers.sdk.degradation_status", return_value={"status": "healthy", "version": "1.0.3"}),
    ):
        client = _client()
        base = client.get("/sdk/v1/health").json()
        detailed = client.get("/sdk/v1/health/detailed").json()

    assert base["version"] == detailed["version"] == SDK_VERSION
    assert base["app_version"] == detailed["app_version"] == "1.0.3"


def test_internal_llm_reports_the_backend_that_would_serve_the_request():
    with (
        patch("app.routers.sdk.health_check", return_value={"status": "healthy", "version": "1.0.3", "services": {}}),
        patch("core.utils.inference_routing.get_routing_snapshot", return_value=_SNAPSHOT),
    ):
        base = _client().get("/sdk/v1/health").json()

    assert base["internal_llm"]["provider"] == "ollama"
    assert base["internal_llm"]["model"] == "qwen2.5-7b-instruct-q4_k_m"
