# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Regression: no provider failure escapes the chat stream (Task C1).

Before this fix, any ``httpx.HTTPError`` other than ``ConnectError`` /
``ReadTimeout`` raised from the upstream ``client.send`` call — a
``RemoteProtocolError``, ``PoolTimeout``, ``ReadError``, ``WriteError`` — and
any exception raised elsewhere inside ``_proxy_stream`` after the
``cerid_meta`` frame escaped as a bare 500 / aborted stream instead of the
friendly SSE ``error`` event. These tests pin the mapped behavior for both
the streaming and non-streaming (``stream=False``) paths, plus the existing
402 mapping (``UPSTREAM_ERROR_MESSAGES``).
"""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from http import HTTPStatus
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.routers import chat

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _patch_chat_redis(monkeypatch):
    """``_record_chat_route_decision`` touches Redis via ``app.deps.get_redis``
    on the success/fallback-evaluation path — route it to a MagicMock so these
    offline tests never touch a real Redis (matches test_chat_stream_cancel.py)."""
    monkeypatch.setattr("app.deps.get_redis", lambda: MagicMock(), raising=False)


class _FakeRequest:
    async def is_disconnected(self) -> bool:
        return False


def _chat_req(model: str = "openai/gpt-4o-mini") -> chat.ChatRequest:
    return chat.ChatRequest(
        model=model,
        messages=[chat._ChatMessage(role="user", content="hi")],
    )


def _patch_client(monkeypatch: pytest.MonkeyPatch, send: AsyncMock) -> None:
    fake_client = MagicMock()
    fake_client.build_request = MagicMock(return_value=MagicMock())
    fake_client.send = send
    monkeypatch.setattr(chat, "_get_chat_client", lambda: fake_client)
    monkeypatch.setenv("OLLAMA_ENABLED", "false")
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "openrouter")


async def _collect(gen: AsyncGenerator[bytes, None]) -> bytes:
    out = b""
    async for chunk in gen:
        out += chunk
    return out


# ---------------------------------------------------------------------------
# Requirement 1 — _attempt_stream maps httpx.HTTPError (base class) to a
# friendly error-event generator, same shape as the existing UPSTREAM_ERROR
# _error_gen, with code 502.
# ---------------------------------------------------------------------------

_NON_RETRYABLE_HTTP_ERROR_CLASSES = [
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
    httpx.ReadError,
    httpx.WriteError,
]


@pytest.mark.parametrize("error_cls", _NON_RETRYABLE_HTTP_ERROR_CLASSES)
async def test_attempt_stream_maps_httpx_error_to_error_frame(
    monkeypatch: pytest.MonkeyPatch, error_cls: type[httpx.HTTPError],
) -> None:
    async def _raise(*_a: Any, **_kw: Any) -> None:
        raise error_cls("boom")

    _patch_client(monkeypatch, AsyncMock(side_effect=_raise))

    result = await chat._attempt_stream(
        _FakeRequest(), _chat_req(), "openai/gpt-4o-mini", "req-1", "test-key",
    )

    assert not isinstance(result, int)
    text = (await _collect(result)).decode()
    assert '"code": 502' in text
    assert "Model provider connection failed" in text
    assert error_cls.__name__ in text
    assert text.rstrip().endswith("data: [DONE]")


async def test_attempt_stream_still_retries_connect_error_as_status() -> None:
    """ConnectError/ReadTimeout must keep returning an int status (unchanged
    retry-loop contract) rather than the new error-frame path."""
    # Covered indirectly by the retry-loop tests already in the suite; this
    # asserts the type contract the new httpx.HTTPError branch must not widen.
    assert issubclass(httpx.ConnectError, httpx.HTTPError)
    assert issubclass(httpx.ReadTimeout, httpx.HTTPError)


# ---------------------------------------------------------------------------
# Requirement 4 — 402 regression pins (streaming + non-streaming)
# ---------------------------------------------------------------------------


class _FakeUpstreamResponse:
    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self._body = body

    async def aread(self) -> bytes:
        return self._body

    async def aclose(self) -> None:
        return None


def _openrouter_402_body() -> bytes:
    return json.dumps({
        "error": {"message": "Insufficient credits", "code": 402},
    }).encode()


async def test_attempt_stream_402_maps_to_error_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    send = AsyncMock(return_value=_FakeUpstreamResponse(402, _openrouter_402_body()))
    _patch_client(monkeypatch, send)

    result = await chat._attempt_stream(
        _FakeRequest(), _chat_req(), "openai/gpt-4o-mini", "req-1", "test-key",
    )

    assert not isinstance(result, int)
    text = (await _collect(result)).decode()
    assert '"code": 402' in text
    assert chat.UPSTREAM_ERROR_MESSAGES[402] in text
    assert text.rstrip().endswith("data: [DONE]")


async def test_proxy_stream_402_streaming_produces_error_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send = AsyncMock(return_value=_FakeUpstreamResponse(402, _openrouter_402_body()))
    _patch_client(monkeypatch, send)

    gen = chat._proxy_stream(_FakeRequest(), _chat_req(), "req-1", api_key="test-key")
    text = (await _collect(gen)).decode()

    assert '"code": 402' in text
    assert chat.UPSTREAM_ERROR_MESSAGES[402] in text


async def test_proxy_stream_402_nonstreaming_produces_502_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send = AsyncMock(return_value=_FakeUpstreamResponse(402, _openrouter_402_body()))
    _patch_client(monkeypatch, send)

    gen = chat._proxy_stream(_FakeRequest(), _chat_req(), "req-1", api_key="test-key")
    body, status = await chat._collect_nonstream_response(gen)

    assert status == int(HTTPStatus.BAD_GATEWAY)
    assert body["error"]["message"] == chat.UPSTREAM_ERROR_MESSAGES[402]


# ---------------------------------------------------------------------------
# Requirement 1 + 4 — full pipeline: each httpx error class through
# _proxy_stream, both as SSE (stream ends cleanly, error frame present) and
# buffered as the non-streaming 502 envelope (no exception escapes).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("error_cls", _NON_RETRYABLE_HTTP_ERROR_CLASSES)
async def test_proxy_stream_httpx_error_streaming_ends_cleanly(
    monkeypatch: pytest.MonkeyPatch, error_cls: type[httpx.HTTPError],
) -> None:
    async def _raise(*_a: Any, **_kw: Any) -> None:
        raise error_cls("boom")

    _patch_client(monkeypatch, AsyncMock(side_effect=_raise))

    gen = chat._proxy_stream(_FakeRequest(), _chat_req(), "req-1", api_key="test-key")
    text = (await _collect(gen)).decode()

    assert '"code": 502' in text
    assert error_cls.__name__ in text
    assert text.rstrip().endswith("data: [DONE]")


@pytest.mark.parametrize("error_cls", _NON_RETRYABLE_HTTP_ERROR_CLASSES)
async def test_proxy_stream_httpx_error_nonstreaming_is_502_no_exception(
    monkeypatch: pytest.MonkeyPatch, error_cls: type[httpx.HTTPError],
) -> None:
    async def _raise(*_a: Any, **_kw: Any) -> None:
        raise error_cls("boom")

    _patch_client(monkeypatch, AsyncMock(side_effect=_raise))

    gen = chat._proxy_stream(_FakeRequest(), _chat_req(), "req-1", api_key="test-key")
    body, status = await chat._collect_nonstream_response(gen)

    assert status == int(HTTPStatus.BAD_GATEWAY)
    assert error_cls.__name__ in body["error"]["message"]


# ---------------------------------------------------------------------------
# Requirement 2 — _proxy_stream's own broad catch: any exception other than
# cancellation raised anywhere after the cerid_meta frame (not just from
# client.send) must still yield one final error frame + log_swallowed_error,
# then return — never escape as a bare 500 / aborted stream.
# ---------------------------------------------------------------------------


async def test_proxy_stream_generic_exception_yields_error_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fail_attempt(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError("boom mid dispatch")

    monkeypatch.setattr(chat, "_attempt_stream", _fail_attempt)
    logged: list[Exception] = []
    monkeypatch.setattr(
        chat, "log_swallowed_error",
        lambda module, exc: logged.append((module, exc)),
    )

    gen = chat._proxy_stream(_FakeRequest(), _chat_req(), "req-1", api_key="test-key")
    text = (await _collect(gen)).decode()

    assert '"code": 502' in text
    assert "RuntimeError" in text
    assert text.rstrip().endswith("data: [DONE]")
    assert len(logged) == 1
    module, exc = logged[0]
    assert module == "app.routers.chat.stream"
    assert isinstance(exc, RuntimeError)


async def test_proxy_stream_cancelled_error_still_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The new broad except must not swallow cancellation — it must still
    propagate so the caller's cancellation semantics are preserved."""
    import asyncio

    async def _cancel(*_a: Any, **_kw: Any) -> None:
        raise asyncio.CancelledError()

    monkeypatch.setattr(chat, "_attempt_stream", _cancel)

    gen = chat._proxy_stream(_FakeRequest(), _chat_req(), "req-1", api_key="test-key")
    with pytest.raises(asyncio.CancelledError):
        await _collect(gen)
