# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The boot-time vector-space probe.

nomic-embed-text-v1.5 and snowflake-arctic-embed-m-v1.5 are both 768-dim, so the
dimension check cannot tell an index built under one from queries embedded by the
other — and on the personal stack the per-chunk ``embedding_model`` stamps said
Snowflake for chunks Quenchforge's nomic had produced. The probe re-embeds stored
chunks and requires they land on themselves, which is the invariant itself.
"""
from __future__ import annotations

import pytest

from app.startup.invariants import probe_vector_space


class _Collection:
    def __init__(self, docs: list[str], embeddings: list[list[float]]) -> None:
        self._docs, self._embs = docs, embeddings

    def get(self, limit: int, include: list[str]) -> dict:
        return {"documents": self._docs[:limit], "embeddings": self._embs[:limit]}


class _Client:
    def __init__(self, collections: dict[str, _Collection], names_only: bool = False) -> None:
        self._cols, self._names_only = collections, names_only

    def list_collections(self) -> list:
        if self._names_only:
            return list(self._cols)
        return [type("C", (), {"name": n})() for n in self._cols]

    def get_collection(self, **kwargs: str) -> _Collection:
        # Keyword-only, like app.deps._EmbeddingAwareClient. A fake that took the
        # name positionally let a probe pass here that did nothing on the stack.
        return self._cols[kwargs["name"]]


STORED = {"alpha": [1.0, 0.0, 0.0], "beta": [0.0, 1.0, 0.0]}


def _same_space(texts: list[str]) -> list[list[float]]:
    return [STORED[t] for t in texts]


def _other_model(texts: list[str]) -> list[list[float]]:
    return [[0.0, 0.0, 1.0] for _ in texts]  # orthogonal to everything stored


def test_the_embedder_that_built_the_index_passes() -> None:
    client = _Client({"domain_finance": _Collection(list(STORED), list(STORED.values()))})
    out = probe_vector_space(client, _same_space)
    assert out["status"] == "ok"
    assert out["collections_checked"] == 1


def test_a_different_model_at_the_same_width_is_reported() -> None:
    client = _Client({
        "domain_finance": _Collection(list(STORED), list(STORED.values())),
        "domain_notes": _Collection(list(STORED), list(STORED.values())),
    })
    out = probe_vector_space(client, _other_model)
    assert out["status"] == "mismatch"
    assert {m["collection"] for m in out["mismatched"]} == {"domain_finance", "domain_notes"}
    assert all(m["median_self_similarity"] < 0.9 for m in out["mismatched"])


def test_only_the_collections_built_under_the_other_model_are_named() -> None:
    """A provider flip mid-life leaves some collections in each space."""
    def mixed(texts: list[str]) -> list[list[float]]:
        return [STORED[t] if t in STORED else [0.0, 0.0, 1.0] for t in texts]

    client = _Client({
        "domain_finance": _Collection(list(STORED), list(STORED.values())),
        "domain_coding": _Collection(["gamma"], [[1.0, 0.0, 0.0]]),
    })
    out = probe_vector_space(client, mixed)
    assert out["status"] == "mismatch"
    assert [m["collection"] for m in out["mismatched"]] == ["domain_coding"]


def test_empty_collections_leave_the_index_unverified_not_healthy() -> None:
    client = _Client({"semantic_query_cache": _Collection([], [])})
    out = probe_vector_space(client, _same_space)
    assert out["status"] == "unverified"


@pytest.mark.parametrize("names_only", [False, True])
def test_either_list_collections_shape_is_probed(names_only: bool) -> None:
    """Clients that list bare names would otherwise resolve every collection to
    "<unknown>", skip them all, and report "unverified" forever."""
    client = _Client({"domain_finance": _Collection(list(STORED), list(STORED.values()))}, names_only)
    assert probe_vector_space(client, _other_model)["status"] == "mismatch"


class TestHealthReportsAMismatchWithoutRestartLooping:
    """A mismatch must be visible on /health, but at 200: a restart cannot change
    which model built the index, so a 503 would put the stack in a restart loop."""

    @staticmethod
    def _get(space: dict):
        from unittest.mock import patch

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import app.routers.health as h
        from app.routers.health import router

        h._health_payload_cache.value = {}
        h._health_payload_cache.updated_at = 0.0
        payload = {
            "status": "healthy",
            "services": {"chromadb": "connected", "redis": "connected", "neo4j": "connected"},
            "invariants": {"healthy_invariants": True, "embedding_vector_space": space},
        }
        app = FastAPI()
        app.include_router(router)
        try:
            with patch("app.routers.health._health_payload_cache._build", return_value=payload):
                return TestClient(app).get("/health")
        finally:
            h._health_payload_cache.value = {}
            h._health_payload_cache.updated_at = 0.0

    def test_mismatch_degrades_at_200(self) -> None:
        resp = self._get({"status": "mismatch", "mismatched": [{"collection": "domain_finance"}]})
        assert resp.status_code == 200
        assert resp.json()["status"] == "degraded"

    @pytest.mark.parametrize("status", ["ok", "unverified", "pending"])
    def test_anything_short_of_a_mismatch_leaves_status_alone(self, status: str) -> None:
        resp = self._get({"status": status})
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"
