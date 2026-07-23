# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 Phase-3e-2b verifiability harness — ANTHROPIC BYOK ADAPTER probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 3.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-008).

3e-2a wired BYOK for the OpenAI-compatible providers (openai/xai) across both
transports. Anthropic is a direct BYOK provider too, but its completion wire
differs from OpenAI's — POST ``/v1/messages`` (not ``/chat/completions``), the
system prompt hoisted to a top-level ``system`` param, ``max_tokens`` REQUIRED,
``x-api-key`` + ``anthropic-version`` headers (not ``Authorization: Bearer``),
a ``content[].text`` response, and a distinct SSE event stream
(``content_block_delta`` vs OpenAI ``choices[].delta``).

3e-2b adds the Anthropic Messages-API adapter to the SAME env-authority seam:
``provider_state.byok_target`` now resolves anthropic (``wire="anthropic"``);
``llm_client._call_anthropic`` serves the non-streaming path; and
``chat._anthropic_stream_translate`` translates the Anthropic SSE stream back to
the OpenAI-shaped SSE the frontend consumes — so a BYOK-only Anthropic user's
chat streams token-by-token instead of 401ing against OpenRouter (CR-008).

RED-then-GREEN; synthetic (no live stack) so it runs in ci-local.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

# Fake keys hoisted to constants so the secret-scanner sees each literal once (a
# repeated literal in a keyword context trips detect-secrets per occurrence).
_K_ANTHROPIC = "sk-ant-live"  # pragma: allowlist secret
_K_OPENAI = "sk-openai-live"  # pragma: allowlist secret


def _clear_byok_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "BYOK_DIRECT_PROVIDERS",
        "OPENAI_API_KEY",
        "XAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# provider_state.byok_target — anthropic now resolves with wire="anthropic"
# ---------------------------------------------------------------------------


def test_byok_target_resolves_anthropic_with_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.routing import provider_state

    _clear_byok_env(monkeypatch)
    monkeypatch.setenv("BYOK_DIRECT_PROVIDERS", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", _K_ANTHROPIC)

    target = provider_state.byok_target("openrouter/anthropic/claude-sonnet-4.6")

    assert target is not None, "anthropic must resolve now that the adapter exists (CR-008)"
    assert target.provider == "anthropic"
    assert target.wire == "anthropic", "anthropic uses the Messages-API wire, not the OpenAI wire"
    assert target.base_url == "https://api.anthropic.com/v1"
    assert target.api_key == _K_ANTHROPIC  # pragma: allowlist secret
    assert target.model == "claude-sonnet-4.6"


def test_byok_target_openai_still_openai_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adding the anthropic wire must not perturb the openai/xai resolution."""
    from core.routing import provider_state

    _clear_byok_env(monkeypatch)
    monkeypatch.setenv("BYOK_DIRECT_PROVIDERS", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", _K_OPENAI)

    target = provider_state.byok_target("openrouter/openai/gpt-4o-mini")
    assert target is not None
    assert target.wire == "openai"


# ---------------------------------------------------------------------------
# llm_client._call_anthropic — non-streaming Messages-API adapter
# ---------------------------------------------------------------------------


class _RecordingResponse:
    status_code = 200

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._body


class _RecordingClient:
    def __init__(self, body: dict[str, Any]) -> None:
        self.posts: list[dict[str, Any]] = []
        self._body = body

    async def post(self, url: str, **kwargs: Any) -> _RecordingResponse:
        self.posts.append({"url": url, **kwargs})
        return _RecordingResponse(self._body)

    @property
    def is_closed(self) -> bool:
        return False

    async def aclose(self) -> None:
        return None


class _RecordingCtx:
    def __init__(self, client: _RecordingClient) -> None:
        self._client = client

    async def __aenter__(self) -> _RecordingClient:
        return self._client

    async def __aexit__(self, *_: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_call_anthropic_translates_openai_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """The adapter hoists the system message, requires max_tokens, sends the
    Anthropic auth headers to /messages, and unwraps content[].text."""
    from core.utils import llm_client

    rec = _RecordingClient({"content": [{"type": "text", "text": "hi from claude"}]})
    captured: dict[str, str] = {}

    def _fake_direct(base_url: str) -> _RecordingCtx:
        captured["base_url"] = base_url
        return _RecordingCtx(rec)

    monkeypatch.setattr(llm_client, "_acquire_direct_client", _fake_direct, raising=True)

    out = await llm_client._call_anthropic(
        [
            {"role": "system", "content": "You are terse."},
            {"role": "user", "content": "hello"},
        ],
        model="claude-sonnet-4.6",
        base_url="https://api.anthropic.com/v1",
        api_key=_K_ANTHROPIC,
        breaker_name="byok-anthropic",
        max_tokens=321,
    )

    assert out == "hi from claude"
    assert captured["base_url"] == "https://api.anthropic.com/v1"
    post = rec.posts[0]
    assert post["url"] == "/messages"
    assert post["headers"]["x-api-key"] == _K_ANTHROPIC
    assert post["headers"]["anthropic-version"]  # a version pin is present
    assert "Authorization" not in post["headers"], "Anthropic uses x-api-key, not bearer"
    body = post["json"]
    assert body["system"] == "You are terse.", "system message must be hoisted out of messages"
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["max_tokens"] == 321, "Anthropic requires max_tokens"
    assert body["model"] == "claude-sonnet-4.6"


@pytest.mark.asyncio
async def test_call_llm_routes_anthropic_model_to_anthropic_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """call_llm dispatches an anthropic BYOK model through _call_anthropic — a
    BYOK-only Anthropic user with no OpenRouter credit still completes."""
    from core.utils import llm_client

    _clear_byok_env(monkeypatch)
    monkeypatch.setenv("BYOK_DIRECT_PROVIDERS", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", _K_ANTHROPIC)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    seen: dict[str, Any] = {}

    async def _fake_anthropic(messages, **kwargs):  # noqa: ANN001, ANN003
        seen["model"] = kwargs.get("model")
        seen["base_url"] = kwargs.get("base_url")
        seen["api_key"] = kwargs.get("api_key")
        seen["breaker"] = kwargs.get("breaker_name")
        return "claude says hi"

    monkeypatch.setattr(llm_client, "_call_anthropic", _fake_anthropic, raising=True)

    out = await llm_client.call_llm(
        [{"role": "user", "content": "hello"}],
        model="openrouter/anthropic/claude-sonnet-4.6",
    )

    assert out == "claude says hi"
    assert seen["model"] == "claude-sonnet-4.6"
    assert seen["base_url"] == "https://api.anthropic.com/v1"
    assert seen["api_key"] == _K_ANTHROPIC
    assert seen["breaker"] == "byok-anthropic"


# ---------------------------------------------------------------------------
# chat — dispatch resolution + Anthropic SSE → OpenAI SSE translation
# ---------------------------------------------------------------------------


def test_chat_dispatch_reports_anthropic_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import chat

    _clear_byok_env(monkeypatch)
    monkeypatch.setenv("BYOK_DIRECT_PROVIDERS", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", _K_ANTHROPIC)

    base_url, key, model, wire = chat._resolve_chat_dispatch(
        "anthropic/claude-sonnet-4.6", "or-user-key"
    )
    assert base_url == "https://api.anthropic.com/v1"
    assert key == _K_ANTHROPIC
    assert model == "claude-sonnet-4.6"
    assert wire == "anthropic"


def test_chat_dispatch_openrouter_reports_openai_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import chat

    _clear_byok_env(monkeypatch)
    base_url, key, model, wire = chat._resolve_chat_dispatch(
        "openai/gpt-4o-mini", "or-user-key"
    )
    assert base_url == chat.OPENROUTER_BASE
    assert wire == "openai"


class _FakeAnthropicUpstream:
    """Yields a canned Anthropic SSE line stream via aiter_lines()."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.closed = False

    async def aiter_lines(self):  # noqa: ANN201
        for ln in self._lines:
            yield ln

    async def aclose(self) -> None:
        self.closed = True


class _FakeRequest:
    async def is_disconnected(self) -> bool:
        return False


_ANTHROPIC_SSE = [
    'event: message_start',
    'data: {"type":"message_start","message":{"id":"msg_1","usage":{"input_tokens":9,"output_tokens":0}}}',
    '',
    'event: content_block_delta',
    'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}',
    '',
    'event: ping',
    'data: {"type":"ping"}',
    '',
    'event: content_block_delta',
    'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" world"}}',
    '',
    'event: message_stop',
    'data: {"type":"message_stop"}',
    '',
]


@pytest.mark.asyncio
async def test_anthropic_stream_translates_to_openai_sse(monkeypatch: pytest.MonkeyPatch) -> None:
    """The chat translator converts Anthropic content_block_delta events into the
    OpenAI-shaped SSE the frontend consumes, and terminates with [DONE]."""
    from app.routers import chat

    # Latency recording is best-effort but touches redis — stub it out.
    monkeypatch.setattr(chat, "_record_chat_model_latency", lambda *a, **k: None, raising=True)

    upstream = _FakeAnthropicUpstream(list(_ANTHROPIC_SSE))
    out = b""
    async for chunk in chat._anthropic_stream_translate(
        _FakeRequest(), upstream, "claude-sonnet-4.6", start_monotonic=0.0
    ):
        out += chunk

    text = out.decode()
    # Reassemble the streamed content deltas.
    content = ""
    saw_done = False
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("data: "):
            continue
        payload = line[len("data: "):]
        if payload == "[DONE]":
            saw_done = True
            continue
        obj = json.loads(payload)
        content += obj["choices"][0]["delta"].get("content", "")

    assert content == "Hello world"
    assert saw_done, "translator must emit a terminating [DONE] sentinel"
    assert upstream.closed, "translator must close the upstream in its finally block"
