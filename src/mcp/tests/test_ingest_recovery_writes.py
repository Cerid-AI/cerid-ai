# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A recovery pass writes to Neo4j only when there is something to repair.

Round 5 item 2. ``recover_artifact`` re-ran ``create_artifact`` for every
orphan group: a ``MERGE ... ON MATCH SET`` that is a write transaction even
when the node already carries the same chunk list — which is the common
shape once the Neo4j half of an ingest succeeded and only the Chroma flip to
``committed`` was lost. Those chunks stay ``pending`` until the flip lands,
so every cron tick re-wrote the same node. The pass now reads the node first
and writes only when it is missing or its chunk list differs.

The driver double records every statement and answers the one read the pass
makes; the job is driven end to end through ``IngestRecoveryJob.run``.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.processor.jobs.ingest_recovery import IngestRecoveryJob
from app.services import ingest_recovery

_WRITE_WORDS = ("MERGE", "CREATE", "SET ", "DELETE", "REMOVE")
_OLD = "2026-01-01T00:00:00+00:00"


class _Result:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def single(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _Tx:
    def __init__(self, driver: "_RecordingNeo4j"):
        self._driver = driver

    def run(self, query: str, **params: Any) -> _Result:
        self._driver.tx_statements.append(" ".join(query.split()))
        return _Result([{"id": params.get("artifact_id")}])


class _Session:
    def __init__(self, driver: "_RecordingNeo4j"):
        self._driver = driver

    def __enter__(self) -> "_Session":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def run(self, query: str, **params: Any) -> _Result:
        q = " ".join(query.split())
        if any(word in q for word in _WRITE_WORDS):
            self._driver.writes.append(q)
            return _Result([])
        self._driver.reads.append(q)
        node = self._driver.artifacts.get(params.get("artifact_id"))
        if "MATCH (a:Artifact {id: $artifact_id})" in q and node is not None:
            return _Result([{
                "id": node["id"], "filename": "f.txt", "domain": "coding",
                "sub_category": "", "tags": "[]", "keywords": "[]", "summary": "",
                "chunk_count": node["chunk_count"], "chunk_ids": node["chunk_ids"],
                "ingested_at": _OLD, "recategorized_at": None, "domain_name": "coding",
            }])
        return _Result([])

    def execute_write(self, fn, **kwargs: Any) -> Any:
        self._driver.writes.append(f"execute_write:{fn.__name__}")
        return fn(_Tx(self._driver), **kwargs)


class _RecordingNeo4j:
    """Records reads, write transactions, and statements inside those transactions."""

    def __init__(self, artifacts: dict[str, dict[str, Any]] | None = None):
        self.artifacts = artifacts or {}
        self.reads: list[str] = []
        self.writes: list[str] = []
        self.tx_statements: list[str] = []

    def session(self, *args: Any, **kwargs: Any) -> _Session:
        return _Session(self)


class _Collection:
    name = "coding"

    def __init__(self, pending: dict[str, dict[str, Any]]):
        self.rows = pending
        self.deleted: list[str] = []

    def get(self, **kwargs: Any) -> dict[str, Any]:
        ids = [cid for cid, meta in self.rows.items() if meta.get("cerid_state") == "pending"]
        return {
            "ids": ids,
            "documents": [f"text of {cid}" for cid in ids],
            "metadatas": [dict(self.rows[cid]) for cid in ids],
        }

    def update(self, ids: list[str], metadatas: list[dict[str, Any]]) -> None:
        for cid, patch in zip(ids, metadatas):
            self.rows[cid].update(patch)

    def delete(self, ids: list[str]) -> None:
        self.deleted.extend(ids)


class _Chroma:
    def __init__(self, collection: _Collection):
        self._collection = collection

    def list_collections(self) -> list[_Collection]:
        return [self._collection]

    def get_collection(self, *, name: str) -> _Collection:
        assert name == self._collection.name
        return self._collection


def _pending(artifact_id: str, *chunk_ids: str) -> dict[str, dict[str, Any]]:
    return {
        cid: {
            "artifact_id": artifact_id, "domain": "coding", "filename": "f.txt",
            "cerid_state": "pending", "cerid_pending_at": _OLD,
        }
        for cid in chunk_ids
    }


async def _run(monkeypatch, collection: _Collection, driver: _RecordingNeo4j) -> dict[str, Any]:
    monkeypatch.setattr(ingest_recovery, "get_chroma", lambda: _Chroma(collection))
    monkeypatch.setattr(ingest_recovery, "get_neo4j", lambda: driver)
    monkeypatch.setattr(ingest_recovery, "get_redis", lambda: None)

    async def progress(_pct: float) -> None:
        return None

    result = await IngestRecoveryJob(max_age_seconds=60.0).run(progress)
    return result.metadata


@pytest.mark.asyncio
async def test_a_pass_with_nothing_pending_never_opens_a_graph_session(monkeypatch):
    collection = _Collection({})
    driver = _RecordingNeo4j()

    stats = await _run(monkeypatch, collection, driver)

    assert stats["orphans_found"] == 0
    assert driver.reads == [] and driver.writes == []


@pytest.mark.asyncio
async def test_an_artifact_the_graph_already_holds_is_flipped_without_a_write(monkeypatch):
    collection = _Collection(_pending("art-1", "c1", "c2"))
    driver = _RecordingNeo4j({
        "art-1": {"id": "art-1", "chunk_count": 2, "chunk_ids": json.dumps(["c1", "c2"])},
    })

    stats = await _run(monkeypatch, collection, driver)

    assert stats["committed"] == 1
    assert driver.writes == [], driver.writes
    assert len(driver.reads) == 1
    assert {meta["cerid_state"] for meta in collection.rows.values()} == {"committed"}


@pytest.mark.asyncio
async def test_a_half_done_ingest_gets_exactly_one_repair_write(monkeypatch):
    collection = _Collection(_pending("art-1", "c1", "c2"))
    driver = _RecordingNeo4j()

    stats = await _run(monkeypatch, collection, driver)

    assert stats["committed"] == 1
    assert driver.writes == ["execute_write:_create_artifact_tx"]
    merge = driver.tx_statements[0]
    assert "MERGE (a:Artifact {id: $artifact_id})" in merge
    assert {meta["cerid_state"] for meta in collection.rows.values()} == {"committed"}


@pytest.mark.asyncio
async def test_a_node_with_a_different_chunk_list_is_repaired(monkeypatch):
    collection = _Collection(_pending("art-1", "c1", "c2"))
    driver = _RecordingNeo4j({
        "art-1": {"id": "art-1", "chunk_count": 1, "chunk_ids": json.dumps(["c1"])},
    })

    stats = await _run(monkeypatch, collection, driver)

    assert stats["committed"] == 1
    assert driver.writes == ["execute_write:_create_artifact_tx"]
