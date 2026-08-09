# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 3c-2 fold — internal_llm call-spec fixes (CR-038/102/103/111).

The stage-resolved model + llm_call_override were silently dropped on the local
branch and the local->cloud fallback, the json-token guard was only on the
fallback path, and the streaming entry ignored the override provider. Synthetic
(fake ollama client + mocked call_llm), no stack/network."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from core.utils import inference_health
from core.utils import internal_llm as mod
from core.utils.internal_llm import llm_call_override


class _FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._body


def _wire_local_capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Fake ollama client recording the posted payload; local default llama3.2:3b."""
    captured: dict[str, Any] = {}
    fake_client = MagicMock()

    async def _post(url: str, *, json: dict) -> _FakeResponse:  # noqa: ARG001
        captured["payload"] = json
        return _FakeResponse({"message": {"content": "ok"}})

    fake_client.post = _post
    monkeypatch.setattr(mod, "_get_ollama_client", AsyncMock(return_value=fake_client))

    class _PassThroughBreaker:
        async def call(self, fn):  # type: ignore[no-untyped-def]
            return await fn()

    monkeypatch.setattr(mod, "get_breaker", lambda _name: _PassThroughBreaker())
    monkeypatch.setenv("OLLAMA_URL", "http://test-host:11434")
    monkeypatch.setattr(mod.config, "OLLAMA_DEFAULT_MODEL", "llama3.2:3b", raising=False)
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MODEL", "", raising=False)
    return captured


# --- CR-038: local dispatch honors a bare (local) stage/override model ---

@pytest.mark.asyncio
async def test_local_dispatch_uses_bare_stage_model(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _wire_local_capture(monkeypatch)
    await mod._call_ollama(
        [{"role": "user", "content": "hi"}],
        temperature=0.1, max_tokens=10, provider="ollama", model="custom-local-8b",
    )
    assert captured["payload"]["model"] == "custom-local-8b"


@pytest.mark.asyncio
async def test_local_dispatch_ignores_openrouter_tier_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tier id ("openrouter/...", carries a "/") can't be served locally →
    the local default is used, not the unusable cloud id."""
    captured = _wire_local_capture(monkeypatch)
    await mod._call_ollama(
        [{"role": "user", "content": "hi"}],
        temperature=0.1, max_tokens=10, provider="ollama",
        model="openrouter/anthropic/claude-sonnet-4.6",
    )
    assert captured["payload"]["model"] == "llama3.2:3b"


# --- CR-102: local->cloud fallback uses the stage-resolved OpenRouter model ---

@pytest.mark.asyncio
async def test_fallback_uses_openrouter_hint_model(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = MagicMock()

    async def _post(url: str, *, json: dict):  # noqa: ARG001
        raise httpx.ConnectError("local daemon down")

    fake_client.post = _post
    monkeypatch.setattr(mod, "_get_ollama_client", AsyncMock(return_value=fake_client))

    class _PassThroughBreaker:
        async def call(self, fn):  # type: ignore[no-untyped-def]
            return await fn()

    monkeypatch.setattr(mod, "get_breaker", lambda _name: _PassThroughBreaker())
    monkeypatch.setenv("OLLAMA_URL", "http://test-host:11434")
    monkeypatch.setenv("INTERNAL_LLM_MAX_RETRIES", "1")
    monkeypatch.setattr(inference_health, "record_fallback", lambda *a, **k: None)
    monkeypatch.setattr(inference_health, "record_success", lambda *a, **k: None)

    captured: dict[str, Any] = {}

    async def _fake_call_llm(messages, **kwargs):  # noqa: ANN001, ARG001
        captured["model"] = kwargs.get("model")
        return "ok"

    monkeypatch.setattr("core.utils.llm_client.call_llm", _fake_call_llm)

    await mod._call_ollama(
        [{"role": "user", "content": "hi"}],
        temperature=0.1, max_tokens=10, provider="ollama",
        model="openrouter/anthropic/claude-sonnet-4.6",
    )
    assert captured["model"] == "openrouter/anthropic/claude-sonnet-4.6", (
        "fallback must use the stage-resolved OpenRouter model, not the JSON fallback"
    )


# --- CR-103: json-token guard on the DIRECT openrouter branch ---

@pytest.mark.asyncio
async def test_direct_openrouter_json_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_PROVIDER", "openrouter", raising=False)
    captured: dict[str, Any] = {}

    async def _fake_call_llm(messages, **kwargs):  # noqa: ANN001, ARG001
        captured["messages"] = messages
        return "{}"

    monkeypatch.setattr("core.utils.llm_client.call_llm", _fake_call_llm)

    await mod.call_internal_llm(
        [{"role": "user", "content": "give me structured data"}],  # no literal "json"
        response_format={"type": "json_object"},
    )
    assert any("json" in str(m.get("content", "")).lower() for m in captured["messages"]), (
        "the direct openrouter branch must inject the json token (else HTTP 400)"
    )


# --- CR-111: the streaming entry honors the override provider ---

@pytest.mark.asyncio
async def test_stream_honors_override_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_PROVIDER", "ollama", raising=False)
    called: dict[str, Any] = {}

    async def _fake_call_internal(messages, **kwargs):  # noqa: ANN001, ARG001
        called["hit"] = True
        return "cloud answer"

    monkeypatch.setattr(mod, "call_internal_llm", _fake_call_internal)

    chunks: list[str] = []
    with llm_call_override("openrouter", "openrouter/anthropic/claude-sonnet-4.6"):
        async for c in mod.call_internal_llm_stream(
            [{"role": "user", "content": "hi"}], stage="synthesis",
        ):
            chunks.append(c)

    assert called.get("hit") is True, "override provider=openrouter must route the stream to cloud"
    assert "cloud answer" in "".join(chunks)
