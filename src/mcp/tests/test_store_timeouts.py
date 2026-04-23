# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase 2.1 — store timeout enforcement.

Verifies:

* ``with_timeout`` returns the wrapped value when the awaitable finishes
  under budget, raises ``StoreTimeoutError`` when it doesn't.
* ``StoreTimeoutError`` is a subclass of ``asyncio.TimeoutError`` so
  legacy handlers continue to work, but new code can pattern-match on
  the typed subclass.
* ``ChromaVectorStore`` and ``Neo4jGraphStore`` propagate
  ``StoreTimeoutError`` when the underlying driver hangs longer than
  the configured budget — i.e. the request bails out rather than
  pinning the event loop until the request budget runs out.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from app.stores.chroma_store import ChromaVectorStore
from app.stores.neo4j_store import Neo4jGraphStore
from core.utils.timeouts import StoreTimeoutError, with_timeout

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# with_timeout — pure helper semantics
# ---------------------------------------------------------------------------


async def test_with_timeout_returns_value_under_budget() -> None:
    async def _fast() -> str:
        return "ok"

    assert await with_timeout(_fast(), seconds=1.0, label="test.fast") == "ok"


async def test_with_timeout_raises_store_timeout_error_when_slow() -> None:
    async def _slow() -> None:
        await asyncio.sleep(0.5)

    with pytest.raises(StoreTimeoutError) as ei:
        await with_timeout(_slow(), seconds=0.05, label="test.slow")
    assert ei.value.label == "test.slow"
    assert ei.value.seconds == pytest.approx(0.05)


async def test_store_timeout_error_is_asyncio_timeout_subclass() -> None:
    async def _slow() -> None:
        await asyncio.sleep(0.5)

    # Legacy code catching asyncio.TimeoutError must still work.
    with pytest.raises(asyncio.TimeoutError):
        await with_timeout(_slow(), seconds=0.05, label="test.subclass")


# ---------------------------------------------------------------------------
# ChromaVectorStore — sync driver hang must surface as StoreTimeoutError
# ---------------------------------------------------------------------------


class _SlowCollection:
    """Fake chromadb collection whose sync calls block the worker thread."""

    def __init__(self, sleep_s: float) -> None:
        self._sleep_s = sleep_s

    def query(self, **_: Any) -> dict[str, Any]:
        time.sleep(self._sleep_s)
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    def get(self, **_: Any) -> dict[str, Any]:
        time.sleep(self._sleep_s)
        return {"ids": [], "documents": [], "metadatas": []}

    def count(self) -> int:
        time.sleep(self._sleep_s)
        return 0


async def test_chroma_search_times_out_when_collection_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.stores.chroma_store.CHROMA_QUERY_TIMEOUT", 0.05)
    store = ChromaVectorStore(_SlowCollection(sleep_s=0.5))

    with pytest.raises(StoreTimeoutError) as ei:
        await store.search([0.0, 0.0, 0.0], top_k=5)
    assert ei.value.label == "chroma.query"


async def test_chroma_count_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.stores.chroma_store.CHROMA_QUERY_TIMEOUT", 0.05)
    store = ChromaVectorStore(_SlowCollection(sleep_s=0.5))

    with pytest.raises(StoreTimeoutError) as ei:
        await store.count()
    assert ei.value.label == "chroma.count"


# ---------------------------------------------------------------------------
# Neo4jGraphStore — sync driver hang must surface as StoreTimeoutError
# ---------------------------------------------------------------------------


class _SlowDriver:
    """Fake neo4j driver whose execute_query blocks; used by list_domains."""

    def __init__(self, sleep_s: float) -> None:
        self._sleep_s = sleep_s

    def execute_query(self, *_: Any, **__: Any) -> tuple[list[Any], Any, Any]:
        time.sleep(self._sleep_s)
        return ([], None, None)


async def test_neo4j_list_domains_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.stores.neo4j_store.NEO4J_QUERY_TIMEOUT", 0.05)
    store = Neo4jGraphStore(_SlowDriver(sleep_s=0.5))

    with pytest.raises(StoreTimeoutError) as ei:
        await store.list_domains()
    assert ei.value.label == "neo4j.list_domains"


async def test_neo4j_get_artifact_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_artifact wraps a sync helper from app.db.neo4j.artifacts —
    monkeypatch that helper to sleep, then assert the timeout fires."""

    def _slow_get_artifact(_driver: Any, _artifact_id: str) -> dict[str, Any]:
        time.sleep(0.5)
        return {}

    monkeypatch.setattr("app.stores.neo4j_store.NEO4J_QUERY_TIMEOUT", 0.05)
    monkeypatch.setattr(
        "app.db.neo4j.artifacts.get_artifact", _slow_get_artifact,
    )
    store = Neo4jGraphStore(driver=object())

    with pytest.raises(StoreTimeoutError) as ei:
        await store.get_artifact("artifact-123")
    assert ei.value.label == "neo4j.get_artifact"
