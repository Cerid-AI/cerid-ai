# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for metamorphic verification module."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


class TestCheckEntailment:
    """check_entailment heuristic overlap tests."""

    def test_high_overlap_returns_true(self):
        from agents.hallucination.metamorphic import check_entailment

        context = "Python was created by Guido van Rossum in 1991"
        variant = "Python was designed by Guido van Rossum around 1991"
        assert check_entailment(variant, context) is True

    def test_low_overlap_returns_false(self):
        from agents.hallucination.metamorphic import check_entailment

        context = "Python was created by Guido van Rossum in 1991"
        variant = "JavaScript was invented by Brendan Eich at Netscape"
        assert check_entailment(variant, context) is False

    def test_empty_variant_returns_false(self):
        from agents.hallucination.metamorphic import check_entailment

        assert check_entailment("", "some context") is False

    def test_empty_context_returns_false(self):
        from agents.hallucination.metamorphic import check_entailment

        assert check_entailment("Python uses indentation", "") is False


class TestGenerateMutations:
    """generate_mutations LLM integration tests."""

    @pytest.mark.asyncio
    async def test_returns_dict_with_keys(self):
        from agents.hallucination.metamorphic import generate_mutations

        mock_response = '{"synonym": "The sky appears blue", "antonym": "The sky appears green"}'
        with patch(
            "utils.internal_llm.call_internal_llm",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await generate_mutations("The sky is blue")

        assert isinstance(result, dict)
        assert "synonym" in result
        assert "antonym" in result
        assert result["synonym"] == "The sky appears blue"
        assert result["antonym"] == "The sky appears green"

    @pytest.mark.asyncio
    async def test_fallback_on_llm_failure(self):
        from agents.hallucination.metamorphic import generate_mutations

        with patch(
            "utils.internal_llm.call_internal_llm",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM unavailable"),
        ):
            result = await generate_mutations("The sky is blue")

        assert result["synonym"] == "The sky is blue"
        assert result["antonym"] == ""


class TestMetamorphicScore:
    """metamorphic_score integration tests."""

    @pytest.mark.asyncio
    async def test_skips_when_feature_disabled(self):
        from agents.hallucination.metamorphic import metamorphic_score

        with patch(
            "config.features.is_feature_enabled",
            return_value=False,
        ):
            result = await metamorphic_score("anything", "anything")

        assert result["skipped"] is True
        assert result["score"] == 1.0
        assert result["factoid_count"] == 0
        assert result["details"] == []

    @pytest.mark.asyncio
    async def test_returns_valid_structure(self):
        from agents.hallucination.metamorphic import metamorphic_score

        mock_llm = '{"synonym": "Python was made by Guido", "antonym": "Python was made by Linus"}'

        with (
            patch(
                "config.features.is_feature_enabled",
                return_value=True,
            ),
            patch(
                "utils.internal_llm.call_internal_llm",
                new_callable=AsyncMock,
                return_value=mock_llm,
            ),
            patch(
                "agents.hallucination.extraction._extract_claims_heuristic",
                return_value=["Python was created by Guido van Rossum in 1991"],
            ),
        ):
            result = await metamorphic_score(
                "Python was created by Guido van Rossum in 1991.",
                "Python was created by Guido van Rossum in 1991.",
            )

        assert "score" in result
        assert 0.0 <= result["score"] <= 1.0
        assert "factoid_count" in result
        assert "suspicious_count" in result
        assert "details" in result
        assert isinstance(result["details"], list)
        assert result["factoid_count"] >= 1

        detail = result["details"][0]
        assert "factoid" in detail
        assert "synonym_entailed" in detail
        assert "antonym_entailed" in detail
        assert detail["status"] in ("ok", "suspicious", "likely_hallucinated")

    @pytest.mark.asyncio
    async def test_max_factoids_limited(self):
        from agents.hallucination.metamorphic import metamorphic_score

        # Generate 20 factoid-like claims
        claims = [f"Fact number {i} is exactly {i * 100}" for i in range(20)]
        mock_llm = '{"synonym": "rephrased", "antonym": "wrong"}'

        with (
            patch(
                "config.features.is_feature_enabled",
                return_value=True,
            ),
            patch(
                "utils.internal_llm.call_internal_llm",
                new_callable=AsyncMock,
                return_value=mock_llm,
            ),
            patch(
                "agents.hallucination.extraction._extract_claims_heuristic",
                return_value=claims,
            ),
        ):
            result = await metamorphic_score("lots of claims", "context")

        assert result["factoid_count"] == 5
        assert len(result["details"]) == 5

    @pytest.mark.asyncio
    async def test_empty_answer_scores_perfect(self):
        from agents.hallucination.metamorphic import metamorphic_score

        with (
            patch(
                "config.features.is_feature_enabled",
                return_value=True,
            ),
            patch(
                "agents.hallucination.extraction._extract_claims_heuristic",
                return_value=[],
            ),
        ):
            result = await metamorphic_score("Hello!", "some context")

        assert result["score"] == 1.0
        assert result["factoid_count"] == 0
