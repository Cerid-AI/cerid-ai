# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for app.services.memory_metrics using fakeredis.

Covers:
- Round-trip: record → read returns count
- 24h windowing: entries older than 24h are excluded
- Defensive: unreachable Redis returns 0
- Multiple failures across modules: aggregate correctly
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fakeredis():
    """Return a real fakeredis client, or a manual in-memory stub if fakeredis
    is not installed (CI may not have it as a direct dep)."""
    try:
        import fakeredis
        return fakeredis.FakeRedis()
    except ImportError:
        return _InMemoryRedisStub()


class _InMemoryRedisStub:
    """Minimal in-memory sorted-set stub sufficient for our sorted-set API."""

    def __init__(self):
        self._sets: dict[str, dict[str, float]] = {}

    def zadd(self, key: str, mapping: dict) -> int:
        if key not in self._sets:
            self._sets[key] = {}
        self._sets[key].update(mapping)
        return len(mapping)

    def zcount(self, key: str, min_score: float, max_score) -> int:
        members = self._sets.get(key, {})
        _max = float("inf") if max_score == "+inf" else float(max_score)
        return sum(1 for score in members.values() if min_score <= score <= _max)

    def zrangebyscore(self, key: str, min_score: float, max_score) -> list:
        members = self._sets.get(key, {})
        _max = float("inf") if max_score == "+inf" else float(max_score)
        return [
            m.encode() for m, score in members.items() if min_score <= score <= _max
        ]

    def ping(self):
        return True

    def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> int:
        members = self._sets.get(key, {})
        to_remove = [m for m, s in members.items() if min_score <= s <= max_score]
        for m in to_remove:
            del members[m]
        return len(to_remove)

    def zcard(self, key: str) -> int:
        return len(self._sets.get(key, {}))


# ---------------------------------------------------------------------------
# Tests: record → read round-trip
# ---------------------------------------------------------------------------


class TestRecordAndRead:
    def test_single_failure_round_trip(self):
        """record_consolidation_failure → memory_consolidation_failures_24h returns 1."""
        from app.services.memory_metrics import (
            memory_consolidation_failures_24h,
            record_consolidation_failure,
        )

        redis = _make_fakeredis()
        record_consolidation_failure(redis, "timeout")
        assert memory_consolidation_failures_24h(redis) == 1

    def test_multiple_failures_aggregate(self):
        """Multiple failure records from different call sites aggregate correctly."""
        from app.services.memory_metrics import (
            memory_consolidation_failures_24h,
            record_consolidation_failure,
        )

        redis = _make_fakeredis()
        for reason in ("circuit_open", "timeout", "llm_call_failed:HTTPStatusError"):
            record_consolidation_failure(redis, reason)

        assert memory_consolidation_failures_24h(redis) == 3

    def test_zero_when_no_failures(self):
        """Empty sorted set returns 0."""
        from app.services.memory_metrics import memory_consolidation_failures_24h

        redis = _make_fakeredis()
        assert memory_consolidation_failures_24h(redis) == 0


# ---------------------------------------------------------------------------
# Tests: 24h windowing
# ---------------------------------------------------------------------------


class TestWindowing:
    def test_old_entries_excluded(self):
        """Entries older than 24h must not be counted."""
        from app.services.memory_metrics import (
            _FAILURES_KEY,
            memory_consolidation_failures_24h,
        )

        redis = _make_fakeredis()

        # Inject an old entry directly (25h ago)
        old_ts = time.time() - (25 * 3600)
        redis.zadd(_FAILURES_KEY, {"old_failure:25h": old_ts})

        # Current entry
        from app.services.memory_metrics import record_consolidation_failure
        record_consolidation_failure(redis, "recent_failure")

        # Only the recent entry should be counted
        assert memory_consolidation_failures_24h(redis) == 1

    def test_entries_at_boundary_included(self):
        """Entries exactly at the 24h boundary (or newer) must be counted."""
        from app.services.memory_metrics import (
            _FAILURES_KEY,
            memory_consolidation_failures_24h,
        )

        redis = _make_fakeredis()

        # Entry at 23h 59m ago (just within window)
        boundary_ts = time.time() - (24 * 3600 - 60)
        redis.zadd(_FAILURES_KEY, {"border_failure:23h59m": boundary_ts})

        assert memory_consolidation_failures_24h(redis) == 1

    def test_multiple_windows_only_24h(self):
        """Mix of old and recent: only 24h window is counted."""
        from app.services.memory_metrics import (
            _FAILURES_KEY,
            memory_consolidation_failures_24h,
        )

        redis = _make_fakeredis()

        now = time.time()
        # 3 old entries (>24h)
        for i in range(3):
            redis.zadd(_FAILURES_KEY, {f"old_{i}": now - 86401 - i})
        # 2 recent entries
        for i in range(2):
            redis.zadd(_FAILURES_KEY, {f"new_{i}": now - i * 60})

        assert memory_consolidation_failures_24h(redis) == 2


# ---------------------------------------------------------------------------
# Tests: defensive — unreachable Redis returns 0
# ---------------------------------------------------------------------------


class TestDefensive:
    def test_read_returns_zero_on_redis_error(self):
        """memory_consolidation_failures_24h returns 0 when Redis raises."""
        from app.services.memory_metrics import memory_consolidation_failures_24h

        bad_redis = MagicMock()
        bad_redis.zcount.side_effect = ConnectionError("Redis is down")

        result = memory_consolidation_failures_24h(bad_redis)
        assert result == 0

    def test_write_does_not_raise_on_redis_error(self):
        """record_consolidation_failure must not raise even on Redis error."""
        from app.services.memory_metrics import record_consolidation_failure

        bad_redis = MagicMock()
        bad_redis.zadd.side_effect = ConnectionError("Redis is down")

        # Must not raise
        record_consolidation_failure(bad_redis, "test reason")

    def test_read_returns_zero_on_none_redis(self):
        """Callers may pass None when Redis is not configured."""
        from app.services.memory_metrics import memory_consolidation_failures_24h

        result = memory_consolidation_failures_24h(None)
        assert result == 0


# ---------------------------------------------------------------------------
# Tests: async variants
# ---------------------------------------------------------------------------


class TestAsyncVariants:
    @pytest.mark.asyncio
    async def test_async_record_and_read(self):
        """async_record_consolidation_failure → async_memory_consolidation_failures_24h."""
        from app.services.memory_metrics import (
            async_memory_consolidation_failures_24h,
            async_record_consolidation_failure,
        )

        redis = _make_fakeredis()
        await async_record_consolidation_failure(redis, "async_test_failure")
        count = await async_memory_consolidation_failures_24h(redis)
        assert count == 1

    @pytest.mark.asyncio
    async def test_async_read_returns_zero_on_error(self):
        """Async reader returns 0 defensively on Redis error."""
        from app.services.memory_metrics import async_memory_consolidation_failures_24h

        bad_redis = MagicMock()
        bad_redis.zcount.side_effect = ConnectionError("down")

        result = await async_memory_consolidation_failures_24h(bad_redis)
        assert result == 0


# ---------------------------------------------------------------------------
# Tests: health invariants surface the field
# ---------------------------------------------------------------------------


class TestHealthInvariantsField:
    def setup_method(self):
        """Reset the health cache between tests."""
        import app.routers.health as h
        h._health_cache = {}
        h._health_cache_ts = 0.0

    @patch("app.routers.health.get_redis")
    @patch("app.routers.health.get_chroma")
    @patch("app.routers.health.get_neo4j")
    def test_field_present_with_zero_default(self, mock_neo4j, mock_chroma, mock_redis):
        """memory_consolidation_failures_last_24h is present with default 0."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.routers.health import router

        # Minimal mocks to make /health return
        driver = MagicMock()
        session = MagicMock()
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = {"orphans": 0}
        mock_neo4j.return_value = driver

        fake_redis = _make_fakeredis()
        mock_redis.return_value = fake_redis

        from core.utils import nli
        prior = getattr(nli, "_MODEL_LOADED", False)
        nli._MODEL_LOADED = True

        # Also patch trust_score and processor metrics to avoid side-effect imports
        with (
            patch(
                "app.routers.health.trust_score_24h_summary",
                return_value={"score": None},
                create=True,
            ),
        ):
            try:
                # Patch the trust_score import path
                with patch("app.services.trust_score.trust_score_24h_summary", return_value={"score": None}, create=True):
                    pass
            except Exception:  # silent-catch-allowed: optional patch on possibly-absent symbol in test setup
                pass

            try:
                app = FastAPI()
                app.include_router(router)
                client = TestClient(app)
                response = client.get("/health")
            finally:
                nli._MODEL_LOADED = prior

        # The field must be present regardless of HTTP status
        body = response.json()
        assert "invariants" in body, f"No 'invariants' key in response: {body}"
        invariants = body["invariants"]
        assert "memory_consolidation_failures_last_24h" in invariants, (
            f"Field 'memory_consolidation_failures_last_24h' missing from invariants: {invariants}"
        )
        assert invariants["memory_consolidation_failures_last_24h"] == 0

    @patch("app.routers.health.get_redis")
    @patch("app.routers.health.get_chroma")
    @patch("app.routers.health.get_neo4j")
    def test_field_reflects_recorded_failures(self, mock_neo4j, mock_chroma, mock_redis):
        """Failures recorded via record_consolidation_failure surface in invariants."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.routers.health import router
        from app.services.memory_metrics import record_consolidation_failure

        driver = MagicMock()
        session = MagicMock()
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = {"orphans": 0}
        mock_neo4j.return_value = driver

        fake_redis = _make_fakeredis()
        # Record 2 failures into the same fakeredis instance
        record_consolidation_failure(fake_redis, "circuit_open")
        record_consolidation_failure(fake_redis, "timeout")
        mock_redis.return_value = fake_redis

        from core.utils import nli
        prior = getattr(nli, "_MODEL_LOADED", False)
        nli._MODEL_LOADED = True

        try:
            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)
            response = client.get("/health")
        finally:
            nli._MODEL_LOADED = prior

        body = response.json()
        invariants = body.get("invariants", {})
        assert invariants.get("memory_consolidation_failures_last_24h") == 2
