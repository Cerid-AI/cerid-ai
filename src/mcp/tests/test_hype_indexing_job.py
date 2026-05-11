# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for app.processor.jobs.hype_indexing — HyPEIndexingJob."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.processor.jobs.hype_indexing import HyPEIndexingJob
from core.processor.priority import Priority

# ---------------------------------------------------------------------------
# Class attributes and instantiation
# ---------------------------------------------------------------------------

class TestHyPEIndexingJobAttrs:
    def test_job_type(self):
        assert HyPEIndexingJob.job_type == "hype_indexing"

    def test_priority_is_low(self):
        job = HyPEIndexingJob("c1", "content", "cerid_general", "art1")
        assert job.priority == Priority.LOW

    def test_estimate_cost_is_zero_usd(self):
        job = HyPEIndexingJob("c1", "content", "cerid_general", "art1")
        cost = job.estimate_cost()
        assert cost.estimated_usd == Decimal("0.00")
        assert cost.model == "ollama/local"
        assert cost.estimated_tokens_in > 0
        assert cost.estimated_tokens_out > 0

    def test_instantiation_stores_params(self):
        job = HyPEIndexingJob("chunk_x", "hello world", "cerid_finance", "artifact_y", n=3)
        assert job._chunk_id == "chunk_x"
        assert job._content == "hello world"
        assert job._collection_name == "cerid_finance"
        assert job._artifact_id == "artifact_y"
        assert job._n == 3


# ---------------------------------------------------------------------------
# run() method
# ---------------------------------------------------------------------------

class TestHyPEIndexingJobRun:
    @pytest.mark.asyncio
    async def test_run_calls_index_chunk_with_hype(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "true")

        progress_calls: list[float] = []

        async def _progress(pct: float) -> None:
            progress_calls.append(pct)

        mock_indexer = AsyncMock(
            return_value={"enabled": True, "n_prompts": 5, "total_tokens": 3800}
        )
        with patch("app.services.hype_indexer.index_chunk_with_hype", mock_indexer):
            job = HyPEIndexingJob("c1", "content text", "cerid_general", "art1")
            result = await job.run(_progress)

        assert result.actual_tokens_in > 0
        assert result.actual_tokens_out > 0
        assert result.metadata["enabled"] is True
        assert result.metadata["n_prompts"] == 5
        assert result.metadata["chunk_id"] == "c1"
        assert result.metadata["artifact_id"] == "art1"

    @pytest.mark.asyncio
    async def test_run_emits_progress_checkpoints(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "true")

        progress_calls: list[float] = []

        async def _progress(pct: float) -> None:
            progress_calls.append(pct)

        mock_indexer = AsyncMock(
            return_value={"enabled": True, "n_prompts": 3, "total_tokens": 3800}
        )
        with patch("app.services.hype_indexer.index_chunk_with_hype", mock_indexer):
            job = HyPEIndexingJob("c1", "content", "cerid_general", "art1")
            await job.run(_progress)

        assert 0.0 in progress_calls
        assert 1.0 in progress_calls
        # All calls are in [0.0, 1.0]
        for p in progress_calls:
            assert 0.0 <= p <= 1.0

    @pytest.mark.asyncio
    async def test_run_propagates_exceptions(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "true")

        async def _progress(pct: float) -> None:
            pass

        mock_indexer = AsyncMock(side_effect=RuntimeError("LLM crashed"))
        with (
            patch("app.services.hype_indexer.index_chunk_with_hype", mock_indexer),
            patch("core.utils.swallowed.log_swallowed_error"),
        ):
            job = HyPEIndexingJob("c1", "content", "cerid_general", "art1")
            with pytest.raises(RuntimeError, match="LLM crashed"):
                await job.run(_progress)

    @pytest.mark.asyncio
    async def test_run_flag_off_returns_disabled(self, monkeypatch):
        """Flag off → index_chunk_with_hype returns {enabled: False} → job succeeds."""
        monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "false")

        progress_calls: list[float] = []

        async def _progress(pct: float) -> None:
            progress_calls.append(pct)

        mock_indexer = AsyncMock(return_value={"enabled": False})
        with patch("app.services.hype_indexer.index_chunk_with_hype", mock_indexer):
            job = HyPEIndexingJob("c1", "content", "cerid_general", "art1")
            result = await job.run(_progress)

        assert result.metadata["enabled"] is False

    def test_new_record_builds_correctly(self):
        job = HyPEIndexingJob("c1", "content", "cerid_general", "art1")
        record = job.new_record()
        assert record.job_type == "hype_indexing"
        assert record.priority == Priority.LOW
        assert record.estimated_tokens_in > 0
