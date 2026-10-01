# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""An install that answers from a local model server is a configured install.

Audit stack, 2026-09-26, no cloud key and a working local model:
``/setup/status`` said ``setup_required: true`` and the wizard reopened in
every browser, because the only thing that made an instance configured was
OPENROUTER_API_KEY.
"""
from __future__ import annotations

import pytest

from app.routers import setup

_ENV = (
    "OPENROUTER_API_KEY", "INTERNAL_LLM_PROVIDER", "OLLAMA_URL", "QUENCHFORGE_URL",
    "INTERNAL_LLM_MODEL", "OLLAMA_DEFAULT_MODEL",
)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)

    def set_(**values: str) -> None:
        for k, v in values.items():
            monkeypatch.setenv(k, v)

    return set_


def test_nothing_set_is_not_configured(env) -> None:
    assert setup._is_configured() is False
    assert setup._missing_keys() == ["OPENROUTER_API_KEY"]


def test_a_cloud_key_alone_is_configured(env) -> None:
    env(**{setup._REQUIRED_KEYS[0]: "placeholder"})
    assert setup._is_configured() is True
    assert setup._missing_keys() == []


@pytest.mark.parametrize("provider", ["ollama", "quenchforge", " Ollama "])
def test_a_local_provider_with_a_url_and_a_model_is_configured(env, provider: str) -> None:
    env(INTERNAL_LLM_PROVIDER=provider, OLLAMA_URL="http://host.docker.internal:11434",
        OLLAMA_DEFAULT_MODEL="gemma-4-26b-a4b")
    assert setup._is_configured() is True
    assert setup._missing_keys() == []


def test_the_model_may_be_named_by_either_setting(env) -> None:
    env(INTERNAL_LLM_PROVIDER="quenchforge", QUENCHFORGE_URL="http://host.docker.internal:11434",
        INTERNAL_LLM_MODEL="gemma-4-26b-a4b")
    assert setup._is_configured() is True


@pytest.mark.parametrize(
    "values",
    [
        {"INTERNAL_LLM_PROVIDER": "ollama", "OLLAMA_DEFAULT_MODEL": "m"},          # nowhere to ask
        {"INTERNAL_LLM_PROVIDER": "ollama", "OLLAMA_URL": "http://h:11434"},        # nothing to ask for
        {"OLLAMA_URL": "http://h:11434", "OLLAMA_DEFAULT_MODEL": "m"},              # not asked to
        {"INTERNAL_LLM_PROVIDER": "openrouter", "OLLAMA_URL": "http://h:11434",
         "OLLAMA_DEFAULT_MODEL": "m"},                                              # asked for the cloud
        {"INTERNAL_LLM_PROVIDER": "ollama", "OLLAMA_URL": "  ", "OLLAMA_DEFAULT_MODEL": "m"},
    ],
)
def test_a_local_provider_that_is_half_written_is_not(env, values: dict[str, str]) -> None:
    env(**values)
    assert setup._is_configured() is False
    assert setup._missing_keys() == ["OPENROUTER_API_KEY"]
