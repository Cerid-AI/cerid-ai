# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Contract tests for Idempotency-Key on the /sdk/v1 write surface (GA P0.5 D1).

The systemic shape under test: an external backend that retries ``POST
/sdk/v1/ingest`` on a 5xx must NOT double-ingest. The implementation is
lock-first (``SET NX`` a sentinel → winner runs the work → result overwrites
the sentinel; a concurrent duplicate sees the sentinel and gets ``409``;
failures release the lock so a retry re-processes). These tests assert the
*pipeline call count*, not just response equality — equality alone would pass
a buggy cache that re-ran the work.

No live stack required (default ``test`` job).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeRedis:
    """Minimal Redis double with correct SETNX + delete semantics."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(self, key, value, nx=False, ex=None):  # noqa: ARG002 — ex unused in the double
        if nx and key in self.store:
            return None  # SETNX contract: do nothing, signal "not set"
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        existed = key in self.store
        self.store.pop(key, None)
        return 1 if existed else 0


_RESULT = {"status": "success", "artifact_id": "art-1", "chunks": 1, "domain": "general"}
_IDEM = "app.middleware.idempotency.get_redis"
_INGEST = "app.routers.sdk.ingest_content"


@pytest.fixture
def client():
    from app.routers import sdk

    app = FastAPI()
    app.include_router(sdk.router)
    return TestClient(app, raise_server_exceptions=False)


def _ingest(client, headers=None):
    return client.post("/sdk/v1/ingest", json={"content": "x", "domain": "general"}, headers=headers or {})


class TestSdkIngestIdempotency:
    def test_same_key_returns_cached_and_runs_pipeline_once(self, client):
        fake = _FakeRedis()
        h = {"Idempotency-Key": "k1", "X-Client-ID": "c1"}
        with patch(_IDEM, return_value=fake), patch(_INGEST, return_value=_RESULT) as m:
            r1 = _ingest(client, h)
            r2 = _ingest(client, h)
        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        assert r1.json() == _RESULT and r2.json() == _RESULT
        assert m.call_count == 1
        # first call really carried the request payload through
        assert m.call_args_list[0].kwargs["domain"] == "general"

    def test_different_key_processes_again(self, client):
        fake = _FakeRedis()
        with patch(_IDEM, return_value=fake), patch(_INGEST, return_value=_RESULT) as m:
            _ingest(client, {"Idempotency-Key": "a", "X-Client-ID": "c1"})
            _ingest(client, {"Idempotency-Key": "b", "X-Client-ID": "c1"})
        assert m.call_count == 2

    def test_no_key_is_legacy_unchanged(self, client):
        fake = _FakeRedis()
        with patch(_IDEM, return_value=fake), patch(_INGEST, return_value=_RESULT) as m:
            _ingest(client)
            _ingest(client)
        assert m.call_count == 2
        assert fake.store == {}  # redis never touched on the no-header path

    def test_failed_request_is_not_cached(self, client):
        fake = _FakeRedis()
        m = MagicMock(side_effect=[RuntimeError("boom"), _RESULT])
        h = {"Idempotency-Key": "k", "X-Client-ID": "c1"}
        with patch(_IDEM, return_value=fake), patch(_INGEST, new=m):
            r1 = _ingest(client, h)
            r2 = _ingest(client, h)
        assert r1.status_code == 500  # failure surfaces, lock released
        assert r2.status_code == 200 and r2.json() == _RESULT  # retry re-processes
        assert m.call_count == 2

    def test_redis_unavailable_degrades_gracefully(self, client):
        h = {"Idempotency-Key": "k", "X-Client-ID": "c1"}
        with patch(_IDEM, side_effect=RuntimeError("down")), patch(_INGEST, return_value=_RESULT) as m:
            r1 = _ingest(client, h)
            r2 = _ingest(client, h)
        assert r1.status_code == 200 and r2.status_code == 200
        assert m.call_count == 2  # no caching, but never a 500 from the layer

    def test_inflight_sentinel_returns_409(self, client):
        from app.middleware.idempotency import _SENTINEL, _cache_key

        fake = _FakeRedis()
        fake.store[_cache_key("c1", "/sdk/v1/ingest", "k")] = _SENTINEL
        with patch(_IDEM, return_value=fake), patch(_INGEST, return_value=_RESULT) as m:
            r = _ingest(client, {"Idempotency-Key": "k", "X-Client-ID": "c1"})
        assert r.status_code == 409
        assert m.call_count == 0  # never runs work while a duplicate is in flight

    def test_corrupt_cache_entry_reprocesses(self, client):
        from app.middleware.idempotency import _cache_key

        fake = _FakeRedis()
        fake.store[_cache_key("c1", "/sdk/v1/ingest", "k")] = "{not-valid-json"
        with patch(_IDEM, return_value=fake), patch(_INGEST, return_value=_RESULT) as m:
            r = _ingest(client, {"Idempotency-Key": "k", "X-Client-ID": "c1"})
        assert r.status_code == 200 and r.json() == _RESULT
        assert m.call_count == 1  # corrupt entry treated as a miss

    def test_default_client_id_shares_cache(self, client):
        fake = _FakeRedis()
        with patch(_IDEM, return_value=fake), patch(_INGEST, return_value=_RESULT) as m:
            _ingest(client, {"Idempotency-Key": "k"})  # no X-Client-ID → "gui"
            _ingest(client, {"Idempotency-Key": "k"})
        assert m.call_count == 1


class TestSdkIdempotencyUnits:
    def test_cache_key_scopes_by_client_and_path(self):
        from app.middleware.idempotency import _cache_key

        assert _cache_key("a", "/p", "k") != _cache_key("b", "/p", "k")
        assert _cache_key("a", "/p1", "k") != _cache_key("a", "/p2", "k")
        assert _cache_key("a", "/p", "k") == _cache_key("a", "/p", "k")

    def test_store_or_release_drops_lock_on_unserializable(self):
        from app.middleware.idempotency import _SENTINEL, _store_or_release

        fake = _FakeRedis()
        fake.store["k"] = _SENTINEL
        _store_or_release(fake, "k", object())  # not JSON-serializable
        assert "k" not in fake.store  # lock released so a retry can re-process

    def test_memory_extract_sync_path_is_idempotent(self):
        from app.routers import sdk

        app = FastAPI()
        app.include_router(sdk.router)
        c = TestClient(app, raise_server_exceptions=False)
        fake = _FakeRedis()
        h = {"Idempotency-Key": "mk", "X-Client-ID": "c1"}
        body = {"response_text": "hi", "conversation_id": "conv-1"}
        with patch(_IDEM, return_value=fake), \
             patch("app.routers.sdk._memory_async_enabled", return_value=False), \
             patch("app.routers.sdk.memory_extract_endpoint", new=AsyncMock(return_value={"ok": True})) as m:
            r1 = c.post("/sdk/v1/memory/extract", json=body, headers=h)
            r2 = c.post("/sdk/v1/memory/extract", json=body, headers=h)
        assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
        assert m.call_count == 1  # wrapper applies on a second endpoint too


class TestEveryMutatingPostIsWrapped:
    """Standing-claim gate for GA_CHECKLIST "Idempotency-Key on all /sdk/v1 POSTs".

    The checklist item says *all*, which makes it a claim that must hold as
    routes are added — not a one-time audit. It silently went stale once
    already: it was verified green on 2026-06-05, then ``/ingest/file``,
    ``/ingest/webhook/{token}`` and ``/ingest/voice-note`` were added without
    the wrap and nothing rechecked it.

    Enumerating the router means a new mutating POST fails here instead of
    quietly falsifying the checklist. A genuinely read-only POST is opted out
    by name below, with the reason recorded.
    """

    # POSTs that create/modify durable state — each MUST route through
    # ``idempotent`` so a client retry cannot double-write.
    MUTATING = {
        "/sdk/v1/ingest",
        "/sdk/v1/ingest/file",
        "/sdk/v1/ingest/external",
        "/sdk/v1/ingest/webhook/{token}",
        "/sdk/v1/ingest/voice-note",
        "/sdk/v1/memory/extract",
    }

    # POSTs that only read/compute — POST is used for request-body ergonomics,
    # not because they mutate. A retry is harmless, so no key is required.
    READ_ONLY = {
        "/sdk/v1/query",
        "/sdk/v1/hallucination",
        "/sdk/v1/llm/complete",
        "/sdk/v1/search",
    }

    def _post_paths(self) -> set[str]:
        from app.routers import sdk

        return {
            r.path
            for r in sdk.router.routes
            if "POST" in getattr(r, "methods", set())
        }

    def test_route_inventory_is_complete(self):
        """Every POST is classified — a new one must be triaged, not ignored."""
        unclassified = self._post_paths() - self.MUTATING - self.READ_ONLY
        assert not unclassified, (
            f"New /sdk/v1 POST route(s) {sorted(unclassified)} are unclassified. "
            "Add to MUTATING (and wrap in `idempotent`) or to READ_ONLY."
        )

    def test_classified_routes_still_exist(self):
        """Catches a rename that would otherwise make the gate vacuous."""
        missing = (self.MUTATING | self.READ_ONLY) - self._post_paths()
        assert not missing, f"Classified route(s) no longer on the router: {sorted(missing)}"

    def test_every_mutating_post_reaches_idempotent(self):
        """The endpoint body must call ``idempotent``.

        This is a source check on the resolved route endpoint, so it catches a
        route added without the wrap. It does NOT prove the call is reached on
        every branch — the behavioural round-trips above
        (``TestSdkIngestIdempotency``) cover that for the ingest path.
        """
        import inspect

        from app.routers import sdk

        by_path = {
            r.path: r
            for r in sdk.router.routes
            if "POST" in getattr(r, "methods", set())
        }
        unwrapped = []
        for path in sorted(self.MUTATING):
            route = by_path[path]
            src = inspect.getsource(route.endpoint)
            if "idempotent(" not in src:
                unwrapped.append(path)
        assert not unwrapped, (
            f"Mutating POST route(s) not wrapped in `idempotent`: {unwrapped}"
        )
