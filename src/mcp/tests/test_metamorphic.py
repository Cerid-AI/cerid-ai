# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for metamorphic verification plugin (plugins/metamorphic/).

Implementation-level tests (check_entailment, generate_mutations, metamorphic_score)
import from the BSL-1.1 plugin directly. The core stub tests verify the delegation
interface (skip when plugin not loaded, delegate when loaded).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Load plugin module directly for unit testing.
# Walk up from tests/ to find the repo root (contains "plugins/" directory).
_here = Path(__file__).resolve()
_plugin_path = None
for _parent in _here.parents:
    _candidate = _parent / "plugins" / "metamorphic" / "plugin.py"
    if _candidate.exists():
        _plugin_path = _candidate
        break
if _plugin_path is None:
    pytest.skip("metamorphic plugin not found", allow_module_level=True)
_spec = importlib.util.spec_from_file_location("cerid_plugin_metamorphic_test", str(_plugin_path))
_plugin = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_plugin)  # type: ignore[union-attr]

check_entailment = _plugin.check_entailment
generate_mutations = _plugin.generate_mutations
_plugin_metamorphic_score = _plugin.metamorphic_score


class TestCheckEntailment:
    """check_entailment heuristic overlap tests."""

    def test_high_overlap_returns_true(self):
        context = "Python was created by Guido van Rossum in 1991"
        variant = "Python was designed by Guido van Rossum around 1991"
        assert check_entailment(variant, context) is True

    def test_low_overlap_returns_false(self):
        context = "Python was created by Guido van Rossum in 1991"
        variant = "JavaScript was invented by Brendan Eich at Netscape"
        assert check_entailment(variant, context) is False

    def test_empty_variant_returns_false(self):
        assert check_entailment("", "some context") is False

    def test_empty_context_returns_false(self):
        assert check_entailment("Python uses indentation", "") is False


class TestGenerateMutations:
    """generate_mutations LLM integration tests."""

    @pytest.mark.asyncio
    async def test_returns_dict_with_keys(self):
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
        with patch(
            "utils.internal_llm.call_internal_llm",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM unavailable"),
        ):
            result = await generate_mutations("The sky is blue")

        assert result["synonym"] == "The sky is blue"
        assert result["antonym"] == ""


class TestStubDelegation:
    """Tests for the core stub (agents.hallucination.metamorphic)."""

    @pytest.mark.asyncio
    async def test_skips_when_plugin_not_loaded(self):
        """Stub returns skip sentinel when no handler is registered."""
        from agents.hallucination.metamorphic import metamorphic_score, set_metamorphic_handler

        # Ensure no handler is set
        set_metamorphic_handler(None)  # type: ignore[arg-type]
        result = await metamorphic_score("anything", "anything")

        assert result["skipped"] is True
        assert result["reason"] == "metamorphic_verification plugin not loaded (Pro tier)"

    @pytest.mark.asyncio
    async def test_delegates_when_plugin_loaded(self):
        """Stub delegates to the injected handler when set."""
        from agents.hallucination.metamorphic import metamorphic_score, set_metamorphic_handler

        async def mock_handler(*args, **kwargs):
            return {"score": 0.42, "from_plugin": True}

        set_metamorphic_handler(mock_handler)
        try:
            result = await metamorphic_score("test", "context")
            assert result["score"] == 0.42
            assert result["from_plugin"] is True
        finally:
            set_metamorphic_handler(None)  # type: ignore[arg-type]


class TestMetamorphicScore:
    """metamorphic_score plugin implementation tests."""

    @pytest.mark.asyncio
    async def test_skips_when_feature_disabled(self):
        with patch(
            "config.features.is_feature_enabled",
            return_value=False,
        ):
            result = await _plugin_metamorphic_score("anything", "anything")

        assert result["skipped"] is True
        assert result["score"] == 1.0
        assert result["factoid_count"] == 0
        assert result["details"] == []

    @pytest.mark.asyncio
    async def test_returns_valid_structure(self):
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
            result = await _plugin_metamorphic_score(
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
            result = await _plugin_metamorphic_score("lots of claims", "context")

        assert result["factoid_count"] == 5
        assert len(result["details"]) == 5

    @pytest.mark.asyncio
    async def test_empty_answer_scores_perfect(self):
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
            result = await _plugin_metamorphic_score("Hello!", "some context")

        assert result["score"] == 1.0
        assert result["factoid_count"] == 0
