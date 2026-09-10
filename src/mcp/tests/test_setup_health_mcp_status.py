# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""``/setup/health``'s ``mcp`` status must reflect app readiness, not just
config presence.

``_service_statuses()`` previously derived ``mcp`` from ``_is_configured()``
alone, so a freshly-booted-but-still-starting instance could read "healthy"
before the lifespan finished. It now goes through three states: "starting"
until the app is ready, "setup_mode" once ready but unconfigured, "healthy"
once ready and configured — reusing ``app.routers.health``'s readiness
signal rather than a new flag.
"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def setup_module():
    sys.modules.pop("app.routers.setup", None)
    return importlib.import_module("app.routers.setup")


@pytest.fixture(autouse=True)
def _stub_infra_probes():
    """Keep _service_statuses() off real network/Neo4j/Redis for these tests."""
    with (
        patch("app.deps.get_neo4j", return_value=None),
        patch("app.deps.get_redis", return_value=MagicMock()),
        patch(
            "app.routers.setup._check_service",
            return_value="healthy",
        ) as check,
    ):
        # _check_service is async — patch returns a coroutine-returning mock.
        async def _fake_check(*_args, **_kwargs) -> str:
            return "healthy"

        check.side_effect = _fake_check
        yield


class TestMcpStatus:
    async def test_starting_when_not_ready(self, setup_module, monkeypatch):
        monkeypatch.setattr(setup_module.health, "is_ready", lambda: False)
        monkeypatch.setattr(setup_module, "_is_configured", lambda: True)

        statuses = await setup_module._service_statuses()

        assert statuses["mcp"] == "starting"

    async def test_setup_mode_when_ready_and_unconfigured(self, setup_module, monkeypatch):
        monkeypatch.setattr(setup_module.health, "is_ready", lambda: True)
        monkeypatch.setattr(setup_module, "_is_configured", lambda: False)

        statuses = await setup_module._service_statuses()

        assert statuses["mcp"] == "setup_mode"

    async def test_healthy_when_ready_and_configured(self, setup_module, monkeypatch):
        monkeypatch.setattr(setup_module.health, "is_ready", lambda: True)
        monkeypatch.setattr(setup_module, "_is_configured", lambda: True)

        statuses = await setup_module._service_statuses()

        assert statuses["mcp"] == "healthy"
