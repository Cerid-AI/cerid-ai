# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Round-trip tests for /settings/recommendations/* (C3.2)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.recommendations import router


class _FakePipeline:
    def __init__(self, owner) -> None:
        self._owner = owner

    def hdel(self, key, field):
        self._owner.hash.pop(field, None)
        return self

    def srem(self, key, member):
        s = self._owner.sets.get(key, set())
        s.discard(member)
        self._owner.sets[key] = s
        return self

    def execute(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _FakeRedis:
    def __init__(self) -> None:
        self.hash: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    def sadd(self, key, member):
        s = self.sets.setdefault(key, set())
        s.add(member)

    def pipeline(self):
        return _FakePipeline(self)


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    fake = _FakeRedis()

    def _get_redis():
        return fake

    monkeypatch.setattr("app.deps.get_redis", _get_redis)
    return TestClient(app), fake


def test_dismiss_records_set_membership(client):
    tc, fake = client
    r = tc.post("/settings/recommendations/sparse_retrieval/dismiss")
    assert r.status_code == 204
    assert "sparse_retrieval" in fake.sets["cerid:recommendations:dismissed:default"]


def test_dismiss_with_explicit_tenant_header(client):
    tc, fake = client
    r = tc.post(
        "/settings/recommendations/hype_indexing/dismiss",
        headers={"X-Cerid-Tenant": "tenant-a"},
    )
    assert r.status_code == 204
    assert "hype_indexing" in fake.sets["cerid:recommendations:dismissed:tenant-a"]


def test_dismiss_rejects_invalid_id(client):
    tc, _fake = client
    r = tc.post("/settings/recommendations//dismiss")
    # Either 404 from FastAPI's route mismatch or 400 from our handler;
    # both signal "rejected", which is what we care about.
    assert r.status_code in {400, 404}


def test_clear_drops_hash_and_dismissal(client):
    tc, fake = client
    fake.hash["sparse_retrieval"] = "{}"
    fake.sets["cerid:recommendations:dismissed:default"] = {"sparse_retrieval"}
    r = tc.delete("/settings/recommendations/sparse_retrieval")
    assert r.status_code == 204
    assert "sparse_retrieval" not in fake.hash
    assert "sparse_retrieval" not in fake.sets["cerid:recommendations:dismissed:default"]


def test_dismiss_idempotent(client):
    tc, fake = client
    tc.post("/settings/recommendations/sparse_retrieval/dismiss")
    r = tc.post("/settings/recommendations/sparse_retrieval/dismiss")
    assert r.status_code == 204
    assert fake.sets["cerid:recommendations:dismissed:default"] == {"sparse_retrieval"}
