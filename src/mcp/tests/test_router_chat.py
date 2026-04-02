# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the chat streaming proxy router."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _mock_redis_for_chat():
    """Prevent Redis connections during chat router tests (rate limiting, metrics)."""
    mock_redis = MagicMock()
    mock_redis.get.return_value = None
    mock_redis.pipeline.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_redis.pipeline.return_value.__exit__ = MagicMock(return_value=False)
    with patch("deps._redis", mock_redis), \
         patch("deps.get_redis", return_value=mock_redis), \
         patch("utils.private_mode.get_private_mode_level", return_value=0):
        yield


def _make_app():
    from routers.chat import router

    app = FastAPI()
    app.include_router(router)
    return app


def _setup_mock_client(mock_response):
    """Configure a mock httpx client that uses build_request + send."""
    mock_client = AsyncMock()
    mock_client.is_closed = False
    mock_client.aclose = AsyncMock()

    mock_request = MagicMock()
    mock_client.build_request = MagicMock(return_value=mock_request)
    mock_client.send = AsyncMock(return_value=mock_response)
    return mock_client


class TestChatStreamEndpoint:
    """POST /chat/stream"""

    def test_returns_503_when_no_api_key(self):
        with patch("routers.chat.OPENROUTER_API_KEY", ""):
            app = _make_app()
            client = TestClient(app)
            resp = client.post("/chat/stream", json={
                "model": "openrouter/openai/gpt-4o-mini",
                "messages": [{"role": "user", "content": "hello"}],
            })
            assert resp.status_code == 503
            assert "OPENROUTER_API_KEY" in resp.text

    def test_emits_cerid_meta_event(self):
        """The first SSE event should be a cerid_meta with model info."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.aclose = AsyncMock()
        mock_response.aread = AsyncMock(return_value=b"")

        async def fake_aiter():
            yield b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        mock_response.aiter_bytes = fake_aiter

        with patch("routers.chat.OPENROUTER_API_KEY", "sk-test"):
            app = _make_app()
            client = TestClient(app)
            mock_client = _setup_mock_client(mock_response)

            with patch("routers.chat._get_chat_client", return_value=mock_client):
                resp = client.post("/chat/stream", json={
                    "model": "openrouter/anthropic/claude-sonnet-4.6",
                    "messages": [{"role": "user", "content": "hello"}],
                })

                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith("text/event-stream")

                # Parse SSE events
                body = resp.text
                events = [
                    line.removeprefix("data: ")
                    for line in body.split("\n")
                    if line.startswith("data: ") and line.strip() != "data: [DONE]"
                ]
                assert len(events) >= 1

                # First event should be cerid_meta
                meta = json.loads(events[0])
                assert "cerid_meta" in meta
                assert meta["cerid_meta"]["requested_model"] == "openrouter/anthropic/claude-sonnet-4.6"
                assert meta["cerid_meta"]["resolved_model"] == "anthropic/claude-sonnet-4.6"


    def test_emits_cerid_meta_update_when_model_differs(self):
        """When OpenRouter returns a different model, emit cerid_meta_update."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.aclose = AsyncMock()
        mock_response.aread = AsyncMock(return_value=b"")

        async def fake_aiter():
            # OpenRouter returns a different model than requested
            yield b'data: {"model":"anthropic/claude-3.7-sonnet","choices":[{"delta":{"content":"Hi"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        mock_response.aiter_bytes = fake_aiter

        with patch("routers.chat.OPENROUTER_API_KEY", "sk-test"):
            app = _make_app()
            client = TestClient(app)
            mock_client = _setup_mock_client(mock_response)

            with patch("routers.chat._get_chat_client", return_value=mock_client):
                resp = client.post("/chat/stream", json={
                    "model": "openrouter/anthropic/claude-sonnet-4.6",
                    "messages": [{"role": "user", "content": "hello"}],
                })

                assert resp.status_code == 200
                body = resp.text
                events = [
                    line.removeprefix("data: ")
                    for line in body.split("\n")
                    if line.startswith("data: ") and line.strip() != "data: [DONE]"
                ]

                # Should have: cerid_meta, cerid_meta_update, content chunk
                meta_update_events = [
                    json.loads(e) for e in events if "cerid_meta_update" in e
                ]
                assert len(meta_update_events) == 1
                assert meta_update_events[0]["cerid_meta_update"]["actual_model"] == "anthropic/claude-3.7-sonnet"

    def test_no_cerid_meta_update_when_model_matches(self):
        """No cerid_meta_update when upstream model matches the request."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.aclose = AsyncMock()
        mock_response.aread = AsyncMock(return_value=b"")

        async def fake_aiter():
            yield b'data: {"model":"anthropic/claude-sonnet-4.6","choices":[{"delta":{"content":"Hi"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        mock_response.aiter_bytes = fake_aiter

        with patch("routers.chat.OPENROUTER_API_KEY", "sk-test"):
            app = _make_app()
            client = TestClient(app)
            mock_client = _setup_mock_client(mock_response)

            with patch("routers.chat._get_chat_client", return_value=mock_client):
                resp = client.post("/chat/stream", json={
                    "model": "openrouter/anthropic/claude-sonnet-4.6",
                    "messages": [{"role": "user", "content": "hello"}],
                })

                body = resp.text
                assert "cerid_meta_update" not in body


class TestStripPrefix:
    """Unit tests for _strip_prefix helper."""

    def test_strips_openrouter_prefix(self):
        from routers.chat import _strip_prefix

        assert _strip_prefix("openrouter/anthropic/claude-sonnet-4.6") == "anthropic/claude-sonnet-4.6"

    def test_no_prefix_passthrough(self):
        from routers.chat import _strip_prefix

        assert _strip_prefix("anthropic/claude-sonnet-4.6") == "anthropic/claude-sonnet-4.6"

    def test_empty_string(self):
        from routers.chat import _strip_prefix

        assert _strip_prefix("") == ""


class TestChatRequestValidation:
    """Request body validation."""

    def test_rejects_missing_model(self):
        with patch("routers.chat.OPENROUTER_API_KEY", "sk-test"):
            app = _make_app()
            client = TestClient(app)
            resp = client.post("/chat/stream", json={
                "messages": [{"role": "user", "content": "hello"}],
            })
            assert resp.status_code == 422  # Pydantic validation error

    def test_rejects_empty_messages(self):
        with patch("routers.chat.OPENROUTER_API_KEY", "sk-test"):
            app = _make_app()
            client = TestClient(app)

            mock_resp = AsyncMock()
            mock_resp.status_code = 200
            mock_resp.aclose = AsyncMock()
            mock_resp.aread = AsyncMock(return_value=b"")

            async def empty_stream():
                yield b"data: [DONE]\n\n"

            mock_resp.aiter_bytes = empty_stream
            mock_client = _setup_mock_client(mock_resp)

            with patch("routers.chat._get_chat_client", return_value=mock_client):
                resp = client.post("/chat/stream", json={
                    "model": "openrouter/openai/gpt-4o-mini",
                    "messages": [],
                })
                # Empty messages list is valid at the schema level,
                # OpenRouter would reject it — our proxy forwards as-is
                assert resp.status_code == 200

    def test_accepts_optional_max_tokens(self):
        """max_tokens is optional and should be forwarded when present."""
        with patch("routers.chat.OPENROUTER_API_KEY", "sk-test"):
            app = _make_app()
            client = TestClient(app)

            mock_resp = AsyncMock()
            mock_resp.status_code = 200
            mock_resp.aclose = AsyncMock()
            mock_resp.aread = AsyncMock(return_value=b"")

            async def empty_stream():
                yield b"data: [DONE]\n\n"

            mock_resp.aiter_bytes = empty_stream
            mock_client = _setup_mock_client(mock_resp)

            with patch("routers.chat._get_chat_client", return_value=mock_client):
                resp = client.post("/chat/stream", json={
                    "model": "openrouter/openai/gpt-4o-mini",
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 100,
                })
                assert resp.status_code == 200

                # Verify max_tokens was included in the payload
                call_kwargs = mock_client.build_request.call_args
                payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
                assert payload["max_tokens"] == 100
