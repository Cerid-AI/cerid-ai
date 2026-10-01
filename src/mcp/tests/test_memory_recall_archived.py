# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Recall must not return a memory whose artifact is archived.

``archived`` is set on the Neo4j node by the retention sweep and by soft
delete and quarantine. The flag is not in the vector store's metadata, so
recall has to ask the graph; it never did, and archived memories kept coming
back.

The vector store, the graph driver and the NLI scorer are faked; recall runs
for real.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from core.agents import memory
from core.agents.memory import recall_memories


class _Collection:
    def __init__(self, ids: list[str]) -> None:
        self._ids = ids

    def query(self, **_kw: Any) -> dict[str, Any]:
        return {
            "ids": [self._ids],
            "documents": [[f"doc {i}" for i in self._ids]],
            "distances": [[0.1] * len(self._ids)],
            "metadatas": [[
                {
                    "artifact_id": i,
                    "memory_type": "empirical",
                    "valid_from": "2025-01-01T00:00:00Z",
                    "access_count": "0",
                }
                for i in self._ids
            ]],
        }


class _Chroma:
    def __init__(self, ids: list[str]) -> None:
        self._collection = _Collection(ids)

    def get_or_create_collection(self, name: str) -> _Collection:
        return self._collection


class _Session:
    def __init__(self, driver: "_Driver") -> None:
        self._driver = driver

    def __enter__(self) -> "_Session":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def run(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        if "SET " in cypher:
            self._driver.reinforced.extend(params.get("ids", []))
            return []
        if "archived" in cypher:
            return [
                {"id": i}
                for i in params.get("ids", [])
                if self._driver.nodes.get(i, {}).get("archived", False)
            ]
        return []


class _Driver:
    def __init__(self, nodes: dict[str, dict[str, Any]]) -> None:
        self.nodes = nodes
        self.reinforced: list[str] = []

    def session(self) -> _Session:
        return _Session(self)


def _recall(monkeypatch: pytest.MonkeyPatch, driver: _Driver | None) -> list[dict]:
    monkeypatch.setattr(memory.config, "ENABLE_MEMORY_RECALL", True)
    monkeypatch.setattr(
        "core.utils.nli.nli_score",
        lambda _p, _h: {"entailment": 0.9, "neutral": 0.05, "contradiction": 0.05},
    )
    return asyncio.run(recall_memories("q", _Chroma(["kept", "gone"]), driver, top_k=10))


def test_an_archived_memory_is_not_recalled(monkeypatch):
    driver = _Driver({"kept": {"archived": False}, "gone": {"archived": True}})

    results = _recall(monkeypatch, driver)

    assert {m["memory_id"] for m in results} == {"kept"}
    assert driver.reinforced == ["kept"], "an archived memory had its access count raised"


def test_a_memory_with_no_archived_property_is_recalled(monkeypatch):
    results = _recall(monkeypatch, _Driver({"kept": {}, "gone": {"archived": False}}))

    assert {m["memory_id"] for m in results} == {"kept", "gone"}
