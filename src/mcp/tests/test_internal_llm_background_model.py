# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Background-stage model slot — ``INTERNAL_LLM_MODEL_BACKGROUND``.

A class-A host runs its chat slot on a 7B model at ~9 tok/s. The background
tail (wiki summaries, entity extraction, community summaries) does not need
that model: a 3B is 2.5x faster with equal extraction recall. This pins the
slot's contract:

- Only background stages use it (the complement of the interactive set).
- It is used only when the gateway actually SERVES it — an unserved name
  would 400 on a strict gateway build, so it falls back to the normal
  resolved local model.
- The fallback logs one INFO per resolution change, not one per call.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import core.utils.internal_llm as mod


@pytest.fixture(autouse=True)
def _fresh_resolver_state(monkeypatch):
    mod.reset_effective_local_model_cache()
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MODEL", "qwen2.5-7b", raising=False)
    monkeypatch.setattr(mod.config, "OLLAMA_DEFAULT_MODEL", "llama3.2:3b", raising=False)
    monkeypatch.setattr(
        mod.config, "QUENCHFORGE_URL", "http://quenchforge.test:11434", raising=False
    )
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MODEL_BACKGROUND", "", raising=False)
    yield
    mod.reset_effective_local_model_cache()


def _wire_served(monkeypatch, names: list[str]) -> None:
    async def _fetch() -> tuple[list[str], float]:
        return names, 1.0

    monkeypatch.setattr(mod, "_fetch_served_models_async", _fetch)


@pytest.mark.asyncio
async def test_background_stage_uses_the_background_model_when_served(monkeypatch):
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MODEL_BACKGROUND", "qwen2.5-3b")
    _wire_served(monkeypatch, ["qwen2.5-7b", "qwen2.5-3b"])
    assert await mod._local_model_for_stage("wiki_summary") == "qwen2.5-3b"


@pytest.mark.asyncio
async def test_background_stage_falls_back_when_not_served(monkeypatch, caplog):
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MODEL_BACKGROUND", "qwen2.5-3b")
    _wire_served(monkeypatch, ["qwen2.5-7b"])
    with caplog.at_level(logging.INFO, logger="ai-companion.internal_llm"):
        first = await mod._local_model_for_stage("wiki_summary")
        second = await mod._local_model_for_stage("entity_extraction")
    assert first == "qwen2.5-7b"
    assert second == "qwen2.5-7b"
    infos = [
        r for r in caplog.records
        if r.name == "ai-companion.internal_llm" and r.levelno == logging.INFO
    ]
    # One line per resolution CHANGE, not one per call.
    assert len(infos) == 1
    assert "qwen2.5-3b" in infos[0].getMessage()


@pytest.mark.asyncio
async def test_interactive_stage_never_uses_the_background_model(monkeypatch):
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MODEL_BACKGROUND", "qwen2.5-3b")
    _wire_served(monkeypatch, ["qwen2.5-7b", "qwen2.5-3b"])
    assert await mod._local_model_for_stage("memory_extract") == "qwen2.5-7b"
    assert await mod._local_model_for_stage("mcp_summarize_domain") == "qwen2.5-7b"
    assert await mod._local_model_for_stage(None) == "qwen2.5-7b"


@pytest.mark.asyncio
async def test_unset_background_slot_changes_nothing(monkeypatch):
    _wire_served(monkeypatch, ["qwen2.5-7b", "qwen2.5-3b"])
    assert await mod._local_model_for_stage("wiki_summary") == "qwen2.5-7b"


class _FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._body


@pytest.mark.asyncio
async def test_call_ollama_sends_the_background_model(monkeypatch):
    """The slot has to reach the wire, not just the resolver."""
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MODEL_BACKGROUND", "qwen2.5-3b")
    _wire_served(monkeypatch, ["qwen2.5-7b", "qwen2.5-3b"])
    seen: dict[str, Any] = {}

    async def _post(url: str, *, json: dict) -> _FakeResponse:  # noqa: ARG001
        seen["model"] = json["model"]
        return _FakeResponse({"message": {"content": "ok"}})

    fake_client = MagicMock()
    fake_client.post = _post
    monkeypatch.setattr(mod, "_get_ollama_client", AsyncMock(return_value=fake_client))

    class _PassThroughBreaker:
        async def call(self, fn):  # type: ignore[no-untyped-def]
            return await fn()

    monkeypatch.setattr(mod, "get_breaker", lambda _name: _PassThroughBreaker())

    result = await mod._call_ollama(
        [{"role": "user", "content": "hi"}],
        temperature=0,
        max_tokens=5,
        stage="wiki_summary",
    )
    assert result == "ok"
    assert seen["model"] == "qwen2.5-3b"


@pytest.mark.asyncio
async def test_operator_stage_pin_beats_the_background_slot(monkeypatch):
    """A bare local model passed in by ``_resolve_stage_model`` is an operator
    pin (``PROVIDER_STAGE_<X>_MODEL``) and must win."""
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MODEL_BACKGROUND", "qwen2.5-3b")
    _wire_served(monkeypatch, ["qwen2.5-7b", "qwen2.5-3b", "pinned-model"])
    seen: dict[str, Any] = {}

    async def _post(url: str, *, json: dict) -> _FakeResponse:  # noqa: ARG001
        seen["model"] = json["model"]
        return _FakeResponse({"message": {"content": "ok"}})

    fake_client = MagicMock()
    fake_client.post = _post
    monkeypatch.setattr(mod, "_get_ollama_client", AsyncMock(return_value=fake_client))

    class _PassThroughBreaker:
        async def call(self, fn):  # type: ignore[no-untyped-def]
            return await fn()

    monkeypatch.setattr(mod, "get_breaker", lambda _name: _PassThroughBreaker())

    await mod._call_ollama(
        [{"role": "user", "content": "hi"}],
        temperature=0,
        max_tokens=5,
        stage="wiki_summary",
        model="pinned-model",
    )
    assert seen["model"] == "pinned-model"


def test_pacing_concurrency_reads_the_declared_setting(monkeypatch):
    """gap 11: the cap was read from bare os.environ and undeclared."""
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MAX_CONCURRENCY", 1, raising=False)
    assert mod._pacing_max_concurrency() == 1
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MAX_CONCURRENCY", 3, raising=False)
    assert mod._pacing_max_concurrency() == 3
