# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for query decomposition module."""

import pytest

from utils.query_decomposer import (
    decompose_heuristic,
    decompose_query,
    needs_decomposition,
    parallel_retrieve,
)


class TestNeedsDecomposition:
    """Tests for needs_decomposition()."""

    def test_empty_query(self):
        assert needs_decomposition("") is False

    def test_short_query(self):
        assert needs_decomposition("what is X?") is False

    def test_simple_query(self):
        assert needs_decomposition("How does the ingestion pipeline work?") is False

    def test_multiple_question_marks(self):
        assert needs_decomposition("What is Redis? And how does caching work in our system?") is True

    def test_conjunction_split_pattern(self):
        assert needs_decomposition("Tell me about chunking and what are the best models for embeddings") is True

    def test_comparison_pattern(self):
        assert needs_decomposition("React vs Vue compared to Angular for frontend development") is True

    def test_list_pattern(self):
        assert needs_decomposition("1) explain chunking 2) explain embeddings 3) explain reranking") is True


class TestDecomposeHeuristic:
    """Tests for decompose_heuristic()."""

    def test_no_split_needed(self):
        result = decompose_heuristic("How does the ingestion pipeline work?")
        assert result == ["How does the ingestion pipeline work?"]

    def test_split_on_multiple_questions(self):
        result = decompose_heuristic("What is Redis? How does caching work in our system?")
        assert len(result) >= 2
        assert all(q.endswith("?") for q in result)

    def test_comparison_decomposition(self):
        result = decompose_heuristic("React vs Vue for frontend development")
        assert len(result) >= 2

    def test_respects_max_subqueries(self):
        result = decompose_heuristic("A? B? C? D? E? F? G?")
        assert len(result) <= 4

    def test_filters_short_fragments(self):
        result = decompose_heuristic("What is the architecture? Hi?")
        for q in result:
            assert len(q.rstrip("?")) >= 5


class TestDecomposeQuery:
    """Tests for decompose_query() async."""

    @pytest.mark.asyncio
    async def test_simple_query_returns_original(self):
        result = await decompose_query("What is Redis?")
        assert result == ["What is Redis?"]

    @pytest.mark.asyncio
    async def test_multi_part_decomposes(self):
        result = await decompose_query("What is Redis? And how does the caching layer work in our pipeline?")
        assert len(result) >= 2

    @pytest.mark.asyncio
    async def test_empty_query(self):
        result = await decompose_query("")
        assert result == [""]


class TestParallelRetrieve:
    """Tests for parallel_retrieve()."""

    @pytest.mark.asyncio
    async def test_single_query(self):
        async def mock_retrieve(q):
            return [{"content": f"result for {q}", "relevance": 0.9, "artifact_id": "a1"}]

        result = await parallel_retrieve(["query1"], mock_retrieve)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_multiple_queries_merged(self):
        call_count = 0

        async def mock_retrieve(q):
            nonlocal call_count
            call_count += 1
            return [{"content": f"result for {q}", "relevance": 0.9, "artifact_id": f"a{call_count}"}]

        result = await parallel_retrieve(["q1", "q2", "q3"], mock_retrieve)
        assert len(result) == 3
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_adds_sub_query_source(self):
        async def mock_retrieve(q):
            return [{"content": "doc", "relevance": 0.9, "artifact_id": "a1"}]

        result = await parallel_retrieve(["query_alpha", "query_beta"], mock_retrieve)
        sources = {r["sub_query_source"] for r in result}
        assert "query_alpha" in sources
        assert "query_beta" in sources

    @pytest.mark.asyncio
    async def test_handles_exception_in_sub_query(self):
        call_count = 0

        async def mock_retrieve(q):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise ValueError("network error")
            return [{"content": "result", "relevance": 0.9, "artifact_id": f"a{call_count}"}]

        result = await parallel_retrieve(["q1", "q2", "q3"], mock_retrieve)
        assert len(result) == 2
