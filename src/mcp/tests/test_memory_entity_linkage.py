# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Episodic-memory → entity graph linkage.

Memories reach the graph by two paths. Conversational memories are stored as
``:Artifact`` nodes and have enqueued entity extraction since Phase K2.1.
Verified-claim promotion creates ``:Memory`` nodes instead, and enqueued
nothing — so that surface carried zero ``MENTIONS`` edges and could not
participate in entity-anchored retrieval. These tests cover the write path,
the promotion-time wiring, the merge-safety invariant, and the collector that
measures the result.
"""
from __future__ import annotations

import importlib.util
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from ._helpers import scripts_dir

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def single(self) -> dict[str, Any] | None:
        return self._row


class _CapturingSession:
    """Records every (cypher, params) pair; returns a canned row."""

    def __init__(self, calls: list[tuple[str, dict[str, Any]]], row: dict[str, Any] | None) -> None:
        self._calls = calls
        self._row = row

    def __enter__(self) -> "_CapturingSession":
        return self

    def __exit__(self, *_: Any) -> bool:
        return False

    def run(self, cypher: str, **kw: Any) -> _Result:
        self._calls.append((cypher, kw))
        return _Result(self._row)


class _CapturingDriver:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._row = row
        self.sessions_opened = 0

    def session(self, **_: Any) -> _CapturingSession:
        self.sessions_opened += 1
        return _CapturingSession(self.calls, self._row)


def _entity(cid: str = "acme-corp", name: str = "Acme Corp") -> Any:
    from core.agents.entity_extraction import Entity

    return Entity(canonical_id=cid, name=name, entity_type="ORG", confidence=0.9)


# ---------------------------------------------------------------------------
# Graph write path
# ---------------------------------------------------------------------------


class TestUpsertEntitiesForMemory:
    def test_writes_edge_from_memory_node(self):
        """The edge must originate at (:Memory), not (:Artifact).

        The original collector matched ``(:Artifact)`` carrying a
        ``memory_type`` property — a shape no node has ever had.
        """
        from app.db.neo4j.entity import upsert_entities_for_memory

        driver = _CapturingDriver(row={"ents": 1, "edges": 1})
        stats = upsert_entities_for_memory(driver, "mem-1", [_entity()])

        cypher, params = driver.calls[0]
        assert "(m:Memory {id: $memory_id})" in cypher
        assert "MERGE (m)-[r:MENTIONS]->(e)" in cypher
        assert params["memory_id"] == "mem-1"
        assert params["payload"][0]["canonical_id"] == "acme-corp"
        assert stats == {"entities_upserted": 1, "edges_upserted": 1}

    def test_does_not_touch_mention_count(self):
        """``e.mention_count`` is artifact-derived and feeds live GA gates.

        Wiki coverage and staleness both select on ``mention_count >= N``.
        Incrementing it from the memory path would silently shift those
        denominators, so the write must only ever initialise it on CREATE.
        """
        from app.db.neo4j.entity import upsert_entities_for_memory

        driver = _CapturingDriver(row={"ents": 1, "edges": 1})
        upsert_entities_for_memory(driver, "mem-1", [_entity()])

        cypher, _ = driver.calls[0]
        assert "coalesce(e.mention_count, 0) + 1" not in cypher
        # Initialising to 0 for a brand-new Entity is still required.
        assert "e.mention_count = 0" in cypher

    def test_sets_no_chunk_ids(self):
        """Memory text is inline, not chunked.

        ``compute_entity_embeddings`` unions chunk_ids across ALL inbound
        MENTIONS filtered on ``chunk_ids IS NOT NULL``; setting the property
        here would feed it ids that resolve to nothing in Chroma.
        """
        from app.db.neo4j.entity import upsert_entities_for_memory

        driver = _CapturingDriver(row={"ents": 1, "edges": 1})
        upsert_entities_for_memory(driver, "mem-1", [_entity()])

        cypher, params = driver.calls[0]
        assert "chunk_ids" not in cypher
        assert "chunk_ids_json" not in params

    def test_empty_entities_opens_no_session(self):
        from app.db.neo4j.entity import upsert_entities_for_memory

        driver = _CapturingDriver()
        stats = upsert_entities_for_memory(driver, "mem-1", [])

        assert stats == {"entities_upserted": 0, "edges_upserted": 0}
        assert driver.sessions_opened == 0


class TestMergeSafety:
    """A memory-sourced MENTIONS edge must survive an entity merge.

    ``_detach_delete_loser`` warns about un-repointed edges and then
    ``DETACH DELETE``s the loser — so an Artifact-scoped re-point would
    silently destroy every memory linkage on each merge.
    """

    def test_repoint_is_not_artifact_scoped(self):
        from app.db.neo4j import entity

        assert "MATCH (a)-[m_old:MENTIONS]->" in entity._REPOINT_MENTIONS
        assert "(a:Artifact)-[m_old:MENTIONS]->" not in entity._REPOINT_MENTIONS

    def test_snapshot_is_not_artifact_scoped(self):
        from app.db.neo4j import entity

        assert "(a:Artifact)-[m:MENTIONS]->" not in entity._SNAPSHOT_MENTIONS

    def test_restore_accepts_both_source_labels(self):
        from app.db.neo4j import entity

        assert "a:Artifact OR a:Memory" in entity._RESTORE_MENTIONS


# ---------------------------------------------------------------------------
# Promotion-time wiring
# ---------------------------------------------------------------------------


class TestVerifiedMemoryEnqueue:
    def test_promotion_enqueues_extraction(self, monkeypatch):
        """The gap this closes: :Memory nodes were created and never linked."""
        from app.routers import agents

        monkeypatch.delenv("CERID_MEMORY_ENTITY_EXTRACTION_ENABLED", raising=False)
        monkeypatch.setattr(agents, "private_blocks", lambda _lvl: False)

        create_fn = MagicMock(return_value="mem-42")
        wrapped = agents._verified_memory_fn(create_fn)

        mock_enqueue = MagicMock()
        with patch("app.db.redis.processor_queue.enqueue_job", mock_enqueue):
            returned = wrapped(object(), {"text": "Acme ships Q3"})

        assert returned == "mem-42"
        create_fn.assert_called_once()
        payload = mock_enqueue.call_args.kwargs["payload"]
        assert payload["memory_id"] == "mem-42"
        assert payload["tenant_id"] == "default"

    def test_private_mode_still_returns_bare_none(self, monkeypatch):
        """Contract preserved: a no-op callable would leak claim text to Chroma.

        Both call sites gate the whole promotion dispatch on
        ``create_memory_fn is not None``.
        """
        from app.routers import agents

        monkeypatch.setattr(agents, "private_blocks", lambda _lvl: True)
        assert agents._verified_memory_fn(MagicMock()) is None

    def test_enqueue_failure_does_not_lose_the_memory(self, monkeypatch):
        """The node is already committed — a dead queue must not raise."""
        from app.routers import agents

        monkeypatch.delenv("CERID_MEMORY_ENTITY_EXTRACTION_ENABLED", raising=False)
        monkeypatch.setattr(agents, "private_blocks", lambda _lvl: False)

        wrapped = agents._verified_memory_fn(MagicMock(return_value="mem-7"))
        with patch(
            "app.db.redis.processor_queue.enqueue_job",
            side_effect=RuntimeError("redis down"),
        ):
            assert wrapped(object(), {"text": "x"}) == "mem-7"

    def test_disabled_by_env(self, monkeypatch):
        from app.routers import agents

        monkeypatch.setenv("CERID_MEMORY_ENTITY_EXTRACTION_ENABLED", "false")
        monkeypatch.setattr(agents, "private_blocks", lambda _lvl: False)

        wrapped = agents._verified_memory_fn(MagicMock(return_value="mem-9"))
        mock_enqueue = MagicMock()
        with patch("app.db.redis.processor_queue.enqueue_job", mock_enqueue):
            assert wrapped(object(), {"text": "x"}) == "mem-9"
        mock_enqueue.assert_not_called()


class TestMemoryEntityExtractionJob:
    def test_registers_in_the_processor_registry(self):
        """A job absent from the registry fails at dequeue with 'unknown job_type'."""
        from app.processor.worker import build_default_registry

        assert "memory_entity_extraction" in build_default_registry()

    @pytest.mark.asyncio
    async def test_missing_memory_is_skipped_not_fatal(self):
        from app.processor.jobs.memory_entity_extraction import MemoryEntityExtractionJob

        job = MemoryEntityExtractionJob(memory_id="gone", tenant_id="default")
        driver = _CapturingDriver(row=None)

        with patch("app.deps.get_neo4j", return_value=driver):
            result = await job.run(lambda _p: _noop())

        assert result.metadata["skipped"] == "memory_not_found"


async def _noop() -> None:
    return None


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


def _load_collector():
    sd = scripts_dir()
    if sd is None:
        pytest.skip("scripts/ dir not reachable from test env")
    spec = importlib.util.spec_from_file_location(
        "k_program_metrics", sd / "k_program_metrics.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestLinkageMetric:
    def test_empty_denominator_is_unavailable_not_failing(self):
        """0/0 previously rendered as ``0.0% / meets_target: False``.

        A *failing gate* that actually means "nothing measured" is how this
        defect stayed invisible: the number looked like a real regression.
        """
        mod = _load_collector()
        driver = _CapturingDriver(row={"total": 0, "linked": 0})

        out = mod.metric_memory_entity_linkage(driver)

        assert out["available"] is False
        assert out["reason"] == "no_memories"
        assert "actual_pct" not in out
        assert "meets_target" not in out

    def test_counts_both_memory_representations(self):
        mod = _load_collector()
        driver = _CapturingDriver(row={"total": 390, "linked": 351})

        out = mod.metric_memory_entity_linkage(driver)

        cypher, _ = driver.calls[0]
        assert "MATCH (m:Memory)" in cypher
        assert "m.filename STARTS WITH 'memory_'" in cypher
        # The phantom shape that made this metric read 0 for its whole life.
        assert "m.memory_type IS NOT NULL" not in cypher
        assert out["available"] is True
        assert out["actual_pct"] == 90.0
        assert out["meets_target"] is True

    def test_below_target_fails(self):
        mod = _load_collector()
        driver = _CapturingDriver(row={"total": 390, "linked": 20})

        out = mod.metric_memory_entity_linkage(driver)

        assert out["actual_pct"] == 5.13
        assert out["meets_target"] is False

    def test_neo4j_unavailable(self):
        mod = _load_collector()
        out = mod.metric_memory_entity_linkage(None)
        assert out["available"] is False
        assert out["reason"] == "neo4j_unavailable"


class TestWikiStalenessMetric:
    """Metric 2 measured orphaning, not refresh-loop freshness.

    An entity with no ``(:Artifact)-[:MENTIONS]->`` edge cannot be refreshed —
    ``WikiRefreshJob`` skips it with ``no_source_artifacts`` and writes nothing,
    so its ``summary_updated_at`` freezes permanently. Sixteen such entities
    were 25% of the live denominator and, being the oldest, were the p95 by
    themselves, failing the gate while every refreshable entity sat well inside
    the target.
    """

    def _rows(self, fresh: int, stuck: int):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(tz=timezone.utc)
        rows = [
            {"ts": (now - timedelta(hours=2)).isoformat(), "refreshable": True}
            for _ in range(fresh)
        ]
        rows += [
            {"ts": (now - timedelta(days=55)).isoformat(), "refreshable": False}
            for _ in range(stuck)
        ]
        return rows

    def _driver(self, rows):
        class _S:
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *_): return False
            def run(self_inner, *_a, **_k): return rows

        class _D:
            def session(self_inner, **_): return _S()

        return _D()

    def test_unrefreshable_entities_do_not_set_the_p95(self):
        mod = _load_collector()
        out = mod.metric_wiki_staleness(self._driver(self._rows(fresh=49, stuck=16)))

        assert out["available"] is True
        assert out["denominator"] == 49
        assert out["actual_hours"] < 168
        assert out["meets_target"] is True

    def test_unrefreshable_entities_are_reported_not_hidden(self):
        """Narrowing the gate is only honest if the excluded set stays visible."""
        mod = _load_collector()
        out = mod.metric_wiki_staleness(self._driver(self._rows(fresh=49, stuck=16)))

        assert out["unrefreshable"] == 16
        assert out["unrefreshable_p95_hours"] > 168

    def test_genuinely_stale_refreshable_entities_still_fail(self):
        """The gate must still be able to fail — this is not a rubber stamp."""
        from datetime import datetime, timedelta, timezone

        mod = _load_collector()
        now = datetime.now(tz=timezone.utc)
        rows = [
            {"ts": (now - timedelta(days=30)).isoformat(), "refreshable": True}
            for _ in range(20)
        ]
        out = mod.metric_wiki_staleness(self._driver(rows))

        assert out["meets_target"] is False
        assert out["actual_hours"] > 168

    def test_all_unrefreshable_reports_unavailable(self):
        mod = _load_collector()
        out = mod.metric_wiki_staleness(self._driver(self._rows(fresh=0, stuck=5)))

        assert out["available"] is False
        assert out["reason"] == "no_refreshable_entities"
        assert out["unrefreshable"] == 5


class TestWikiStaleSweepCandidates:
    """The sweep must not spend its nightly budget on entities that can only skip."""

    def test_sweep_excludes_entities_with_no_source_artifact(self):
        import inspect

        from app import scheduler

        src = inspect.getsource(scheduler._run_wiki_stale_sweep)
        assert "exists((:Artifact)-[:MENTIONS]->(e))" in src


class TestContradictionSurfacingMetric:
    """A p95 over one observation is not a p95.

    This reported `1066h, meets_target: false` from n=1 — a confident-looking
    gate failure manufactured from a single row. 13 of the 14 live findings
    carry `entity_slug=""` (their source artifact was deleted before the anchor
    lookup ran; one references "probe-art-1", i.e. test data), so they never got
    the HAS_CONTRADICTION edge the metric traverses.
    """

    def _driver(self, n: int, hours: float):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(tz=timezone.utc)
        rows = [
            {
                "detected": (now - timedelta(hours=hours)).isoformat(),
                "surfaced": now.isoformat(),
            }
            for _ in range(n)
        ]

        class _S:
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *_): return False
            def run(self_inner, *_a, **_k): return rows

        class _D:
            def session(self_inner, **_): return _S()

        return _D()

    def test_single_sample_is_insufficient_not_failing(self):
        mod = _load_collector()
        out = mod.metric_contradiction_surfacing(self._driver(n=1, hours=1066))

        assert out["available"] is False
        assert out["reason"] == "insufficient_samples"
        assert out["denominator"] == 1
        # The manufactured verdict must be gone, not merely relabelled.
        assert "meets_target" not in out
        assert "actual_hours" not in out

    def test_enough_samples_still_gates(self):
        """Guards against the fix becoming a blanket opt-out."""
        mod = _load_collector()
        out = mod.metric_contradiction_surfacing(self._driver(n=40, hours=1066))

        assert out["available"] is True
        assert out["meets_target"] is False
        assert out["actual_hours"] > 24

    def test_enough_samples_can_pass(self):
        mod = _load_collector()
        out = mod.metric_contradiction_surfacing(self._driver(n=40, hours=2))

        assert out["available"] is True
        assert out["meets_target"] is True


class TestClosedGateArithmetic:
    """Closing a metric must not make GA easier to reach.

    `contradiction_surfacing` was closed 2026-08-02 (never had a measurable
    population — 13 of 14 findings lack the HAS_CONTRADICTION edge because their
    source artifact was deleted). Closure removes it from the denominator, so the
    requirement went 4-of-6 -> 4-of-5, which is STRICTER. These tests exist so a
    future edit cannot quietly rescale the bar down instead.
    """

    def test_required_count_is_still_four(self):
        mod = _load_collector()
        assert mod.GATE_REQUIRED == 4

    def test_contradiction_is_the_only_closed_metric(self):
        mod = _load_collector()
        assert set(mod.CLOSED_METRICS) == {"contradiction_surfacing"}

    def test_closed_metric_carries_a_substantive_rationale(self):
        """Closure without a recorded reason is indistinguishable from deletion."""
        mod = _load_collector()
        rationale = mod.CLOSED_METRICS["contradiction_surfacing"]
        assert len(rationale) > 200
        assert "entity_slug" in rationale
        assert "revisit" in rationale.lower()

    def test_closed_metric_is_excluded_from_the_count_but_still_reported(self, monkeypatch):
        mod = _load_collector()

        monkeypatch.setattr(mod, "_get_neo4j", lambda: None)
        monkeypatch.setattr(mod, "_get_redis", lambda: None)
        passing = {"available": True, "meets_target": True}
        for name in (
            "metric_wiki_coverage", "metric_wiki_staleness",
            "metric_memory_entity_linkage",
        ):
            monkeypatch.setattr(mod, name, lambda _d, _p=passing: dict(_p))
        for name in ("metric_faithfulness", "metric_chunks_per_answer"):
            monkeypatch.setattr(mod, name, lambda _r: {"available": True, "meets_target": True})
        # A closed metric reporting meets_target must NOT count toward the gate.
        monkeypatch.setattr(
            mod, "metric_contradiction_surfacing",
            lambda _d: {"available": True, "meets_target": True},
        )

        snap = mod.collect_all()

        assert snap["gate_of"] == 5
        assert snap["targets_evaluated"] == 5
        assert snap["targets_met"] == 5
        # Still present in the payload, flagged rather than dropped.
        closed = snap["metrics"]["contradiction_surfacing"]
        assert closed["gate_status"] == "closed"
        assert "closed_rationale" in closed

    def test_gate_does_not_pass_below_the_requirement(self, monkeypatch):
        mod = _load_collector()

        monkeypatch.setattr(mod, "_get_neo4j", lambda: None)
        monkeypatch.setattr(mod, "_get_redis", lambda: None)
        for name in (
            "metric_wiki_coverage", "metric_wiki_staleness",
            "metric_memory_entity_linkage",
        ):
            monkeypatch.setattr(mod, name, lambda _d: {"available": True, "meets_target": True})
        # The two that genuinely have no samples today.
        for name in ("metric_faithfulness", "metric_chunks_per_answer"):
            monkeypatch.setattr(mod, name, lambda _r: {"available": False, "reason": "no_data"})
        monkeypatch.setattr(
            mod, "metric_contradiction_surfacing",
            lambda _d: {"available": False, "reason": "insufficient_samples"},
        )

        snap = mod.collect_all()

        assert snap["targets_met"] == 3
        assert snap["gate_passes"] is False, "3 of a required 4 must not pass"
