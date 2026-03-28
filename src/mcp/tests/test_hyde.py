# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for HyDE (Hypothetical Document Embeddings) fallback retrieval."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from utils.hyde import (
    generate_hypothetical_document,
    reciprocal_rank_fusion,
    should_trigger_hyde,
)

# ---------------------------------------------------------------------------
# Tests: should_trigger_hyde
# ---------------------------------------------------------------------------

class TestShouldTriggerHyde:
    def test_should_trigger_low_score(self):
        assert should_trigger_hyde(0.2) is True

    def test_should_not_trigger_high_score(self):
        assert should_trigger_hyde(0.8) is False

    def test_should_not_trigger_already_attempted(self):
        assert should_trigger_hyde(0.1, already_attempted=True) is False

    def test_boundary_at_threshold(self):
        """Score exactly at threshold should NOT trigger (< not <=)."""
        from config.constants import HYDE_TRIGGER_THRESHOLD
        assert should_trigger_hyde(HYDE_TRIGGER_THRESHOLD) is False


# ---------------------------------------------------------------------------
# Tests: reciprocal_rank_fusion
# ---------------------------------------------------------------------------

class TestReciprocalRankFusion:
    def test_rrf_merges_results(self):
        original = [{"id": "a", "content": "A"}, {"id": "b", "content": "B"}]
        hyde = [{"id": "c", "content": "C"}, {"id": "d", "content": "D"}]
        merged = reciprocal_rank_fusion(original, hyde)
        merged_ids = [r["id"] for r in merged]
        assert set(merged_ids) == {"a", "b", "c", "d"}
        assert len(merged) == 4
        # First-ranked items from each list should rank highest
        assert merged_ids[0] in ("a", "c")

    def test_rrf_deduplicates_by_id(self):
        original = [{"id": "x", "content": "X1"}, {"id": "y", "content": "Y"}]
        hyde = [{"id": "x", "content": "X2"}, {"id": "z", "content": "Z"}]
        merged = reciprocal_rank_fusion(original, hyde)
        merged_ids = [r["id"] for r in merged]
        # 'x' appears in both lists so gets combined score → should rank first
        assert merged_ids[0] == "x"
        # Total unique IDs
        assert len(merged) == 3
        assert set(merged_ids) == {"x", "y", "z"}

    def test_rrf_uses_chunk_id_fallback(self):
        original = [{"chunk_id": "c1", "content": "A"}]
        hyde = [{"chunk_id": "c2", "content": "B"}]
        merged = reciprocal_rank_fusion(original, hyde)
        assert len(merged) == 2

    def test_rrf_empty_inputs(self):
        assert reciprocal_rank_fusion([], []) == []

    def test_rrf_one_empty(self):
        original = [{"id": "a"}]
        merged = reciprocal_rank_fusion(original, [])
        assert len(merged) == 1


# ---------------------------------------------------------------------------
# Tests: generate_hypothetical_document
# ---------------------------------------------------------------------------

class TestGenerateHypotheticalDocument:
    @pytest.mark.asyncio
    async def test_generate_returns_none_on_failure(self):
        with patch(
            "utils.internal_llm.call_internal_llm",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM unavailable"),
        ):
            result = await generate_hypothetical_document("What is RAG?")
            assert result is None

    @pytest.mark.asyncio
    async def test_generate_returns_text_on_success(self):
        with patch(
            "utils.internal_llm.call_internal_llm",
            new_callable=AsyncMock,
            return_value="RAG combines retrieval with generation.",
        ):
            result = await generate_hypothetical_document("What is RAG?")
            assert result == "RAG combines retrieval with generation."

    @pytest.mark.asyncio
    async def test_generate_returns_none_on_empty(self):
        with patch(
            "utils.internal_llm.call_internal_llm",
            new_callable=AsyncMock,
            return_value="   ",
        ):
            result = await generate_hypothetical_document("What is RAG?")
            assert result is None

    @pytest.mark.asyncio
    async def test_generate_uses_domain(self):
        mock_llm = AsyncMock(return_value="Answer about finance.")
        with patch("utils.internal_llm.call_internal_llm", mock_llm):
            await generate_hypothetical_document("budget question", domain="finance")
            call_args = mock_llm.call_args[0][0]
            user_msg = call_args[1]["content"]
            assert "finance" in user_msg
