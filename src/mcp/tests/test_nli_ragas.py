# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for NLI-based RAGAS faithfulness metric."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


class TestNliFaithfulness:
    """Verify NLI-based faithfulness scoring."""

    @pytest.mark.asyncio
    @patch("core.utils.nli.nli_score")
    @patch("core.agents.hallucination.extraction._extract_claims_heuristic")
    async def test_fully_faithful(self, mock_extract, mock_nli):
        """All claims entailed -> score 1.0."""
        from app.eval.ragas_metrics import faithfulness

        mock_extract.return_value = ["ML is a subset of AI", "Python is popular"]
        mock_nli.return_value = {
            "entailment": 0.9,
            "contradiction": 0.03,
            "neutral": 0.07,
            "label": "entailment",
        }

        result = await faithfulness("answer text", ["context about ML and Python"])
        assert result.score == 1.0

    @pytest.mark.asyncio
    @patch("core.utils.nli.nli_score")
    @patch("core.agents.hallucination.extraction._extract_claims_heuristic")
    async def test_contradictory(self, mock_extract, mock_nli):
        """All claims contradicted -> score 0.0."""
        mock_extract.return_value = ["Python 3.12 released Oct 2023"]
        mock_nli.return_value = {
            "entailment": 0.05,
            "contradiction": 0.85,
            "neutral": 0.1,
            "label": "contradiction",
        }

        from app.eval.ragas_metrics import faithfulness

        result = await faithfulness("answer", ["Python 3.9 released Oct 2020"])
        assert result.score == 0.0

    @pytest.mark.asyncio
    @patch("core.utils.nli.nli_score")
    @patch("core.agents.hallucination.extraction._extract_claims_heuristic")
    async def test_mixed(self, mock_extract, mock_nli):
        """1 entailed + 1 neutral out of 2 -> score 0.5."""
        mock_extract.return_value = ["Claim A", "Claim B"]
        mock_nli.side_effect = [
            {
                "entailment": 0.9,
                "contradiction": 0.03,
                "neutral": 0.07,
                "label": "entailment",
            },
            {
                "entailment": 0.2,
                "contradiction": 0.15,
                "neutral": 0.65,
                "label": "neutral",
            },
        ]

        from app.eval.ragas_metrics import faithfulness

        result = await faithfulness("answer", ["context"])
        assert result.score == 0.5

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.extraction._extract_claims_heuristic")
    async def test_no_claims(self, mock_extract):
        """No claims extracted -> score 1.0 (nothing to verify)."""
        mock_extract.return_value = []

        from app.eval.ragas_metrics import faithfulness

        result = await faithfulness("Hello!", ["context"])
        assert result.score == 1.0

    @pytest.mark.asyncio
    async def test_fallback_to_llm_when_nli_unavailable(self):
        """When NLI import fails, falls back to faithfulness_llm."""
        import json
        from unittest.mock import AsyncMock

        mock_resp = json.dumps({"score": 0.75, "reasoning": "LLM fallback"})

        with (
            patch(
                "core.agents.hallucination.extraction._extract_claims_heuristic",
                return_value=["some claim"],
            ),
            patch(
                "app.eval.ragas_metrics.faithfulness_llm",
                new_callable=AsyncMock,
                return_value=MagicMock(score=0.75, reasoning="LLM fallback"),
            ) as mock_llm_fb,
            patch.dict("sys.modules", {"core.utils.nli": None}),
        ):
            from app.eval.ragas_metrics import faithfulness

            result = await faithfulness("answer", ["context"])
            mock_llm_fb.assert_awaited_once()
            assert result.score == 0.75
