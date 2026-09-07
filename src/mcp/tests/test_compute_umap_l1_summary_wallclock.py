# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Task 9: ComputeUmap3DJob._run_l1_summary_batch threads the
COMMUNITY_SUMMARY_WALL_CLOCK_S budget into summarize_communities and
surfaces the summarised/remaining split so a stage cut short by the
budget is visible in the job's own result, not just a log line."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("deps", MagicMock())

import config  # noqa: E402
from app.processor.jobs.compute_umap_3d import ComputeUmap3DJob  # noqa: E402


@pytest.mark.asyncio
class TestL1SummaryBatchWallClock:
    async def test_passes_configured_wall_clock_and_surfaces_remaining(self, monkeypatch):
        monkeypatch.setattr(config, "COMMUNITY_SUMMARY_WALL_CLOCK_S", 123.0)
        monkeypatch.setattr("app.deps.get_chroma", lambda: MagicMock())

        captured_kwargs: dict = {}

        async def fake_summarize_communities(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return {
                "summarised": 3,
                "skipped_existing": 0,
                "skipped_no_chunks": 0,
                "errors": 0,
                "remaining": 7,
            }

        monkeypatch.setattr(
            "app.db.neo4j.community_summaries.summarize_communities",
            fake_summarize_communities,
        )

        job = ComputeUmap3DJob()
        tokens_in, tokens_out, summarised, remaining = await job._run_l1_summary_batch(
            MagicMock(),
        )

        assert captured_kwargs["wall_clock_s"] == 123.0
        assert summarised == 3
        assert remaining == 7
        assert tokens_in == 0  # llm_caller (the counting wrapper) never ran
        assert tokens_out == 0

    async def test_chroma_unavailable_returns_zeroed_four_tuple(self, monkeypatch):
        monkeypatch.setattr("app.deps.get_chroma", lambda: None)

        job = ComputeUmap3DJob()
        result = await job._run_l1_summary_batch(MagicMock())

        assert result == (0, 0, 0, 0)
