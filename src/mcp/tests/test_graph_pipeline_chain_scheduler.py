# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Task 9: the AF-013 graph-pipeline stages (compute_entity_embeddings ->
build_similarity_edges -> compute_umap_3d -> compute_trust_state ->
derive_domains) used to be five fixed crons stacked 15/22/30/31/32 minutes
apart, each racing its predecessor's completion. They now run as one chain,
registered once on the first stage's cron; each stage still blocks on its
own completion (or its own _STAGE_COMPLETION_TIMEOUT_S), so the chain never
starts stage N+1 before stage N is terminal.

Covers: the chain fires automatically on the first stage's cron, the
individual per-stage jobs stay registered (paused) so manual per-stage
triggers and cache-invalidation lookups (trigger_job) keep working, and the
chain awaits each stage to completion before starting the next.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("deps", MagicMock())

from app import scheduler as sched  # noqa: E402
from app.scheduler import get_job_status, start_scheduler, stop_scheduler  # noqa: E402


def _job_ids() -> set[str]:
    return {j["id"] for j in get_job_status()["jobs"]}


def _job(job_id: str) -> dict:
    return next(j for j in get_job_status()["jobs"] if j["id"] == job_id)


class TestGraphPipelineChainRegistration:
    @pytest.mark.asyncio
    async def test_chain_is_registered_and_actively_scheduled(self):
        stop_scheduler()
        try:
            start_scheduler()
            assert "graph_pipeline" in _job_ids()
            assert _job("graph_pipeline")["next_run"] is not None
        finally:
            stop_scheduler()

    @pytest.mark.asyncio
    async def test_individual_stages_stay_registered_but_paused(self):
        """trigger_job() resolves stages by scheduler job id (manual per-
        stage runs, cache-invalidation lookups) — chaining must remove only
        their OWN automatic cron firing, not their registration."""
        stop_scheduler()
        try:
            start_scheduler()
            for job_id in (
                "compute_entity_embeddings",
                "build_similarity_edges",
                "compute_umap_3d",
                "compute_trust_state",
                "derive_domains",
            ):
                assert job_id in _job_ids(), job_id
                assert _job(job_id)["next_run"] is None, job_id
        finally:
            stop_scheduler()


class TestGraphPipelineChainOrdering:
    @pytest.mark.asyncio
    async def test_stage_n_plus_1_starts_only_after_stage_n_completes(self, monkeypatch):
        events: list[tuple[str, str]] = []

        async def fake_await_completion(queue, job, job_name, start):  # noqa: ARG001
            events.append((job_name, "start"))
            await asyncio.sleep(0)
            events.append((job_name, "end"))
            return "job-x"

        monkeypatch.setattr(sched, "_enqueue_and_await_completion", fake_await_completion)

        import app.main as app_main  # noqa: PLC0415

        monkeypatch.setattr(
            app_main.app.state, "processor_queue", MagicMock(), raising=False,
        )

        with patch("app.deps.get_neo4j", return_value=None):
            await sched._run_graph_pipeline_chain()

        # build_similarity_edges never touches _enqueue_and_await_completion
        # (it runs synchronously to completion in-process) so it is absent
        # from `events` — its ordering is already guaranteed by plain await.
        names_in_order = [
            "compute_entity_embeddings",
            "compute_umap_3d",
            "compute_trust_state",
            "derive_domains",
        ]
        expected: list[tuple[str, str]] = []
        for name in names_in_order:
            expected.append((name, "start"))
            expected.append((name, "end"))
        assert events == expected

    @pytest.mark.asyncio
    async def test_a_stage_that_never_reaches_terminal_still_lets_the_chain_continue(
        self, monkeypatch,
    ):
        """A stage whose own _STAGE_COMPLETION_TIMEOUT_S fires still returns
        (never raises) — the chain must still proceed to the next stage."""
        order: list[str] = []

        async def fake_await_completion(queue, job, job_name, start):  # noqa: ARG001
            order.append(job_name)
            # Mirrors the real function's behaviour on timeout: it returns
            # the job_id, it never raises.
            return "job-x"

        monkeypatch.setattr(sched, "_enqueue_and_await_completion", fake_await_completion)

        import app.main as app_main  # noqa: PLC0415

        monkeypatch.setattr(
            app_main.app.state, "processor_queue", MagicMock(), raising=False,
        )

        with patch("app.deps.get_neo4j", return_value=None):
            await sched._run_graph_pipeline_chain()

        assert order == [
            "compute_entity_embeddings",
            "compute_umap_3d",
            "compute_trust_state",
            "derive_domains",
        ]


class TestGraphPipelineChainRespectsPerStageSchedules:
    """An empty ``SCHEDULE_*`` disables that stage — the documented contract
    for every scheduled job. Chaining the five stages must not make one
    stage's cron the on/off switch for the other four."""

    @staticmethod
    async def _run_chain_collecting_stages(monkeypatch) -> list[str]:
        order: list[str] = []

        async def fake_await_completion(queue, job, job_name, start):  # noqa: ARG001
            order.append(job_name)
            return "job-x"

        monkeypatch.setattr(sched, "_enqueue_and_await_completion", fake_await_completion)

        import app.main as app_main  # noqa: PLC0415

        monkeypatch.setattr(
            app_main.app.state, "processor_queue", MagicMock(), raising=False,
        )

        with patch("app.deps.get_neo4j", return_value=None):
            await sched._run_graph_pipeline_chain()
        return order

    @pytest.mark.asyncio
    async def test_empty_umap_schedule_skips_only_umap(self, monkeypatch, caplog):
        monkeypatch.setattr(sched.config, "SCHEDULE_COMPUTE_UMAP_3D", "", raising=False)

        with caplog.at_level(logging.INFO, logger="ai-companion.scheduler"):
            order = await self._run_chain_collecting_stages(monkeypatch)

        assert order == [
            "compute_entity_embeddings",
            "compute_trust_state",
            "derive_domains",
        ]
        skip_lines = [
            r.getMessage() for r in caplog.records
            if "compute_umap_3d" in r.getMessage() and "skipping" in r.getMessage()
        ]
        assert len(skip_lines) == 1
        assert "SCHEDULE_COMPUTE_UMAP_3D" in skip_lines[0]

    @pytest.mark.asyncio
    async def test_all_stages_run_when_every_schedule_is_set(self, monkeypatch):
        order = await self._run_chain_collecting_stages(monkeypatch)
        assert order == [
            "compute_entity_embeddings",
            "compute_umap_3d",
            "compute_trust_state",
            "derive_domains",
        ]
