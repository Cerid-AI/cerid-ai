# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The setup wizard is offered chat models, and told which one is in use."""
from __future__ import annotations

import pytest

from app.routers.setup import _local_chat_choice

SERVED = ["nomic-embed-text-v1.5", "qwen3.5-4b-instruct", "gemma-4-26b-a4b"]


@pytest.fixture(autouse=True)
def _no_pins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_DEFAULT_MODEL", raising=False)
    monkeypatch.delenv("INTERNAL_LLM_MODEL", raising=False)


def test_an_embedder_is_not_offered_for_chat() -> None:
    models, configured = _local_chat_choice(SERVED)
    assert models == ["qwen3.5-4b-instruct", "gemma-4-26b-a4b"]
    assert configured is None


@pytest.mark.parametrize("key", ["OLLAMA_DEFAULT_MODEL", "INTERNAL_LLM_MODEL"])
def test_the_model_already_named_is_reported(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    monkeypatch.setenv(key, "gemma-4-26b-a4b")
    assert _local_chat_choice(SERVED)[1] == "gemma-4-26b-a4b"


def test_a_named_model_the_server_does_not_serve_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_DEFAULT_MODEL", "llama3.2:3b")
    assert _local_chat_choice(SERVED)[1] is None


def test_a_named_embedder_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_DEFAULT_MODEL", "nomic-embed-text-v1.5")
    assert _local_chat_choice(SERVED)[1] is None


def test_nothing_served_offers_nothing() -> None:
    assert _local_chat_choice([]) == ([], None)


def test_two_named_models_report_the_one_inference_asks_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INTERNAL_LLM_MODEL is read first when a call is made, so it is the one in use."""
    monkeypatch.setenv("OLLAMA_DEFAULT_MODEL", "qwen3.5-4b-instruct")
    monkeypatch.setenv("INTERNAL_LLM_MODEL", "gemma-4-26b-a4b")
    assert _local_chat_choice(SERVED)[1] == "gemma-4-26b-a4b"
