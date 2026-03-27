# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Ollama local LLM proxy router."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _enable_ollama():
    """Return a patch that sets OLLAMA_ENABLED=true."""
    return patch.dict("os.environ", {"OLLAMA_ENABLED": "true", "OLLAMA_URL": "http://localhost:11434"})


def _disable_ollama():
    """Return a patch that sets OLLAMA_ENABLED=false."""
    return patch.dict("os.environ", {"OLLAMA_ENABLED": "false"})


@pytest.fixture(autouse=True)
def _reset_ollama_breaker():
    """Ensure the ollama circuit breaker starts closed for each test."""
    from utils.circuit_breaker import CircuitState, get_breaker

    b = get_breaker("ollama")
    b._state = CircuitState.CLOSED
    b._failure_count = 0


# ---------------------------------------------------------------------------
# Disabled state
# ---------------------------------------------------------------------------

class TestOllamaDisabled:
    """When OLLAMA_ENABLED=false, all endpoints return 503."""

    @pytest.mark.asyncio
    async def test_models_disabled(self):
        from fastapi import HTTPException

        from app.routers.ollama_proxy import list_ollama_models

        with _disable_ollama():
            with pytest.raises(HTTPException) as exc_info:
                await list_ollama_models()
            assert exc_info.value.status_code == 503
            assert "disabled" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_chat_disabled(self):
        from fastapi import HTTPException

        from app.routers.ollama_proxy import ChatMessage, ChatRequest, chat_completion

        req = ChatRequest(
            model="llama3.2",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        with _disable_ollama():
            with pytest.raises(HTTPException) as exc_info:
                await chat_completion(req)
            assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_pull_disabled(self):
        from fastapi import HTTPException

        from app.routers.ollama_proxy import PullRequest, pull_model

        with _disable_ollama():
            with pytest.raises(HTTPException) as exc_info:
                await pull_model(PullRequest(model="llama3.2"))
            assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# Model listing
# ---------------------------------------------------------------------------

class TestListModels:
    @pytest.mark.asyncio
    async def test_list_models_success(self):
        from app.routers.ollama_proxy import list_ollama_models

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "models": [
                {"name": "llama3.2:latest", "size": 4_000_000_000, "digest": "abc123"},
                {"name": "mistral:latest", "size": 3_500_000_000, "digest": "def456"},
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with _enable_ollama(), patch("routers.ollama_proxy.httpx.AsyncClient", return_value=mock_client):
            result = await list_ollama_models()

        assert len(result.models) == 2
        assert result.models[0].name == "llama3.2:latest"
        assert result.models[1].name == "mistral:latest"

    @pytest.mark.asyncio
    async def test_list_models_connection_error(self):
        from fastapi import HTTPException

        from app.routers.ollama_proxy import list_ollama_models

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with _enable_ollama(), patch("routers.ollama_proxy.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(HTTPException) as exc_info:
                await list_ollama_models()
            assert exc_info.value.status_code == 503
            assert "ollama" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_list_models_timeout(self):
        from fastapi import HTTPException

        from app.routers.ollama_proxy import list_ollama_models

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.TimeoutException("timed out")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with _enable_ollama(), patch("routers.ollama_proxy.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(HTTPException) as exc_info:
                await list_ollama_models()
            assert exc_info.value.status_code == 504


# ---------------------------------------------------------------------------
# Chat completions (non-streaming)
# ---------------------------------------------------------------------------

class TestChatSync:
    @pytest.mark.asyncio
    async def test_chat_success(self):
        from app.routers.ollama_proxy import ChatMessage, ChatRequest, chat_completion

        ollama_response = {
            "model": "llama3.2",
            "message": {"role": "assistant", "content": "Hello! How can I help?"},
            "done": True,
            "total_duration": 500_000_000,
            "eval_count": 12,
        }

        mock_resp = MagicMock()
        mock_resp.json.return_value = ollama_response
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        req = ChatRequest(
            model="llama3.2",
            messages=[ChatMessage(role="user", content="Hello")],
            stream=False,
            temperature=0.7,
            max_tokens=100,
        )

        with _enable_ollama(), patch("routers.ollama_proxy.httpx.AsyncClient", return_value=mock_client):
            result = await chat_completion(req)

        assert result["model"] == "llama3.2"
        assert result["message"]["content"] == "Hello! How can I help?"

        # Verify Ollama payload format
        call_payload = mock_client.post.call_args[1]["json"]
        assert call_payload["model"] == "llama3.2"
        assert call_payload["options"]["temperature"] == 0.7
        assert call_payload["options"]["num_predict"] == 100
        assert call_payload["stream"] is False

    @pytest.mark.asyncio
    async def test_chat_connection_error(self):
        from fastapi import HTTPException

        from app.routers.ollama_proxy import ChatMessage, ChatRequest, chat_completion

        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        req = ChatRequest(
            model="llama3.2",
            messages=[ChatMessage(role="user", content="Hello")],
        )

        with _enable_ollama(), patch("routers.ollama_proxy.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(HTTPException) as exc_info:
                await chat_completion(req)
            assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_chat_circuit_breaker_open(self):
        from fastapi import HTTPException

        from app.routers.ollama_proxy import ChatMessage, ChatRequest, chat_completion
        from utils.circuit_breaker import CircuitState, get_breaker

        # Force circuit breaker open
        breaker = get_breaker("ollama")
        breaker._state = CircuitState.OPEN
        breaker._last_failure_time = __import__("time").monotonic()

        req = ChatRequest(
            model="llama3.2",
            messages=[ChatMessage(role="user", content="Hello")],
        )

        with _enable_ollama():
            with pytest.raises(HTTPException) as exc_info:
                await chat_completion(req)
            assert exc_info.value.status_code == 503
            assert "circuit breaker" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# Chat completions (streaming)
# ---------------------------------------------------------------------------

class TestChatStream:
    @pytest.mark.asyncio
    async def test_stream_circuit_breaker_open(self):
        from fastapi import HTTPException

        from app.routers.ollama_proxy import ChatMessage, ChatRequest, chat_completion
        from utils.circuit_breaker import CircuitState, get_breaker

        breaker = get_breaker("ollama")
        breaker._state = CircuitState.OPEN
        breaker._last_failure_time = __import__("time").monotonic()

        req = ChatRequest(
            model="llama3.2",
            messages=[ChatMessage(role="user", content="Hello")],
            stream=True,
        )

        with _enable_ollama():
            with pytest.raises(HTTPException) as exc_info:
                await chat_completion(req)
            assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# Provider validation (no auth required)
# ---------------------------------------------------------------------------

class TestProviderValidation:
    @pytest.mark.asyncio
    async def test_ollama_validation_no_auth(self):
        """Ollama validation should not require an API key."""
        from config.providers import validate_provider_key

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": []}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("config.providers.httpx.AsyncClient", return_value=mock_client):
            valid, msg = await validate_provider_key("ollama", "")

        assert valid is True
        assert msg == "OK"

        # Verify no Authorization header was sent
        call_args = mock_client.get.call_args
        if call_args[1].get("headers"):
            assert "Authorization" not in call_args[1]["headers"]

    @pytest.mark.asyncio
    async def test_ollama_validation_not_running(self):
        """When Ollama is not running, validation returns a clear error."""
        from config.providers import validate_provider_key

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("config.providers.httpx.AsyncClient", return_value=mock_client):
            valid, msg = await validate_provider_key("ollama", "")

        assert valid is False
        assert "ollama" in msg.lower()

    def test_ollama_no_api_key_required(self):
        """Ollama provider registry entry should not require an API key."""
        from config.providers import PROVIDER_REGISTRY

        entry = PROVIDER_REGISTRY["ollama"]
        assert entry["requires_api_key"] is False
        assert entry["test_endpoint"] == "/api/tags"

    def test_ollama_models_in_registry(self):
        """Ollama provider should list common local models."""
        from config.providers import PROVIDER_REGISTRY

        models = PROVIDER_REGISTRY["ollama"]["models"]
        assert "llama3.2" in models
        assert "mistral" in models
        assert "codellama" in models
        assert "gemma2" in models
        assert "phi3" in models
        assert "qwen2.5" in models

    def test_ollama_base_url_default(self):
        """Ollama base_url should default to localhost:11434."""
        from config.providers import PROVIDER_REGISTRY

        entry = PROVIDER_REGISTRY["ollama"]
        assert "11434" in entry["base_url"]


# ---------------------------------------------------------------------------
# Ollama appears in configured providers (no key needed)
# ---------------------------------------------------------------------------

class TestConfiguredProviders:
    def test_ollama_always_configured(self):
        """Ollama should appear in configured providers without any env var."""
        from config.providers import get_configured_providers

        providers = get_configured_providers()
        ollama = [p for p in providers if p["name"] == "ollama"]
        assert len(ollama) == 1
        assert ollama[0]["requires_api_key"] is False
        assert ollama[0]["key_set"] is True
