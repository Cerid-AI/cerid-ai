# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the shared Bifrost LLM call utility."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from utils.bifrost import call_bifrost, extract_content
from utils.circuit_breaker import CircuitOpenError

MOCK_RESPONSE = {
    "choices": [{"message": {"content": "Hello, world!"}}],
}


@pytest.fixture(autouse=True)
def _reset_breakers():
    """Ensure circuit breakers start closed for each test."""
    from utils.circuit_breaker import CircuitState, get_breaker

    for name in ("bifrost-rerank", "bifrost-claims", "bifrost-verify",
                 "bifrost-synopsis", "bifrost-memory"):
        b = get_breaker(name)
        b._state = CircuitState.CLOSED
        b._failure_count = 0


class TestCallBifrost:
    @pytest.mark.asyncio
    async def test_successful_call(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp

        with patch("utils.bifrost.get_bifrost_client", new_callable=AsyncMock, return_value=mock_client), \
             patch("utils.bifrost._clear_credits_exhausted"):
            result = await call_bifrost(
                [{"role": "user", "content": "test"}],
                breaker_name="bifrost-memory",
            )

        assert result == MOCK_RESPONSE
        mock_client.post.assert_called_once()
        call_payload = mock_client.post.call_args[1]["json"]
        assert call_payload["messages"] == [{"role": "user", "content": "test"}]
        assert call_payload["temperature"] == 0.3  # default

    @pytest.mark.asyncio
    async def test_custom_parameters(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp

        with patch("utils.bifrost.get_bifrost_client", new_callable=AsyncMock, return_value=mock_client), \
             patch("utils.bifrost._clear_credits_exhausted"):
            await call_bifrost(
                [{"role": "user", "content": "test"}],
                breaker_name="bifrost-claims",
                model="openrouter/openai/gpt-4o-mini",
                temperature=0.1,
                max_tokens=1200,
            )

        call_payload = mock_client.post.call_args[1]["json"]
        assert call_payload["model"] == "openrouter/openai/gpt-4o-mini"
        assert call_payload["temperature"] == 0.1
        assert call_payload["max_tokens"] == 1200

    @pytest.mark.asyncio
    async def test_http_error_propagates(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 Server Error", request=MagicMock(), response=MagicMock(status_code=500)
        )

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp

        with patch("utils.bifrost.get_bifrost_client", new_callable=AsyncMock, return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                await call_bifrost(
                    [{"role": "user", "content": "test"}],
                    breaker_name="bifrost-memory",
                )

    @pytest.mark.asyncio
    async def test_circuit_open_raises(self):
        import time as _time

        from utils.circuit_breaker import CircuitState, get_breaker

        breaker = get_breaker("bifrost-memory")
        breaker._state = CircuitState.OPEN
        breaker._last_failure_time = _time.monotonic()  # just failed → still in recovery

        with pytest.raises(CircuitOpenError):
            await call_bifrost(
                [{"role": "user", "content": "test"}],
                breaker_name="bifrost-memory",
            )

    @pytest.mark.asyncio
    async def test_extra_payload_merged(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp

        with patch("utils.bifrost.get_bifrost_client", new_callable=AsyncMock, return_value=mock_client), \
             patch("utils.bifrost._clear_credits_exhausted"):
            await call_bifrost(
                [{"role": "user", "content": "test"}],
                breaker_name="bifrost-rerank",
                extra_payload={"response_format": {"type": "json_object"}},
            )

        call_payload = mock_client.post.call_args[1]["json"]
        assert call_payload["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_custom_timeout(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp

        with patch("utils.bifrost.get_bifrost_client", new_callable=AsyncMock, return_value=mock_client), \
             patch("utils.bifrost._clear_credits_exhausted"):
            await call_bifrost(
                [{"role": "user", "content": "test"}],
                breaker_name="bifrost-verify",
                timeout=60.0,
            )

        # Verify the custom timeout was passed to the post() call
        call_kwargs = mock_client.post.call_args[1]
        assert call_kwargs["timeout"] == 60.0


class TestExtractContent:
    def test_basic_extraction(self):
        data = {"choices": [{"message": {"content": "  Hello  "}}]}
        assert extract_content(data) == "Hello"

    def test_empty_content(self):
        data = {"choices": [{"message": {"content": ""}}]}
        assert extract_content(data) == ""

    def test_missing_key_raises(self):
        with pytest.raises(KeyError):
            extract_content({})
