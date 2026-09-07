# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for Redis query cache (utils/query_cache.py)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import fakeredis
import pytest

from utils.query_cache import (
    CACHE_PREFIX,
    DEFAULT_TTL,
    _cache_key,
    get_cached,
    invalidate_all,
    invalidate_by_domain,
    invalidate_cache_non_blocking,
    invalidate_query_caches,
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


# ---------------------------------------------------------------------------
# Domain-scoped invalidation (Task 7 — C1 mirrors the semantic cache's rule)
# ---------------------------------------------------------------------------


class TestDomainScopedInvalidation:
    """An ingest into domain D must evict only C1 entries whose cached result
    touched D, not the whole cache (mirrors ``core.retrieval.semantic_cache``)."""

    @staticmethod
    def _warm_legacy_sentinel(redis: Any) -> None:
        invalidate_by_domain("_warmup_")

    @patch("utils.query_cache.get_redis")
    def test_ingest_domain_evicts_matching_entry(self, mock_get_redis):
        redis = fakeredis.FakeRedis(decode_responses=True)
        mock_get_redis.return_value = redis
        self._warm_legacy_sentinel(redis)

        set_cached("finance q", "finance", 10, {"answer": "f"}, domains_searched=["finance"])

        count = invalidate_by_domain("finance")

        assert count == 1
        assert get_cached("finance q", "finance", 10) is None

    @patch("utils.query_cache.get_redis")
    def test_ingest_domain_keeps_entry_that_searched_other_domain(self, mock_get_redis):
        redis = fakeredis.FakeRedis(decode_responses=True)
        mock_get_redis.return_value = redis
        self._warm_legacy_sentinel(redis)

        set_cached("finance q", "finance", 10, {"answer": "f"}, domains_searched=["finance"])
        set_cached("coding q", "coding", 10, {"answer": "c"}, domains_searched=["coding"])

        invalidate_by_domain("finance")

        assert get_cached("finance q", "finance", 10) is None
        kept = get_cached("coding q", "coding", 10)
        assert kept is not None and kept["answer"] == "c"

    @patch("utils.query_cache.get_redis")
    def test_legacy_entry_without_domains_evicted_on_any_invalidation(self, mock_get_redis):
        """An entry written without ``domains_searched`` carries no domain-index
        membership — the first domain-scoped invalidation falls back to a full
        flush to retire it, then never needs to again."""
        redis = fakeredis.FakeRedis(decode_responses=True)
        mock_get_redis.return_value = redis

        set_cached("legacy q", "finance", 10, {"answer": "l"})

        count = invalidate_by_domain("coding")

        assert count == 1
        assert get_cached("legacy q", "finance", 10) is None

    @patch("utils.query_cache.get_redis")
    def test_invalidate_query_caches_with_domain_scopes_c1(self, mock_get_redis):
        """The public ``invalidate_query_caches`` contract threads ``domain``
        through to the C1 domain-scoped path (not the full flush)."""
        redis = fakeredis.FakeRedis(decode_responses=True)
        mock_get_redis.return_value = redis
        self._warm_legacy_sentinel(redis)

        set_cached("finance q", "finance", 10, {"answer": "f"}, domains_searched=["finance"])
        set_cached("coding q", "coding", 10, {"answer": "c"}, domains_searched=["coding"])

        with patch("core.retrieval.semantic_cache.invalidate_cache"):
            invalidate_query_caches(trigger="ingestion.ingest_content", domain="finance")

        assert get_cached("finance q", "finance", 10) is None
        assert get_cached("coding q", "coding", 10) is not None

    @patch("utils.query_cache.get_redis")
    def test_invalidate_query_caches_without_domain_still_flushes_all(self, mock_get_redis):
        """Call sites that never pass ``domain`` (e.g. memory.archive_old_memories)
        must keep today's full-flush behavior."""
        redis = fakeredis.FakeRedis(decode_responses=True)
        mock_get_redis.return_value = redis

        set_cached("finance q", "finance", 10, {"answer": "f"}, domains_searched=["finance"])
        set_cached("coding q", "coding", 10, {"answer": "c"}, domains_searched=["coding"])

        with patch("core.retrieval.semantic_cache.invalidate_cache"):
            invalidate_query_caches(trigger="memory.archive_old_memories")

        assert get_cached("finance q", "finance", 10) is None
        assert get_cached("coding q", "coding", 10) is None

    @patch("utils.query_cache.get_redis")
    def test_unrestricted_entry_evicted_by_ingest_into_any_searched_domain(self, mock_get_redis):
        """An entry computed with no domain restriction records
        ``domains_searched`` as every configured domain (the
        ``list(config.DOMAINS)`` fallback in ``query_agent.py``) — an ingest
        into any ONE of those domains must still evict it. C1 mirrors C2."""
        import config

        redis = fakeredis.FakeRedis(decode_responses=True)
        mock_get_redis.return_value = redis
        self._warm_legacy_sentinel(redis)

        set_cached(
            "unrestricted q", "all", 10, {"answer": "u"},
            domains_searched=list(config.DOMAINS),
        )

        picked_domain = config.DOMAINS[len(config.DOMAINS) // 2]
        invalidate_by_domain(picked_domain)

        assert get_cached("unrestricted q", "all", 10) is None


class TestDomainIndexEvictionBounds:
    """Fix round 1 — the per-domain index must not grow without bound: a
    domain-scoped invalidation must prune an id out of every domain index it
    was registered under (not just the one being processed), and a member
    whose payload already TTL-expired (dangling) must be dropped from the
    index without being double-counted as a fresh eviction. C1 has no FIFO/
    size-bound eviction path (unlike the semantic cache), so only the
    dangling-member and cross-domain-pruning cases apply here."""

    @staticmethod
    def _warm_legacy_sentinel(redis: Any) -> None:
        invalidate_by_domain("_warmup_")

    @patch("utils.query_cache.get_redis")
    def test_dangling_member_pruned_and_not_double_counted(self, mock_get_redis):
        redis = fakeredis.FakeRedis(decode_responses=True)
        mock_get_redis.return_value = redis
        self._warm_legacy_sentinel(redis)

        set_cached("dead q", "finance", 10, {"answer": "d"}, domains_searched=["finance"])
        set_cached("live q", "finance", 20, {"answer": "l"}, domains_searched=["finance"])
        assert redis.scard("qcache:domain_index:finance") == 2

        # Simulate the "dead" entry's own TTL expiring — its payload key is
        # gone but the domain-index membership (a plain SET member, no
        # per-member TTL) is still there until something prunes it.
        dead_key = _cache_key("dead q", "finance", 10)
        redis.delete(dead_key)

        count = invalidate_by_domain("finance")

        assert count == 1, "only the live entry should count as a fresh eviction"
        assert redis.scard("qcache:domain_index:finance") == 0

    @patch("utils.query_cache.get_redis")
    def test_multi_domain_entry_pruned_from_other_domain_index_too(self, mock_get_redis):
        redis = fakeredis.FakeRedis(decode_responses=True)
        mock_get_redis.return_value = redis
        self._warm_legacy_sentinel(redis)

        set_cached(
            "multi q", "finance,coding", 10, {"answer": "m"},
            domains_searched=["finance", "coding"],
        )
        assert redis.scard("qcache:domain_index:coding") == 1

        invalidate_by_domain("finance")

        assert redis.scard("qcache:domain_index:finance") == 0
        assert redis.scard("qcache:domain_index:coding") == 0


class TestConcurrentLegacySweep:
    """Fix round 1 — the legacy-sweep sentinel must be claimed atomically
    (SET NX), not via a GET-then-SET race where two concurrent first-ever
    domain-scoped invalidations could both run a full flush."""

    @patch("utils.query_cache.get_redis")
    def test_two_racing_callers_produce_exactly_one_full_flush(self, mock_get_redis):
        import threading

        import utils.query_cache as query_cache_module

        redis = fakeredis.FakeRedis(decode_responses=True)
        mock_get_redis.return_value = redis
        barrier = threading.Barrier(2)
        call_count = {"n": 0}
        lock = threading.Lock()
        real_full_flush = query_cache_module._full_flush

        def _counting_full_flush(r):
            with lock:
                call_count["n"] += 1
            return real_full_flush(r)

        def _race(domain: str) -> None:
            barrier.wait(timeout=5)
            invalidate_by_domain(domain)

        with patch.object(query_cache_module, "_full_flush", side_effect=_counting_full_flush):
            threads = [
                threading.Thread(target=_race, args=("finance",)),
                threading.Thread(target=_race, args=("coding",)),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

        assert call_count["n"] == 1, "exactly one caller should perform the legacy sweep"


# ---------------------------------------------------------------------------
# Observability: Sentry capture tests (R1-3)
# ---------------------------------------------------------------------------


class TestQueryCacheSentryCapture:
    """Assert Sentry.capture_exception fires at every silent-catch site."""

    @patch("utils.query_cache.get_redis", side_effect=RuntimeError("redis down"))
    @patch("sentry_sdk.capture_exception")
    def test_read_failed_captured(self, mock_capture, _mock_redis):
        """get_cached() reports to Sentry and returns None on failure."""
        result = get_cached("q", "dom", 5)
        assert result is None
        mock_capture.assert_called_once()

    @patch("utils.query_cache.get_redis", side_effect=OSError("write error"))
    @patch("sentry_sdk.capture_exception")
    def test_write_failed_captured(self, mock_capture, _mock_redis):
        """set_cached() reports to Sentry on failure (no exception raised)."""
        set_cached("q", "dom", 5, {"results": [], "answer": "x"})
        mock_capture.assert_called_once()

    @patch("utils.query_cache.get_redis", side_effect=RuntimeError("scan error"))
    @patch("sentry_sdk.capture_exception")
    def test_invalidation_failed_captured(self, mock_capture, _mock_redis):
        """invalidate_all() reports to Sentry on failure."""
        invalidate_all()
        mock_capture.assert_called_once()
