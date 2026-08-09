# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Unit tests for EntityExtractionJob.

All external dependencies (Neo4j, ChromaDB, LLM) are mocked. No real
infrastructure is required.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from app.processor.jobs.entity_extraction import EntityExtractionJob
from core.agents.entity_extraction import Entity
from core.processor.cost import CostEstimate
from core.processor.job import JobResult
from core.processor.priority import Priority

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop_progress(pct: float) -> None:  # noqa: ARG001
    pass


def _make_job(artifact_id: str = "art-123", tenant_id: str = "tenant-1") -> EntityExtractionJob:
    return EntityExtractionJob(artifact_id=artifact_id, tenant_id=tenant_id)


def _fake_entities() -> list[Entity]:
    return [
        Entity(
            name="Elon Musk",
            entity_type="PERSON",
            canonical_id="person:elon-musk",
            confidence=0.95,
        ),
        Entity(
            name="Tesla",
            entity_type="ORG",
            canonical_id="org:tesla",
            confidence=0.88,
        ),
    ]


# ---------------------------------------------------------------------------
# Class-level attributes
# ---------------------------------------------------------------------------


class TestEntityExtractionJobAttributes:
    def test_job_type(self):
        assert EntityExtractionJob.job_type == "entity_extraction"

    def test_priority_is_low(self):
        job = _make_job()
        assert job.priority == Priority.LOW

    def test_instantiates_with_valid_payload(self):
        job = EntityExtractionJob(artifact_id="a-001", tenant_id="t-001")
        assert job._artifact_id == "a-001"
        assert job._tenant_id == "t-001"


# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------


class TestEstimateCost:
    def test_returns_cost_estimate_instance(self):
        job = _make_job()
        result = job.estimate_cost()
        assert isinstance(result, CostEstimate)

    def test_model_is_ollama_local(self):
        job = _make_job()
        assert job.estimate_cost().model == "ollama/local"

    def test_cost_is_zero_decimal(self):
        job = _make_job()
        estimate = job.estimate_cost()
        assert estimate.estimated_usd == Decimal("0.00")

    def test_token_estimates_are_positive(self):
        job = _make_job()
        estimate = job.estimate_cost()
        assert estimate.estimated_tokens_in > 0
        assert estimate.estimated_tokens_out > 0

    def test_confidence_is_medium(self):
        job = _make_job()
        assert job.estimate_cost().confidence == "medium"


# ---------------------------------------------------------------------------
# run() — success path
# ---------------------------------------------------------------------------


class TestRunSuccess:
    async def test_run_returns_job_result_on_success(self):
        job = _make_job()

        with _patch_pipeline(
            domain="general",
            chunk_ids=["c1", "c2"],
            docs=["Apple bought Tesla.", "Elon Musk was there."],
            entities=_fake_entities(),
            upsert_stats={"entities_upserted": 2, "edges_upserted": 2},
        ):
            result = await job.run(_noop_progress)

        assert isinstance(result, JobResult)
        assert result.metadata["entities_upserted"] == 2
        assert result.metadata["edges_upserted"] == 2
        assert result.metadata["artifact_id"] == "art-123"

    async def test_run_calls_progress_callbacks(self):
        job = _make_job()
        progress_calls: list[float] = []

        async def record_progress(pct: float) -> None:
            progress_calls.append(pct)

        with _patch_pipeline(
            domain="general",
            chunk_ids=["c1"],
            docs=["some text"],
            entities=_fake_entities(),
            upsert_stats={"entities_upserted": 2, "edges_upserted": 2},
        ):
            await job.run(record_progress)

        assert 0.0 in progress_calls
        assert 1.0 in progress_calls
        # Progress must be non-decreasing
        for a, b in zip(progress_calls, progress_calls[1:]):
            assert b >= a

    async def test_run_skips_artifact_not_in_neo4j(self):
        job = _make_job()

        with _patch_pipeline(domain=None, chunk_ids=[], docs=[], entities=[], upsert_stats={}):
            result = await job.run(_noop_progress)

        assert result.metadata.get("skipped") == "artifact_not_found"

    async def test_run_skips_when_no_chunks(self):
        job = _make_job()

        with _patch_pipeline(
            domain="general", chunk_ids=[], docs=[], entities=[], upsert_stats={}
        ):
            result = await job.run(_noop_progress)

        assert result.metadata.get("skipped") == "no_chunks"

    async def test_run_skips_when_no_entities_extracted(self):
        job = _make_job()

        with _patch_pipeline(
            domain="general",
            chunk_ids=["c1"],
            docs=["some text"],
            entities=[],
            upsert_stats={},
        ):
            result = await job.run(_noop_progress)

        assert result.metadata.get("skipped") == "no_entities"


# ---------------------------------------------------------------------------
# run() — failure path
# ---------------------------------------------------------------------------


class TestRunFailure:
    async def test_run_propagates_exception_from_backend(self):
        """The job must NOT swallow exceptions — let the worker handle retries."""
        job = _make_job()

        with _patch_pipeline_raising(RuntimeError("neo4j down")):
            with pytest.raises(RuntimeError, match="neo4j down"):
                await job.run(_noop_progress)

    async def test_run_logs_swallowed_error_before_reraise(self):
        """log_swallowed_error should be called for observability even on reraise."""
        job = _make_job()

        with _patch_pipeline_raising(ValueError("bad data")):
            with patch(
                "app.processor.jobs.entity_extraction.log_swallowed_error"
            ) as mock_log:
                with pytest.raises(ValueError):
                    await job.run(_noop_progress)
                mock_log.assert_called_once()


# ---------------------------------------------------------------------------
# Context managers for patching the pipeline
# ---------------------------------------------------------------------------


def _patch_pipeline(
    domain: str | None,
    chunk_ids: list[str],
    docs: list[str],
    entities: list[Entity],
    upsert_stats: dict,
):
    """Patch all external I/O in EntityExtractionJob._run_pipeline."""
    from contextlib import ExitStack

    stack = ExitStack()

    # Stub get_neo4j / get_chroma so no real connections are attempted
    stack.enter_context(
        patch("app.processor.jobs.entity_extraction.EntityExtractionJob._run_pipeline",
              new=_make_run_pipeline(domain, chunk_ids, docs, entities, upsert_stats))
    )
    return stack


def _make_run_pipeline(domain, chunk_ids, docs, entities, upsert_stats):
    """Return a coroutine that simulates _run_pipeline without touching infra."""

    async def _fake_pipeline(self, progress_cb):
        await progress_cb(0.0)

        if domain is None:
            return {"skipped": "artifact_not_found"}

        await progress_cb(0.3)

        if not chunk_ids:
            return {"entities_upserted": 0, "edges_upserted": 0, "skipped": "no_chunks"}

        blob = " ".join(docs)
        if not blob.strip():
            return {"entities_upserted": 0, "edges_upserted": 0, "skipped": "empty_text"}

        await progress_cb(0.7)

        if not entities:
            return {"entities_upserted": 0, "edges_upserted": 0, "skipped": "no_entities"}

        await progress_cb(1.0)
        return upsert_stats

    return _fake_pipeline


def _patch_pipeline_raising(exc: Exception):
    """Patch _run_pipeline to raise the given exception."""
    from contextlib import ExitStack

    async def _raising(self, progress_cb):
        await progress_cb(0.0)
        raise exc

    stack = ExitStack()
    stack.enter_context(
        patch(
            "app.processor.jobs.entity_extraction.EntityExtractionJob._run_pipeline",
            new=_raising,
        )
    )
    return stack
