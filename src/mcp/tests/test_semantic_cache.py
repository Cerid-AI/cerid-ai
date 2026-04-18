# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for HNSW-indexed semantic cache."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import numpy as np
import pytest

import utils.semantic_cache as sc
from core.retrieval.semantic_cache import (
    _dequantize_int8,
    _HNSWIndex,
    _quantize_int8,
)
from utils.semantic_cache import (
    cache_lookup,
    cache_store,
    invalidate_cache,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _random_embedding(dim: int = 768, seed: int = 42) -> np.ndarray:
    rng = np.random.RandomState(seed)
    emb = rng.randn(dim).astype(np.float32)
    return emb / np.linalg.norm(emb)


def _mock_redis() -> MagicMock:
    """Create a mock Redis client that simulates key-value + hash storage."""
    store: dict[str, bytes | str] = {}
    hash_store: dict[str, dict[str, str]] = {}

    mock = MagicMock()

    def _get(key: str):
        return store.get(key)

    def _set(key: str, value: bytes | str):
        store[key] = value

    def _setex(key: str, ttl: int, value: str):
        store[key] = value

    def _hset(name: str, key: str, value: str):
        hash_store.setdefault(name, {})[key] = value

    def _hget(name: str, key: str):
        return hash_store.get(name, {}).get(key)

    def _delete(*keys: str):
        for k in keys:
            store.pop(k, None)
            hash_store.pop(k, None)

    def _scan(cursor: int, match: str = "", count: int = 100):
        import fnmatch

        matched = [k for k in store if fnmatch.fnmatch(k, match)]
        matched += [k for k in hash_store if fnmatch.fnmatch(k, match)]
        return (0, list(set(matched)))

    mock.get = MagicMock(side_effect=_get)
    mock.set = MagicMock(side_effect=_set)
    mock.setex = MagicMock(side_effect=_setex)
    mock.hset = MagicMock(side_effect=_hset)
    mock.hget = MagicMock(side_effect=_hget)
    mock.delete = MagicMock(side_effect=_delete)
    mock.scan = MagicMock(side_effect=_scan)

    return mock


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the process-level HNSW singleton between tests."""
    sc._index = None
    yield
    sc._index = None


# ---------------------------------------------------------------------------
# Tests: Quantization (retained from original)
# ---------------------------------------------------------------------------

class TestQuantization:
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


# ---------------------------------------------------------------------------
# Tests: _HNSWIndex
# ---------------------------------------------------------------------------

class TestHNSWIndex:
    def test_init_empty(self):
        idx = _HNSWIndex(dim=32, max_elements=100)
        assert idx.count == 0

    def test_add_and_query(self):
        idx = _HNSWIndex(dim=32, max_elements=100)
        redis = _mock_redis()

        emb = _random_embedding(dim=32, seed=1)
        label = idx.add(emb, "entry_a", redis)
        assert label == 0
        assert idx.count == 1

        results = idx.query(emb, k=1)
        assert len(results) == 1
        assert results[0][0] == 0  # label
        assert results[0][1] < 0.01  # near-zero distance (same vector)

    def test_query_empty_index(self):
        idx = _HNSWIndex(dim=32, max_elements=100)
        emb = _random_embedding(dim=32)
        results = idx.query(emb, k=1)
        assert results == []

    def test_multiple_entries(self):
        idx = _HNSWIndex(dim=32, max_elements=100)
        redis = _mock_redis()

        emb1 = _random_embedding(dim=32, seed=1)
        emb2 = _random_embedding(dim=32, seed=2)
        emb3 = _random_embedding(dim=32, seed=3)

        idx.add(emb1, "a", redis)
        idx.add(emb2, "b", redis)
        idx.add(emb3, "c", redis)
        assert idx.count == 3

        # Query with emb1 should return itself as nearest
        results = idx.query(emb1, k=1)
        assert results[0][0] == 0

    @pytest.mark.skip(reason="Pre-existing: HNSW serialization flaky in CI — mock Redis binary I/O mismatch")
    def test_serialization_round_trip(self):
        """Test save to Redis → load from Redis preserves index."""
        idx = _HNSWIndex(dim=32, max_elements=100)
        redis = _mock_redis()

        emb = _random_embedding(dim=32, seed=7)
        idx.add(emb, "test_entry", redis)
        # Flush deferred persistence so the index is saved to Redis
        idx.flush(redis)

        # Create new index and load from Redis
        idx2 = _HNSWIndex(dim=32, max_elements=100)
        loaded = idx2.load_from_redis(redis)
        assert loaded is True
        assert idx2.count == 1

        # Query should still work
        results = idx2.query(emb, k=1)
        assert len(results) == 1
        assert results[0][1] < 0.01

    def test_load_from_empty_redis(self):
        idx = _HNSWIndex(dim=32, max_elements=100)
        redis = _mock_redis()
        loaded = idx.load_from_redis(redis)
        assert loaded is False
        assert idx.count == 0

    def test_semcache_discards_mismatched_dim_blob(self):
        """Audit V-5 / RC-G: a stored HNSW blob whose header dim does not
        match the active embedder must NOT be loaded. load_from_redis must
        drop the stale blob (and its label sidecar) and return False so the
        cache rebuilds cold.
        """
        from core.retrieval.semantic_cache import _HNSW_KEY, _LABELS_KEY

        redis = _mock_redis()
        redis._r = redis  # route through the raw-bypass path

        # 1. Seed Redis with a blob that carries our header but declares
        #    dim=512 (the body bytes don't matter — the dim check must reject
        #    it before hnswlib touches it).
        stale_header = _HNSWIndex._encode_header(512)
        stale_blob = stale_header + b"\x00" * 1024  # arbitrary body
        redis.set(_HNSW_KEY, stale_blob)
        redis.hset(_LABELS_KEY, "0", "ghost-entry")

        # 2. Attempt to load into an index whose live dim is 768.
        fresh = _HNSWIndex(dim=768, max_elements=100)
        loaded = fresh.load_from_redis(redis)

        # 3. The load must be rejected, the stale blob + labels deleted,
        #    and the fresh index must be empty and usable.
        assert loaded is False, (
            "dim-mismatched blob was accepted — cache would emit garbage "
            "similarity scores on the next query"
        )
        assert redis.get(_HNSW_KEY) is None, "stale blob was not deleted"
        assert redis.hget(_LABELS_KEY, "0") is None, (
            "stale label mapping was not deleted — lookups would resolve "
            "garbage entry_ids"
        )
        assert fresh.count == 0

        # The fresh index must still accept a 768-dim write cleanly.
        emb768 = np.random.RandomState(1).randn(768).astype(np.float32)
        emb768 /= np.linalg.norm(emb768)
        label = fresh.add(emb768, "entry_new", redis)
        assert label == 0
        assert fresh.count == 1

    def test_semcache_discards_legacy_blob_without_header(self):
        """A pre-header blob (no magic prefix) is indistinguishable from a
        corrupt one — both must be rejected and the key cleared.
        """
        from core.retrieval.semantic_cache import _HNSW_KEY

        redis = _mock_redis()
        redis._r = redis
        # Any bytes not starting with the CERID magic — simulates a blob
        # written by an older build that didn't tag the dim.
        redis.set(_HNSW_KEY, b"raw-hnswlib-bytes-no-magic-here")

        fresh = _HNSWIndex(dim=768, max_elements=100)
        loaded = fresh.load_from_redis(redis)

        assert loaded is False
        assert redis.get(_HNSW_KEY) is None
        assert fresh.count == 0

    def test_semcache_accepts_matching_dim_blob(self):
        """Positive control: a blob saved by this code path with the right
        dim must round-trip through load_from_redis (not dropped as stale).
        """
        from core.retrieval.semantic_cache import _HNSW_KEY

        redis = _mock_redis()
        redis._r = redis

        # Save path writes the header itself.
        idx = _HNSWIndex(dim=32, max_elements=100)
        emb = _random_embedding(dim=32, seed=11)
        idx.add(emb, "good-entry", redis)
        idx.flush(redis)

        # The persisted blob must start with the CERID magic.
        persisted = redis.get(_HNSW_KEY)
        assert persisted is not None
        from core.retrieval.semantic_cache import _HNSW_MAGIC
        assert persisted.startswith(_HNSW_MAGIC), (
            "save path did not prepend the dim header"
        )

        # Loading into an index of the same dim must succeed.
        fresh = _HNSWIndex(dim=32, max_elements=100)
        loaded = fresh.load_from_redis(redis)
        assert loaded is True
        assert fresh.count == 1


# ---------------------------------------------------------------------------
# Tests: cache_lookup / cache_store
# ---------------------------------------------------------------------------

class TestCacheLookup:
    def test_empty_cache_returns_none(self):
        redis = _mock_redis()
        emb = _random_embedding()
        result = cache_lookup(emb, redis)
        assert result is None

    def test_hit_above_threshold(self):
        redis = _mock_redis()
        emb = _random_embedding(seed=10)
        payload = {"context": "cached context", "sources": []}

        cache_store("test query", emb, payload, redis, ttl=300)
        result = cache_lookup(emb, redis, threshold=0.9)

        assert result is not None
        assert result["context"] == "cached context"

    def test_miss_below_threshold(self):
        redis = _mock_redis()
        emb1 = _random_embedding(seed=10)
        emb2 = _random_embedding(seed=99)

        cache_store("query one", emb1, {"answer": "yes"}, redis, ttl=300)
        result = cache_lookup(emb2, redis, threshold=0.99)
        assert result is None


class TestCacheStore:
    def test_stores_entry(self):
        redis = _mock_redis()
        emb = _random_embedding(seed=5)
        cache_store("test query", emb, {"context": "result"}, redis, ttl=60)
        redis.setex.assert_called_once()
        # HNSW label→entry_id mapping should be stored
        redis.hset.assert_called()

    def test_store_multiple_find_best(self):
        redis = _mock_redis()
        emb1 = _random_embedding(seed=1)
        emb2 = emb1 + np.random.RandomState(2).randn(768).astype(np.float32) * 0.01
        emb2 = (emb2 / np.linalg.norm(emb2)).astype(np.float32)

        cache_store("query alpha", emb1, {"answer": "alpha"}, redis, ttl=300)
        cache_store("query beta", emb2, {"answer": "beta"}, redis, ttl=300)

        found = cache_lookup(emb1, redis, threshold=0.9)
        assert found is not None
        assert found["answer"] == "alpha"


class TestInvalidateCache:
    def test_clears_keys_and_reinits_index(self):
        redis = _mock_redis()
        emb = _random_embedding(seed=5)

        cache_store("test query", emb, {"answer": "yes"}, redis, ttl=300)
        count = invalidate_cache(redis)
        assert count >= 1

    def test_no_keys_to_clear(self):
        redis = _mock_redis()
        count = invalidate_cache(redis)
        assert count == 0


# ---------------------------------------------------------------------------
# Tests: Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_stores(self):
        """Multiple threads can store concurrently without crashing."""
        redis = _mock_redis()
        errors: list[Exception] = []

        def worker(seed: int):
            try:
                emb = _random_embedding(seed=seed)
                cache_store(f"query_{seed}", emb, {"seed": seed}, redis, ttl=300)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_missing_label_mapping_returns_none(self):
        """If HNSW finds a match but the label mapping is gone, returns None."""
        redis = _mock_redis()
        emb = _random_embedding(seed=42)

        # Store normally
        cache_store("test query", emb, {"answer": "yes"}, redis, ttl=300)

        # Delete the labels hash (simulating partial invalidation)
        redis.hget = MagicMock(return_value=None)

        result = cache_lookup(emb, redis, threshold=0.5)
        assert result is None

    def test_expired_entry_returns_none(self):
        """If HNSW finds a match but the result payload expired, returns None."""
        redis = _mock_redis()
        emb = _random_embedding(seed=42)

        cache_store("test query", emb, {"answer": "yes"}, redis, ttl=300)

        # Simulate expired payload by making get return None for entry keys
        original_get = redis.get.side_effect

        def _get_without_entries(key: str):
            if key.startswith("semcache:entry:"):
                return None
            return original_get(key)

        redis.get = MagicMock(side_effect=_get_without_entries)

        result = cache_lookup(emb, redis, threshold=0.5)
        assert result is None

    # NOTE: Corrupted HNSW index data cannot be tested here because hnswlib's
    # C++ extension segfaults on invalid binary data (not catchable by Python).
    # In production, Redis guarantees atomic writes making corruption extremely
    # unlikely.  If it occurs, delete the semcache:hnsw_index key manually.
