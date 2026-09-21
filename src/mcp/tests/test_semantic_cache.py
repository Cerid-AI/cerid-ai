# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the chromadb-collection-backed semantic cache."""

from __future__ import annotations

import json
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import fakeredis
import numpy as np
import pytest

from core.context.identity import tenant_id_var
from core.retrieval.semantic_cache import (
    _LAST_INVALIDATED_KEY,
    _scope_token,
    cache_lookup,
    cache_store,
    flush_cache,
    invalidate_cache,
    invalidate_cache_non_blocking,
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

    def get(self) -> dict:
        with self._lock:
            return {"ids": list(self._ids)}

    def delete(self, ids=None, where=None) -> None:
        with self._lock:
            if where is not None and not ids:
                # chromadb 1.x REJECTS an empty `where` rather than treating it
                # as clear-all. The fake previously mirrored the 0.5 semantics,
                # which let the production clear-all path throw on every
                # mutation while these tests stayed green. Match the real server.
                if not where:
                    raise ValueError(
                        "Expected where to have exactly one operator, got {} in delete"
                    )
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
        payload = {"context": "cached context", "sources": [{"filename": "c.md"}]}

        cache_store("test query", emb, payload, redis, ttl=300)
        result = cache_lookup(emb, redis, threshold=0.9)

        assert result is not None
        assert result["context"] == "cached context"

    def test_empty_and_degraded_results_never_stored(self, _reset_backend):
        """Live-caught 2026-07-13: a budget-degraded empty was cached and
        re-served at sim=1.0, poisoning every similar query for the TTL."""
        redis = _mock_redis()
        emb = _random_embedding(seed=11)
        cache_store("empty q", emb, {"context": "", "sources": []}, redis, ttl=300)
        cache_store(
            "degraded q", emb,
            {"sources": [{"filename": "x.md"}], "budget_exceeded": True},
            redis, ttl=300,
        )
        assert _reset_backend.count() == 0
        redis.setex.assert_not_called()

    def test_domain_scope_mismatch_is_a_miss(self, _reset_backend):
        """Same query text under a different domain filter must not hit."""
        redis = _mock_redis()
        emb = _random_embedding(seed=12)
        payload = {"context": "coding result", "sources": [{"filename": "c.md"}]}
        cache_store("scoped q", emb, payload, redis, ttl=300, domains=["coding"])

        assert cache_lookup(emb, redis, threshold=0.9, domains=["notes"]) is None
        assert cache_lookup(emb, redis, threshold=0.9) is None  # all-domains scope
        hit = cache_lookup(emb, redis, threshold=0.9, domains=["coding"])
        assert hit is not None and hit["context"] == "coding result"

    def test_miss_below_threshold(self):
        redis = _mock_redis()
        emb1 = _random_embedding(seed=10)
        emb2 = _random_embedding(seed=99)

        cache_store("query one", emb1, {"answer": "yes", "sources": [{"filename": "y.md"}]}, redis, ttl=300)
        assert cache_lookup(emb2, redis, threshold=0.99) is None

    def test_stale_hit_is_not_served(self, _reset_backend):
        """CR-046: a candidate stored before the last invalidation watermark must
        NOT be served — it's evicted and treated as a miss (the invalidation
        SCAN/delete missed it)."""
        redis = _mock_redis()
        emb = _random_embedding(seed=21)
        cache_store("stale q", emb, {"context": "old answer", "sources": [{"filename": "x.md"}]}, redis, ttl=300)
        # Serves fine before any invalidation.
        assert cache_lookup(emb, redis, threshold=0.9) is not None

        # Stamp an invalidation watermark AFTER the entry was stored.
        redis.setex(_LAST_INVALIDATED_KEY, 9999, str(time.time() + 1000))

        # The stale entry must not be served, and it's evicted from the index.
        assert cache_lookup(emb, redis, threshold=0.9) is None
        assert _reset_backend.count() == 0, "stale entry was not evicted"

    def test_orphan_evicted_when_payload_expired(self, _reset_backend):
        """Index entry whose Redis payload TTL'd should be lazy-deleted."""
        redis = _mock_redis()
        emb = _random_embedding(seed=7)

        cache_store("ephemeral", emb, {"answer": "yes", "sources": [{"filename": "y.md"}]}, redis, ttl=300)
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
        cache_store("test query", emb, {"context": "result", "sources": [{"filename": "a.md"}]}, redis, ttl=60)

        redis.setex.assert_called_once()
        assert _reset_backend.count() == 1

    def test_store_multiple_find_best(self, _reset_backend):
        redis = _mock_redis()
        emb1 = _random_embedding(seed=1)
        emb2 = emb1 + np.random.RandomState(2).randn(768).astype(np.float32) * 0.01
        emb2 = (emb2 / np.linalg.norm(emb2)).astype(np.float32)

        cache_store("query alpha", emb1, {"answer": "alpha", "sources": [{"filename": "alpha.md"}]}, redis, ttl=300)
        cache_store("query beta", emb2, {"answer": "beta", "sources": [{"filename": "beta.md"}]}, redis, ttl=300)

        found = cache_lookup(emb1, redis, threshold=0.9)
        assert found is not None
        assert found["answer"] == "alpha"

    def test_idempotent_same_query(self, _reset_backend):
        """Storing the same query twice upserts (no duplicate index entries)."""
        redis = _mock_redis()
        emb = _random_embedding(seed=3)
        cache_store("dup query", emb, {"v": 1, "sources": [{"filename": "v.md"}]}, redis, ttl=300)
        cache_store("dup query", emb, {"v": 2, "sources": [{"filename": "v.md"}]}, redis, ttl=300)
        assert _reset_backend.count() == 1


# ---------------------------------------------------------------------------
# Tests: invalidate_cache
# ---------------------------------------------------------------------------

class TestInvalidateCache:
    def test_clears_keys_and_index(self, _reset_backend):
        redis = _mock_redis()
        emb = _random_embedding(seed=5)

        cache_store("test query", emb, {"answer": "yes", "sources": [{"filename": "y.md"}]}, redis, ttl=300)
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


# ---------------------------------------------------------------------------
# Tests: invalidate_cache_non_blocking (Phase 2.2 fire-and-forget wrapper)
# ---------------------------------------------------------------------------

class TestInvalidateCacheNonBlocking:
    """The ingest hook sites (app/services/ingestion.py) are synchronous
    functions with no reliable running event loop, so this wrapper uses a
    daemon thread rather than the asyncio create_task idiom the flat query
    cache uses."""

    def test_spawns_daemon_thread_targeting_invalidate_cache(self):
        redis = _mock_redis()
        with patch("core.retrieval.semantic_cache.threading.Thread") as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread

            invalidate_cache_non_blocking(redis, trigger="test-trigger")

            mock_thread_cls.assert_called_once()
            _, kwargs = mock_thread_cls.call_args
            assert kwargs["target"] is invalidate_cache
            assert kwargs["args"] == (redis, "test-trigger")
            assert kwargs["daemon"] is True
            mock_thread.start.assert_called_once()

    def test_actually_invalidates_when_run(self, _reset_backend):
        """End-to-end: let a real thread run, joined deterministically."""
        redis = _mock_redis()
        emb = _random_embedding(seed=20)
        cache_store(
            "bg query", emb, {"answer": "yes", "sources": [{"filename": "y.md"}]},
            redis, ttl=300,
        )
        assert _reset_backend.count() == 1

        created_threads: list[threading.Thread] = []
        real_thread_cls = threading.Thread

        def _capture_thread(*args, **kwargs):
            t = real_thread_cls(*args, **kwargs)
            created_threads.append(t)
            return t

        with patch(
            "core.retrieval.semantic_cache.threading.Thread", side_effect=_capture_thread,
        ):
            invalidate_cache_non_blocking(redis)

        assert len(created_threads) == 1
        created_threads[0].join(timeout=5)
        assert _reset_backend.count() == 0


# ---------------------------------------------------------------------------
# Tests: invalidation metric (entries dropped + trigger source)
# ---------------------------------------------------------------------------

class TestInvalidationMetric:
    def test_records_count_and_trigger(self, _reset_backend):
        redis = fakeredis.FakeRedis(decode_responses=True)
        emb = _random_embedding(seed=21)
        cache_store(
            "metric query", emb, {"answer": "y", "sources": [{"filename": "y.md"}]},
            redis, ttl=300,
        )

        count = invalidate_cache(redis, trigger="kb_admin.clear_domain")

        assert count >= 1
        raw = redis.zrange("cerid:metrics:cache_invalidation_count", 0, -1)
        assert len(raw) == 1
        payload = json.loads(raw[0])
        assert payload["v"] == float(count)
        assert payload["t"]["trigger"] == "kb_admin.clear_domain"

    def test_default_trigger_is_unspecified(self, _reset_backend):
        redis = fakeredis.FakeRedis(decode_responses=True)
        invalidate_cache(redis)

        raw = redis.zrange("cerid:metrics:cache_invalidation_count", 0, -1)
        payload = json.loads(raw[0])
        assert payload["t"]["trigger"] == "unspecified"


# ---------------------------------------------------------------------------
# Tests: stale-hit detection
# ---------------------------------------------------------------------------

class TestStaleHitDetection:
    _WATERMARK_KEY = "semcache_meta:last_invalidated_at"

    def test_watermark_written_on_invalidation(self, _reset_backend):
        redis = fakeredis.FakeRedis(decode_responses=True)
        assert redis.get(self._WATERMARK_KEY) is None

        invalidate_cache(redis)

        watermark = redis.get(self._WATERMARK_KEY)
        assert watermark is not None
        assert float(watermark) > 0

    def test_watermark_key_survives_its_own_invalidation_scan(self, _reset_backend):
        """The watermark must NOT be swept by invalidate_cache's own
        semcache:* SCAN, or it could never accumulate history."""
        redis = fakeredis.FakeRedis(decode_responses=True)
        invalidate_cache(redis)
        first = redis.get(self._WATERMARK_KEY)

        invalidate_cache(redis)
        second = redis.get(self._WATERMARK_KEY)

        assert first is not None and second is not None
        assert float(second) >= float(first)

    def test_hit_surviving_invalidation_is_flagged_stale(self, _reset_backend):
        """Simulate the race invalidate_cache guards against: an entry
        whose stored_at predates the watermark but still answers a lookup
        (e.g. it was written after invalidate_cache's SCAN paged past it,
        or the backend's delete(where={}) silently failed)."""
        redis = fakeredis.FakeRedis(decode_responses=True)
        emb = _random_embedding(seed=22)

        # A watermark from "the future" relative to the entry below proves
        # the entry predates the last known invalidation.
        redis.setex(self._WATERMARK_KEY, 3600, str(time.time() + 10))

        stale_payload = {
            "domain_scope": "__all__",
            "result": {"answer": "stale", "sources": [{"filename": "s.md"}]},
            "stored_at": time.time(),
        }
        entry_id = "deadbeefcafefeed"
        redis.setex(f"semcache:entry:{entry_id}", 300, json.dumps(stale_payload))
        _reset_backend.upsert(
            ids=[entry_id],
            embeddings=[emb.tolist()],
            metadatas=[{"created_at": time.time(), "domain_scope": "__all__"}],
        )

        result = cache_lookup(emb, redis, threshold=0.5)

        # CR-046: a stale hit is now suppressed, not served — but the metric still
        # fires so the missed invalidation remains observable.
        assert result is None
        raw = redis.zrange("cerid:metrics:cache_stale_hit_count", 0, -1)
        assert len(raw) == 1

    def test_fresh_hit_after_invalidation_is_not_flagged_stale(self, _reset_backend):
        redis = fakeredis.FakeRedis(decode_responses=True)
        invalidate_cache(redis)  # sets the watermark

        emb = _random_embedding(seed=23)
        cache_store(
            "fresh query", emb, {"answer": "fresh", "sources": [{"filename": "f.md"}]},
            redis, ttl=300,
        )

        result = cache_lookup(emb, redis, threshold=0.9)

        assert result is not None
        raw = redis.zrange("cerid:metrics:cache_stale_hit_count", 0, -1)
        assert raw == []

    def test_no_watermark_yet_skips_check(self, _reset_backend):
        """Before any invalidation has ever run, there's nothing to compare
        against — must not misflag every hit as stale."""
        redis = fakeredis.FakeRedis(decode_responses=True)
        emb = _random_embedding(seed=24)
        cache_store(
            "no watermark query", emb, {"answer": "y", "sources": [{"filename": "y.md"}]},
            redis, ttl=300,
        )

        result = cache_lookup(emb, redis, threshold=0.9)

        assert result is not None
        raw = redis.zrange("cerid:metrics:cache_stale_hit_count", 0, -1)
        assert raw == []


# ---------------------------------------------------------------------------
# Tests: size-bound enforcement (Phase 2.2 — max_entries was a dead knob)
# ---------------------------------------------------------------------------

class TestDomainScopedInvalidation:
    """Task 7: an ingest into domain D must evict only entries whose result
    touched D, not the whole cache (the ``cache_hit_rate 0.0`` bug — every
    ingest previously called ``invalidate_cache`` with no domain and flushed
    everything)."""

    @staticmethod
    def _warm_legacy_sentinel(redis: Any) -> None:
        """Consume the one-time legacy-sweep fallback on an empty cache so a
        test can exercise the steady-state domain-indexed eviction path."""
        invalidate_cache(redis, domain="_warmup_")

    def test_ingest_domain_evicts_matching_entry(self, _reset_backend):
        redis = fakeredis.FakeRedis(decode_responses=True)
        self._warm_legacy_sentinel(redis)
        emb = _random_embedding(seed=40)
        cache_store(
            "finance query", emb,
            {"answer": "f", "sources": [{"filename": "f.md"}]},
            redis, ttl=300, domains=["finance"], domains_searched=["finance"],
        )

        count = invalidate_cache(redis, trigger="ingestion.ingest_content", domain="finance")

        assert count == 1
        assert cache_lookup(emb, redis, threshold=0.9, domains=["finance"]) is None

    def test_ingest_domain_keeps_entry_that_searched_other_domain(self, _reset_backend):
        redis = fakeredis.FakeRedis(decode_responses=True)
        self._warm_legacy_sentinel(redis)
        emb_finance = _random_embedding(seed=41)
        emb_coding = _random_embedding(seed=42)
        cache_store(
            "finance query", emb_finance,
            {"answer": "f", "sources": [{"filename": "f.md"}]},
            redis, ttl=300, domains=["finance"], domains_searched=["finance"],
        )
        cache_store(
            "coding query", emb_coding,
            {"answer": "c", "sources": [{"filename": "c.md"}]},
            redis, ttl=300, domains=["coding"], domains_searched=["coding"],
        )

        invalidate_cache(redis, domain="finance")

        assert cache_lookup(emb_finance, redis, threshold=0.9, domains=["finance"]) is None
        hit = cache_lookup(emb_coding, redis, threshold=0.9, domains=["coding"])
        assert hit is not None and hit["answer"] == "c"

    def test_legacy_entry_without_domains_evicted_on_any_invalidation(self, _reset_backend):
        """An entry stored without ``domains_searched`` (written before this
        feature shipped) carries no domain-index membership and can't be
        found selectively — the first domain-scoped invalidation after
        deploy falls back to a full flush specifically to retire these, then
        never needs to again."""
        redis = fakeredis.FakeRedis(decode_responses=True)
        emb = _random_embedding(seed=43)
        cache_store(
            "legacy query", emb, {"answer": "l", "sources": [{"filename": "l.md"}]},
            redis, ttl=300, domains=["finance"],
        )

        # Domain-scoped invalidation for an UNRELATED domain still evicts it.
        # (count includes the full flush's other bookkeeping keys, e.g. the
        # age index — mirrors TestInvalidateCache's `count >= 1` convention.)
        count = invalidate_cache(redis, domain="coding")

        assert count >= 1
        assert cache_lookup(emb, redis, threshold=0.9, domains=["finance"]) is None

    def test_domain_invalidation_is_o_of_entries_touching_domain(self, _reset_backend):
        """A domain-scoped invalidation must not touch entries in other
        domains' indexes — assert via the domain-index membership left
        behind, not just via cache_lookup."""
        redis = fakeredis.FakeRedis(decode_responses=True)
        self._warm_legacy_sentinel(redis)
        emb_a = _random_embedding(seed=44)
        emb_b = _random_embedding(seed=45)
        cache_store(
            "a", emb_a, {"answer": "a", "sources": [{"filename": "a.md"}]},
            redis, ttl=300, domains=["finance"], domains_searched=["finance"],
        )
        cache_store(
            "b", emb_b, {"answer": "b", "sources": [{"filename": "b.md"}]},
            redis, ttl=300, domains=["coding"], domains_searched=["coding"],
        )

        invalidate_cache(redis, domain="finance")

        assert redis.scard("semcache:domain_index:finance") == 0
        assert redis.scard("semcache:domain_index:coding") == 1

    def test_unrestricted_entry_evicted_by_ingest_into_any_searched_domain(self, _reset_backend):
        """An entry computed with no domain restriction records
        ``domains_searched`` as every configured domain (the
        ``list(config.DOMAINS)`` fallback in ``query_agent.py``) — an ingest
        into any ONE of those domains must still evict it."""
        import config

        redis = fakeredis.FakeRedis(decode_responses=True)
        self._warm_legacy_sentinel(redis)
        emb = _random_embedding(seed=46)
        cache_store(
            "unrestricted query", emb,
            {"answer": "u", "sources": [{"filename": "u.md"}]},
            redis, ttl=300, domains_searched=list(config.DOMAINS),
        )

        picked_domain = config.DOMAINS[len(config.DOMAINS) // 2]
        invalidate_cache(redis, domain=picked_domain)

        assert cache_lookup(emb, redis, threshold=0.9) is None


class TestDomainIndexEvictionBounds:
    """Fix round 1 — the per-domain index must not grow without bound: FIFO
    eviction and domain-scoped invalidation must both prune the id out of
    every domain index it was registered under, not just leave dangling
    members behind for a domain that's queried but rarely ingested into."""

    @staticmethod
    def _warm_legacy_sentinel(redis: Any) -> None:
        invalidate_cache(redis, domain="_warmup_")

    def test_fifo_eviction_prunes_domain_index(self, _reset_backend):
        redis = fakeredis.FakeRedis(decode_responses=True)
        emb0 = _random_embedding(seed=210)
        emb1 = _random_embedding(seed=211)

        cache_store(
            "bound q0", emb0, {"answer": "0", "sources": [{"filename": "0.md"}]},
            redis, ttl=300, domains_searched=["finance"], max_entries=1,
        )
        cache_store(
            "bound q1", emb1, {"answer": "1", "sources": [{"filename": "1.md"}]},
            redis, ttl=300, domains_searched=["finance"], max_entries=1,
        )

        # q0 was FIFO-evicted to stay within max_entries=1 — its id must be
        # gone from the domain index too, not just the payload/backend.
        assert _reset_backend.count() == 1
        assert redis.scard("semcache:domain_index:finance") == 1

    def test_dangling_member_pruned_and_not_double_counted(self, _reset_backend):
        """A member whose payload already TTL-expired is dangling — a domain
        invalidation must drop it from the index but not report it as a
        fresh eviction (it was already gone)."""
        redis = fakeredis.FakeRedis(decode_responses=True)
        self._warm_legacy_sentinel(redis)
        emb_dead = _random_embedding(seed=212)
        emb_live = _random_embedding(seed=213)

        cache_store(
            "dead q", emb_dead, {"answer": "d", "sources": [{"filename": "d.md"}]},
            redis, ttl=300, domains_searched=["finance"],
        )
        cache_store(
            "live q", emb_live, {"answer": "l", "sources": [{"filename": "l.md"}]},
            redis, ttl=300, domains_searched=["finance"],
        )
        assert redis.scard("semcache:domain_index:finance") == 2

        # Simulate the "dead" entry's own TTL expiring — its Redis payload is
        # gone but the domain-index membership (a plain SET member, no
        # per-member TTL) is still there until something prunes it.
        dead_id = None
        for m in redis.smembers("semcache:domain_index:finance"):
            payload_raw = redis.get(f"semcache:entry:{m}")
            if payload_raw and json.loads(payload_raw)["result"]["answer"] == "d":
                dead_id = m
        assert dead_id is not None
        redis.delete(f"semcache:entry:{dead_id}")

        count = invalidate_cache(redis, domain="finance")

        assert count == 1, "only the live entry should count as a fresh eviction"
        assert redis.scard("semcache:domain_index:finance") == 0

    def test_multi_domain_entry_pruned_from_other_domain_index_too(self, _reset_backend):
        """An entry indexed under two domains, evicted via ONE of them, must
        not leave a dangling reference in the other domain's index."""
        redis = fakeredis.FakeRedis(decode_responses=True)
        self._warm_legacy_sentinel(redis)
        emb = _random_embedding(seed=214)
        cache_store(
            "multi q", emb, {"answer": "m", "sources": [{"filename": "m.md"}]},
            redis, ttl=300, domains_searched=["finance", "coding"],
        )
        assert redis.scard("semcache:domain_index:coding") == 1

        invalidate_cache(redis, domain="finance")

        assert redis.scard("semcache:domain_index:finance") == 0
        assert redis.scard("semcache:domain_index:coding") == 0


class TestConcurrentLegacySweep:
    """Fix round 1 — the legacy-sweep sentinel must be claimed atomically
    (SET NX), not via a GET-then-SET race where two concurrent first-ever
    domain-scoped invalidations could both run a full flush."""

    def test_two_racing_callers_produce_exactly_one_full_flush(self, _reset_backend):
        redis = fakeredis.FakeRedis(decode_responses=True)
        barrier = threading.Barrier(2)
        call_count = {"n": 0}
        lock = threading.Lock()

        import core.retrieval.semantic_cache as semantic_cache_module
        real_full_flush = semantic_cache_module._full_flush

        def _counting_full_flush(redis_client):
            with lock:
                call_count["n"] += 1
            return real_full_flush(redis_client)

        def _race(domain: str) -> None:
            barrier.wait(timeout=5)
            invalidate_cache(redis, domain=domain)

        with patch.object(semantic_cache_module, "_full_flush", side_effect=_counting_full_flush):
            threads = [
                threading.Thread(target=_race, args=("finance",)),
                threading.Thread(target=_race, args=("coding",)),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

        assert call_count["n"] == 1, "exactly one caller should perform the legacy sweep"


class TestSizeBoundEviction:
    def test_fifo_eviction_when_over_bound(self, _reset_backend):
        redis = fakeredis.FakeRedis(decode_responses=True)

        for i in range(3):
            emb = _random_embedding(seed=100 + i)
            cache_store(
                f"bound query {i}", emb,
                {"answer": str(i), "sources": [{"filename": f"{i}.md"}]},
                redis, ttl=300, max_entries=2,
            )

        assert _reset_backend.count() == 2, "oldest entry should have been evicted"
        assert redis.zcard("semcache:age_index") == 2

    def test_evicted_entry_no_longer_served(self, _reset_backend):
        redis = fakeredis.FakeRedis(decode_responses=True)
        emb0 = _random_embedding(seed=200)
        emb1 = _random_embedding(seed=201)
        emb2 = _random_embedding(seed=202)

        cache_store(
            "q0", emb0, {"answer": "0", "sources": [{"filename": "0.md"}]},
            redis, ttl=300, max_entries=2,
        )
        cache_store(
            "q1", emb1, {"answer": "1", "sources": [{"filename": "1.md"}]},
            redis, ttl=300, max_entries=2,
        )
        cache_store(
            "q2", emb2, {"answer": "2", "sources": [{"filename": "2.md"}]},
            redis, ttl=300, max_entries=2,
        )

        # q0 was evicted — even a self-similarity=1.0 lookup must miss.
        assert cache_lookup(emb0, redis, threshold=0.99) is None
        # q2 (most recent) is still present.
        assert cache_lookup(emb2, redis, threshold=0.99) is not None

    def test_default_bound_uses_semantic_cache_max_entries_setting(self, _reset_backend):
        """``max_entries=None`` must fall through to the
        ``SEMANTIC_CACHE_MAX_ENTRIES`` setting rather than being silently
        unenforced (the pre-Phase-2.2 dead-knob behavior)."""
        redis = fakeredis.FakeRedis(decode_responses=True)
        with patch("core.retrieval.semantic_cache.SEMANTIC_CACHE_MAX_ENTRIES", 1):
            emb0 = _random_embedding(seed=300)
            emb1 = _random_embedding(seed=301)
            cache_store(
                "q0", emb0, {"answer": "0", "sources": [{"filename": "0.md"}]}, redis, ttl=300,
            )
            cache_store(
                "q1", emb1, {"answer": "1", "sources": [{"filename": "1.md"}]}, redis, ttl=300,
            )

        assert _reset_backend.count() == 1


class TestTenantScope:
    """Multi-user mode: the cache is the one retrieval surface that could
    hand tenant A's answer — and A's source filenames and chunk text — to
    tenant B. Every other surface fuses tenant_id into its where-clause."""

    def _store_as(self, tenant: str, redis, emb, payload) -> None:
        token = tenant_id_var.set(tenant)
        try:
            cache_store("what is our runway", emb, payload, redis, ttl=300)
        finally:
            tenant_id_var.reset(token)

    def _lookup_as(self, tenant: str, redis, emb):
        token = tenant_id_var.set(tenant)
        try:
            return cache_lookup(emb, redis, threshold=0.9)
        finally:
            tenant_id_var.reset(token)

    def test_other_tenant_gets_a_miss(self, monkeypatch, _reset_backend):
        monkeypatch.setenv("CERID_MULTI_USER", "true")
        redis = _mock_redis()
        emb = _random_embedding(seed=77)
        payload = {
            "context": "runway is 14 months",
            "sources": [{"filename": "acme-board-deck.md"}],
        }
        self._store_as("tenant-a", redis, emb, payload)

        assert self._lookup_as("tenant-b", redis, emb) is None

    def test_same_tenant_still_hits(self, monkeypatch, _reset_backend):
        monkeypatch.setenv("CERID_MULTI_USER", "true")
        redis = _mock_redis()
        emb = _random_embedding(seed=78)
        payload = {"context": "own answer", "sources": [{"filename": "own.md"}]}
        self._store_as("tenant-a", redis, emb, payload)

        result = self._lookup_as("tenant-a", redis, emb)
        assert result is not None
        assert result["context"] == "own answer"

    def test_single_user_token_is_unchanged(self, monkeypatch):
        """Default deployments must not lose their cache to this fix."""
        monkeypatch.delenv("CERID_MULTI_USER", raising=False)
        assert _scope_token(None) == "__all__"
        assert _scope_token(["finance"], ["finance"]) == "finance|allow=finance"
