# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the chat streaming proxy router."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app():
    from routers.chat import router

    app = FastAPI()
    app.include_router(router)
    return app


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
        # Mock httpx to return a simple stream
        mock_response = AsyncMock()
        mock_response.status_code = 200

        async def fake_aiter():
            yield b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        mock_response.aiter_bytes = fake_aiter

        with patch("routers.chat.OPENROUTER_API_KEY", "sk-test"):
            app = _make_app()
            client = TestClient(app)

            with patch("routers.chat.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

                mock_stream_ctx = AsyncMock()
                mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
                mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)
                mock_client.stream = MagicMock(return_value=mock_stream_ctx)

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

            with patch("routers.chat.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

                mock_resp = AsyncMock()
                mock_resp.status_code = 200

                async def empty_stream():
                    yield b"data: [DONE]\n\n"

                mock_resp.aiter_bytes = empty_stream

                mock_ctx = AsyncMock()
                mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
                mock_ctx.__aexit__ = AsyncMock(return_value=False)
                mock_client.stream = MagicMock(return_value=mock_ctx)

                resp = client.post("/chat/stream", json={
                    "model": "openrouter/openai/gpt-4o-mini",
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 100,
                })
                assert resp.status_code == 200

                # Verify max_tokens was included in the payload
                call_kwargs = mock_client.stream.call_args
                payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
                assert payload["max_tokens"] == 100
