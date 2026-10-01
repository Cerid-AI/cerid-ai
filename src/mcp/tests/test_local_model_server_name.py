# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The local model server is named from what it reports about itself.

Three servers speak the Ollama API on port 11434. Health and the setup
system check called all of them "ollama" because that is the configured
provider id.
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import httpx
import pytest
import respx

import app.routers.health as health_router
import app.routers.setup as setup_router
import utils.inference_config as inference_config
from utils.local_model_server import NEUTRAL_NAME, local_server_name

URL = "http://models.test:11434"
TAGS = {"models": [{"name": "qwen3.5-4b-instruct"}, {"name": "nomic-embed-text-v1.5"}]}


def test_the_mlx_server_is_named_from_its_version() -> None:
    assert local_server_name("cerid-mlx-c33d6c31d1f1", None) == "MLX server"


def test_quenchforge_is_named_from_its_landing_route() -> None:
    assert local_server_name(None, {"service": "quenchforge", "version": "0.8.0"}) == "Quenchforge"


def test_ollama_is_named_from_its_landing_text() -> None:
    assert local_server_name("0.5.7", "Ollama is running") == "Ollama"


@pytest.mark.parametrize(
    ("version", "landing"),
    [(None, None), ("0.5.7", None), ("1.2.3", {"status": "ok"}), (None, "hello")],
)
def test_a_server_that_does_not_say_gets_the_neutral_name(version, landing) -> None:
    assert local_server_name(version, landing) == NEUTRAL_NAME


@pytest.fixture()
def _local_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", URL)
    monkeypatch.delenv("QUENCHFORGE_URL", raising=False)
    monkeypatch.setenv("OLLAMA_ENABLED", "true")
    monkeypatch.setattr(health_router, "_ollama_probe_cache", None)
    monkeypatch.setattr(health_router, "_local_server_cache", None)
    monkeypatch.setattr(health_router, "get_chroma", lambda: MagicMock())
    monkeypatch.setattr(health_router, "get_redis", lambda: MagicMock())
    monkeypatch.setattr(health_router, "get_neo4j", lambda: None)


@respx.mock
def test_health_names_the_mlx_server(_local_provider) -> None:
    respx.get(f"{URL}/api/tags").mock(return_value=httpx.Response(200, json=TAGS))
    respx.get(f"{URL}/api/version").mock(
        return_value=httpx.Response(200, json={"version": "cerid-mlx-c33d6c31d1f1"}),
    )

    result = health_router.health_check()

    assert result["local_model_server"] == {
        "name": "MLX server",
        "version": "cerid-mlx-c33d6c31d1f1",
        "url": URL,
    }
    assert result["internal_llm_provider"] == "ollama"


@respx.mock
def test_health_uses_the_neutral_name_when_the_server_is_down(_local_provider) -> None:
    respx.get(f"{URL}/api/tags").mock(side_effect=httpx.ConnectError("down"))
    respx.get(f"{URL}/api/version").mock(side_effect=httpx.ConnectError("down"))
    respx.get(f"{URL}/").mock(side_effect=httpx.ConnectError("down"))

    result = health_router.health_check()

    assert result["local_model_server"] == {"name": NEUTRAL_NAME, "version": None, "url": URL}


@respx.mock
def test_health_has_no_local_server_on_a_cloud_instance(
    _local_provider, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OLLAMA_ENABLED", "false")

    assert "local_model_server" not in health_router.health_check()


@dataclass
class _FakeHW:
    ram_gb: int = 64
    os: str = "macOS 26"
    cpu: str = "Apple M4 Max"
    cpu_cores: int | None = 16
    gpu: str = "Apple M4 Max"
    gpu_acceleration: str = "metal"
    gpu_type: str = "metal"
    recommended_local_backend: str = "ollama"


@pytest.fixture()
def _system_check_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("utils.host_info.get_host_hardware", lambda: _FakeHW())
    monkeypatch.setattr(inference_config, "_config", inference_config.InferenceConfig(), raising=False)
    monkeypatch.setenv("OLLAMA_URL", URL)


@respx.mock
async def test_system_check_names_the_mlx_server(_system_check_env) -> None:
    respx.get(f"{URL}/api/tags").mock(return_value=httpx.Response(200, json=TAGS))
    respx.get(f"{URL}/api/version").mock(
        return_value=httpx.Response(200, json={"version": "cerid-mlx-c33d6c31d1f1"}),
    )

    result = await setup_router.system_check(response=MagicMock())

    assert result["local_server_name"] == "MLX server"
    assert result["local_server_version"] == "cerid-mlx-c33d6c31d1f1"
    assert result["recommended_local_backend"] == "ollama"


@respx.mock
async def test_system_check_names_quenchforge(_system_check_env) -> None:
    respx.get(f"{URL}/api/tags").mock(return_value=httpx.Response(200, json=TAGS))
    respx.get(f"{URL}/api/version").mock(return_value=httpx.Response(404, json={}))
    respx.get(f"{URL}/").mock(
        return_value=httpx.Response(200, json={"service": "quenchforge", "version": "0.8.0"}),
    )

    result = await setup_router.system_check(response=MagicMock())

    assert result["local_server_name"] == "Quenchforge"
    assert result["local_server_version"] == "0.8.0"


@respx.mock
async def test_system_check_uses_the_neutral_name_for_an_unknown_server(_system_check_env) -> None:
    respx.get(f"{URL}/api/tags").mock(return_value=httpx.Response(200, json=TAGS))
    respx.get(f"{URL}/api/version").mock(return_value=httpx.Response(404, json={}))
    respx.get(f"{URL}/").mock(return_value=httpx.Response(200, json={"status": "ok"}))

    result = await setup_router.system_check(response=MagicMock())

    assert result["local_server_name"] == NEUTRAL_NAME
    assert result["local_server_version"] is None
