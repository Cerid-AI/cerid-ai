# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""An API that answers in setup mode is up.

``/setup/health`` reports ``mcp: setup_mode`` until a provider is configured.
That is the state the wizard runs in, and the request was answered by the
API it describes, so it must not hold ``all_healthy`` at false.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
import respx

import app.deps as deps
import app.routers.setup as setup_router

CHROMA = "http://chroma.test:8000"


@pytest.fixture(autouse=True)
def _unconfigured_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENROUTER_API_KEY", "INTERNAL_LLM_PROVIDER"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CHROMA_URL", CHROMA)
    session = MagicMock()
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    monkeypatch.setattr(deps, "get_neo4j", lambda: driver)
    monkeypatch.setattr(deps, "get_redis", lambda: MagicMock())
    monkeypatch.setattr(
        "app.agents.hallucination.startup_self_test.get_self_test_status_sync",
        lambda _redis: None,
    )


def _mcp(body: dict) -> dict:
    return next(s for s in body["services"] if s["name"] == "mcp")


@respx.mock
async def test_setup_mode_counts_as_up(monkeypatch: pytest.MonkeyPatch) -> None:
    respx.get(f"{CHROMA}/api/v2/heartbeat").mock(return_value=httpx.Response(200, json={}))
    monkeypatch.setattr(setup_router.health, "is_ready", lambda: True)

    body = await setup_router.setup_health()

    assert _mcp(body)["status"] == "setup_mode"
    assert body["all_healthy"] is True


@respx.mock
async def test_a_starting_api_is_not_up(monkeypatch: pytest.MonkeyPatch) -> None:
    respx.get(f"{CHROMA}/api/v2/heartbeat").mock(return_value=httpx.Response(200, json={}))
    monkeypatch.setattr(setup_router.health, "is_ready", lambda: False)

    body = await setup_router.setup_health()

    assert _mcp(body)["status"] == "starting"
    assert body["all_healthy"] is False


@respx.mock
async def test_setup_mode_does_not_hide_a_store_that_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    respx.get(f"{CHROMA}/api/v2/heartbeat").mock(side_effect=httpx.ConnectError("down"))
    monkeypatch.setattr(setup_router.health, "is_ready", lambda: True)

    body = await setup_router.setup_health()

    assert body["all_healthy"] is False
