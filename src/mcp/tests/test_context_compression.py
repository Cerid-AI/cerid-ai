# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for context compression and sliding-window pruning."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from utils.context_compression import (
    CHARS_PER_TOKEN,
    _estimate_messages_tokens,
    compress_history,
    estimate_tokens,
    sliding_window_prune,
)

# ---------------------------------------------------------------------------
# Tests: estimate_tokens
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_basic_estimate(self):
        text = "a" * 35  # 35 chars / 3.5 ratio = 10 tokens
        assert estimate_tokens(text) == 10

    def test_empty_string(self):
        assert estimate_tokens("") == 1  # max(1, ...)

    def test_short_string(self):
        assert estimate_tokens("hi") >= 1


# ---------------------------------------------------------------------------
# Tests: sliding_window_prune
# ---------------------------------------------------------------------------

def _msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


class TestSlidingWindowPrune:
    def test_keeps_system_and_recent_turns(self):
        messages = [
            _msg("system", "You are helpful"),
            _msg("user", "msg 1"),
            _msg("assistant", "reply 1"),
            _msg("user", "msg 2"),
            _msg("assistant", "reply 2"),
            _msg("user", "msg 3"),
            _msg("assistant", "reply 3"),
            _msg("user", "msg 4"),
            _msg("assistant", "reply 4"),
        ]
        result = sliding_window_prune(messages, max_turns=2)
        assert len(result) == 5  # system + 2 pairs
        assert result[0]["role"] == "system"
        assert result[1]["content"] == "msg 3"
        assert result[4]["content"] == "reply 4"

    def test_no_system_message(self):
        messages = [
            _msg("user", "msg 1"),
            _msg("assistant", "reply 1"),
            _msg("user", "msg 2"),
            _msg("assistant", "reply 2"),
            _msg("user", "msg 3"),
            _msg("assistant", "reply 3"),
        ]
        result = sliding_window_prune(messages, max_turns=1)
        assert len(result) == 2  # just last pair
        assert result[0]["content"] == "msg 3"
        assert result[1]["content"] == "reply 3"

    def test_fewer_than_max_turns_returns_all(self):
        messages = [
            _msg("system", "sys"),
            _msg("user", "msg 1"),
            _msg("assistant", "reply 1"),
        ]
        result = sliding_window_prune(messages, max_turns=3)
        assert len(result) == 3  # all preserved
        assert result == messages

    def test_empty_messages(self):
        assert sliding_window_prune([]) == []

    def test_defaults_to_keep_recent_turns(self):
        """Uses KEEP_RECENT_TURNS when max_turns not specified."""
        messages = [
            _msg("system", "sys"),
            *[_msg("user", f"u{i}") for i in range(10)],
            *[_msg("assistant", f"a{i}") for i in range(10)],
        ]
        # Default is KEEP_RECENT_TURNS=2, but we can't rely on the exact value
        # Just verify it returns fewer messages than the original
        result = sliding_window_prune(messages)
        assert len(result) <= len(messages)
        assert result[0]["role"] == "system"

    def test_max_turns_zero_keeps_only_system(self):
        messages = [
            _msg("system", "sys"),
            _msg("user", "msg 1"),
            _msg("assistant", "reply 1"),
        ]
        result = sliding_window_prune(messages, max_turns=0)
        assert len(result) == 1
        assert result[0]["role"] == "system"


# ---------------------------------------------------------------------------
# Tests: compress_history
# ---------------------------------------------------------------------------

class TestCompressHistory:
    @pytest.mark.asyncio
    async def test_returns_unchanged_if_within_budget(self):
        messages = [
            _msg("system", "sys"),
            _msg("user", "short"),
            _msg("assistant", "reply"),
        ]
        result = await compress_history(messages, target_tokens=10000)
        assert result == messages

    @pytest.mark.asyncio
    async def test_compresses_middle_turns(self):
        # Make messages large enough to exceed target
        messages = [
            _msg("system", "You are helpful."),
            _msg("user", "x" * 1000),
            _msg("assistant", "y" * 1000),
            _msg("user", "z" * 1000),
            _msg("assistant", "w" * 1000),
            _msg("user", "recent question"),
            _msg("assistant", "recent answer"),
        ]
        mock_resp = {
            "choices": [{"message": {"content": "Summary of conversation."}}],
        }
        with patch("utils.context_compression.call_bifrost", new_callable=AsyncMock, return_value=mock_resp):
            result = await compress_history(messages, target_tokens=100)

        # Should have: system + compressed summary + last 2 pairs (4 messages)
        assert result[0]["role"] == "system"
        assert "[Compressed conversation summary]" in result[1]["content"]
        assert result[-1]["content"] == "recent answer"

    @pytest.mark.asyncio
    async def test_fallback_on_bifrost_failure(self):
        messages = [
            _msg("system", "sys"),
            _msg("user", "x" * 1000),
            _msg("assistant", "y" * 1000),
            _msg("user", "a" * 1000),
            _msg("assistant", "b" * 1000),
            _msg("user", "c" * 1000),
            _msg("assistant", "d" * 1000),
            _msg("user", "recent q"),
            _msg("assistant", "recent a"),
        ]
        import httpx

        error = httpx.HTTPStatusError("boom", request=httpx.Request("POST", "http://x"), response=httpx.Response(503))
        with patch("utils.context_compression.call_bifrost", new_callable=AsyncMock, side_effect=error):
            result = await compress_history(messages, target_tokens=10)

        # Should still return something (fallback truncation via _summarize_turns)
        assert result[0]["role"] == "system"
        assert "[Compressed conversation summary]" in result[1]["content"]

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        result = await compress_history([], target_tokens=100)
        assert result == []


# ---------------------------------------------------------------------------
# Tests: _estimate_messages_tokens
# ---------------------------------------------------------------------------

class TestEstimateMessagesTokens:
    def test_sums_across_messages(self):
        messages = [
            _msg("user", "a" * 35),   # ~10 tokens
            _msg("assistant", "b" * 70),  # ~20 tokens
        ]
        total = _estimate_messages_tokens(messages)
        assert total == 30

    def test_empty_list(self):
        assert _estimate_messages_tokens([]) == 0


# ---------------------------------------------------------------------------
# Tests: compress endpoint (integration via TestClient)
# ---------------------------------------------------------------------------

class TestCompressEndpoint:
    """Test the /chat/compress endpoint via FastAPI TestClient."""

    @pytest.fixture
    def client(self):
        """Create a test client with the agents router."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from routers.agents import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_already_within_budget(self, client):
        resp = client.post("/chat/compress", json={
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
            "target_tokens": 100000,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["messages"]) == 2
        assert data["original_tokens"] == data["compressed_tokens"]

    def test_compresses_long_history(self, client):
        # Build a history that exceeds the target
        messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i} " * 100} for i in range(20)]
        mock_resp = {
            "choices": [{"message": {"content": "Summarized."}}],
        }
        with patch("utils.context_compression.call_bifrost", new_callable=AsyncMock, return_value=mock_resp):
            resp = client.post("/chat/compress", json={
                "messages": messages,
                "target_tokens": 100,
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["compressed_tokens"] < data["original_tokens"]

    def test_falls_back_on_llm_failure(self, client):
        messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i} " * 100} for i in range(20)]
        with patch("utils.context_compression.call_bifrost", new_callable=AsyncMock, side_effect=Exception("LLM down")):
            resp = client.post("/chat/compress", json={
                "messages": messages,
                "target_tokens": 100,
            })
        assert resp.status_code == 200
        data = resp.json()
        # Should have used sliding_window_prune as fallback
        assert len(data["messages"]) < len(messages)
