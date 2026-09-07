# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""``GET /providers/ollama/recommendations`` populates the wizard's dead
``expected_tokens_per_sec`` field from the measured local generation rate,
size-scaled across the catalog by each model's parameter count."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

import app.routers.providers as providers_router
import utils.inference_config as inference_config


@dataclass
class _FakeHW:
    ram_gb: int = 64
    cpu: str = "Xeon W-3245"
    gpu: str = "AMD Radeon Pro Vega II"
    os: str = "macOS 14.5"


@pytest.fixture(autouse=True)
def _fake_hardware(monkeypatch):
    monkeypatch.setattr("utils.host_info.get_host_hardware", lambda: _FakeHW())


async def test_served_model_gets_measured_rate_others_scaled(monkeypatch):
    cfg = inference_config.InferenceConfig(local_gen_tok_s=9.0)
    monkeypatch.setattr(inference_config, "_config", cfg, raising=False)
    monkeypatch.setattr(
        "core.utils.internal_llm.effective_local_model_async",
        AsyncMock(return_value="llama3.1:8b"),
    )

    result = await providers_router.get_ollama_recommendations()

    by_id = {m["id"]: m for m in result["models"]}
    assert by_id["llama3.1:8b"]["expected_tokens_per_sec"] == pytest.approx(9.0)
    # 3B is ~2.67x smaller than the served 8B -> proportionally faster.
    # Compared with abs tolerance: the endpoint rounds to 1 decimal.
    assert by_id["llama3.2:3b"]["expected_tokens_per_sec"] == pytest.approx(9.0 * 8 / 3, abs=0.05)
    assert by_id["mistral:7b"]["expected_tokens_per_sec"] == pytest.approx(9.0 * 8 / 7, abs=0.05)


async def test_unmeasured_rate_yields_no_expected_tokens_per_sec(monkeypatch):
    cfg = inference_config.InferenceConfig()
    monkeypatch.setattr(inference_config, "_config", cfg, raising=False)

    result = await providers_router.get_ollama_recommendations()

    assert all(m["expected_tokens_per_sec"] is None for m in result["models"])
