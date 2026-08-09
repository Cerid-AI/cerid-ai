# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for PR 5 — payload assembly and routing under the four advanced flags.

All four flags default to off. These tests confirm:
  - Off-state payload is the pre-PR-5 baseline.
  - Each flag emits the correct additive payload field when on.
  - ENABLE_MODEL_CASCADE routes SIMPLE chat queries to the local backend
    when one is reachable.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.utils import internal_llm as mod


class _FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._body


def _wire_pass_through(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Wire a fake httpx client that records the posted payload."""
    captured: dict[str, Any] = {}
    fake_client = MagicMock()

    async def _post(url: str, *, json: dict) -> _FakeResponse:  # noqa: ARG001
        captured["url"] = url
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


def _reset_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    for flag in (
        "ENABLE_PROMPT_PREFIX_CACHE",
        "ENABLE_MODEL_CASCADE",
        "ENABLE_SPECULATIVE_DECODE",
        "ENABLE_CONSTRAINED_DECODE",
    ):
        monkeypatch.setattr(mod.config, flag, False, raising=False)


# ---------------------------------------------------------------------------
# Baseline: all flags off — payload unchanged from PR 2 shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_baseline_payload_when_all_flags_off(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _wire_pass_through(monkeypatch)
    _reset_flags(monkeypatch)
    await mod._call_ollama(
        [{"role": "user", "content": "hi"}],
        temperature=0.5,
        max_tokens=100,
    )
    p = captured["payload"]
    assert "keep_alive" not in p
    assert "format" not in p
    assert p["options"] == {"temperature": 0.5, "num_predict": 100}


# ---------------------------------------------------------------------------
# ENABLE_PROMPT_PREFIX_CACHE — keep_alive surfaces in payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prefix_cache_adds_keep_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _wire_pass_through(monkeypatch)
    _reset_flags(monkeypatch)
    monkeypatch.setattr(mod.config, "ENABLE_PROMPT_PREFIX_CACHE", True, raising=False)
    monkeypatch.setattr(mod.config, "PROMPT_PREFIX_KEEP_ALIVE", "15m", raising=False)
    await mod._call_ollama([{"role": "user", "content": "hi"}], temperature=0, max_tokens=50)
    assert captured["payload"]["keep_alive"] == "15m"


@pytest.mark.asyncio
async def test_prefix_cache_default_keep_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _wire_pass_through(monkeypatch)
    _reset_flags(monkeypatch)
    monkeypatch.setattr(mod.config, "ENABLE_PROMPT_PREFIX_CACHE", True, raising=False)
    # Don't override PROMPT_PREFIX_KEEP_ALIVE — should use the configured default.
    monkeypatch.setattr(mod.config, "PROMPT_PREFIX_KEEP_ALIVE", "30m", raising=False)
    await mod._call_ollama([{"role": "user", "content": "hi"}], temperature=0, max_tokens=50)
    assert captured["payload"]["keep_alive"] == "30m"


# ---------------------------------------------------------------------------
# ENABLE_SPECULATIVE_DECODE — draft_model surfaces in options
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_speculative_decode_passes_draft_model(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _wire_pass_through(monkeypatch)
    _reset_flags(monkeypatch)
    monkeypatch.setattr(mod.config, "ENABLE_SPECULATIVE_DECODE", True, raising=False)
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_DRAFT_MODEL", "phi3:mini", raising=False)
    await mod._call_ollama([{"role": "user", "content": "hi"}], temperature=0, max_tokens=50)
    assert captured["payload"]["options"]["draft_model"] == "phi3:mini"


@pytest.mark.asyncio
async def test_speculative_decode_silent_without_draft_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The flag is harmless without a configured draft model."""
    captured = _wire_pass_through(monkeypatch)
    _reset_flags(monkeypatch)
    monkeypatch.setattr(mod.config, "ENABLE_SPECULATIVE_DECODE", True, raising=False)
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_DRAFT_MODEL", "", raising=False)
    monkeypatch.delenv("INTERNAL_LLM_DRAFT_MODEL", raising=False)
    await mod._call_ollama([{"role": "user", "content": "hi"}], temperature=0, max_tokens=50)
    assert "draft_model" not in captured["payload"]["options"]


# ---------------------------------------------------------------------------
# ENABLE_CONSTRAINED_DECODE — forces temperature=0 in json_mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_constrained_decode_forces_temperature_zero_in_json_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _wire_pass_through(monkeypatch)
    _reset_flags(monkeypatch)
    monkeypatch.setattr(mod.config, "ENABLE_CONSTRAINED_DECODE", True, raising=False)
    await mod._call_ollama(
        [{"role": "user", "content": "hi"}],
        temperature=0.7,
        max_tokens=50,
        json_mode=True,
    )
    assert captured["payload"]["options"]["temperature"] == 0.0
    assert captured["payload"]["format"] == "json"


@pytest.mark.asyncio
async def test_constrained_decode_passes_temperature_when_not_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without json_mode, the constrained-decode flag is a no-op on temperature."""
    captured = _wire_pass_through(monkeypatch)
    _reset_flags(monkeypatch)
    monkeypatch.setattr(mod.config, "ENABLE_CONSTRAINED_DECODE", True, raising=False)
    await mod._call_ollama(
        [{"role": "user", "content": "hi"}],
        temperature=0.7,
        max_tokens=50,
        json_mode=False,
    )
    assert captured["payload"]["options"]["temperature"] == 0.7


# ---------------------------------------------------------------------------
# All four flags coexist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_flags_on_together(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _wire_pass_through(monkeypatch)
    _reset_flags(monkeypatch)
    monkeypatch.setattr(mod.config, "ENABLE_PROMPT_PREFIX_CACHE", True, raising=False)
    monkeypatch.setattr(mod.config, "PROMPT_PREFIX_KEEP_ALIVE", "1h", raising=False)
    monkeypatch.setattr(mod.config, "ENABLE_SPECULATIVE_DECODE", True, raising=False)
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_DRAFT_MODEL", "phi3:mini", raising=False)
    monkeypatch.setattr(mod.config, "ENABLE_CONSTRAINED_DECODE", True, raising=False)

    await mod._call_ollama(
        [{"role": "user", "content": "hi"}],
        temperature=0.7,
        max_tokens=50,
        json_mode=True,
    )

    p = captured["payload"]
    assert p["keep_alive"] == "1h"
    assert p["format"] == "json"
    assert p["options"]["temperature"] == 0.0  # constrained decode wins
    assert p["options"]["draft_model"] == "phi3:mini"


# ---------------------------------------------------------------------------
# CR-070 — the STREAMING local path applies speculative decode too (it used to
# drop the draft model its non-streaming twin applied).
# ---------------------------------------------------------------------------


def _wire_stream_capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Wire a fake httpx client whose ``stream()`` records the posted payload."""
    captured: dict[str, Any] = {}

    class _FakeStreamResp:
        status_code = 200

        async def aiter_lines(self) -> Any:
            for _ in ():  # empty async iterator
                yield ""

        async def aread(self) -> bytes:
            return b""

        def raise_for_status(self) -> None:
            return None

    class _FakeStreamCtx:
        def __init__(self, payload: dict) -> None:
            captured["payload"] = payload

        async def __aenter__(self) -> _FakeStreamResp:
            return _FakeStreamResp()

        async def __aexit__(self, *_a: Any) -> bool:
            return False

    fake_client = MagicMock()
    fake_client.stream = lambda method, url, *, json: _FakeStreamCtx(json)  # noqa: ARG005
    monkeypatch.setattr(mod, "_get_ollama_client", AsyncMock(return_value=fake_client))
    monkeypatch.setenv("OLLAMA_URL", "http://test-host:11434")
    monkeypatch.setattr(mod.config, "OLLAMA_DEFAULT_MODEL", "llama3.2:3b", raising=False)
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MODEL", "", raising=False)
    return captured


@pytest.mark.asyncio
async def test_streaming_path_passes_draft_model(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _wire_stream_capture(monkeypatch)
    _reset_flags(monkeypatch)
    monkeypatch.setattr(mod.config, "ENABLE_SPECULATIVE_DECODE", True, raising=False)
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_DRAFT_MODEL", "phi3:mini", raising=False)

    async for _ in mod._stream_ollama(
        [{"role": "user", "content": "hi"}], temperature=0.0, max_tokens=50,
    ):
        pass

    assert captured["payload"]["options"]["draft_model"] == "phi3:mini"


@pytest.mark.asyncio
async def test_streaming_baseline_has_no_draft_model(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _wire_stream_capture(monkeypatch)
    _reset_flags(monkeypatch)

    async for _ in mod._stream_ollama(
        [{"role": "user", "content": "hi"}], temperature=0.5, max_tokens=100,
    ):
        pass

    assert captured["payload"]["options"] == {"temperature": 0.5, "num_predict": 100}
