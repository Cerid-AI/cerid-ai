# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""History notes on returned versions, and ``as_of`` on the request surfaces
(forget phase 5, spec §7)."""
from __future__ import annotations

from typing import Any

import pytest

from core.lineage.history import attach_history, history_note
from tests.helpers.fake_chroma import FakeChromaClient, FakeChromaCollection


def _client() -> tuple[FakeChromaClient, FakeChromaCollection]:
    col = FakeChromaCollection("conversations")
    rows = [
        ("v1_0", "v1", 1, "Office is on the 3rd floor", "2025-01-01", "2025-06-01", 0),
        ("v2_1", "v2", 2, "second chunk of v2", "2025-06-01", "2026-05-01", 1),
        ("v2_0", "v2", 2, "Source: memory_x | Domain: conversations\n\nOffice is on the 5th floor",
         "2025-06-01", "2026-05-01", 0),
        ("v3_0", "v3", 3, "Office is on the 9th floor", "2026-05-01", "", 0),
        ("other_0", "other", 1, "unrelated", "2025-01-01", "", 0),
    ]
    for cid, aid, version, doc, vf, vt, index in rows:
        col.upsert(ids=[cid], documents=[doc], embeddings=[[1.0, 0.0]], metadatas=[{
            "artifact_id": aid, "lineage_id": "other" if aid == "other" else "v1", "version": version,
            "valid_from": vf, "valid_to": vt, "chunk_index": index,
        }])
    return FakeChromaClient([col]), col


class _Graph:
    """Answers the hidden-version query: these ids are archived, flagged or retired."""

    def __init__(self, hidden: set[str] | None = None) -> None:
        self.hidden = hidden or set()
        self.asked: list[list[str]] = []

    def session(self) -> _Graph:
        return self

    def __enter__(self) -> _Graph:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def run(self, cypher: str, **p: Any):
        self.asked.append(list(p["ids"]))
        return [{"id": i} for i in p["ids"] if i in self.hidden]


def _current() -> dict[str, Any]:
    return {"artifact_id": "v3", "chunk_id": "v3_0", "collection": "conversations", "lineage_id": "v1",
            "version": 3, "content": "Office is on the 9th floor"}


def test_a_current_version_carries_its_earlier_ones_newest_first(monkeypatch):
    monkeypatch.setattr("core.lineage.history.forgotten_ids", lambda kind: frozenset())
    client, _ = _client()
    row = _current()
    attach_history([row], client, _Graph())
    assert row["history"] == [
        {"value": "Office is on the 5th floor", "valid_from": "2025-06-01", "valid_to": "2026-05-01"},
        {"value": "Office is on the 3rd floor", "valid_from": "2025-01-01", "valid_to": "2025-06-01"},
    ]
    assert history_note(row).splitlines() == [
        "Earlier versions (newest first):",
        "- until 2026-05-01: Office is on the 5th floor",
        "- until 2025-06-01: Office is on the 3rd floor",
    ]


def test_forgotten_versions_never_appear_in_history(monkeypatch):
    monkeypatch.setattr("core.lineage.history.forgotten_ids",
                        lambda kind: frozenset({"v2"}) if kind == "artifact" else frozenset())
    client, _ = _client()
    row = _current()
    attach_history([row], client, _Graph())
    assert [h["value"] for h in row["history"]] == ["Office is on the 3rd floor"]


def test_rows_without_history_are_left_alone_and_read_nothing(monkeypatch):
    monkeypatch.setattr("core.lineage.history.forgotten_ids", lambda kind: frozenset())

    class NoReads:
        def get_or_create_collection(self, **_kw):
            raise AssertionError("no lineage to read")

    rows = [{"artifact_id": "a", "version": 1, "lineage_id": "a"}, {"artifact_id": "b"}]
    attach_history(rows, NoReads(), _Graph())
    assert all("history" not in r for r in rows)
    assert history_note(rows[0]) == ""


def test_a_forgotten_passage_never_appears_in_history(monkeypatch):
    monkeypatch.setattr("core.lineage.history.forgotten_ids",
                        lambda kind: frozenset({"v2_0"}) if kind == "chunk" else frozenset())
    client, _ = _client()
    row = _current()
    attach_history([row], client, _Graph())
    # v2's first passage is forgotten; its other passage stands for it
    assert [h["value"] for h in row["history"]] == ["second chunk of v2", "Office is on the 3rd floor"]


def test_archived_flagged_or_retired_versions_stay_hidden(monkeypatch):
    monkeypatch.setattr("core.lineage.history.forgotten_ids", lambda kind: frozenset())
    client, _ = _client()
    row = _current()
    graph = _Graph(hidden={"v2"})
    attach_history([row], client, graph)
    assert [h["value"] for h in row["history"]] == ["Office is on the 3rd floor"]
    assert graph.asked == [["v1", "v2", "v3"]]  # one graph read for every candidate


def test_without_the_graph_no_history_is_shown(monkeypatch):
    monkeypatch.setattr("core.lineage.history.forgotten_ids", lambda kind: frozenset())
    client, _ = _client()
    row = _current()
    attach_history([row], client, None)
    assert "history" not in row


def test_the_lineage_read_is_tenant_scoped(monkeypatch):
    monkeypatch.setattr("core.lineage.history.forgotten_ids", lambda kind: frozenset())
    scoped: list[dict[str, Any]] = []

    def scope(where):
        scoped.append(where)
        return where

    monkeypatch.setattr("core.lineage.history.with_tenant_scope", scope)
    client, _ = _client()
    attach_history([_current()], client, _Graph())
    assert scoped == [{"lineage_id": "v1"}]


def test_history_is_limited(monkeypatch):
    monkeypatch.setattr("core.lineage.history.forgotten_ids", lambda kind: frozenset())
    client, _ = _client()
    row = _current()
    attach_history([row], client, _Graph(), limit=1)
    assert len(row["history"]) == 1


@pytest.mark.parametrize("value,ok", [
    ("2023-12-31", True), ("2023-12-31T10:00:00Z", True), ("2023-12-31T10:00:00+02:00", True),
    ("last year", False), ("2023-13", False), ("", True), (None, True),
])
def test_mcp_as_of_is_validated(value, ok):
    from app.tool_registry import InvalidParamsError
    from app.tools import _as_of

    if ok:
        assert _as_of({"as_of": value}) == (value or None)
    else:
        with pytest.raises(InvalidParamsError):
            _as_of({"as_of": value})


def test_the_query_route_refuses_a_malformed_as_of():
    from pydantic import ValidationError

    from app.routers.agents import AgentQueryRequest, MemoryRecallRequest

    assert AgentQueryRequest(query="q", as_of="2023-12-31").as_of == "2023-12-31"
    for model in (AgentQueryRequest, MemoryRecallRequest):
        with pytest.raises(ValidationError):
            model(query="q", as_of="yesterday")


@pytest.mark.asyncio
async def test_the_memory_surface_asks_as_of_and_keeps_version_fields(monkeypatch):
    from core.agents import query_agent

    seen: dict[str, Any] = {}

    async def recall(query, **kw):
        seen.update(kw)
        return [{"text": "Office is on the 5th floor", "adjusted_score": 0.9, "memory_id": "v2",
                 "valid_from": "2025-06-01", "valid_to": "2026-05-01", "superseded_by": "v3",
                 "lineage_id": "v1", "version": 2,
                 "history": [{"value": "3rd floor", "valid_from": "2025-01-01", "valid_to": "2025-06-01"}]}]

    monkeypatch.setattr("core.agents.memory.recall_memories", recall)
    rows = await query_agent._recall_memory_surface("which floor", None, None, 5, as_of="2025-12-31")
    assert seen["as_of"] == "2025-12-31"
    assert rows[0]["valid_to"] == "2026-05-01" and rows[0]["superseded_by"] == "v3"
    assert rows[0]["history"][0]["value"] == "3rd floor"
