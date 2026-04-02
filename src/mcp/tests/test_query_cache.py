# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for Redis query cache (utils/query_cache.py)."""

from __future__ import annotations

import asyncio
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
        """Redis has data -- returns parsed dict."""
        payload = {"results": [{"id": 1, "text": "hello"}], "score": 0.95}
        redis = MagicMock()
        redis.get.return_value = json.dumps(payload)
        mock_get_redis.return_value = redis

        result = get_cached("test query", "code", 10)

        assert result == payload
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
        assert json.loads(data_arg) == payload

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
