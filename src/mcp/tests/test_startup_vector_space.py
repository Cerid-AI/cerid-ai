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
        self.offsets_read: list[int] = []

    def count(self) -> int:
        return len(self._docs)

    def get(self, limit: int, include: list[str], offset: int = 0) -> dict:
        self.offsets_read.append(offset)
        return {
            "documents": self._docs[offset:offset + limit],
            "embeddings": self._embs[offset:offset + limit],
        }


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


def _collection_with_minority(total: int, out_of_space_at: list[int]) -> tuple[_Collection, dict[str, list[float]]]:
    """``total`` chunks whose leading rows are all in-space; the rows at
    ``out_of_space_at`` were produced by the other model."""
    stored: dict[str, list[float]] = {}
    for i in range(total):
        stored[f"chunk{i}"] = [1.0, 0.0, 0.0] if i not in out_of_space_at else [0.0, 1.0, 0.0]
    return _Collection(list(stored), list(stored.values())), stored


def _serving(texts: list[str]) -> list[list[float]]:
    return [[1.0, 0.0, 0.0] for _ in texts]  # the serving model's view of every chunk


def test_a_minority_behind_in_space_leaders_is_seen_and_reported() -> None:
    """Measured on the Studio 2026-10-06: the first three chunks of ``coding``
    were in-space while 26% of the collection was not, and the probe said ok.
    A spread sample has to see a 25% minority and name it with its fraction."""
    col, _ = _collection_with_minority(20, [i for i in range(3, 20) if i % 3 == 2])
    out = probe_vector_space(_Client({"domain_coding": col}), _serving)
    assert out["status"] == "mismatch"
    (m,) = out["mismatched"]
    assert m["collection"] == "domain_coding"
    assert m["out_of_space"] >= 1
    assert m["fraction"] > 0.1
    assert m["sampled"] == 12
    # The median is still in-space here: the fraction rule is what fires.
    assert m["median_self_similarity"] >= 0.9


def test_the_sample_is_spread_over_the_whole_collection() -> None:
    col, _ = _collection_with_minority(120, [])
    probe_vector_space(_Client({"domain_mail": col}), _serving)
    assert len(col.offsets_read) == 12
    assert min(col.offsets_read) == 0
    assert max(col.offsets_read) >= 100
    assert col.offsets_read == sorted(col.offsets_read)


def test_a_collection_smaller_than_the_sample_is_probed_whole() -> None:
    col, _ = _collection_with_minority(5, [4])
    out = probe_vector_space(_Client({"domain_notes": col}), _serving)
    assert col.offsets_read == [0]
    (m,) = out["mismatched"]
    assert m["sampled"] == 5
    assert m["out_of_space"] == 1
    assert m["fraction"] == 0.2


def test_a_few_strays_under_the_fraction_floor_stay_ok() -> None:
    col, _ = _collection_with_minority(120, [57])  # one stray in 120, sampled or not
    out = probe_vector_space(_Client({"domain_mail": col}), _serving)
    assert out["status"] == "ok"
    assert out["max_fraction"] <= 0.1


def test_totals_are_reported_across_collections() -> None:
    bad, _ = _collection_with_minority(5, [0, 1, 2, 3, 4])
    good, _ = _collection_with_minority(5, [])
    out = probe_vector_space(_Client({"domain_coding": bad, "domain_notes": good}), _serving)
    assert out["status"] == "mismatch"
    assert out["collections_checked"] == 2
    assert out["sampled"] == 10
    assert out["out_of_space"] == 5
    assert out["max_fraction"] == 1.0
    assert {c["collection"] for c in out["collections"]} == {"domain_coding", "domain_notes"}


class TestTheSnapshotCarriesWhenItWasTaken:
    """``/health`` serves the last probe result; a reader has to be able to tell a
    fresh one from a boot-time result the embedder was down for."""

    @staticmethod
    def _run(monkeypatch, embed):
        import app.deps as deps
        import app.startup.invariants as inv
        import core.utils.embeddings as embeddings

        col, _ = _collection_with_minority(4, [])
        monkeypatch.setattr(deps, "get_chroma", lambda: _Client({"domain_finance": col}))
        monkeypatch.setattr(embeddings, "get_embedding_function", lambda: embed)
        monkeypatch.setattr(inv, "_vector_space_snapshot", {"status": "pending"})
        return inv.run_startup_vector_space_check(), inv.get_vector_space_snapshot()

    def test_checked_at_is_an_iso_utc_timestamp(self, monkeypatch) -> None:
        from datetime import datetime, timezone

        result, snap = self._run(monkeypatch, _serving)
        assert result["status"] == "ok"
        assert snap["checked_at"] == result["checked_at"]
        parsed = datetime.fromisoformat(result["checked_at"])
        assert parsed.tzinfo is not None
        assert abs((datetime.now(tz=timezone.utc) - parsed).total_seconds()) < 60

    def test_an_embedder_failure_is_dated_too(self, monkeypatch) -> None:
        def hung(texts: list[str]) -> list[list[float]]:
            raise TimeoutError("ReadTimeout")

        result, _ = self._run(monkeypatch, hung)
        assert result["status"] == "unverified"
        assert "checked_at" in result


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
