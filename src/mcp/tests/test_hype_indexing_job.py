# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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
        job = HyPEIndexingJob("c1", "cerid_general", "art1")
        assert job.priority == Priority.LOW

    def test_estimate_cost_is_zero_usd(self):
        job = HyPEIndexingJob("c1", "cerid_general", "art1")
        cost = job.estimate_cost()
        assert cost.estimated_usd == Decimal("0.00")
        assert cost.model == "ollama/local"
        assert cost.estimated_tokens_in > 0
        assert cost.estimated_tokens_out > 0

    def test_instantiation_stores_params(self):
        # AF-094: no ``content`` param — the job carries only the ids it
        # needs to re-fetch the chunk text from Chroma at run time.
        job = HyPEIndexingJob("chunk_x", "cerid_finance", "artifact_y", n=3)
        assert job._chunk_id == "chunk_x"
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
        mock_fetch = AsyncMock(return_value="content text")
        with (
            patch("app.services.hype_indexer.index_chunk_with_hype", mock_indexer),
            patch.object(HyPEIndexingJob, "_fetch_content", mock_fetch),
        ):
            job = HyPEIndexingJob("c1", "cerid_general", "art1")
            result = await job.run(_progress)

        mock_indexer.assert_awaited_once()
        assert mock_indexer.await_args.args == ("c1", "content text")
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
        mock_fetch = AsyncMock(return_value="content")
        with (
            patch("app.services.hype_indexer.index_chunk_with_hype", mock_indexer),
            patch.object(HyPEIndexingJob, "_fetch_content", mock_fetch),
        ):
            job = HyPEIndexingJob("c1", "cerid_general", "art1")
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
        mock_fetch = AsyncMock(return_value="content")
        with (
            patch("app.services.hype_indexer.index_chunk_with_hype", mock_indexer),
            patch.object(HyPEIndexingJob, "_fetch_content", mock_fetch),
            patch("core.utils.swallowed.log_swallowed_error"),
        ):
            job = HyPEIndexingJob("c1", "cerid_general", "art1")
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
        mock_fetch = AsyncMock(return_value="content")
        with (
            patch("app.services.hype_indexer.index_chunk_with_hype", mock_indexer),
            patch.object(HyPEIndexingJob, "_fetch_content", mock_fetch),
        ):
            job = HyPEIndexingJob("c1", "cerid_general", "art1")
            result = await job.run(_progress)

        assert result.metadata["enabled"] is False
        # AF-071: a disabled-flag no-op must not report the fixed cost
        # estimate as an actual — it never called the LLM.
        assert result.actual_tokens_in == 0
        assert result.actual_tokens_out == 0

    @pytest.mark.asyncio
    async def test_run_empty_content_returns_zero_actuals(self, monkeypatch):
        """Chunk content missing/empty on re-fetch → short-circuits before
        calling index_chunk_with_hype at all — still a no-op."""
        monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "true")

        async def _progress(pct: float) -> None:
            pass

        mock_indexer = AsyncMock(
            return_value={"enabled": True, "n_prompts": 5, "total_tokens": 3800}
        )
        mock_fetch = AsyncMock(return_value="")
        with (
            patch("app.services.hype_indexer.index_chunk_with_hype", mock_indexer),
            patch.object(HyPEIndexingJob, "_fetch_content", mock_fetch),
        ):
            job = HyPEIndexingJob("c1", "cerid_general", "art1")
            result = await job.run(_progress)

        mock_indexer.assert_not_awaited()
        assert result.metadata["n_prompts"] == 0
        assert result.actual_tokens_in == 0
        assert result.actual_tokens_out == 0

    def test_new_record_builds_correctly(self):
        job = HyPEIndexingJob("c1", "cerid_general", "art1")
        record = job.new_record()
        assert record.job_type == "hype_indexing"
        assert record.priority == Priority.LOW
        assert record.estimated_tokens_in > 0

    @pytest.mark.asyncio
    async def test_fetch_content_reads_from_chroma(self):
        """AF-094: content is re-fetched from the chunk's Chroma collection
        by id, not carried in the job payload."""
        mock_collection = AsyncMock()
        mock_collection.get = lambda ids, include: {"documents": ["fetched text"]}
        mock_chroma = AsyncMock()
        mock_chroma.get_or_create_collection = lambda name: mock_collection

        with patch("app.deps.get_chroma", return_value=mock_chroma):
            job = HyPEIndexingJob("c1", "cerid_general", "art1")
            content = await job._fetch_content()

        assert content == "fetched text"

    @pytest.mark.asyncio
    async def test_fetch_content_returns_empty_when_chunk_missing(self):
        mock_collection = AsyncMock()
        mock_collection.get = lambda ids, include: {"documents": []}
        mock_chroma = AsyncMock()
        mock_chroma.get_or_create_collection = lambda name: mock_collection

        with patch("app.deps.get_chroma", return_value=mock_chroma):
            job = HyPEIndexingJob("missing", "cerid_general", "art1")
            content = await job._fetch_content()

        assert content == ""
