# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""/setup/health must emit the service the wizard renders (F245).

The setup wizard's HealthDashboard carries a full SERVICE_META entry for
``verification_pipeline`` — category ``ai_pipeline``, a fix action, and a
"Re-check" button wired to POST /setup/retest-verification — and groups
services by category, dropping any category with no members. The backend
never put ``verification_pipeline`` in its ``services`` list, though it did
name it in ``_OPTIONAL`` as if it did, so the whole AI Pipeline card never
rendered and a broken verification self-test was invisible during onboarding.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.routers import setup

    app = FastAPI()
    app.include_router(setup.router)
    return TestClient(app)


def _healthy_infra():
    return patch(
        "app.routers.setup._service_statuses",
        AsyncMock(
            return_value={
                "neo4j": "healthy",
                "chromadb": "healthy",
                "redis": "healthy",
                "mcp": "healthy",
            }
        ),
    )


def _service(body, name):
    return next((s for s in body["services"] if s["name"] == name), None)


@pytest.mark.parametrize(
    ("self_test", "expected"),
    [
        ({"status": "pass", "claims_found": 2, "extraction_method": "llm"}, "healthy"),
        ({"status": "fail", "claims_found": 0, "extraction_method": "error"}, "error"),
        (None, "degraded"),
    ],
)
def test_verification_pipeline_is_reported(client, self_test, expected):
    with _healthy_infra(), patch(
        "app.agents.hallucination.startup_self_test.get_self_test_status_sync",
        return_value=self_test,
    ), patch("app.deps.get_redis", MagicMock()):
        body = client.get("/setup/health").json()

    svc = _service(body, "verification_pipeline")
    assert svc is not None, [s["name"] for s in body["services"]]
    assert svc["status"] == expected


def test_a_failing_self_test_does_not_block_setup(client):
    """It is in _OPTIONAL — a failure must not flip all_healthy."""
    with _healthy_infra(), patch(
        "app.agents.hallucination.startup_self_test.get_self_test_status_sync",
        return_value={"status": "fail", "claims_found": 0},
    ), patch("app.deps.get_redis", MagicMock()):
        body = client.get("/setup/health").json()

    assert body["all_healthy"] is True
    assert _service(body, "verification_pipeline")["status"] == "error"


def test_the_optional_set_only_names_services_that_are_emitted(client):
    """_OPTIONAL named a service the list never contained — the tell."""
    from app.routers import setup

    with _healthy_infra(), patch(
        "app.agents.hallucination.startup_self_test.get_self_test_status_sync",
        return_value=None,
    ), patch("app.deps.get_redis", MagicMock()):
        body = client.get("/setup/health").json()

    emitted = {s["name"] for s in body["services"]}
    assert setup._OPTIONAL_SERVICES <= emitted, setup._OPTIONAL_SERVICES - emitted
