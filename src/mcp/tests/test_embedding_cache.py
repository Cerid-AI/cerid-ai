# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``core/utils/embedding_cache.py``.

Covers the LRU contract, namespace isolation (Quenchforge nomic vs ONNX
Arctic must not share entries), bounded-size eviction, and the disable
path (``CERID_EMBED_CACHE_SIZE=0``).
"""

from __future__ import annotations

import threading

import numpy as np
import pytest

from core.utils.embedding_cache import (
    EmbeddingCache,
    PersistentEmbeddingCache,
    _reset_singleton_for_testing,
    get_embedding_cache,
)


@pytest.fixture(autouse=True)
def _isolate_singleton(monkeypatch):
    """Each test gets a fresh singleton so env-var changes take effect."""
    _reset_singleton_for_testing()
    yield
    _reset_singleton_for_testing()


class TestEmbeddingCache:
    def test_miss_returns_none(self):
        cache = EmbeddingCache(max_size=10)
        assert cache.get("ns", "hello") is None

    def test_put_then_get_round_trip(self):
        cache = EmbeddingCache(max_size=10)
        vec = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        cache.put("ns", "hello", vec)
        result = cache.get("ns", "hello")
        assert result is not None
        np.testing.assert_array_equal(result, vec)

    def test_put_stores_contiguous_float32_copy(self):
        cache = EmbeddingCache(max_size=10)
        parent = np.zeros((3, 4), dtype=np.float64)
        parent[1, :] = [1.0, 2.0, 3.0, 4.0]
        view = parent[1, :]  # non-contiguous when ndim>1, view at minimum
        cache.put("ns", "k", view)
        # Mutate the source; cache must be insulated.
        parent[1, 0] = 99.0
        cached = cache.get("ns", "k")
        assert cached is not None
        assert cached.dtype == np.float32
        assert cached.flags["C_CONTIGUOUS"]
        np.testing.assert_array_equal(cached, [1.0, 2.0, 3.0, 4.0])

    def test_namespace_isolation(self):
        """Two namespaces with the same text must keep separate entries."""
        cache = EmbeddingCache(max_size=10)
        cache.put("qf:nomic", "hello", np.array([1.0], dtype=np.float32))
        cache.put("onnx:arctic", "hello", np.array([2.0], dtype=np.float32))
        assert cache.get("qf:nomic", "hello")[0] == 1.0
        assert cache.get("onnx:arctic", "hello")[0] == 2.0

    def test_lru_eviction_oldest_first(self):
        cache = EmbeddingCache(max_size=2)
        cache.put("ns", "a", np.array([1.0], dtype=np.float32))
        cache.put("ns", "b", np.array([2.0], dtype=np.float32))
        cache.put("ns", "c", np.array([3.0], dtype=np.float32))
        # "a" was inserted first and not touched → evicted.
        assert cache.get("ns", "a") is None
        assert cache.get("ns", "b") is not None
        assert cache.get("ns", "c") is not None

    def test_get_promotes_to_mru(self):
        cache = EmbeddingCache(max_size=2)
        cache.put("ns", "a", np.array([1.0], dtype=np.float32))
        cache.put("ns", "b", np.array([2.0], dtype=np.float32))
        # Touch "a" so it becomes MRU.
        assert cache.get("ns", "a") is not None
        cache.put("ns", "c", np.array([3.0], dtype=np.float32))
        # "b" was LRU → evicted; "a" survived.
        assert cache.get("ns", "a") is not None
        assert cache.get("ns", "b") is None
        assert cache.get("ns", "c") is not None

    def test_repeat_put_overwrites_and_promotes(self):
        cache = EmbeddingCache(max_size=2)
        cache.put("ns", "a", np.array([1.0], dtype=np.float32))
        cache.put("ns", "b", np.array([2.0], dtype=np.float32))
        cache.put("ns", "a", np.array([99.0], dtype=np.float32))  # overwrite
        cache.put("ns", "c", np.array([3.0], dtype=np.float32))
        # "b" was LRU after "a"'s overwrite-promote → evicted.
        assert cache.get("ns", "b") is None
        assert cache.get("ns", "a")[0] == 99.0
        assert cache.get("ns", "c") is not None

    def test_disabled_cache_no_ops(self):
        cache = EmbeddingCache(max_size=0)
        cache.put("ns", "a", np.array([1.0], dtype=np.float32))
        assert cache.get("ns", "a") is None
        assert cache.stats()["size"] == 0

    def test_stats_track_hits_and_misses(self):
        cache = EmbeddingCache(max_size=10)
        cache.put("ns", "a", np.array([1.0], dtype=np.float32))
        cache.get("ns", "a")        # hit
        cache.get("ns", "missing")  # miss
        cache.get("ns", "a")        # hit
        s = cache.stats()
        assert s["hits"] == 2
        assert s["misses"] == 1
        assert s["size"] == 1
        assert s["hit_rate"] == pytest.approx(2 / 3)

    def test_clear_resets_counters(self):
        cache = EmbeddingCache(max_size=10)
        cache.put("ns", "a", np.array([1.0], dtype=np.float32))
        cache.get("ns", "a")
        cache.clear()
        s = cache.stats()
        assert s == {
            "hits": 0,
            "misses": 0,
            "size": 0,
            "max_size": 10,
            "hit_rate": 0.0,
        }

    def test_thread_safety_concurrent_writers(self):
        """Concurrent put/get should not raise or deadlock."""
        cache = EmbeddingCache(max_size=100)
        errors: list[Exception] = []

        def worker(start: int):
            try:
                for i in range(start, start + 50):
                    cache.put("ns", str(i), np.array([float(i)], dtype=np.float32))
                    cache.get("ns", str(i))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i * 50,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
        assert errors == []
        # Cache stays within bound.
        assert cache.stats()["size"] <= 100


class TestSingleton:
    def test_env_var_controls_size(self, monkeypatch):
        monkeypatch.setenv("CERID_EMBED_CACHE_SIZE", "7")
        cache = get_embedding_cache()
        assert cache.stats()["max_size"] == 7

    def test_invalid_env_var_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("CERID_EMBED_CACHE_SIZE", "not-a-number")
        cache = get_embedding_cache()
        assert cache.stats()["max_size"] == 50000

    def test_zero_size_disables(self, monkeypatch):
        monkeypatch.setenv("CERID_EMBED_CACHE_SIZE", "0")
        cache = get_embedding_cache()
        cache.put("ns", "x", np.array([1.0], dtype=np.float32))
        assert cache.get("ns", "x") is None

    def test_disk_path_env_promotes_to_persistent(self, monkeypatch, tmp_path):
        """Setting CERID_EMBED_CACHE_PATH must give a persistent cache."""
        monkeypatch.setenv("CERID_EMBED_CACHE_PATH", str(tmp_path / "cache.db"))
        cache = get_embedding_cache()
        assert isinstance(cache, PersistentEmbeddingCache)

    def test_empty_disk_path_stays_memory_only(self, monkeypatch):
        """Default empty CERID_EMBED_CACHE_PATH is a plain memory cache."""
        monkeypatch.setenv("CERID_EMBED_CACHE_PATH", "")
        cache = get_embedding_cache()
        assert isinstance(cache, EmbeddingCache)
        assert not isinstance(cache, PersistentEmbeddingCache)


class TestPersistentEmbeddingCache:
    def test_put_then_get_uses_memory_first(self, tmp_path):
        cache = PersistentEmbeddingCache(
            max_size=10, db_path=tmp_path / "cache.db",
        )
        vec = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        cache.put("ns", "hello", vec)
        # Memory hit (the entry was just inserted in memory).
        result = cache.get("ns", "hello")
        assert result is not None
        np.testing.assert_array_equal(result, vec)
        s = cache.stats()
        assert s["hits"] == 1
        # No disk hit needed — memory served.
        assert s["disk_hits"] == 0

    def test_disk_survives_process_reset(self, tmp_path):
        """Build a cache, put a vector, drop it, build a new one — the
        vector is still retrievable from disk in the new process."""
        db = tmp_path / "cache.db"
        # Process 1: write
        c1 = PersistentEmbeddingCache(max_size=10, db_path=db)
        vec = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        c1.put("qf:nomic", "long-text", vec)
        del c1
        # Process 2: read
        c2 = PersistentEmbeddingCache(max_size=10, db_path=db)
        # Memory tier is fresh — first get must come from disk.
        result = c2.get("qf:nomic", "long-text")
        assert result is not None
        np.testing.assert_array_equal(result, vec)
        s = c2.stats()
        # The disk hit counter went up.
        assert s["disk_hits"] == 1
        # And the memory tier got promoted.
        assert s["size"] == 1

    def test_namespace_isolation_on_disk(self, tmp_path):
        """Different backends share the same DB but never cross-contaminate."""
        cache = PersistentEmbeddingCache(
            max_size=10, db_path=tmp_path / "cache.db",
        )
        cache.put("qf:nomic", "hello", np.array([1.0], dtype=np.float32))
        cache.put("onnx:arctic", "hello", np.array([2.0], dtype=np.float32))
        # Clear memory so disk is consulted.
        cache.clear()
        a = cache.get("qf:nomic", "hello")
        b = cache.get("onnx:arctic", "hello")
        assert a is not None and a[0] == 1.0
        assert b is not None and b[0] == 2.0

    def test_disk_disabled_falls_back_to_memory_only(self, tmp_path):
        """A bad parent path leaves the cache disk-disabled but functional."""
        # /dev/null/cache.db — parent isn't a directory, mkdir will fail.
        cache = PersistentEmbeddingCache(
            max_size=10, db_path="/dev/null/cache.db",
        )
        assert cache.stats()["disk_enabled"] is False
        # Memory tier still works.
        cache.put("ns", "x", np.array([1.0], dtype=np.float32))
        assert cache.get("ns", "x") is not None

    def test_disk_miss_does_not_promote(self, tmp_path):
        """A disk miss must NOT add a None row to memory."""
        cache = PersistentEmbeddingCache(
            max_size=10, db_path=tmp_path / "cache.db",
        )
        result = cache.get("ns", "never-stored")
        assert result is None
        s = cache.stats()
        assert s["size"] == 0
        assert s["disk_misses"] == 1

    def test_overwrite_on_disk(self, tmp_path):
        """Re-putting an existing key updates both tiers."""
        cache = PersistentEmbeddingCache(
            max_size=10, db_path=tmp_path / "cache.db",
        )
        cache.put("ns", "k", np.array([1.0], dtype=np.float32))
        cache.put("ns", "k", np.array([99.0], dtype=np.float32))
        # Reset memory; disk should hold the latest.
        cache.clear()
        result = cache.get("ns", "k")
        assert result is not None
        assert result[0] == 99.0
