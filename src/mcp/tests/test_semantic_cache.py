# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the chromadb-collection-backed semantic cache."""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from core.retrieval.semantic_cache import (
    cache_lookup,
    cache_store,
    flush_cache,
    invalidate_cache,
    set_cache_backend,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _random_embedding(dim: int = 768, seed: int = 42) -> np.ndarray:
    rng = np.random.RandomState(seed)
    emb = rng.randn(dim).astype(np.float32)
    return emb / np.linalg.norm(emb)


def _mock_redis() -> MagicMock:
    """Mock Redis with KV + scan; matches the contract semantic_cache uses."""
    store: dict[str, str] = {}

    mock = MagicMock()

    def _get(key: str):
        return store.get(key)

    def _setex(key: str, ttl: int, value: str):
        store[key] = value

    def _delete(*keys: str):
        n = 0
        for k in keys:
            if k in store:
                del store[k]
                n += 1
        return n

    def _scan(cursor: int, match: str = "", count: int = 100):
        import fnmatch
        matched = [k for k in store if fnmatch.fnmatch(k, match)]
        return (0, matched)

    mock.get = MagicMock(side_effect=_get)
    mock.setex = MagicMock(side_effect=_setex)
    mock.delete = MagicMock(side_effect=_delete)
    mock.scan = MagicMock(side_effect=_scan)

    return mock


class _FakeBackend:
    """Duck-typed _CacheBackend for tests.

    Stores (id, embedding, metadata) and answers `query` via brute-force
    cosine similarity — fast enough at test scales, lets us assert exact
    nearest-neighbour returns without depending on chromadb.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ids: list[str] = []
        self._embs: list[np.ndarray] = []
        self._meta: list[dict[str, Any]] = []

    def upsert(self, *, ids, embeddings, metadatas=None) -> None:
        with self._lock:
            metas = metadatas or [{} for _ in ids]
            for i, (eid, emb, m) in enumerate(zip(ids, embeddings, metas)):
                arr = np.asarray(emb, dtype=np.float32)
                if eid in self._ids:
                    j = self._ids.index(eid)
                    self._embs[j] = arr
                    self._meta[j] = m
                else:
                    self._ids.append(eid)
                    self._embs.append(arr)
                    self._meta.append(m)

    def query(self, *, query_embeddings, n_results=1, include=None):
        with self._lock:
            if not self._ids:
                return {"ids": [[]], "distances": [[]]}
            q = np.asarray(query_embeddings[0], dtype=np.float32)
            qn = q / max(np.linalg.norm(q), 1e-12)
            sims: list[tuple[str, float]] = []
            for eid, emb in zip(self._ids, self._embs):
                en = emb / max(np.linalg.norm(emb), 1e-12)
                sims.append((eid, float(np.dot(qn, en))))
            sims.sort(key=lambda t: t[1], reverse=True)
            top = sims[: n_results]
            return {
                "ids": [[t[0] for t in top]],
                "distances": [[1.0 - t[1] for t in top]],
            }

    def delete(self, ids=None, where=None) -> None:
        with self._lock:
            if where is not None and not ids:
                # Empty-where == clear all (mirrors chromadb 0.5+ semantics)
                self._ids.clear()
                self._embs.clear()
                self._meta.clear()
                return
            if ids:
                for eid in ids:
                    if eid in self._ids:
                        j = self._ids.index(eid)
                        del self._ids[j]
                        del self._embs[j]
                        del self._meta[j]

    def count(self) -> int:
        with self._lock:
            return len(self._ids)


@pytest.fixture(autouse=True)
def _reset_backend():
    """Each test gets a fresh backend; cleared after."""
    backend = _FakeBackend()
    set_cache_backend(backend)
    yield backend
    set_cache_backend(None)


# ---------------------------------------------------------------------------
# Tests: cache_lookup
# ---------------------------------------------------------------------------

class TestCacheLookup:
    def test_empty_cache_returns_none(self):
        redis = _mock_redis()
        emb = _random_embedding()
        assert cache_lookup(emb, redis) is None

    def test_hit_above_threshold(self, _reset_backend):
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
        assert cache_lookup(emb2, redis, threshold=0.99) is None

    def test_orphan_evicted_when_payload_expired(self, _reset_backend):
        """Index entry whose Redis payload TTL'd should be lazy-deleted."""
        redis = _mock_redis()
        emb = _random_embedding(seed=7)

        cache_store("ephemeral", emb, {"answer": "yes"}, redis, ttl=300)
        assert _reset_backend.count() == 1

        # Simulate Redis payload expiry: GET returns None for any entry
        # key. The index entry is still present; lookup must return None
        # AND delete the orphan.
        redis.get = MagicMock(return_value=None)

        assert cache_lookup(emb, redis, threshold=0.5) is None
        assert _reset_backend.count() == 0, "orphan was not evicted from index"


# ---------------------------------------------------------------------------
# Tests: cache_store
# ---------------------------------------------------------------------------

class TestCacheStore:
    def test_stores_payload_and_index(self, _reset_backend):
        redis = _mock_redis()
        emb = _random_embedding(seed=5)
        cache_store("test query", emb, {"context": "result"}, redis, ttl=60)

        redis.setex.assert_called_once()
        assert _reset_backend.count() == 1

    def test_store_multiple_find_best(self, _reset_backend):
        redis = _mock_redis()
        emb1 = _random_embedding(seed=1)
        emb2 = emb1 + np.random.RandomState(2).randn(768).astype(np.float32) * 0.01
        emb2 = (emb2 / np.linalg.norm(emb2)).astype(np.float32)

        cache_store("query alpha", emb1, {"answer": "alpha"}, redis, ttl=300)
        cache_store("query beta", emb2, {"answer": "beta"}, redis, ttl=300)

        found = cache_lookup(emb1, redis, threshold=0.9)
        assert found is not None
        assert found["answer"] == "alpha"

    def test_idempotent_same_query(self, _reset_backend):
        """Storing the same query twice upserts (no duplicate index entries)."""
        redis = _mock_redis()
        emb = _random_embedding(seed=3)
        cache_store("dup query", emb, {"v": 1}, redis, ttl=300)
        cache_store("dup query", emb, {"v": 2}, redis, ttl=300)
        assert _reset_backend.count() == 1


# ---------------------------------------------------------------------------
# Tests: invalidate_cache
# ---------------------------------------------------------------------------

class TestInvalidateCache:
    def test_clears_keys_and_index(self, _reset_backend):
        redis = _mock_redis()
        emb = _random_embedding(seed=5)

        cache_store("test query", emb, {"answer": "yes"}, redis, ttl=300)
        count = invalidate_cache(redis)

        assert count >= 1
        assert _reset_backend.count() == 0

    def test_no_keys_to_clear(self):
        redis = _mock_redis()
        assert invalidate_cache(redis) == 0


# ---------------------------------------------------------------------------
# Tests: flush_cache (now a no-op)
# ---------------------------------------------------------------------------

class TestFlushCache:
    def test_flush_is_safe_noop(self):
        """Public API hinge — must accept any redis_client without error."""
        flush_cache(MagicMock())
        flush_cache(None)


# ---------------------------------------------------------------------------
# Tests: backend disabled / unset
# ---------------------------------------------------------------------------

class TestBackendDisabled:
    def test_lookup_returns_none_when_backend_unregistered(self):
        set_cache_backend(None)
        assert cache_lookup(_random_embedding(), _mock_redis()) is None

    def test_store_is_noop_when_backend_unregistered(self):
        set_cache_backend(None)
        redis = _mock_redis()
        cache_store("q", _random_embedding(), {"a": 1}, redis, ttl=60)
        # Payload write is gated on the backend too — not just the index.
        redis.setex.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_stores(self):
        redis = _mock_redis()
        errors: list[Exception] = []

        def worker(seed: int):
            try:
                emb = _random_embedding(seed=seed)
                cache_store(f"query_{seed}", emb, {"seed": seed}, redis, ttl=300)
            except Exception as e:
                from core.utils.swallowed import log_swallowed_error
                log_swallowed_error('tests.test_semantic_cache', e)
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"


# ---------------------------------------------------------------------------
# Tests: Sentry capture (observability boundary)
# ---------------------------------------------------------------------------

class TestSentryCapture:
    def test_lookup_failure_captured(self):
        """cache_lookup must report unexpected backend errors to Sentry."""
        emb = np.random.randn(768).astype(np.float32)
        emb /= np.linalg.norm(emb)

        bad_backend = MagicMock()
        bad_backend.count.side_effect = RuntimeError("backend gone")
        set_cache_backend(bad_backend)

        with patch("sentry_sdk.capture_exception") as mock_capture:
            result = cache_lookup(emb, MagicMock(), threshold=0.5)

        assert result is None
        mock_capture.assert_called_once()
