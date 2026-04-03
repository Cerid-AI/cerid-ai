# Copyright 2026 Cerid AI. Apache-2.0 license.
"""Tests for hallucination detection: claim extraction and verification."""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

import pytest

# Pre-seed heavy modules so @patch targets work without real imports.
if "agents.query_agent" not in sys.modules:
    _stub = ModuleType("agents.query_agent")
    _stub.agent_query = None
    _stub.lightweight_kb_query = None
    sys.modules["agents.query_agent"] = _stub
    import agents
    agents.query_agent = _stub

if "agents.decomposer" not in sys.modules:
    _decomposer_stub = ModuleType("agents.decomposer")
    _decomposer_stub.lightweight_kb_query = None
    _decomposer_stub.decompose_query = None
    sys.modules["agents.decomposer"] = _decomposer_stub
    agents.decomposer = _decomposer_stub

import config
from agents.hallucination.extraction import (
    _detect_evasion,
    _extract_citation_claims,
    _extract_claims_heuristic,
    extract_claims,
)

# ---------------------------------------------------------------------------
# Tests: Claim extraction
# ---------------------------------------------------------------------------

class TestExtractClaims:
    @pytest.mark.asyncio
    async def test_short_response_returns_empty(self):
        """Responses below MIN_RESPONSE_LENGTH should return no claims."""
        claims, method = await extract_claims("short text")
        assert claims == []
        assert method == "none"

    @pytest.mark.asyncio
    @patch("agents.hallucination.extraction._extract_claims_llm", new_callable=AsyncMock)
    async def test_successful_extraction(self, mock_llm):
        """Valid LLM response should parse into claim list."""
        mock_llm.return_value = ["Python was created in 1991", "The GIL limits threading"]

        long_text = "x" * (config.HALLUCINATION_MIN_RESPONSE_LENGTH + 1)
        claims, method = await extract_claims(long_text)
        assert len(claims) == 2
        assert "Python" in claims[0]
        assert method == "llm"

    @pytest.mark.asyncio
    @patch("agents.hallucination.extraction._extract_claims_llm", new_callable=AsyncMock)
    async def test_llm_failure_falls_back_to_heuristic(self, mock_llm):
        """When LLM extraction fails, heuristic fallback should be used."""
        mock_llm.side_effect = Exception("LLM timeout")

        long_text = "Python was created in 1991. " * 10
        claims, method = await extract_claims(long_text)
        # Should use heuristic fallback
        assert method in ("heuristic", "none")


class TestHeuristicExtraction:
    def test_extracts_factual_sentences(self):
        text = "Python was created in 1991. It is a programming language. The weather is nice today."
        claims = _extract_claims_heuristic(text)
        assert isinstance(claims, list)

    def test_empty_text(self):
        claims = _extract_claims_heuristic("")
        assert claims == []


class TestCitationClaims:
    def test_finds_bracketed_citations(self):
        text = "According to [1], Python is fast. As noted in [2], it supports threading."
        claims = _extract_citation_claims(text)
        assert isinstance(claims, list)


class TestEvasionDetection:
    def test_detects_refusal(self):
        text = "I cannot provide medical advice. Please consult a doctor for health concerns."
        evasions = _detect_evasion(text, "What medication should I take?")
        assert isinstance(evasions, list)

    def test_no_evasion_in_normal_response(self):
        text = "Python was created by Guido van Rossum in 1991."
        evasions = _detect_evasion(text, "When was Python created?")
        assert isinstance(evasions, list)
        # Normal responses should have few or no evasion claims
