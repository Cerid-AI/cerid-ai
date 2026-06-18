# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Supersession-at-read filtering in recall_memories (Fix C).

A memory explicitly marked ``superseded_by`` (by the write-path conflict
resolution) must not be surfaced at recall — recall historically ignored the
flag and could return a stale value alongside its replacement.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.agents.memory import recall_memories

_HIGH_ENTAILMENT = {"entailment": 0.9, "neutral": 0.05, "contradiction": 0.05}


def _chroma(ids: list[str]) -> MagicMock:
    metas = [
        {
            "artifact_id": aid,
            "memory_type": "empirical",
            "valid_from": "2025-01-01T00:00:00Z",
            "access_count": "0",
            "summary": f"summary-{aid}",
        }
        for aid in ids
    ]
    collection = MagicMock()
    collection.query.return_value = {
        "ids": [ids],
        "documents": [[f"doc {aid}" for aid in ids]],
        "distances": [[0.1] * len(ids)],
        "metadatas": [metas],
    }
    client = MagicMock()
    client.get_or_create_collection.return_value = collection
    return client


class _FakeSession:
    def __init__(self, superseded: set[str]) -> None:
        self._superseded = superseded

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *a) -> bool:
        return False

    def run(self, cypher: str, **kw):  # type: ignore[no-untyped-def]
        if "superseded_by IS NOT NULL" in cypher:
            ids = kw.get("ids", [])
            return [{"id": sid} for sid in ids if sid in self._superseded]
        return []  # reinforcement query


class _FakeDriver:
    def __init__(self, superseded: set[str]) -> None:
        self._superseded = superseded

    def session(self) -> _FakeSession:
        return _FakeSession(self._superseded)


@pytest.mark.asyncio
async def test_recall_filters_superseded_memory() -> None:
    chroma = _chroma(["a", "b"])
    driver = _FakeDriver(superseded={"a"})
    with patch("core.utils.nli.nli_score", return_value=_HIGH_ENTAILMENT):
        results = await recall_memories("q", chroma, driver, top_k=10)
    got = {m["memory_id"] for m in results}
    assert "b" in got
    assert "a" not in got  # superseded → filtered at read


@pytest.mark.asyncio
async def test_recall_keeps_superseded_when_flag_off(monkeypatch) -> None:
    monkeypatch.setattr(
        "config.features.ENABLE_MEMORY_SUPERSESSION_FILTER", False,
    )
    chroma = _chroma(["a", "b"])
    driver = _FakeDriver(superseded={"a"})
    with patch("core.utils.nli.nli_score", return_value=_HIGH_ENTAILMENT):
        results = await recall_memories("q", chroma, driver, top_k=10)
    got = {m["memory_id"] for m in results}
    assert got == {"a", "b"}  # flag off → no supersession filtering


@pytest.mark.asyncio
async def test_recall_unaffected_without_neo4j() -> None:
    chroma = _chroma(["a", "b"])
    with patch("core.utils.nli.nli_score", return_value=_HIGH_ENTAILMENT):
        results = await recall_memories("q", chroma, None, top_k=10)
    assert {m["memory_id"] for m in results} == {"a", "b"}
