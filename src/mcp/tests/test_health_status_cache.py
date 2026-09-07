# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Task 5 — /health/status caching, the quenchforge probe cache, and the
shared single-flight/stale-while-revalidate cache helper.

Evidence: /health/status was polled 14,841 times/day by a sync handler that
ran a live Neo4j RETURN 1, breaker reads, and (via health_check()) a GET
/api/tags probe against quenchforge with timeout=1 on every call — 14,197 of
the day's 18,237 /api/tags probes. This module pins the fix: a shared
CachedPayload helper (extracted from the existing /health pattern) backs
/health/status, and health_check()'s ollama probe is cached for 60s
independent of either cache's TTL.
"""
from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.routers.health as h
from app.routers.health import HEALTH_STATUS_CACHE_TTL_S, CachedPayload


def _make_app():
    app = FastAPI()
    app.include_router(h.router)
    return app


# ---------------------------------------------------------------------------
# CachedPayload — the extracted single-flight + stale-while-revalidate helper
# ---------------------------------------------------------------------------


class TestCachedPayload:
    def test_serves_from_cache_within_ttl(self):
        calls = {"n": 0}

        def build():
            calls["n"] += 1
            return {"n": calls["n"]}

        clock = {"t": 0.0}
        cache = CachedPayload(build=build, ttl=15.0, empty={})
        cache._now = lambda: clock["t"]  # deterministic clock, see impl

        asyncio.run(cache.get())
        clock["t"] += 1.0
        asyncio.run(cache.get())

        assert calls["n"] == 1, "two calls within the TTL must build exactly once"

    def test_rebuilds_after_ttl_expires(self):
        calls = {"n": 0}

        def build():
            calls["n"] += 1
            return {"n": calls["n"]}

        clock = {"t": 0.0}
        cache = CachedPayload(build=build, ttl=15.0, empty={})
        cache._now = lambda: clock["t"]

        asyncio.run(cache.get())
        clock["t"] += 20.0  # past the TTL

        async def scenario():
            # stale-while-revalidate: first post-TTL call serves the stale
            # value and kicks off a background rebuild — await it directly.
            await cache.get()
            assert cache._inflight is not None
            await cache._inflight

        asyncio.run(scenario())

        assert calls["n"] == 2, "a call after the TTL must trigger a rebuild"

    def test_concurrent_cold_calls_share_one_build(self):
        """First-ever call on an empty cache: N concurrent callers must
        share ONE build rather than each spawning their own."""
        calls: list[int] = []
        release = threading.Event()

        def build():
            calls.append(1)
            release.wait(timeout=5)
            return {"n": len(calls)}

        cache = CachedPayload(build=build, ttl=15.0, empty={})

        async def scenario():
            tasks = [asyncio.create_task(cache.get()) for _ in range(5)]
            await asyncio.sleep(0.05)  # let every task reach the coalescing lock
            release.set()
            return await asyncio.gather(*tasks)

        results = asyncio.run(scenario())

        assert len(calls) == 1, f"expected exactly one build, got {len(calls)}"
        assert all(r == {"n": 1} for r in results)


class TestCachedPayloadErrorSemantics:
    """Fix round 1, finding 2: a cold-cache build failure must propagate for
    caches that had a real 500-on-error contract before caching existed
    (raise_on_cold_failure=True); a later refresh failure with a good
    snapshot already cached must never raise — it keeps serving the stale
    snapshot."""

    def test_cold_failure_propagates_when_raise_on_cold_failure(self):
        def failing_build():
            raise RuntimeError("boom")

        cache = CachedPayload(
            build=failing_build, ttl=15.0, empty={}, raise_on_cold_failure=True,
        )

        with pytest.raises(RuntimeError, match="boom"):
            asyncio.run(cache.get())

    def test_cold_failure_swallowed_by_default(self):
        """Without raise_on_cold_failure (the /health default), a cold-cache
        build failure still returns the empty fallback — unchanged from the
        original /health behavior this cache was extracted from."""
        def failing_build():
            raise RuntimeError("boom")

        cache = CachedPayload(build=failing_build, ttl=15.0, empty={"fallback": True})

        result = asyncio.run(cache.get())

        assert result == {"fallback": True}

    def test_stale_refresh_failure_keeps_serving_last_good_snapshot(self):
        """Even with raise_on_cold_failure=True, a refresh triggered from a
        WARM cache (a prior good snapshot exists) must never raise — the
        stale-while-revalidate background refresh is fire-and-forget, so a
        raise there would leak an unretrieved exception, and the whole point
        of stale-while-revalidate is that a bad refresh doesn't take down
        the endpoint."""
        calls = {"n": 0}

        def flaky_build():
            calls["n"] += 1
            if calls["n"] == 1:
                return {"good": "snapshot"}
            raise RuntimeError("refresh boom")

        cache = CachedPayload(
            build=flaky_build, ttl=15.0, empty={}, raise_on_cold_failure=True,
        )
        clock = {"t": 0.0}
        cache._now = lambda: clock["t"]

        first = asyncio.run(cache.get())
        assert first == {"good": "snapshot"}

        clock["t"] += 20.0  # past the TTL — next get() triggers a refresh

        async def scenario():
            result = await cache.get()
            # The background refresh (still in flight) never raises — a
            # stale-refresh failure is swallowed internally and its Future
            # resolves to the stale value, not an exception.
            assert cache._inflight is not None
            refreshed = await cache._inflight
            return result, refreshed

        second, refreshed = asyncio.run(scenario())

        assert second == {"good": "snapshot"}, "must keep serving the stale value"
        assert refreshed == {"good": "snapshot"}
        assert calls["n"] == 2


# ---------------------------------------------------------------------------
# /health/status endpoint — HEALTH_STATUS_CACHE_TTL_S=15 with the shared helper
# ---------------------------------------------------------------------------


class TestHealthStatusEndpointCache:
    def setup_method(self):
        assert HEALTH_STATUS_CACHE_TTL_S == 15

    def test_two_calls_within_ttl_build_once(self, monkeypatch):
        calls = {"n": 0}

        def fake_degradation_status():
            calls["n"] += 1
            return {"status": "healthy", "n": calls["n"]}

        monkeypatch.setattr(
            h, "_health_status_cache",
            CachedPayload(build=fake_degradation_status, ttl=HEALTH_STATUS_CACHE_TTL_S, empty={}),
        )

        client = TestClient(_make_app())
        r1 = client.get("/health/status")
        r2 = client.get("/health/status")

        assert calls["n"] == 1
        assert r1.json() == r2.json() == {"status": "healthy", "n": 1}

    def test_call_after_ttl_rebuilds(self, monkeypatch):
        calls = {"n": 0}

        def fake_degradation_status():
            calls["n"] += 1
            return {"status": "healthy", "n": calls["n"]}

        cache = CachedPayload(build=fake_degradation_status, ttl=HEALTH_STATUS_CACHE_TTL_S, empty={})
        clock = {"t": 0.0}
        cache._now = lambda: clock["t"]
        monkeypatch.setattr(h, "_health_status_cache", cache)

        client = TestClient(_make_app())
        client.get("/health/status")
        clock["t"] += HEALTH_STATUS_CACHE_TTL_S + 1.0

        async def force_rebuild():
            await cache.get()
            if cache._inflight is not None:
                await cache._inflight

        asyncio.run(force_rebuild())

        assert calls["n"] == 2

    def test_concurrent_calls_during_slow_build_share_it(self, monkeypatch):
        calls = {"n": 0}
        release = threading.Event()

        def slow_degradation_status():
            calls["n"] += 1
            release.wait(timeout=5)
            return {"status": "healthy"}

        cache = CachedPayload(build=slow_degradation_status, ttl=HEALTH_STATUS_CACHE_TTL_S, empty={})
        monkeypatch.setattr(h, "_health_status_cache", cache)

        async def scenario():
            tasks = [asyncio.create_task(cache.get()) for _ in range(4)]
            await asyncio.sleep(0.05)
            release.set()
            return await asyncio.gather(*tasks)

        results = asyncio.run(scenario())

        assert calls["n"] == 1
        assert all(r == {"status": "healthy"} for r in results)

    def test_cold_cache_raising_build_500s(self, monkeypatch):
        """Fix round 1, finding 2: before this cache existed,
        degradation_status() raising propagated to a 500. A cold cache
        (nothing cached yet) whose build raises must still 500, not
        silently serve {} with 200."""
        def failing_degradation_status():
            raise RuntimeError("degradation_status boom")

        monkeypatch.setattr(
            h, "_health_status_cache",
            CachedPayload(
                build=failing_degradation_status,
                ttl=HEALTH_STATUS_CACHE_TTL_S,
                empty={},
                raise_on_cold_failure=True,
            ),
        )

        # raise_server_exceptions=False so the 500 surfaces as a response,
        # not a raised exception at the test level (matches production).
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/health/status")

        assert resp.status_code == 500

    def test_stale_cache_raising_refresh_keeps_serving_200(self, monkeypatch):
        """A later refresh failure (a good snapshot already served at least
        once) must keep returning the last good snapshot via the real
        endpoint, not 500 — mirrors
        TestCachedPayloadErrorSemantics.test_stale_refresh_failure_keeps_serving_last_good_snapshot,
        which also proves the refresh actually ran and failed
        (calls["n"] == 2) without the TestClient/asyncio.run event-loop
        mismatch a manual drain of ``cache._inflight`` here would risk."""
        def flaky_degradation_status():
            if not flaky_degradation_status.called:
                flaky_degradation_status.called = True
                return {"status": "healthy"}
            raise RuntimeError("refresh boom")
        flaky_degradation_status.called = False

        cache = CachedPayload(
            build=flaky_degradation_status,
            ttl=HEALTH_STATUS_CACHE_TTL_S,
            empty={},
            raise_on_cold_failure=True,
        )
        clock = {"t": 0.0}
        cache._now = lambda: clock["t"]
        monkeypatch.setattr(h, "_health_status_cache", cache)

        client = TestClient(_make_app())
        r1 = client.get("/health/status")
        assert r1.status_code == 200
        assert r1.json() == {"status": "healthy"}

        clock["t"] += HEALTH_STATUS_CACHE_TTL_S + 1.0
        r2 = client.get("/health/status")

        assert r2.status_code == 200
        assert r2.json() == {"status": "healthy"}, "must keep serving the stale value"


# ---------------------------------------------------------------------------
# health_check()'s quenchforge /api/tags probe — cached independent of either
# CachedPayload TTL, so it never runs more than once per 60s per process.
# ---------------------------------------------------------------------------


class TestOllamaProbeCache:
    def setup_method(self):
        h._ollama_probe_cache = None
        h._ollama_probe_cache_ts = 0.0
        import core.utils.internal_llm as internal_llm

        internal_llm.reset_effective_local_model_cache()

    @patch("app.routers.health.get_redis")
    @patch("app.routers.health.get_neo4j")
    @patch("app.routers.health.get_chroma")
    def test_probe_runs_once_across_n_calls_within_60s(
        self, mock_chroma, mock_neo4j, mock_redis, monkeypatch,
    ):
        mock_chroma.return_value = MagicMock()
        mock_neo4j.return_value = None
        mock_redis.return_value = MagicMock()
        monkeypatch.setenv("OLLAMA_ENABLED", "true")
        monkeypatch.setenv("OLLAMA_URL", "http://fake-quenchforge:11434")
        import core.utils.internal_llm as internal_llm
        monkeypatch.setattr(internal_llm.config, "INTERNAL_LLM_MODEL", "llama3.1-8b", raising=False)

        calls = {"n": 0}

        class _Resp:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"models": [{"name": "llama3.1-8b"}]}

        def fake_get(url, timeout):
            calls["n"] += 1
            return _Resp()

        monkeypatch.setattr("httpx.get", fake_get)

        for _ in range(10):
            result = h.health_check()

        # One call for the reachability probe, one for the local-model
        # resolver's own served-list fetch (Task 6) — each independently
        # cached, so neither repeats across the remaining nine calls.
        assert calls["n"] == 2, f"expected exactly two /api/tags probes, got {calls['n']}"
        assert result["ollama"] == {
            "reachable": True,
            "models": 1,
            "url": "http://fake-quenchforge:11434",
            "effective_local_model": "llama3.1-8b",
        }

    @patch("app.routers.health.get_redis")
    @patch("app.routers.health.get_neo4j")
    @patch("app.routers.health.get_chroma")
    def test_probe_reruns_after_60s(self, mock_chroma, mock_neo4j, mock_redis, monkeypatch):
        mock_chroma.return_value = MagicMock()
        mock_neo4j.return_value = None
        mock_redis.return_value = MagicMock()
        monkeypatch.setenv("OLLAMA_ENABLED", "true")
        monkeypatch.setenv("OLLAMA_URL", "http://fake-quenchforge:11434")

        calls = {"n": 0}
        clock = {"t": 0.0}

        class _Resp:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"models": []}

        def fake_get(url, timeout):
            calls["n"] += 1
            return _Resp()

        monkeypatch.setattr("httpx.get", fake_get)
        monkeypatch.setattr(h.time, "monotonic", lambda: clock["t"])

        h.health_check()
        clock["t"] += 61.0
        h.health_check()

        # The reachability probe's 60s TTL expires and refetches once more;
        # the local-model resolver (Task 6) resolves once per process and
        # never refetches, so the total is the probe's two calls plus the
        # resolver's one.
        assert calls["n"] == 3
