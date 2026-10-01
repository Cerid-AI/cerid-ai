# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The retention sweep archives by age, except decisions and preferences.

A decision or a preference does not go stale because it is old, so the sweep
must leave them alone whatever their age. The memory type is carried on the
graph node only as the ``memory_{type}_`` filename prefix.

The graph driver is faked: it holds nodes and applies the sweep's own
parameters to them. It does not interpret Cypher, so the text of the WHERE
clause is outside what this test can see.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from core.agents.memory import archive_old_memories

_OLD = "2020-01-01T00:00:00"
_RECENT = "2999-01-01T00:00:00"


class _Result:
    def __init__(self, count: int) -> None:
        self._count = count

    def single(self) -> dict[str, int]:
        return {"archived_count": self._count}


class _Session:
    def __init__(self, nodes: list[dict[str, Any]]) -> None:
        self._nodes = nodes

    def __enter__(self) -> "_Session":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def run(self, _cypher: str, **params: Any) -> _Result:
        spared = tuple(params.get("spared_prefixes") or ())
        swept = [
            n for n in self._nodes
            if n["ingested_at"] < params["cutoff"]
            and not n.get("archived", False)
            and not (spared and (n.get("filename") or "").startswith(spared))
        ]
        for n in swept:
            n["archived"] = True
        return _Result(len(swept))


class _Driver:
    def __init__(self, nodes: list[dict[str, Any]]) -> None:
        self.nodes = nodes

    def session(self) -> _Session:
        return _Session(self.nodes)


@pytest.fixture(autouse=True)
def _no_cache_bust(monkeypatch):
    monkeypatch.setattr("utils.query_cache.invalidate_query_caches", lambda **_kw: None)


def _node(filename: str | None, ingested_at: str = _OLD) -> dict[str, Any]:
    return {"filename": filename, "ingested_at": ingested_at}


def _archived(driver: _Driver) -> set[str | None]:
    return {n["filename"] for n in driver.nodes if n.get("archived")}


def test_old_decisions_and_preferences_are_not_archived():
    driver = _Driver([
        _node("memory_decision_abcd1234_20200101_000000_0"),
        _node("memory_preference_abcd1234_20200101_000000_1"),
        _node("memory_fact_abcd1234_20200101_000000_2"),
        _node("memory_conversational_abcd1234_20200101_000000_3"),
    ])

    result = asyncio.run(archive_old_memories(driver, retention_days=90))

    assert _archived(driver) == {
        "memory_fact_abcd1234_20200101_000000_2",
        "memory_conversational_abcd1234_20200101_000000_3",
    }
    assert result["archived_count"] == 2


def test_other_old_memories_are_still_archived_and_recent_ones_are_not():
    driver = _Driver([
        _node("memory_project_context_abcd1234_20200101_000000_0"),
        _node(None),
        _node("memory_fact_abcd1234_29990101_000000_1", ingested_at=_RECENT),
    ])

    result = asyncio.run(archive_old_memories(driver, retention_days=90))

    assert _archived(driver) == {
        "memory_project_context_abcd1234_20200101_000000_0",
        None,
    }
    assert result["archived_count"] == 2
