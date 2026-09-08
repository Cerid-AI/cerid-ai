# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""``/setup/system-check`` surfaces measured local throughput + derived
expectations so the wizard can render something other than "CPU-only
detected — inference will be slower"."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.routers.setup as setup_router
import utils.inference_config as inference_config


@dataclass
class _FakeHW:
    ram_gb: int = 16
    os: str = "macOS 14.5"
    cpu: str = "Apple M2"
    cpu_cores: int | None = 8
    gpu: str = "Apple M2"
    gpu_acceleration: str = "metal"
    gpu_type: str = ""
    recommended_local_backend: str = "ollama"


@pytest.fixture(autouse=True)
def _fake_environment(monkeypatch):
    monkeypatch.setattr("utils.host_info.get_host_hardware", lambda: _FakeHW())
    # No Ollama reachable in this test tier — avoid a real network attempt.
    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.get = AsyncMock(side_effect=ConnectionError("no ollama in tests"))
    monkeypatch.setattr(setup_router.httpx, "AsyncClient", MagicMock(return_value=fake_client))


async def test_system_check_carries_local_throughput_block_when_measured(monkeypatch):
    cfg = inference_config.InferenceConfig(local_prompt_tok_s=100.0, local_gen_tok_s=9.0, local_probe_at=123.0)
    monkeypatch.setattr(inference_config, "_config", cfg, raising=False)

    result = await setup_router.system_check(response=MagicMock())

    assert result["local_throughput"] == {
        "prompt_tok_s": 100.0,
        "gen_tok_s": 9.0,
        "probe_at": 123.0,
        "expectations": inference_config.expectations_for(cfg),
        "contended": False,
    }
    assert result["local_throughput"]["expectations"]["memory_extract"]["basis"] == "measured"


async def test_system_check_local_throughput_unmeasured(monkeypatch):
    cfg = inference_config.InferenceConfig()
    monkeypatch.setattr(inference_config, "_config", cfg, raising=False)

    result = await setup_router.system_check(response=MagicMock())

    block = result["local_throughput"]
    assert block["prompt_tok_s"] is None
    assert block["gen_tok_s"] is None
    assert block["expectations"]["memory_extract"]["basis"] == "unmeasured"
    assert block["contended"] is False
