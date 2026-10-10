# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The lineage writer's store-independent parts, and the extraction path's use
of it. Its Cypher runs against Neo4j in tests/integration/test_preservation_lineage.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.lineage.current import is_current, is_current_memory
from core.lineage.writer import SupersedeResult, stamp_rows, supersede
from tests.helpers.fake_chroma import FakeChromaCollection


def _collection() -> FakeChromaCollection:
    col = FakeChromaCollection("conversations")
    for cid, aid in (("r1", "old"), ("r2", "old"), ("r3", "new"), ("r4", "other")):
        col.upsert(ids=[cid], documents=[cid], embeddings=[[1.0, 0.0]],
                   metadatas=[{"artifact_id": aid, "memory_type": "fact", "decay_anchor": "keep"}])
    return col


def test_stamp_rows_writes_lineage_fields_on_every_row_of_each_version():
    col = _collection()
    previous = stamp_rows(col, [
        {"id": "old", "lineage_id": "old", "version": 1, "valid_to": "2026-05-01", "superseded_by": "new"},
        {"id": "new", "lineage_id": "old", "version": 2, "valid_to": None, "superseded_by": None},
    ])
    for cid in ("r1", "r2"):
        meta = col.metadata_of(cid)
        assert meta["valid_to"] == "2026-05-01" and meta["superseded_by"] == "new" and meta["version"] == 1
        assert meta["decay_anchor"] == "keep"  # every other key is kept
    assert col.metadata_of("r3") == {**col.metadata_of("r3"), "lineage_id": "old", "version": 2,
                                     "valid_to": "", "superseded_by": ""}
    assert "lineage_id" not in col.metadata_of("r4")
    assert sorted(previous[0][0]) == ["r1", "r2", "r3"]


@pytest.mark.parametrize("old,new,chroma,reason", [
    ("a", "a", object(), "itself"),
    ("", "b", object(), "itself"),
    ("a", "b", None, "vector store"),
])
def test_supersede_refuses_before_touching_the_graph(old, new, chroma, reason):
    class NoGraph:
        def session(self):
            raise AssertionError("the graph must not be touched")

    result = supersede(NoGraph(), chroma, old, new)
    assert not result.ok and reason in result.reason


@pytest.mark.parametrize("meta,current", [
    ({}, True), ({"valid_to": ""}, True), ({"valid_to": "2026-05-01"}, False),
    ({"superseded_by": "x"}, False), (None, True),
])
def test_current_means_an_empty_valid_to(meta, current):
    assert is_current(meta) is current


def test_a_transcript_row_is_never_a_memory_candidate():
    assert not is_current_memory({"artifact_id": "chat_1"})
    assert is_current_memory({"memory_type": "fact"})
    assert not is_current_memory({"memory_type": "fact", "valid_to": "2026-05-01"})


@pytest.mark.asyncio
@pytest.mark.parametrize("ok,routed", [(True, True), (False, False)])
async def test_the_ledger_hears_only_of_a_supersession_that_landed(ok, routed):
    from core.agents import memory

    col = _collection()

    class Client:
        def get_or_create_collection(self, **_kw):
            return col

    with patch("core.lineage.writer.supersede", return_value=SupersedeResult(ok, reason="refused")) as sup, \
         patch("core.agents.fact_invalidation.route_supersession_to_ledger", new_callable=AsyncMock) as route:
        await memory._supersede_memory(object(), Client(), "old", "new", valid_to="2026-05-01", new_content="NEW")
    assert sup.call_args.kwargs == {"valid_to": "2026-05-01"}
    assert route.await_count == (1 if routed else 0)
    if routed:
        assert route.await_args.kwargs == {"old_content": "r1", "new_content": "NEW", "old_artifact_id": "old"}


@pytest.mark.asyncio
async def test_a_failing_writer_never_loses_the_new_memory():
    from core.agents import memory

    with patch("core.lineage.writer.supersede", side_effect=RuntimeError("graph down")), \
         patch("core.agents.fact_invalidation.route_supersession_to_ledger", new_callable=AsyncMock) as route:
        await memory._supersede_memory(object(), None, "old", "new", valid_to="", new_content="NEW")
    route.assert_not_awaited()
