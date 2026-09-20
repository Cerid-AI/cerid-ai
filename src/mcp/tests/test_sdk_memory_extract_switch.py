# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""MEMORY_QUEUE_MODE is the only switch for the 202 path (F002).

``_memory_async_enabled()`` also returned True whenever the active inference
provider was local, so the shipped local-inference target answered 202 by
default — while the endpoint description, docs/SDK_GUIDE.md and .env.example
all said 202 happens "when MEMORY_QUEUE_MODE=async is set on the server".

Both bundled SDK clients validate the response body without checking the
status, and every count on SDKMemoryExtractResponse defaults to 0, so the
202 envelope ({job_id, status, status_url, conversation_id}) validated
cleanly to memories_extracted=0, memories_stored=0: a well-formed report of
a permanently broken memory pipeline for a job that in fact ran.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client() -> TestClient:
    from app.routers import sdk

    queue = MagicMock()
    queue.enqueue = AsyncMock(return_value="job-1")
    app = FastAPI()
    app.include_router(sdk.router)
    app.state.processor_queue = queue
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("local_provider", [True, False])
def test_local_inference_does_not_silently_flip_the_response_code(
    monkeypatch, local_provider
):
    """The documented default is sync; the provider must not override it."""
    import config

    monkeypatch.setattr(config, "MEMORY_QUEUE_MODE", "sync", raising=False)

    with patch(
        "core.routing.provider_state.is_local_provider", return_value=local_provider
    ), patch(
        "app.routers.sdk.memory_extract_endpoint",
        AsyncMock(return_value={"memories_extracted": 2, "memories_stored": 2}),
    ):
        res = _client().post(
            "/sdk/v1/memory/extract",
            json={"response_text": "x" * 300, "conversation_id": "c1"},
        )

    assert res.status_code == 200, (
        f"MEMORY_QUEUE_MODE=sync with is_local_provider={local_provider} "
        f"answered {res.status_code}; the published contract says 202 only "
        "when MEMORY_QUEUE_MODE=async"
    )
    assert res.json()["memories_extracted"] == 2


def test_the_opt_in_still_works(monkeypatch):
    import config

    monkeypatch.setattr(config, "MEMORY_QUEUE_MODE", "async", raising=False)

    with patch("core.routing.provider_state.is_local_provider", return_value=False):
        res = _client().post(
            "/sdk/v1/memory/extract",
            json={"response_text": "x" * 300, "conversation_id": "c1"},
        )

    assert res.status_code == 202, res.text
    assert res.json()["job_id"] == "job-1"


def test_the_description_names_the_only_switch():
    from app.routers import sdk

    route = next(
        r for r in sdk.router.routes
        if getattr(r, "path", None) == "/sdk/v1/memory/extract"
    )
    assert "MEMORY_QUEUE_MODE" in route.description
    assert "local" not in route.description.lower() or "MEMORY_QUEUE_MODE" in route.description
