# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase D — bi-temporal interval closure (core/agents/fact_invalidation.py).

In-memory fakes model the closure Cypher (STATE facts with no other live
provenance close; EVENT facts and still-live STATE facts stay open) and the
Chroma mirror-close (only ``valid_to`` moves; ``decay_anchor`` and every other
key are preserved — the i20b decay contract must be untouched). The D2 ledger
routing is exercised with a patched NLI + sink: a genuine disagreement reaches
the sink, an orderly update does not, and closure never depends on NLI.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.agents.fact_invalidation import close_superseded_memory_intervals

_STATE_PREDICATE = "empirical"
_EVENT_PREDICATE = "conversational"
_NOW = "2026-07-14T00:00:00Z"


# ---------------------------------------------------------------------------
# Fakes: Neo4j closure + Chroma get/update
# ---------------------------------------------------------------------------


class _FakeFact:
    def __init__(self, uid: str, predicate: str, valid_from: str) -> None:
        self.uid = uid
        self.predicate = predicate
        self.valid_from = valid_from
        self.valid_to: str | None = None
        self.invalid_at: str | None = None


class _FakeGraph:
    """Applies _CLOSE_INTERVALS_CYPHER's MATCH/WHERE/SET semantics in memory."""

    def __init__(
        self,
        *,
        artifacts: dict[str, str | None],
        facts: dict[str, _FakeFact],
        provenance: dict[str, set[str]],
    ) -> None:
        self.artifacts = artifacts        # artifact_id -> superseded_by (None = live)
        self.facts = facts                # uid -> _FakeFact
        self.provenance = provenance      # fact_uid -> {artifact_id, ...}

    def close(self, *, old_id, state_predicates, valid_to, now) -> int:
        preds = set(state_predicates)
        closed = 0
        for uid, fact in self.facts.items():
            if old_id not in self.provenance.get(uid, set()):
                continue  # not reachable from old via :FACT
            if fact.invalid_at is not None:
                continue  # f.invalid_at IS NULL
            if fact.predicate not in preds:
                continue  # STATE only; EVENT facts untouched
            other_live = any(
                o != old_id and self.artifacts.get(o) is None
                for o in self.provenance.get(uid, set())
            )
            if other_live:
                continue  # NOT EXISTS other live provenance
            fact.valid_to = valid_to
            fact.invalid_at = now
            closed += 1
        return closed


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def single(self):
        return self._row


class _FakeSession:
    def __init__(self, graph: _FakeGraph) -> None:
        self._graph = graph

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def run(self, cypher: str, **params):
        if "SET f.valid_to" in cypher:
            return _FakeResult({"closed": self._graph.close(**params)})
        return _FakeResult(None)


class _FakeDriver:
    def __init__(self, graph: _FakeGraph) -> None:
        self._graph = graph

    def session(self) -> _FakeSession:
        return _FakeSession(self._graph)


class _FakeCollection:
    def __init__(
        self,
        *,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict],
        raise_on: str | None = None,
    ) -> None:
        self._ids = ids
        self._documents = documents
        self.metadatas = metadatas
        self.updated: dict | None = None
        self._raise_on = raise_on

    def get(self, where=None, include=None):
        if self._raise_on == "get":
            raise RuntimeError("boom get")
        return {
            "ids": list(self._ids),
            "documents": list(self._documents),
            "metadatas": [dict(m) for m in self.metadatas],
        }

    def update(self, ids=None, metadatas=None):
        if self._raise_on == "update":
            raise RuntimeError("boom update")
        self.updated = {"ids": ids, "metadatas": metadatas}
        index = {cid: k for k, cid in enumerate(self._ids)}
        for cid, meta in zip(ids, metadatas):
            self.metadatas[index[cid]] = meta


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _graph_single_state(valid_from: str = "2026-03-01") -> _FakeGraph:
    """One STATE fact provenanced only by the (superseded) old artifact."""
    return _FakeGraph(
        artifacts={"old": "new", "new": None},
        facts={"old|empirical": _FakeFact("old|empirical", _STATE_PREDICATE, valid_from)},
        provenance={"old|empirical": {"old"}},
    )


def _collection(
    *,
    valid_to: str = "",
    documents: list[str] | None = None,
    raise_on: str | None = None,
) -> _FakeCollection:
    return _FakeCollection(
        ids=["chunk-1"],
        documents=documents if documents is not None else ["old value: office is on 5th"],
        metadatas=[
            {
                "artifact_id": "old",
                "memory_type": _STATE_PREDICATE,
                "valid_from": "2026-03-01",
                "valid_to": valid_to,
                "decay_anchor": "2026-03-01T00:00:00Z",
                "access_count": "3",
                "summary": "office location",
            }
        ],
        raise_on=raise_on,
    )


async def _run(driver, collection, *, new_valid_from="2026-05-01", new_content="new value: office is on 9th", now=_NOW):
    return await close_superseded_memory_intervals(
        driver,
        collection,
        old_artifact_id="old",
        new_artifact_id="new",
        new_valid_from=new_valid_from,
        new_content=new_content,
        now=now,
    )


# NLI helpers
def _nli(contradiction: float):
    return [{"contradiction": contradiction, "entailment": 0.1, "neutral": 0.1, "label": "x"}]


# ---------------------------------------------------------------------------
# Neo4j closure: STATE vs EVENT, provenance liveness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_state_fact_solely_provenanced_closes() -> None:
    graph = _graph_single_state()
    with patch("core.agents.fact_invalidation.batch_nli_score", return_value=_nli(0.1)):
        closed = await _run(_FakeDriver(graph), _collection())
    fact = graph.facts["old|empirical"]
    assert closed == 1
    assert fact.valid_to == "2026-05-01"   # world time = new_valid_from
    assert fact.invalid_at == _NOW          # system time = now


@pytest.mark.asyncio
async def test_event_fact_untouched() -> None:
    graph = _FakeGraph(
        artifacts={"old": "new", "new": None},
        facts={"old|conv|2026-03-01": _FakeFact("old|conv|2026-03-01", _EVENT_PREDICATE, "2026-03-01")},
        provenance={"old|conv|2026-03-01": {"old"}},
    )
    with patch("core.agents.fact_invalidation.batch_nli_score", return_value=_nli(0.1)):
        closed = await _run(_FakeDriver(graph), _collection())
    fact = graph.facts["old|conv|2026-03-01"]
    assert closed == 0
    assert fact.valid_to is None      # EVENT facts coexist — never closed
    assert fact.invalid_at is None


@pytest.mark.asyncio
async def test_state_fact_with_second_live_provenance_stays_open() -> None:
    graph = _FakeGraph(
        artifacts={"old": "new", "new": None, "other": None},  # other is LIVE
        facts={"old|empirical": _FakeFact("old|empirical", _STATE_PREDICATE, "2026-03-01")},
        provenance={"old|empirical": {"old", "other"}},
    )
    with patch("core.agents.fact_invalidation.batch_nli_score", return_value=_nli(0.1)):
        closed = await _run(_FakeDriver(graph), _collection())
    fact = graph.facts["old|empirical"]
    assert closed == 0
    assert fact.valid_to is None      # still live via `other`
    assert fact.invalid_at is None


@pytest.mark.asyncio
async def test_state_fact_with_superseded_second_provenance_closes() -> None:
    graph = _FakeGraph(
        artifacts={"old": "new", "new": None, "other": "new"},  # other is superseded too
        facts={"old|empirical": _FakeFact("old|empirical", _STATE_PREDICATE, "2026-03-01")},
        provenance={"old|empirical": {"old", "other"}},
    )
    with patch("core.agents.fact_invalidation.batch_nli_score", return_value=_nli(0.1)):
        closed = await _run(_FakeDriver(graph), _collection())
    fact = graph.facts["old|empirical"]
    assert closed == 1                # no live provenance remains → closes
    assert fact.valid_to == "2026-05-01"
    assert fact.invalid_at == _NOW


@pytest.mark.asyncio
async def test_empty_new_valid_from_falls_back_to_now() -> None:
    graph = _graph_single_state()
    coll = _collection()
    with patch("core.agents.fact_invalidation.batch_nli_score", return_value=_nli(0.1)):
        await _run(_FakeDriver(graph), coll, new_valid_from="")
    fact = graph.facts["old|empirical"]
    assert fact.valid_to == _NOW              # world-time unknown → observation instant
    assert coll.metadatas[0]["valid_to"] == _NOW


# ---------------------------------------------------------------------------
# Chroma mirror-close: only valid_to moves; decay_anchor + other keys preserved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chroma_mirror_close_only_touches_valid_to() -> None:
    graph = _graph_single_state()
    coll = _collection()
    before = dict(coll.metadatas[0])
    with patch("core.agents.fact_invalidation.batch_nli_score", return_value=_nli(0.1)):
        await _run(_FakeDriver(graph), coll)
    after = coll.metadatas[0]
    assert coll.updated is not None
    assert coll.updated["ids"] == ["chunk-1"]
    assert after["valid_to"] == "2026-05-01"                 # closed
    assert after["decay_anchor"] == before["decay_anchor"]   # i20b decay untouched
    assert after["valid_from"] == before["valid_from"]
    assert after["memory_type"] == before["memory_type"]
    assert after["access_count"] == before["access_count"]
    assert after["summary"] == before["summary"]


@pytest.mark.asyncio
async def test_chroma_close_failure_does_not_break_neo4j_closure() -> None:
    graph = _graph_single_state()
    coll = _collection(raise_on="update")
    with patch("core.agents.fact_invalidation.batch_nli_score", return_value=_nli(0.1)):
        closed = await _run(_FakeDriver(graph), coll)
    assert closed == 1                                    # Neo4j closure still applied
    assert graph.facts["old|empirical"].invalid_at == _NOW


# ---------------------------------------------------------------------------
# D2 ledger routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contradiction_above_threshold_invokes_sink() -> None:
    graph = _graph_single_state()
    sink = AsyncMock()
    with (
        patch("core.agents.fact_invalidation.batch_nli_score", return_value=_nli(0.9)),
        patch(
            "core.agents.hallucination.contradiction_sink.get_contradiction_sink",
            return_value=sink,
        ),
    ):
        await _run(_FakeDriver(graph), _collection(documents=["OLD text"]),
                   new_content="NEW text")
    sink.assert_awaited_once_with(
        claim_text="NEW text",
        source_text="OLD text",
        source_artifact_id="old",
        severity="medium",
    )


@pytest.mark.asyncio
async def test_contradiction_below_threshold_no_sink() -> None:
    graph = _graph_single_state()
    sink = AsyncMock()
    with (
        patch("core.agents.fact_invalidation.batch_nli_score", return_value=_nli(0.1)),
        patch(
            "core.agents.hallucination.contradiction_sink.get_contradiction_sink",
            return_value=sink,
        ),
    ):
        closed = await _run(_FakeDriver(graph), _collection(documents=["OLD text"]))
    assert closed == 1                 # orderly knowledge-update — closed
    sink.assert_not_awaited()          # ...but no ledger entry


@pytest.mark.asyncio
async def test_sink_none_closure_still_happens() -> None:
    graph = _graph_single_state()
    with (
        patch("core.agents.fact_invalidation.batch_nli_score", return_value=_nli(0.9)),
        patch(
            "core.agents.hallucination.contradiction_sink.get_contradiction_sink",
            return_value=None,
        ),
    ):
        closed = await _run(_FakeDriver(graph), _collection(documents=["OLD text"]))
    assert closed == 1
    assert graph.facts["old|empirical"].invalid_at == _NOW


@pytest.mark.asyncio
async def test_nli_raises_closure_still_happens() -> None:
    graph = _graph_single_state()
    coll = _collection(documents=["OLD text"])
    with patch(
        "core.agents.fact_invalidation.batch_nli_score",
        side_effect=RuntimeError("nli down"),
    ):
        closed = await _run(_FakeDriver(graph), coll)
    assert closed == 1                                    # closure independent of NLI
    assert graph.facts["old|empirical"].invalid_at == _NOW
    assert coll.metadatas[0]["valid_to"] == "2026-05-01"  # Chroma close applied too


@pytest.mark.asyncio
async def test_ledger_disabled_skips_nli(monkeypatch) -> None:
    monkeypatch.setattr("config.features.ENABLE_CONTRADICTION_LEDGER", False)
    monkeypatch.setattr("config.ENABLE_CONTRADICTION_LEDGER", False)
    graph = _graph_single_state()
    nli = patch(
        "core.agents.fact_invalidation.batch_nli_score", return_value=_nli(0.9)
    ).start()
    try:
        closed = await _run(_FakeDriver(graph), _collection(documents=["OLD text"]))
    finally:
        patch.stopall()
    assert closed == 1          # closure still happens
    nli.assert_not_called()     # ledger off → no classification


# ---------------------------------------------------------------------------
# Knowledge-update gate (plan Phase D gate): old invalidated + still as-of-queryable
# ---------------------------------------------------------------------------


def _as_of_admits(valid_from: str, valid_to: str, t: str) -> bool:
    """The m0006 as-of predicate as a pure assertion:
    ``valid_from <= T AND (valid_to IS NULL/empty OR valid_to > T)``."""
    if valid_from and not (valid_from <= t):
        return False
    if valid_to and not (valid_to > t):
        return False
    return True


@pytest.mark.asyncio
async def test_knowledge_update_gate() -> None:
    # Old STATE value (office on 5th, valid from 2026-03-01) provenanced only by
    # the superseded artifact; a still-open new value (provenanced by the live
    # `new` artifact) coexists as a separate fact and must NOT be closed.
    graph = _FakeGraph(
        artifacts={"old": "new", "new": None},
        facts={
            "old|empirical": _FakeFact("old|empirical", _STATE_PREDICATE, "2026-03-01"),
            "new|empirical": _FakeFact("new|empirical", _STATE_PREDICATE, "2026-05-01"),
        },
        provenance={"old|empirical": {"old"}, "new|empirical": {"new"}},
    )
    coll = _collection()
    with patch("core.agents.fact_invalidation.batch_nli_score", return_value=_nli(0.9)):
        await _run(_FakeDriver(graph), coll, new_valid_from="2026-05-01")

    old_fact = graph.facts["old|empirical"]
    new_fact = graph.facts["new|empirical"]

    # Old value invalidated: fact closed + chunk valid_to closed.
    assert old_fact.valid_to == "2026-05-01"
    assert old_fact.invalid_at == _NOW
    assert coll.metadatas[0]["valid_to"] == "2026-05-01"
    # New value current (open interval, untouched).
    assert new_fact.valid_to is None
    assert new_fact.invalid_at is None

    # Old value still "as-of"-queryable inside its closed [valid_from, valid_to).
    assert _as_of_admits("2026-03-01", "2026-05-01", "2026-04-01") is True   # inside
    assert _as_of_admits("2026-03-01", "2026-05-01", "2026-06-01") is False  # after close
    # New (open) value is current at any T from its start onward.
    assert _as_of_admits("2026-05-01", "", "2026-06-01") is True
