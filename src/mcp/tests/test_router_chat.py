# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the chat streaming proxy router."""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _patch_chat_redis(monkeypatch):
    """Route ``app.deps.get_redis`` to a MagicMock for every test in this
    module. Phase 0.4a added best-effort route-decision + per-model
    latency telemetry to the hot /chat/stream path (local ``from app.deps
    import get_redis`` inside the writer helpers); without this, the
    first unmocked call in the suite would pay app.deps._retry's
    exponential-backoff cost against an unreachable Redis host.
    """
    monkeypatch.setattr("app.deps.get_redis", lambda: MagicMock(), raising=False)
    # Drop the process-wide chat httpx pool. Earlier tests (or a prior case
    # in this module) may have filled ``_chat_client`` with a real
    # AsyncClient bound to a dead event loop; patching
    # ``httpx.AsyncClient`` only affects *new* constructions, so a stale
    # singleton silently bypasses the mock and hits the network.
    import app.routers.chat as chat_mod

    chat_mod._chat_client = None
    yield
    chat_mod._chat_client = None


def _make_app():
    from app.routers.chat import router

    app = FastAPI()
    app.include_router(router)
    return app


def _setup_mock_client(mock_client_cls, mock_response):
    """Configure a mock httpx.AsyncClient that uses build_request + send."""
    import app.routers.chat as chat_mod

    # Ensure the next ``_get_chat_client()`` call constructs via the patch.
    chat_mod._chat_client = None

    mock_client = AsyncMock()
    mock_client.aclose = AsyncMock()
    mock_client.is_closed = False
    mock_client_cls.return_value = mock_client

    mock_request = MagicMock()
    mock_client.build_request = MagicMock(return_value=mock_request)
    mock_client.send = AsyncMock(return_value=mock_response)
    return mock_client


class TestChatStreamEndpoint:
    """POST /chat/stream"""

    def test_api_key_is_read_at_request_time_not_module_import(self):
        """Regression: chat.py used to capture OPENROUTER_API_KEY at module-import
        time. The setup wizard's ``/setup/configure`` endpoint patches
        ``os.environ`` at runtime when a new key is saved — a module-level
        capture would freeze the stale boot-time value and return 401 forever
        even though ``/providers/credits`` (which reads ``os.getenv`` per call)
        happily reported the new key as valid. Confirm chat.py now reads
        fresh from os.environ on every request.
        """
        import app.routers.chat as chat_module

        # Sanity: the module must NOT expose a top-level OPENROUTER_API_KEY
        # constant that can diverge from os.environ. A bare module attribute
        # named OPENROUTER_API_KEY is the anti-pattern.
        assert not (
            hasattr(chat_module, "OPENROUTER_API_KEY")
            and isinstance(getattr(chat_module, "OPENROUTER_API_KEY"), str)
        ), (
            "chat.py must not expose a module-level OPENROUTER_API_KEY constant — "
            "this freezes at module-import time and diverges from os.environ "
            "when the setup wizard patches env at runtime. Read via os.getenv "
            "or the _env_openrouter_key() helper instead."
        )

        # Behavioral check: resolve the key TWICE with different env values.
        # Each call must return the current value, proving no stale capture.
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "placeholder-alpha"}, clear=False):  # pragma: allowlist secret
            assert chat_module._env_openrouter_key() == "placeholder-alpha"
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "placeholder-bravo"}, clear=False):  # pragma: allowlist secret
            assert chat_module._env_openrouter_key() == "placeholder-bravo"

    def test_returns_503_when_no_api_key(self):
        env = {
            "OPENROUTER_API_KEY": "",
            "INTERNAL_LLM_PROVIDER": "openrouter",
            "OLLAMA_ENABLED": "false",
        }
        with patch.dict(os.environ, env, clear=False):  # pragma: allowlist secret
            app = _make_app()
            client = TestClient(app)
            resp = client.post("/chat/stream", json={
                "model": "openrouter/openai/gpt-4o-mini",
                "messages": [{"role": "user", "content": "hello"}],
            })
            assert resp.status_code == 503
            assert "OPENROUTER_API_KEY" in resp.text

    def test_allows_local_provider_without_openrouter_key(self):
        """GUI chat works with INTERNAL_LLM_PROVIDER=ollama and no cloud key."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.aclose = AsyncMock()
        mock_response.aread = AsyncMock(return_value=b"")

        async def fake_aiter():
            yield b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        mock_response.aiter_bytes = fake_aiter

        env = {
            "OPENROUTER_API_KEY": "",
            "INTERNAL_LLM_PROVIDER": "ollama",
            "OLLAMA_ENABLED": "true",
            "OLLAMA_URL": "http://127.0.0.1:11434",
            "INTERNAL_LLM_MODEL": "llama3.2",
        }
        with patch.dict(os.environ, env, clear=False):  # pragma: allowlist secret
            app = _make_app()
            client = TestClient(app)
            with patch("app.routers.chat.httpx.AsyncClient") as mock_client_cls:
                _setup_mock_client(mock_client_cls, mock_response)
                resp = client.post("/chat/stream", json={
                    "model": "llama3.2",
                    "messages": [{"role": "user", "content": "hello"}],
                })
            assert resp.status_code == 200
            assert "Hi" in resp.text or "cerid_meta" in resp.text

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

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "unit-test-placeholder"}, clear=False):  # pragma: allowlist secret
            app = _make_app()
            client = TestClient(app)

            with patch("app.routers.chat.httpx.AsyncClient") as mock_client_cls:
                _setup_mock_client(mock_client_cls, mock_response)

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

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "unit-test-placeholder"}, clear=False):  # pragma: allowlist secret
            app = _make_app()
            client = TestClient(app)

            with patch("app.routers.chat.httpx.AsyncClient") as mock_client_cls:
                _setup_mock_client(mock_client_cls, mock_response)

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

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "unit-test-placeholder"}, clear=False):  # pragma: allowlist secret
            app = _make_app()
            client = TestClient(app)

            with patch("app.routers.chat.httpx.AsyncClient") as mock_client_cls:
                _setup_mock_client(mock_client_cls, mock_response)

                resp = client.post("/chat/stream", json={
                    "model": "openrouter/anthropic/claude-sonnet-4.6",
                    "messages": [{"role": "user", "content": "hello"}],
                })

                body = resp.text
                assert "cerid_meta_update" not in body


class TestStripPrefix:
    """Unit tests for _strip_prefix helper."""

    def test_strips_openrouter_prefix(self):
        from app.routers.chat import _strip_prefix

        assert _strip_prefix("openrouter/anthropic/claude-sonnet-4.6") == "anthropic/claude-sonnet-4.6"

    def test_no_prefix_passthrough(self):
        from app.routers.chat import _strip_prefix

        assert _strip_prefix("anthropic/claude-sonnet-4.6") == "anthropic/claude-sonnet-4.6"

    def test_empty_string(self):
        from app.routers.chat import _strip_prefix

        assert _strip_prefix("") == ""


class TestChatRequestValidation:
    """Request body validation."""

    def test_rejects_missing_model(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "unit-test-placeholder"}, clear=False):  # pragma: allowlist secret
            app = _make_app()
            client = TestClient(app)
            resp = client.post("/chat/stream", json={
                "messages": [{"role": "user", "content": "hello"}],
            })
            assert resp.status_code == 422  # Pydantic validation error

    def test_rejects_empty_messages(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "unit-test-placeholder"}, clear=False):  # pragma: allowlist secret
            app = _make_app()
            client = TestClient(app)

            with patch("app.routers.chat.httpx.AsyncClient") as mock_client_cls:
                mock_resp = AsyncMock()
                mock_resp.status_code = 200
                mock_resp.aclose = AsyncMock()
                mock_resp.aread = AsyncMock(return_value=b"")

                async def empty_stream():
                    yield b"data: [DONE]\n\n"

                mock_resp.aiter_bytes = empty_stream

                _setup_mock_client(mock_client_cls, mock_resp)

                resp = client.post("/chat/stream", json={
                    "model": "openrouter/openai/gpt-4o-mini",
                    "messages": [],
                })
                # Empty messages list is valid at the schema level,
                # OpenRouter would reject it — our proxy forwards as-is
                assert resp.status_code == 200

    def test_accepts_optional_max_tokens(self):
        """max_tokens is optional and should be forwarded when present."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "unit-test-placeholder"}, clear=False):  # pragma: allowlist secret
            app = _make_app()
            client = TestClient(app)

            with patch("app.routers.chat.httpx.AsyncClient") as mock_client_cls:
                mock_resp = AsyncMock()
                mock_resp.status_code = 200
                mock_resp.aclose = AsyncMock()
                mock_resp.aread = AsyncMock(return_value=b"")

                async def empty_stream():
                    yield b"data: [DONE]\n\n"

                mock_resp.aiter_bytes = empty_stream

                mock_client = _setup_mock_client(mock_client_cls, mock_resp)

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


class TestChatTelemetry:
    """Phase 0.4a: route-decision counters + per-model latency (fakeredis)."""

    @staticmethod
    def _mock_ok_response():
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.aclose = AsyncMock()
        mock_response.aread = AsyncMock(return_value=b"")

        async def fake_aiter():
            yield b"data: [DONE]\n\n"

        mock_response.aiter_bytes = fake_aiter
        return mock_response

    def test_explicit_route_increments_counter(self, monkeypatch):
        import fakeredis

        fake = fakeredis.FakeRedis(decode_responses=True)
        monkeypatch.setattr("app.deps.get_redis", lambda: fake, raising=False)

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "unit-test-placeholder"}, clear=False):  # pragma: allowlist secret
            app = _make_app()
            client = TestClient(app)
            with patch("app.routers.chat.httpx.AsyncClient") as mock_client_cls:
                _setup_mock_client(mock_client_cls, self._mock_ok_response())
                resp = client.post("/chat/stream", json={
                    "model": "openrouter/openai/gpt-4o-mini",
                    "messages": [{"role": "user", "content": "hello"}],
                })
                assert resp.status_code == 200

        from app.routers.chat import get_chat_route_counts_today

        counts = get_chat_route_counts_today(fake)
        # "auto_failed" bucket added in E1 CR-050 (observable smart-route failure).
        assert counts == {"explicit": 1, "auto": 0, "auto_failed": 0, "fallback": 0}

    def test_model_latency_recorded_on_success(self, monkeypatch):
        import fakeredis

        fake = fakeredis.FakeRedis(decode_responses=True)
        monkeypatch.setattr("app.deps.get_redis", lambda: fake, raising=False)

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "unit-test-placeholder"}, clear=False):  # pragma: allowlist secret
            app = _make_app()
            client = TestClient(app)
            with patch("app.routers.chat.httpx.AsyncClient") as mock_client_cls:
                _setup_mock_client(mock_client_cls, self._mock_ok_response())
                resp = client.post("/chat/stream", json={
                    "model": "openrouter/anthropic/claude-sonnet-4.6",
                    "messages": [{"role": "user", "content": "hello"}],
                })
                assert resp.status_code == 200

        from app.routers.chat import get_chat_model_latency_stats

        stats = get_chat_model_latency_stats(fake)
        assert "anthropic/claude-sonnet-4.6" in stats
        assert stats["anthropic/claude-sonnet-4.6"]["count"] == 1
        assert stats["anthropic/claude-sonnet-4.6"]["p50_s"] >= 0.0
