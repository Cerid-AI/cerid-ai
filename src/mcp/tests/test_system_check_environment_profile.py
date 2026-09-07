# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""``/setup/system-check`` reports which environment profile this host should
run (``suggested_profile``) and which one is actually in force
(``active_profile``).

``active_profile`` is recomputed here rather than read off settings: the
Private Mode level that can degrade a cloud profile lives in Redis and is
mutable at runtime, so the boot-time resolution in ``config/settings.py``
goes stale the moment the operator flips the toolbar.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.routers.setup as setup_router


@dataclass
class _FakeHW:
    ram_gb: int = 160
    os: str = "macOS 14.5"
    cpu: str = "Intel Xeon W-3245"
    cpu_cores: int | None = 16
    gpu: str = "Radeon Pro Vega II"
    gpu_acceleration: str = "none"
    gpu_type: str = "amd-mac"
    recommended_local_backend: str = "quenchforge"


@pytest.fixture(autouse=True)
def _fake_environment(monkeypatch):
    monkeypatch.setattr("utils.host_info.get_host_hardware", lambda: _FakeHW())
    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.get = AsyncMock(side_effect=ConnectionError("no ollama in tests"))
    monkeypatch.setattr(setup_router.httpx, "AsyncClient", MagicMock(return_value=fake_client))
    monkeypatch.setattr(setup_router, "get_private_mode_level", lambda: 0)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")


async def test_reference_host_suggests_hybrid(monkeypatch):
    """amd-mac + a cloud key + Private Mode L0 — the spec's worked example."""
    monkeypatch.setattr(setup_router.config, "CERID_ENVIRONMENT_PROFILE", "", raising=False)

    result = await setup_router.system_check(response=MagicMock())

    assert result["suggested_profile"] == "hybrid"
    assert result["active_profile"] == ""


async def test_active_profile_reflects_the_configured_profile(monkeypatch):
    monkeypatch.setattr(
        setup_router.config, "CERID_ENVIRONMENT_PROFILE", "hybrid", raising=False,
    )

    result = await setup_router.system_check(response=MagicMock())

    assert result["active_profile"] == "hybrid"


async def test_live_private_mode_degrades_the_active_profile(monkeypatch):
    """The boot-time resolution cannot see a runtime Private Mode flip."""
    monkeypatch.setattr(
        setup_router.config, "CERID_ENVIRONMENT_PROFILE", "hybrid", raising=False,
    )
    monkeypatch.setattr(setup_router, "get_private_mode_level", lambda: 2)

    result = await setup_router.system_check(response=MagicMock())

    assert result["suggested_profile"] == "local-only"
    assert result["active_profile"] == "local-only"


async def test_no_cloud_key_suggests_local_only(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(setup_router.config, "CERID_ENVIRONMENT_PROFILE", "", raising=False)

    result = await setup_router.system_check(response=MagicMock())

    assert result["suggested_profile"] == "local-only"
