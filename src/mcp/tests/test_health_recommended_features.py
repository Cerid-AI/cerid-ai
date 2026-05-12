# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for /health.recommended_features wire-in (C3.2)."""

from __future__ import annotations

import json

from app.routers.health import _load_recommendations


class _FakeRedis:
    """Tiny in-memory shim — only the two methods _load_recommendations uses."""

    def __init__(self) -> None:
        self.hash: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    def hgetall(self, key):
        # Return bytes to mirror the real redis-py default decode_responses=False.
        return {k.encode(): v.encode() for k, v in self.hash.items()}

    def smembers(self, key):
        return {m.encode() for m in self.sets.get(key, set())}


_HASH_KEY = "cerid:recommendations"
_DISMISSED_PREFIX = "cerid:recommendations:dismissed:"


def test_empty_hash_returns_empty_list():
    redis = _FakeRedis()
    assert _load_recommendations(redis, _DISMISSED_PREFIX, _HASH_KEY) == []


def test_no_redis_returns_empty_list():
    assert _load_recommendations(None, _DISMISSED_PREFIX, _HASH_KEY) == []


def test_active_entry_appears():
    redis = _FakeRedis()
    redis.hash["sparse_retrieval"] = json.dumps({
        "id": "sparse_retrieval",
        "label": "SPLADE-v3",
        "reason": "Your corpus is now 200 documents.",
        "triggered_at": "2026-05-12T00:00:00+00:00",
        "corpus_size": 200,
        "enable_payload": {"enable_sparse_retrieval": True},
    })
    out = _load_recommendations(redis, _DISMISSED_PREFIX, _HASH_KEY)
    assert len(out) == 1
    assert out[0]["id"] == "sparse_retrieval"
    assert out[0]["corpus_size"] == 200


def test_dismissed_entry_filtered_out():
    redis = _FakeRedis()
    redis.hash["sparse_retrieval"] = json.dumps({"id": "sparse_retrieval"})
    redis.hash["hype_indexing"] = json.dumps({"id": "hype_indexing"})
    redis.sets[_DISMISSED_PREFIX + "default"] = {"sparse_retrieval"}
    out = _load_recommendations(redis, _DISMISSED_PREFIX, _HASH_KEY)
    ids = {entry["id"] for entry in out}
    assert ids == {"hype_indexing"}


def test_malformed_entry_skipped_not_raised():
    """A corrupted JSON value must drop *just that entry*, not the whole list.

    Mirrors the corrupted-line tolerance pattern from sparse_index and
    bm25 — observability code never breaks the host endpoint.
    """
    redis = _FakeRedis()
    redis.hash["sparse_retrieval"] = "{not-valid-json"
    redis.hash["hype_indexing"] = json.dumps({"id": "hype_indexing"})
    out = _load_recommendations(redis, _DISMISSED_PREFIX, _HASH_KEY)
    ids = {entry["id"] for entry in out}
    assert ids == {"hype_indexing"}


def test_redis_hgetall_failure_returns_empty():
    """Redis transient errors must not break /health."""

    class _BoomRedis:
        def hgetall(self, _):
            raise RuntimeError("boom")

        def smembers(self, _):
            return set()

    assert _load_recommendations(_BoomRedis(), _DISMISSED_PREFIX, _HASH_KEY) == []
