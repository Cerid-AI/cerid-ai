# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The backend and model chosen in the wizard are what inference uses.

``/setup/configure`` wrote ``OLLAMA_ENABLED`` and ``OLLAMA_DEFAULT_MODEL``.
A call reads ``INTERNAL_LLM_PROVIDER`` and ``INTERNAL_LLM_MODEL``, so the
choice changed nothing and a local-only install stayed in setup mode.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

import app.routers.setup as setup_router
import config
import core.utils.internal_llm as internal_llm
from core.utils.circuit_breaker import get_breaker

URL = "http://models.test:11434"
SERVED = ["nomic-embed-text-v1.5", "qwen3.5-4b-instruct", "gemma-4-26b-a4b"]
TAGS = {"models": [{"name": name} for name in SERVED]}
_KEYS = (
    "OPENROUTER_API_KEY", "INTERNAL_LLM_PROVIDER", "INTERNAL_LLM_MODEL",
    "OLLAMA_ENABLED", "OLLAMA_DEFAULT_MODEL", "QUENCHFORGE_URL", "ARCHIVE_PATH",
)


@pytest.fixture()
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / ".env"
    path.write_text("# instance\n", encoding="utf-8")
    monkeypatch.setattr(setup_router, "_ENV_FILE", path)
    monkeypatch.setattr(
        config, "HOST_SETTINGS_PATH", str(tmp_path / "host_settings.json"), raising=False,
    )
    # setenv before delenv registers each key with monkeypatch, so whatever
    # configure writes to the process is undone after the test.
    for key in _KEYS:
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
    monkeypatch.setenv("OLLAMA_URL", URL)
    for attr in ("INTERNAL_LLM_PROVIDER", "INTERNAL_LLM_MODEL", "PIPELINE_PROVIDERS"):
        monkeypatch.setattr(config, attr, getattr(config, attr))
    monkeypatch.setattr(config, "INTERNAL_LLM_MODEL_BACKGROUND", "", raising=False)

    async def _no_warmup() -> None:
        return None

    monkeypatch.setattr(setup_router, "_post_configure_warmup", _no_warmup)
    internal_llm.reset_effective_local_model_cache()
    get_breaker("ollama").reset()
    yield path
    internal_llm.reset_effective_local_model_cache()


def _written(path: Path) -> dict[str, str]:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if "=" in ln]
    return dict(ln.split("=", 1) for ln in lines if not ln.startswith("#"))


def _local(model: str | None = "qwen3.5-4b-instruct", **extra) -> setup_router.ConfigureRequest:
    return setup_router.ConfigureRequest(
        inference_backend="ollama", ollama_enabled=True, ollama_model=model, **extra,
    )


async def test_the_chosen_backend_and_model_are_written(env_file: Path) -> None:
    result = await setup_router.configure(_local())

    assert result.success is True
    written = _written(env_file)
    assert written["INTERNAL_LLM_PROVIDER"] == "ollama"
    assert written["INTERNAL_LLM_MODEL"] == "qwen3.5-4b-instruct"
    assert written["OLLAMA_DEFAULT_MODEL"] == "qwen3.5-4b-instruct"


async def test_the_chosen_model_is_the_one_setup_reports(env_file: Path) -> None:
    await setup_router.configure(_local())

    assert setup_router._local_chat_choice(SERVED)[1] == "qwen3.5-4b-instruct"


@respx.mock
async def test_the_chosen_model_is_the_one_a_call_asks_for(env_file: Path) -> None:
    respx.get(f"{URL}/api/tags").mock(return_value=httpx.Response(200, json=TAGS))
    respx.get(f"{URL}/").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    chat = respx.post(f"{URL}/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"content": "ok"}}),
    )

    await setup_router.configure(_local())
    answer = await internal_llm.call_internal_llm(
        [{"role": "user", "content": "hello"}], stage="topic_extraction",
    )

    assert answer == "ok"
    assert chat.call_count == 1
    assert json.loads(chat.calls.last.request.content)["model"] == "qwen3.5-4b-instruct"


async def test_a_model_already_configured_is_kept_when_none_is_sent(
    env_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("INTERNAL_LLM_MODEL", "gemma-4-26b-a4b")

    result = await setup_router.configure(_local(model=None, force=True))

    assert result.success is True
    assert _written(env_file)["INTERNAL_LLM_MODEL"] == "gemma-4-26b-a4b"


async def test_no_model_is_picked_for_the_user(env_file: Path) -> None:
    result = await setup_router.configure(_local(model=None))

    assert result.success is False
    assert "model" in (result.error or "").lower()
    assert _written(env_file) == {}


async def test_a_local_only_configure_leaves_setup_mode(
    env_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup_router.health, "is_ready", lambda: True)
    assert setup_router._is_configured() is False

    await setup_router.configure(_local())

    assert setup_router._is_configured() is True
    assert setup_router._missing_keys() == []


async def test_a_cloud_only_configure_writes_no_local_provider(env_file: Path) -> None:
    result = await setup_router.configure(
        setup_router.ConfigureRequest(
            inference_backend="cloud",
            openrouter_api_key="placeholder",  # pragma: allowlist secret
            ollama_enabled=False,
        ),
    )

    assert result.success is True
    written = _written(env_file)
    assert "INTERNAL_LLM_PROVIDER" not in written
    assert "INTERNAL_LLM_MODEL" not in written


async def test_a_cloud_only_configure_keeps_an_existing_local_configuration(
    env_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file.write_text(
        "INTERNAL_LLM_PROVIDER=ollama\n"
        "INTERNAL_LLM_MODEL=gemma-4-26b-a4b\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("INTERNAL_LLM_MODEL", "gemma-4-26b-a4b")

    await setup_router.configure(
        setup_router.ConfigureRequest(
            inference_backend="cloud",
            openrouter_api_key="placeholder",  # pragma: allowlist secret
            force=True,
        ),
    )

    written = _written(env_file)
    assert written["INTERNAL_LLM_PROVIDER"] == "ollama"
    assert written["INTERNAL_LLM_MODEL"] == "gemma-4-26b-a4b"


async def test_a_local_backend_the_user_switched_off_is_not_written(env_file: Path) -> None:
    await setup_router.configure(
        setup_router.ConfigureRequest(
            inference_backend="ollama", ollama_enabled=False,
            ollama_model="qwen3.5-4b-instruct", archive_path="/archive",
        ),
    )

    assert "INTERNAL_LLM_PROVIDER" not in _written(env_file)


async def test_an_unknown_backend_is_refused(env_file: Path) -> None:
    result = await setup_router.configure(
        setup_router.ConfigureRequest(
            inference_backend="somewhere", ollama_enabled=True, ollama_model="qwen3.5-4b-instruct",
        ),
    )

    assert result.success is False
    assert _written(env_file) == {}


async def test_a_configured_instance_still_needs_force(
    env_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    monkeypatch.setenv("OPENROUTER_API_KEY", "placeholder-cloud-key")

    with pytest.raises(HTTPException) as refused:
        await setup_router.configure(_local())

    assert refused.value.status_code == 409
    assert _written(env_file) == {}


async def test_the_choice_survives_a_restart_through_the_per_machine_file(
    env_file: Path,
) -> None:
    """Startup restores host settings over .env, so the wizard saves there too."""
    from app.sync.user_state import read_host_settings

    await setup_router.configure(_local("gemma-4-26b-a4b"))

    saved = read_host_settings(config.HOST_SETTINGS_PATH)
    assert saved["internal_llm_provider"] == "ollama"
    assert saved["internal_llm_model"] == "gemma-4-26b-a4b"
