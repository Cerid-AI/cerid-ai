# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for semantic query cache module."""

import json
from unittest.mock import MagicMock

import numpy as np

from utils.semantic_cache import (
    _cosine_similarity,
    _dequantize_int8,
    _quantize_int8,
    cache_lookup,
    cache_store,
    invalidate_cache,
)


class TestQuantization:
    """Tests for int8 quantization round-trip."""

    def test_round_trip_preserves_approximate_values(self):
        original = np.array([0.1, 0.5, 0.9, -0.3, 0.0], dtype=np.float32)
        data, scale, zp = _quantize_int8(original)
        recovered = _dequantize_int8(data, scale, zp)
        np.testing.assert_allclose(original, recovered, atol=0.01)

    def test_uniform_embedding(self):
        original = np.array([0.5, 0.5, 0.5], dtype=np.float32)
        data, scale, zp = _quantize_int8(original)
        recovered = _dequantize_int8(data, scale, zp)
        np.testing.assert_allclose(original, recovered, atol=0.01)

    def test_output_is_bytes(self):
        emb = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        data, _, _ = _quantize_int8(emb)
        assert isinstance(data, bytes)
        assert len(data) == 3


class TestCosineSimilarity:
    """Tests for _cosine_similarity()."""

    def test_identical_vectors(self):
        v = np.array([1.0, 2.0, 3.0])
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-6

    def test_zero_vector(self):
        assert _cosine_similarity(np.zeros(3), np.array([1.0, 2.0, 3.0])) == 0.0


class TestCacheLookup:
    """Tests for cache_lookup()."""

    def test_empty_cache_returns_none(self):
        redis = MagicMock()
        redis.lrange.return_value = []
        result = cache_lookup(np.array([1.0, 0.0]), redis)
        assert result is None

    def test_hit_above_threshold(self):
        emb = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        data, scale, zp = _quantize_int8(emb)

        index_entry = json.dumps({
            "id": "test123",
            "emb_hex": data.hex(),
            "scale": scale,
            "zero_point": zp,
            "query": "cached query",
        })

        cached_result = {"context": "cached context", "sources": []}

        redis = MagicMock()
        redis.lrange.return_value = [index_entry.encode()]
        redis.get.return_value = json.dumps(cached_result)

        result = cache_lookup(emb, redis, threshold=0.9)
        assert result is not None
        assert result["context"] == "cached context"

    def test_miss_below_threshold(self):
        cached_emb = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        query_emb = np.array([0.0, 0.0, 1.0], dtype=np.float32)
        data, scale, zp = _quantize_int8(cached_emb)

        index_entry = json.dumps({
            "id": "test123",
            "emb_hex": data.hex(),
            "scale": scale,
            "zero_point": zp,
            "query": "cached query",
        })

        redis = MagicMock()
        redis.lrange.return_value = [index_entry.encode()]

        result = cache_lookup(query_emb, redis, threshold=0.9)
        assert result is None


class TestCacheStore:
    """Tests for cache_store()."""

    def test_stores_entry(self):
        redis = MagicMock()
        emb = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        cache_store("test query", emb, {"context": "result"}, redis, ttl=60)
        redis.setex.assert_called_once()
        redis.lpush.assert_called_once()
        redis.ltrim.assert_called_once()

    def test_trims_to_max_entries(self):
        redis = MagicMock()
        emb = np.array([1.0, 0.0], dtype=np.float32)
        cache_store("test", emb, {}, redis, max_entries=100)
        redis.ltrim.assert_called_once()
        args = redis.ltrim.call_args[0]
        assert args[2] == 99


class TestInvalidateCache:
    """Tests for invalidate_cache()."""

    def test_clears_keys(self):
        redis = MagicMock()
        redis.scan.return_value = (0, [b"semcache:entry:abc", b"semcache:index"])
        count = invalidate_cache(redis)
        assert count == 2
        redis.delete.assert_called_once()

    def test_no_keys_to_clear(self):
        redis = MagicMock()
        redis.scan.return_value = (0, [])
        count = invalidate_cache(redis)
        assert count == 0
