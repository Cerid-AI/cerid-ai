# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Task 5 — /observability/trust-score caching.

Evidence: 1,244 calls/day, uncached, two Cypher queries plus four JSON reads
per call — for a score the client hook itself calls "computed nightly".
Cached for TRUST_SCORE_CACHE_TTL_S=60 via the same CachedPayload helper
/health/status uses.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.routers.observability as obs
from app.routers.health import CachedPayload
from config.constants import TRUST_SCORE_CACHE_TTL_S


def _make_app():
    app = FastAPI()
    app.include_router(obs.router)
    return app


def test_trust_score_cache_ttl_constant():
    assert TRUST_SCORE_CACHE_TTL_S == 60


class TestTrustScoreEndpointCache:
    def test_two_calls_within_ttl_build_once(self, monkeypatch):
        calls = {"n": 0}

        def fake_build():
            calls["n"] += 1
            return {"score": 90, "n": calls["n"]}

        monkeypatch.setattr(
            obs, "_trust_score_cache",
            CachedPayload(build=fake_build, ttl=TRUST_SCORE_CACHE_TTL_S, empty={}),
        )

        client = TestClient(_make_app())
        r1 = client.get("/observability/trust-score")
        r2 = client.get("/observability/trust-score")

        assert calls["n"] == 1
        assert r1.json() == r2.json() == {"score": 90, "n": 1}

    def test_call_after_ttl_rebuilds(self, monkeypatch):
        import asyncio

        calls = {"n": 0}

        def fake_build():
            calls["n"] += 1
            return {"score": 90, "n": calls["n"]}

        cache = CachedPayload(build=fake_build, ttl=TRUST_SCORE_CACHE_TTL_S, empty={})
        clock = {"t": 0.0}
        cache._now = lambda: clock["t"]
        monkeypatch.setattr(obs, "_trust_score_cache", cache)

        client = TestClient(_make_app())
        client.get("/observability/trust-score")
        clock["t"] += TRUST_SCORE_CACHE_TTL_S + 1.0

        async def force_rebuild():
            await cache.get()
            if cache._inflight is not None:
                await cache._inflight

        asyncio.run(force_rebuild())

        assert calls["n"] == 2

    def test_build_calls_compute_trust_score_and_dumps_model(self, monkeypatch):
        """The cache's build function must still route through
        compute_trust_score(neo4j_driver=...) and return its .model_dump(),
        so the response shape is byte-identical to the uncached endpoint."""
        from app.services.trust_score import compute_trust_score

        driver_used = {"driver": "unset"}

        def fake_get_neo4j():
            driver_used["driver"] = "resolved"
            return None

        monkeypatch.setattr("app.deps.get_neo4j", fake_get_neo4j)
        monkeypatch.setattr(
            obs, "_trust_score_cache",
            CachedPayload(
                build=obs._build_trust_score_payload, ttl=TRUST_SCORE_CACHE_TTL_S, empty={},
            ),
        )

        client = TestClient(_make_app())
        resp = client.get("/observability/trust-score")

        expected = compute_trust_score(neo4j_driver=None).model_dump()
        # Both calls independently compute "now"-based fields (band, etc.) —
        # compare shape/keys rather than exact equality on any timestamp.
        assert set(resp.json().keys()) == set(expected.keys())
        assert driver_used["driver"] == "resolved"

    def test_cold_cache_raising_build_500s(self, monkeypatch):
        """Fix round 1, finding 2: before this cache existed,
        compute_trust_score() raising propagated to a 500. A cold cache
        whose build raises must still 500, not silently serve {} with 200."""
        def failing_build():
            raise RuntimeError("trust score boom")

        monkeypatch.setattr(
            obs, "_trust_score_cache",
            CachedPayload(
                build=failing_build,
                ttl=TRUST_SCORE_CACHE_TTL_S,
                empty={},
                raise_on_cold_failure=True,
            ),
        )

        # raise_server_exceptions=False so the 500 surfaces as a response,
        # not a raised exception at the test level (matches production).
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/observability/trust-score")

        assert resp.status_code == 500

    def test_stale_cache_raising_refresh_keeps_serving_200(self, monkeypatch):
        """A later refresh failure (a good snapshot already served at least
        once) must keep returning the last good snapshot via the real
        endpoint, not 500 — mirrors
        TestCachedPayloadErrorSemantics.test_stale_refresh_failure_keeps_serving_last_good_snapshot
        in test_health_status_cache.py, which also proves the refresh
        actually ran and failed (calls["n"] == 2) without the TestClient/
        asyncio.run event-loop mismatch a manual drain of
        ``cache._inflight`` here would risk."""
        def flaky_build():
            if not flaky_build.called:
                flaky_build.called = True
                return {"score": 90}
            raise RuntimeError("refresh boom")
        flaky_build.called = False

        cache = CachedPayload(
            build=flaky_build, ttl=TRUST_SCORE_CACHE_TTL_S, empty={},
            raise_on_cold_failure=True,
        )
        clock = {"t": 0.0}
        cache._now = lambda: clock["t"]
        monkeypatch.setattr(obs, "_trust_score_cache", cache)

        client = TestClient(_make_app())
        r1 = client.get("/observability/trust-score")
        assert r1.status_code == 200
        assert r1.json() == {"score": 90}

        clock["t"] += TRUST_SCORE_CACHE_TTL_S + 1.0
        r2 = client.get("/observability/trust-score")

        assert r2.status_code == 200
        assert r2.json() == {"score": 90}, "must keep serving the stale value"
