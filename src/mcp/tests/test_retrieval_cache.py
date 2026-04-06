# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for retrieval-level cache (utils/retrieval_cache.py)."""

from __future__ import annotations

import json
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

# Ensure numpy stub exists before importing the module under test.
# The host Python may lack numpy; retrieval_cache.py imports it at module level.
if "numpy" not in sys.modules:
    _np = ModuleType("numpy")

    class _FakeNdArray:
        """Minimal ndarray stand-in for int8 quantization in _hash_embedding."""
        def __init__(self, data, dtype=None):
            self._data = list(data)
            self._dtype = dtype
        def __mul__(self, other):
            return _FakeNdArray([v * other for v in self._data], dtype=self._dtype)
        def __rmul__(self, other):
            return self.__mul__(other)
        def tobytes(self):
            if self._dtype == "int8":
                return bytes(int(v) & 0xFF for v in self._data)
            return bytes(int(v) & 0xFF for v in self._data)
        def astype(self, dtype):
            return _FakeNdArray(self._data, dtype=dtype)

    def _fake_array(data, dtype=None):
        return _FakeNdArray(list(data), dtype=dtype)

    def _fake_clip(arr, lo, hi):
        return _FakeNdArray([max(lo, min(hi, v)) for v in arr._data], dtype=arr._dtype)

    def _fake_round(arr):
        return _FakeNdArray([round(v) for v in arr._data], dtype=arr._dtype)

    _np.array = _fake_array
    _np.clip = _fake_clip
    _np.round = _fake_round
    _np.float32 = "float32"
    _np.int8 = "int8"
    sys.modules["numpy"] = _np

from utils.retrieval_cache import RetrievalCache

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_EMBEDDING = [0.1, 0.2, 0.3, 0.4, 0.5]
SAMPLE_RESULTS = [
    {"id": "chunk_1", "text": "hello world", "score": 0.95},
    {"id": "chunk_2", "text": "foo bar", "score": 0.88},
]


def _make_cache(ttl: int = 300) -> RetrievalCache:
    """Create a fresh cache instance (not the module singleton)."""
    return RetrievalCache(ttl=ttl)


# ---------------------------------------------------------------------------
# _hash_embedding
# ---------------------------------------------------------------------------


class TestHashEmbedding:
    def test_hash_embedding_deterministic(self):
        """Same embedding -> same hash every time."""
        h1 = RetrievalCache._hash_embedding(SAMPLE_EMBEDDING)
        h2 = RetrievalCache._hash_embedding(SAMPLE_EMBEDDING)
        assert h1 == h2

    def test_hash_embedding_quantization(self):
        """Quantization produces consistent int8 results -- close floats map to same hash."""
        # Values within 1/127 of each other should quantize the same
        h1 = RetrievalCache._hash_embedding([0.5000])
        h2 = RetrievalCache._hash_embedding([0.5001])
        # Both quantize to int8(round(0.5*127)) = int8(64)
        assert h1 == h2

    def test_hash_embedding_is_hex_string(self):
        """Hash output is a hex string (SHA-256)."""
        h = RetrievalCache._hash_embedding(SAMPLE_EMBEDDING)
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex digest
        assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# _cache_key
# ---------------------------------------------------------------------------


class TestCacheKey:
    @patch.object(RetrievalCache, "_redis")
    def test_cache_key_includes_generation(self, mock_redis_method):
        """Key contains current generation counter."""
        redis = MagicMock()
        redis.get.return_value = b"5"
        mock_redis_method.return_value = redis

        cache = _make_cache()
        key = cache._cache_key(SAMPLE_EMBEDDING, 10)

        assert ":5:" in key
        assert key.startswith(RetrievalCache.PREFIX)

    @patch.object(RetrievalCache, "_redis")
    def test_cache_key_generation_zero_default(self, mock_redis_method):
        """Generation defaults to 0 when Redis key is unset."""
        redis = MagicMock()
        redis.get.return_value = None
        mock_redis_method.return_value = redis

        cache = _make_cache()
        key = cache._cache_key(SAMPLE_EMBEDDING, 10)

        assert ":0:" in key


# ---------------------------------------------------------------------------
# get (cache hit / miss)
# ---------------------------------------------------------------------------


class TestGet:
    @patch.object(RetrievalCache, "_redis")
    def test_get_cache_hit(self, mock_redis_method):
        """Redis has valid data -> returns parsed results, increments hits."""
        redis = MagicMock()
        # _generation() call
        redis.get.side_effect = [
            b"1",                        # generation lookup
            json.dumps(SAMPLE_RESULTS),  # actual cache data
        ]
        mock_redis_method.return_value = redis

        cache = _make_cache()
        result = cache.get(SAMPLE_EMBEDDING, top_k=10)

        assert result == SAMPLE_RESULTS
        assert cache._hits == 1
        assert cache._misses == 0

    @patch.object(RetrievalCache, "_redis")
    def test_get_cache_miss(self, mock_redis_method):
        """Redis returns None -> returns None, increments misses."""
        redis = MagicMock()
        redis.get.side_effect = [
            b"1",   # generation lookup
            None,   # cache miss
        ]
        mock_redis_method.return_value = redis

        cache = _make_cache()
        result = cache.get(SAMPLE_EMBEDDING, top_k=10)

        assert result is None
        assert cache._misses == 1
        assert cache._hits == 0

    @patch.object(RetrievalCache, "_redis")
    def test_get_stale_generation(self, mock_redis_method):
        """Key from old generation -> cache miss."""
        redis = MagicMock()
        redis.get.side_effect = [b"2", None]  # gen=2, no data at gen-2 key
        mock_redis_method.return_value = redis

        cache = _make_cache()
        assert cache.get(SAMPLE_EMBEDDING, top_k=10) is None
        assert cache._misses == 1

    @patch.object(RetrievalCache, "_redis")
    def test_get_redis_error(self, mock_redis_method):
        """Redis failure -> returns None, increments misses."""
        redis = MagicMock()
        redis.get.side_effect = OSError("connection lost")
        mock_redis_method.return_value = redis

        cache = _make_cache()
        result = cache.get(SAMPLE_EMBEDDING, top_k=10)

        assert result is None
        assert cache._misses == 1


# ---------------------------------------------------------------------------
# set
# ---------------------------------------------------------------------------


class TestSet:
    @patch.object(RetrievalCache, "_redis")
    def test_set_stores_with_ttl(self, mock_redis_method):
        """Stores serialized results with correct TTL."""
        redis = MagicMock()
        redis.get.return_value = b"3"  # generation
        mock_redis_method.return_value = redis

        cache = _make_cache(ttl=600)
        cache.set(SAMPLE_EMBEDDING, top_k=10, results=SAMPLE_RESULTS)

        redis.setex.assert_called_once()
        call_args = redis.setex.call_args[0]
        key = call_args[0]
        ttl = call_args[1]
        data = json.loads(call_args[2])

        assert key.startswith(RetrievalCache.PREFIX)
        assert ":3:" in key  # generation included
        assert ttl == 600
        assert data == SAMPLE_RESULTS

    @patch.object(RetrievalCache, "_redis")
    def test_set_redis_error(self, mock_redis_method):
        """Redis failure -> silent, no exception raised."""
        redis = MagicMock()
        redis.get.return_value = b"0"  # generation
        redis.setex.side_effect = RuntimeError("write refused")
        mock_redis_method.return_value = redis

        cache = _make_cache()
        # Must not raise
        cache.set(SAMPLE_EMBEDDING, top_k=5, results=SAMPLE_RESULTS)


# ---------------------------------------------------------------------------
# invalidate_all
# ---------------------------------------------------------------------------


class TestInvalidateAll:
    @patch.object(RetrievalCache, "_redis")
    def test_invalidate_all_increments_generation(self, mock_redis_method):
        """Generation counter is bumped via INCR."""
        redis = MagicMock()
        redis.incr.return_value = 4
        mock_redis_method.return_value = redis

        cache = _make_cache()
        cache.invalidate_all()

        redis.incr.assert_called_once_with(RetrievalCache.GENERATION_KEY)

    @patch.object(RetrievalCache, "_redis")
    def test_invalidate_all_redis_error(self, mock_redis_method):
        """Redis error during invalidation -> silent failure."""
        redis = MagicMock()
        redis.incr.side_effect = OSError("redis unreachable")
        mock_redis_method.return_value = redis

        cache = _make_cache()
        # Must not raise
        cache.invalidate_all()


# ---------------------------------------------------------------------------
# hit_rate
# ---------------------------------------------------------------------------


class TestHitRate:
    def test_hit_rate_calculation(self):
        """Correct ratio after known hits/misses."""
        cache = _make_cache()
        cache._hits = 3
        cache._misses = 7

        stats = cache.hit_rate()

        assert stats["hits"] == 3
        assert stats["misses"] == 7
        assert stats["rate"] == 0.3  # 3/10

    def test_hit_rate_zero_total(self):
        """No requests -> rate is 0.0, no division by zero."""
        cache = _make_cache()
        stats = cache.hit_rate()

        assert stats["rate"] == 0.0
        assert stats["hits"] == 0
        assert stats["misses"] == 0

class TestLazyRedis:
    def test_lazy_redis_init(self):
        """Redis client is not created at construction time -- only on first access."""
        # get_redis is imported locally inside _redis(), so patch at the source
        with patch("deps.get_redis") as mock_get_redis:
            cache = RetrievalCache.__new__(RetrievalCache)
            cache._ttl = 300
            cache._hits = 0
            cache._misses = 0

            # Construction alone should not touch Redis
            mock_get_redis.assert_not_called()

            # Now trigger _redis() access
            mock_get_redis.return_value = MagicMock()
            _client = cache._redis()
            mock_get_redis.assert_called_once()
