# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 Phase-4 gate — ChatRequest.stream=false is honored (CR-064).

``POST /chat/stream`` previously accepted ``stream`` on ``ChatRequest`` and
ignored it — every request got ``text/event-stream``, so a ``stream:false``
client (a direct API/SDK caller) received SSE it never asked for. The endpoint
now buffers the proxy stream into a single OpenAI-shaped ``chat.completion`` when
``stream`` is false. These tests cover the aggregator (including SSE frames
split across byte boundaries) and the endpoint branch; offline, no live stack.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from http import HTTPStatus

from app.routers import chat


async def _gen_from(chunks: list[bytes]) -> AsyncGenerator[bytes, None]:
    for c in chunks:
        yield c


async def test_collect_aggregates_delta_content():
    body, status = await chat._collect_nonstream_response(_gen_from([
        b'data: {"cerid_meta": {"resolved_model": "gpt-4o-mini"}}\n\n',
        b'data: {"choices":[{"index":0,"delta":{"content":"Hello"}}]}\n\n',
        b'data: {"choices":[{"index":0,"delta":{"content":", world"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]))
    assert status == int(HTTPStatus.OK)
    assert body["object"] == "chat.completion"
    assert body["model"] == "gpt-4o-mini"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "Hello, world"}
    assert body["choices"][0]["finish_reason"] == "stop"


async def test_collect_reassembles_frame_split_across_chunks():
    # A single SSE frame arrives split across two byte-chunks (upstream
    # aiter_bytes boundary). The aggregator must reassemble on \n\n, not parse
    # each yielded chunk in isolation.
    body, status = await chat._collect_nonstream_response(_gen_from([
        b'data: {"choices":[{"index":0,"delta":{"content":"Hel',
        b'lo"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]))
    assert status == int(HTTPStatus.OK)
    assert body["choices"][0]["message"]["content"] == "Hello"


async def test_collect_surfaces_upstream_error_as_502():
    body, status = await chat._collect_nonstream_response(_gen_from([
        b'data: {"error":{"message":"all models failed","type":"upstream_error"}}\n\n'
        b"data: [DONE]\n\n",
    ]))
    assert status == int(HTTPStatus.BAD_GATEWAY)
    assert body == {"error": {"message": "all models failed", "type": "upstream_error"}}


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(chat.router)
    return TestClient(app)


def test_stream_false_returns_json_completion(monkeypatch):
    async def _fake_key(request): return "test-key"
    monkeypatch.setattr(chat, "_resolve_api_key", _fake_key)

    def _fake_proxy(request, req, request_id, api_key=""):
        async def _gen() -> AsyncGenerator[bytes, None]:
            yield b'data: {"cerid_meta": {"resolved_model": "gpt-4o-mini"}}\n\n'
            yield b'data: {"choices":[{"index":0,"delta":{"content":"Hi there"}}]}\n\n'
            yield b"data: [DONE]\n\n"
        return _gen()

    monkeypatch.setattr(chat, "_proxy_stream", _fake_proxy)

    resp = _client().post("/chat/stream", json={
        "model": "openrouter/openai/gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
    })
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    data = resp.json()
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == "Hi there"


def test_stream_true_still_returns_sse(monkeypatch):
    async def _fake_key(request): return "test-key"
    monkeypatch.setattr(chat, "_resolve_api_key", _fake_key)

    def _fake_proxy(request, req, request_id, api_key=""):
        async def _gen() -> AsyncGenerator[bytes, None]:
            yield b'data: {"choices":[{"index":0,"delta":{"content":"x"}}]}\n\n'
            yield b"data: [DONE]\n\n"
        return _gen()

    monkeypatch.setattr(chat, "_proxy_stream", _fake_proxy)

    resp = _client().post("/chat/stream", json={
        "model": "openrouter/openai/gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    })
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")


def test_stream_false_without_api_key_is_json_503(monkeypatch):
    async def _no_key(request): return ""
    monkeypatch.setattr(chat, "_resolve_api_key", _no_key)
    # Local provider path accepts a sentinel key and rewrites cloud model
    # ids — pin cloud-only so this case still asserts the JSON 503 config
    # error when OpenRouter is missing (not a 502 from a local connect fail).
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OLLAMA_ENABLED", "false")
    chat._chat_client = None

    resp = _client().post("/chat/stream", json={
        "model": "openrouter/openai/gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
    })
    assert resp.status_code == 503
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["error"]["type"] == "config_error"
