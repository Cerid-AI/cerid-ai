# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for RAGAS-inspired evaluation metrics."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.eval.ragas_metrics import (
    MetricResult,
    _parse_score,
    answer_relevancy,
    context_precision,
    context_recall,
    evaluate_all,
    faithfulness,
)

# ---------------------------------------------------------------------------
# Tests: _parse_score
# ---------------------------------------------------------------------------

class TestParseScore:
    def test_valid_json(self):
        raw = json.dumps({"score": 0.85, "reasoning": "Well grounded"})
        result = _parse_score(raw)
        assert result.score == 0.85
        assert result.reasoning == "Well grounded"

    def test_clamps_score_above_1(self):
        raw = json.dumps({"score": 1.5, "reasoning": "Over"})
        result = _parse_score(raw)
        assert result.score == 1.0

    def test_clamps_score_below_0(self):
        raw = json.dumps({"score": -0.3, "reasoning": "Negative"})
        result = _parse_score(raw)
        assert result.score == 0.0

    def test_missing_score_defaults_to_zero(self):
        raw = json.dumps({"reasoning": "No score field"})
        result = _parse_score(raw)
        assert result.score == 0.0

    def test_invalid_json_extracts_number(self):
        raw = "The score is 0.72 based on analysis"
        result = _parse_score(raw)
        assert result.score == 0.72

    def test_no_parseable_content(self):
        raw = "No numbers here at all"
        result = _parse_score(raw)
        assert result.score == 0.0
        assert "Failed to parse" in result.reasoning

    def test_empty_string(self):
        result = _parse_score("")
        assert result.score == 0.0

    def test_score_as_string(self):
        raw = json.dumps({"score": "0.9", "reasoning": "Good"})
        result = _parse_score(raw)
        assert result.score == 0.9


# ---------------------------------------------------------------------------
# Tests: faithfulness
# ---------------------------------------------------------------------------

class TestFaithfulness:
    @pytest.mark.asyncio
    async def test_calls_llm_with_contexts(self):
        mock_resp = json.dumps({"score": 0.9, "reasoning": "Supported"})
        with patch("app.eval.ragas_metrics.call_llm", new_callable=AsyncMock, return_value=mock_resp):
            result = await faithfulness("Earth orbits the Sun", ["Astronomy textbook content"])
        assert isinstance(result, MetricResult)
        assert result.score == 0.9

    @pytest.mark.asyncio
    async def test_handles_empty_contexts(self):
        mock_resp = json.dumps({"score": 0.0, "reasoning": "No context"})
        with patch("app.eval.ragas_metrics.call_llm", new_callable=AsyncMock, return_value=mock_resp):
            result = await faithfulness("Some claim", [])
        assert result.score == 0.0


# ---------------------------------------------------------------------------
# Tests: answer_relevancy
# ---------------------------------------------------------------------------

class TestAnswerRelevancy:
    @pytest.mark.asyncio
    async def test_returns_metric_result(self):
        mock_resp = json.dumps({"score": 0.95, "reasoning": "Relevant"})
        with patch("app.eval.ragas_metrics.call_llm", new_callable=AsyncMock, return_value=mock_resp):
            result = await answer_relevancy("What is Python?", "Python is a programming language")
        assert result.score == 0.95


# ---------------------------------------------------------------------------
# Tests: context_precision
# ---------------------------------------------------------------------------

class TestContextPrecision:
    @pytest.mark.asyncio
    async def test_returns_metric_result(self):
        mock_resp = json.dumps({"score": 0.8, "reasoning": "Mostly relevant"})
        with patch("app.eval.ragas_metrics.call_llm", new_callable=AsyncMock, return_value=mock_resp):
            result = await context_precision("What is Python?", ["Python docs", "Unrelated content"])
        assert result.score == 0.8


# ---------------------------------------------------------------------------
# Tests: context_recall
# ---------------------------------------------------------------------------

class TestContextRecall:
    @pytest.mark.asyncio
    async def test_returns_metric_result(self):
        mock_resp = json.dumps({"score": 0.7, "reasoning": "Partial coverage"})
        with patch("app.eval.ragas_metrics.call_llm", new_callable=AsyncMock, return_value=mock_resp):
            result = await context_recall(
                "What is Python?",
                "Python is a language created by Guido",
                ["Python programming language docs"],
            )
        assert result.score == 0.7


# ---------------------------------------------------------------------------
# Tests: evaluate_all
# ---------------------------------------------------------------------------

class TestEvaluateAll:
    @pytest.mark.asyncio
    async def test_returns_all_four_metrics(self):
        mock_resp = json.dumps({"score": 0.8, "reasoning": "OK"})
        with patch("app.eval.ragas_metrics.call_llm", new_callable=AsyncMock, return_value=mock_resp):
            results = await evaluate_all(
                "What is Python?",
                "Python is a language",
                ["Python documentation"],
            )
        assert set(results.keys()) == {"faithfulness", "answer_relevancy", "context_precision", "context_recall"}
        for v in results.values():
            assert isinstance(v, MetricResult)
            assert v.score == 0.8
