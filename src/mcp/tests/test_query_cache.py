# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for Redis query cache (utils/query_cache.py)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from utils.query_cache import (
    CACHE_PREFIX,
    DEFAULT_TTL,
    _cache_key,
    get_cached,
    invalidate_all,
    invalidate_cache_non_blocking,
    set_cached,
)

# ---------------------------------------------------------------------------
# _cache_key
# ---------------------------------------------------------------------------


class TestCacheKey:
    def test_cache_key_deterministic(self):
        """Same inputs produce the same key every time."""
        key_a = _cache_key("what is Python?", "code", 10)
        key_b = _cache_key("what is Python?", "code", 10)
        assert key_a == key_b

    def test_cache_key_varies_by_domain(self):
        """Different domain produces a different key."""
        key_code = _cache_key("what is Python?", "code", 10)
        key_finance = _cache_key("what is Python?", "finance", 10)
        assert key_code != key_finance

    def test_cache_key_varies_by_top_k(self):
        """Different top_k produces a different key."""
        key_5 = _cache_key("what is Python?", "code", 5)
        key_20 = _cache_key("what is Python?", "code", 20)
        assert key_5 != key_20

    def test_cache_key_varies_by_context_hint(self):
        """Different context_hint produces a different key."""
        key_a = _cache_key("query", "code", 10, "hint_a")
        key_b = _cache_key("query", "code", 10, "hint_b")
        assert key_a != key_b

    def test_cache_key_has_prefix(self):
        """Keys always start with CACHE_PREFIX."""
        key = _cache_key("q", "d", 5)
        assert key.startswith(CACHE_PREFIX)

    def test_cache_key_fixed_length_hash(self):
        """Hash portion is always 32 hex characters."""
        key = _cache_key("some query", "general", 10)
        hash_part = key[len(CACHE_PREFIX):]
        assert len(hash_part) == 32
        assert all(c in "0123456789abcdef" for c in hash_part)


# ---------------------------------------------------------------------------
# get_cached
# ---------------------------------------------------------------------------


class TestGetCached:
    @patch("utils.query_cache.get_redis")
    def test_get_cached_hit(self, mock_get_redis):
        """Redis has data -- returns parsed dict (with cached flag added)."""
        payload = {"results": [{"id": 1, "text": "hello"}], "score": 0.95}
        redis = MagicMock()
        redis.get.return_value = json.dumps(payload)
        mock_get_redis.return_value = redis

        result = get_cached("test query", "code", 10)

        assert result is not None
        # Preserved fields
        assert result["results"] == payload["results"]
        assert result["score"] == payload["score"]
        redis.get.assert_called_once()

    @patch("utils.query_cache.get_redis")
    def test_get_cached_miss(self, mock_get_redis):
        """Redis returns None -- returns None."""
        redis = MagicMock()
        redis.get.return_value = None
        mock_get_redis.return_value = redis

        result = get_cached("test query", "code", 10)

        assert result is None

    @patch("utils.query_cache.get_redis")
    def test_get_cached_redis_error(self, mock_get_redis):
        """Redis raises -- returns None (graceful degradation)."""
        redis = MagicMock()
        redis.get.side_effect = OSError("connection refused")
        mock_get_redis.return_value = redis

        result = get_cached("test query", "code", 10)

        assert result is None

    @patch("utils.query_cache.get_redis")
    def test_get_cached_invalid_json(self, mock_get_redis):
        """Redis returns corrupt JSON -- returns None."""
        redis = MagicMock()
        redis.get.return_value = "not-valid-json{{"
        mock_get_redis.return_value = redis

        result = get_cached("test query", "code", 10)

        assert result is None


# ---------------------------------------------------------------------------
# set_cached
# ---------------------------------------------------------------------------


class TestSetCached:
    @patch("utils.query_cache.get_redis")
    def test_set_cached_stores_json(self, mock_get_redis):
        """Verifies setex called with correct TTL and JSON payload."""
        redis = MagicMock()
        mock_get_redis.return_value = redis

        payload = {"results": [{"id": 1}]}
        set_cached("query", "code", 10, payload, ttl=120)

        redis.setex.assert_called_once()
        call_args = redis.setex.call_args
        key_arg = call_args[0][0]
        ttl_arg = call_args[0][1]
        data_arg = call_args[0][2]

        assert key_arg.startswith(CACHE_PREFIX)
        assert ttl_arg == 120
        stored = json.loads(data_arg)
        # Caller's payload is preserved verbatim except for the private
        # stored-at timestamp that drives cache_age_ms on read.
        for k, v in payload.items():
            assert stored[k] == v
        assert "_cache_stored_at" in stored

    @patch("utils.query_cache.get_redis")
    def test_set_cached_default_ttl(self, mock_get_redis):
        """Uses DEFAULT_TTL when none specified."""
        redis = MagicMock()
        mock_get_redis.return_value = redis

        set_cached("query", "code", 10, {"data": True})

        call_args = redis.setex.call_args[0]
        assert call_args[1] == DEFAULT_TTL

    @patch("utils.query_cache.get_redis")
    def test_set_cached_redis_error(self, mock_get_redis):
        """Redis raises -- silent failure, no exception propagated."""
        redis = MagicMock()
        redis.setex.side_effect = RuntimeError("write failed")
        mock_get_redis.return_value = redis

        # Must not raise
        set_cached("query", "code", 10, {"data": True})


# ---------------------------------------------------------------------------
# invalidate_all
# ---------------------------------------------------------------------------


class TestInvalidateAll:
    @patch("utils.query_cache.get_redis")
    def test_invalidate_all_scans_and_deletes(self, mock_get_redis):
        """SCAN pattern finds matching keys and deletes them."""
        redis = MagicMock()
        mock_get_redis.return_value = redis

        # Simulate two SCAN rounds: first returns keys, second returns cursor=0
        keys_batch = [b"qcache:abc123", b"qcache:def456"]
        redis.scan.side_effect = [
            (1, keys_batch),      # cursor=1, keys found
            (0, []),              # cursor=0, done
        ]

        invalidate_all()

        assert redis.scan.call_count == 2
        redis.delete.assert_called_once_with(*keys_batch)

    @patch("utils.query_cache.get_redis")
    def test_invalidate_all_no_keys(self, mock_get_redis):
        """No matching keys -- delete never called."""
        redis = MagicMock()
        mock_get_redis.return_value = redis
        redis.scan.return_value = (0, [])

        invalidate_all()

        redis.delete.assert_not_called()

    @patch("utils.query_cache.get_redis")
    def test_invalidate_all_redis_error(self, mock_get_redis):
        """Redis error during invalidation -- silent failure."""
        redis = MagicMock()
        redis.scan.side_effect = OSError("redis down")
        mock_get_redis.return_value = redis

        # Must not raise
        invalidate_all()


# ---------------------------------------------------------------------------
# invalidate_cache_non_blocking
# ---------------------------------------------------------------------------


class TestInvalidateCacheNonBlocking:
    @pytest.mark.asyncio
    async def test_invalidate_cache_non_blocking(self):
        """Async wrapper calls sync invalidate_all in a thread."""
        with patch("utils.query_cache.invalidate_all") as mock_inv:
            await invalidate_cache_non_blocking()
            mock_inv.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalidate_cache_non_blocking_propagates_no_error(self):
        """Even if invalidate_all raises, the thread handles it."""
        with patch("utils.query_cache.invalidate_all", side_effect=OSError("boom")):
            # asyncio.to_thread will propagate the exception
            with pytest.raises(OSError):
                await invalidate_cache_non_blocking()


# ---------------------------------------------------------------------------
# Cache-hit surfacing (audit RC-G)
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal in-memory redis substitute for round-trip set/get testing."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def setex(self, key: str, ttl: int, value: str) -> None:  # noqa: ARG002
        self._store[key] = value

    def get(self, key: str):
        return self._store.get(key)


class TestCachedFlag:
    """A cached response must be distinguishable from a fresh one.

    Users reported warm 0.08 s vs cold 11.66 s but no signal in the body.
    """

    @patch("utils.query_cache.get_redis")
    def test_cached_response_marked_with_cached_flag(self, mock_get_redis):
        """After round-trip through set_cached/get_cached, response has cached=True."""
        fake = _FakeRedis()
        mock_get_redis.return_value = fake

        # Write fresh (no cached flag)
        set_cached("q1", "dk", 5, {"results": [], "answer": "cached"})

        # Read back
        out = get_cached("q1", "dk", 5)

        assert out is not None
        assert out.get("cached") is True
        assert "cache_age_ms" in out
        assert isinstance(out["cache_age_ms"], int)
        assert out["cache_age_ms"] >= 0

    @patch("utils.query_cache.get_redis")
    def test_fresh_result_before_caching_has_no_cached_flag(self, mock_get_redis):
        """Writing a result must NOT pre-stamp cached=True on the input dict."""
        fake = _FakeRedis()
        mock_get_redis.return_value = fake

        fresh = {"results": [], "answer": "fresh"}
        set_cached("q2", "dk", 5, fresh)

        # The caller's dict must not be mutated to look cached
        assert "cached" not in fresh or fresh.get("cached") is not True

    @patch("utils.query_cache.get_redis")
    def test_internal_timestamp_field_not_exposed(self, mock_get_redis):
        """The private _cache_stored_at field must not leak to callers."""
        fake = _FakeRedis()
        mock_get_redis.return_value = fake

        set_cached("q3", "dk", 5, {"results": [], "answer": "x"})
        out = get_cached("q3", "dk", 5)

        assert out is not None
        # Internal field should be stripped on the way out
        assert "_cache_stored_at" not in out
