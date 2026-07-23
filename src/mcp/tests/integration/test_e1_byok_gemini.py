# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 Phase-3e-2c verifiability harness — GOOGLE GEMINI BYOK ADAPTER probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 3.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-008).

3e-2a/2b wired BYOK for openai/xai (OpenAI wire) and anthropic (Messages API).
Google Gemini is the last direct BYOK provider, and its ``generateContent`` wire
differs again: ``POST /v1beta/models/{model}:generateContent`` with an
``x-goog-api-key`` header, a ``contents[].parts[].text`` body whose roles are
``user`` / ``model`` (OpenAI ``assistant`` → ``model``), the system prompt hoisted
to ``systemInstruction``, generation params under ``generationConfig``
(``maxOutputTokens`` / ``temperature`` / ``responseMimeType`` for JSON), and a
``candidates[].content.parts[].text`` response. Streaming uses
``:streamGenerateContent?alt=sse``.

3e-2c completes the BYOK provider set: ``provider_state.byok_target`` resolves
google (``wire="gemini"``); ``llm_client._call_gemini`` serves the non-streaming
path; and ``chat._gemini_stream_translate`` translates the Gemini SSE stream back
to the OpenAI-shaped SSE the frontend consumes.

RED-then-GREEN; synthetic (no live stack) so it runs in ci-local.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

# Fake keys hoisted to constants so the secret-scanner sees each literal once (a
# repeated literal in a keyword context trips detect-secrets per occurrence).
_K_GOOGLE = "gk-live"  # pragma: allowlist secret


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
# provider_state.byok_target — google now resolves with wire="gemini"
# ---------------------------------------------------------------------------


def test_byok_target_resolves_google_with_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.routing import provider_state

    _clear_byok_env(monkeypatch)
    monkeypatch.setenv("BYOK_DIRECT_PROVIDERS", "google")
    monkeypatch.setenv("GOOGLE_API_KEY", _K_GOOGLE)

    target = provider_state.byok_target("openrouter/google/gemini-2.5-flash")

    assert target is not None, "google must resolve now that the adapter exists (CR-008)"
    assert target.provider == "google"
    assert target.wire == "gemini"
    assert target.base_url == "https://generativelanguage.googleapis.com/v1beta"
    assert target.api_key == _K_GOOGLE  # pragma: allowlist secret
    assert target.model == "gemini-2.5-flash"


# ---------------------------------------------------------------------------
# llm_client._call_gemini — non-streaming generateContent adapter
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
async def test_call_gemini_translates_openai_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """The adapter hoists the system message to systemInstruction, maps the
    assistant role to 'model', sets generationConfig, sends x-goog-api-key to the
    generateContent endpoint, and unwraps candidates[].content.parts[].text."""
    from core.utils import llm_client

    rec = _RecordingClient(
        {"candidates": [{"content": {"parts": [{"text": "hi from gemini"}], "role": "model"}}]}
    )
    captured: dict[str, str] = {}

    def _fake_direct(base_url: str) -> _RecordingCtx:
        captured["base_url"] = base_url
        return _RecordingCtx(rec)

    monkeypatch.setattr(llm_client, "_acquire_direct_client", _fake_direct, raising=True)

    out = await llm_client._call_gemini(
        [
            {"role": "system", "content": "You are terse."},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "again"},
        ],
        model="gemini-2.5-flash",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_key=_K_GOOGLE,
        breaker_name="byok-google",
        max_tokens=321,
    )

    assert out == "hi from gemini"
    assert captured["base_url"] == "https://generativelanguage.googleapis.com/v1beta"
    post = rec.posts[0]
    assert post["url"] == "/models/gemini-2.5-flash:generateContent"
    assert post["headers"]["x-goog-api-key"] == _K_GOOGLE
    assert "Authorization" not in post["headers"], "Gemini uses x-goog-api-key, not bearer"
    body = post["json"]
    assert body["systemInstruction"] == {"parts": [{"text": "You are terse."}]}
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "hello"}]},
        {"role": "model", "parts": [{"text": "hi"}]},  # assistant → model
        {"role": "user", "parts": [{"text": "again"}]},
    ]
    assert body["generationConfig"]["maxOutputTokens"] == 321


@pytest.mark.asyncio
async def test_call_gemini_json_mode_sets_response_mime_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A json_object response_format maps to Gemini's responseMimeType."""
    from core.utils import llm_client

    rec = _RecordingClient(
        {"candidates": [{"content": {"parts": [{"text": "{}"}], "role": "model"}}]}
    )
    monkeypatch.setattr(
        llm_client, "_acquire_direct_client", lambda base_url: _RecordingCtx(rec), raising=True
    )

    await llm_client._call_gemini(
        [{"role": "user", "content": "give json"}],
        model="gemini-2.5-flash",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_key=_K_GOOGLE,
        breaker_name="byok-google",
        response_format={"type": "json_object"},
    )

    assert rec.posts[0]["json"]["generationConfig"]["responseMimeType"] == "application/json"


@pytest.mark.asyncio
async def test_call_llm_routes_google_model_to_gemini_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """call_llm dispatches a google BYOK model through _call_gemini."""
    from core.utils import llm_client

    _clear_byok_env(monkeypatch)
    monkeypatch.setenv("BYOK_DIRECT_PROVIDERS", "google")
    monkeypatch.setenv("GOOGLE_API_KEY", _K_GOOGLE)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    seen: dict[str, Any] = {}

    async def _fake_gemini(messages, **kwargs):  # noqa: ANN001, ANN003
        seen["model"] = kwargs.get("model")
        seen["base_url"] = kwargs.get("base_url")
        seen["api_key"] = kwargs.get("api_key")
        seen["breaker"] = kwargs.get("breaker_name")
        return "gemini says hi"

    monkeypatch.setattr(llm_client, "_call_gemini", _fake_gemini, raising=True)

    out = await llm_client.call_llm(
        [{"role": "user", "content": "hello"}],
        model="openrouter/google/gemini-2.5-flash",
    )

    assert out == "gemini says hi"
    assert seen["model"] == "gemini-2.5-flash"
    assert seen["base_url"] == "https://generativelanguage.googleapis.com/v1beta"
    assert seen["api_key"] == _K_GOOGLE
    assert seen["breaker"] == "byok-google"


# ---------------------------------------------------------------------------
# chat — dispatch resolution + Gemini SSE → OpenAI SSE translation
# ---------------------------------------------------------------------------


def test_chat_dispatch_reports_gemini_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import chat

    _clear_byok_env(monkeypatch)
    monkeypatch.setenv("BYOK_DIRECT_PROVIDERS", "google")
    monkeypatch.setenv("GOOGLE_API_KEY", _K_GOOGLE)

    base_url, key, model, wire = chat._resolve_chat_dispatch(
        "google/gemini-2.5-flash", "or-user-key"
    )
    assert base_url == "https://generativelanguage.googleapis.com/v1beta"
    assert key == _K_GOOGLE
    assert model == "gemini-2.5-flash"
    assert wire == "gemini"


class _FakeGeminiUpstream:
    """Yields a canned Gemini streamGenerateContent SSE line stream."""

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


# Gemini classic streamGenerateContent?alt=sse: one `data:` line per chunk, each a
# partial GenerateContentResponse; the stream simply ends (no [DONE] sentinel).
_GEMINI_SSE = [
    'data: {"candidates":[{"content":{"parts":[{"text":"Hello"}],"role":"model"}}]}',
    '',
    'data: {"candidates":[{"content":{"parts":[{"text":" world"}],"role":"model"}}],"usageMetadata":{"promptTokenCount":9,"candidatesTokenCount":5}}',
    '',
]


@pytest.mark.asyncio
async def test_gemini_stream_translates_to_openai_sse(monkeypatch: pytest.MonkeyPatch) -> None:
    """The chat translator converts Gemini candidates[].content.parts[].text chunks
    into the OpenAI-shaped SSE the frontend consumes, and terminates with [DONE]
    (Gemini classic sends no sentinel of its own)."""
    from app.routers import chat

    monkeypatch.setattr(chat, "_record_chat_model_latency", lambda *a, **k: None, raising=True)

    upstream = _FakeGeminiUpstream(list(_GEMINI_SSE))
    out = b""
    async for chunk in chat._gemini_stream_translate(
        _FakeRequest(), upstream, "gemini-2.5-flash", start_monotonic=0.0
    ):
        out += chunk

    text = out.decode()
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
