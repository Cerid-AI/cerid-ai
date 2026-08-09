# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Escalation policy + type-aware routing + supported-gate + NLI-premise tests.

Covers B2 / Phase 3.1-3.5:
- EscalationPolicy tiers reproduce verify_claim's pre-3.2 decisions (the seam).
- Evasion/ignorance claims bypass KB grounding and route to the type-aware
  external verifier (fixes the calibration study's evasion=0.375 / ignorance=0.727
  misgrades where a hedge was graded "verified" on its literal content).
- The named supported-gate constant (0.5) pins the promote-to-verified boundary.
- The KB-grounding NLI premise is no longer double-truncated to 512 chars.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import config
from core.agents.hallucination.escalation import (
    EscalationPolicy,
    EscalationTier,
    GroundingSignals,
    get_escalation_policy,
)
from core.agents.hallucination.verification import (
    _SUPPORTED_MIN_CONFIDENCE,
    _parse_verification_verdict,
    _score_kb_grounding,
    verify_claim,
)

_VERIFIED_THRESHOLD = 0.65
_ENTAILMENT_THRESHOLD = 0.7
_FLOOR = 0.15


def _policy() -> EscalationPolicy:
    return EscalationPolicy(
        verified_threshold=_VERIFIED_THRESHOLD,
        entailment_threshold=_ENTAILMENT_THRESHOLD,
    )


def _signals(*, similarity=0.8, raw_similarity=0.8, entailment=0.0, contradiction=0.0):
    return GroundingSignals(
        similarity=similarity,
        raw_similarity=raw_similarity,
        entailment=entailment,
        contradiction=contradiction,
    )


# ---------------------------------------------------------------------------
# EscalationPolicy — reproduces verify_claim's decisions (the seam)
# ---------------------------------------------------------------------------
class TestEscalationPolicy:
    def test_classify_temporal_always_web(self):
        # Even strong entailment goes to WEB when the claim is time-sensitive.
        tier = _policy().classify(_signals(entailment=0.95), is_temporal=True)
        assert tier is EscalationTier.WEB

    def test_classify_strong_entailment_trusts_kb(self):
        tier = _policy().classify(_signals(entailment=0.9), is_temporal=False)
        assert tier is EscalationTier.TRUST_KB

    def test_classify_high_similarity_aligned_trusts_kb(self):
        # entailment in [floor, threshold): similarity-high trust path.
        tier = _policy().classify(_signals(entailment=0.30), is_temporal=False)
        assert tier is EscalationTier.TRUST_KB

    def test_classify_low_entailment_escalates_cross_model(self):
        # High similarity, entailment below the alignment floor → keyword overlap
        # only → cross-model.
        tier = _policy().classify(_signals(entailment=0.05), is_temporal=False)
        assert tier is EscalationTier.CROSS_MODEL

    def test_semantic_alignment_floor_boundary(self):
        p = _policy()
        assert p.semantic_alignment_ok(_signals(entailment=_FLOOR)) is True
        assert p.semantic_alignment_ok(_signals(entailment=_FLOOR - 0.01)) is False

    def test_kb_contradiction_requires_two_signals(self):
        p = _policy()
        # strong topical + aligned → authoritative contradiction
        assert p.kb_contradiction_authoritative(
            _signals(raw_similarity=0.7, entailment=0.2)
        ) is True
        # weak topical → not authoritative (escalate)
        assert p.kb_contradiction_authoritative(
            _signals(raw_similarity=0.5, entailment=0.2)
        ) is False
        # strong topical but unaligned (keywords only) → not authoritative
        assert p.kb_contradiction_authoritative(
            _signals(raw_similarity=0.7, entailment=0.05)
        ) is False

    def test_is_temporal_matches_inline_predicate(self):
        p = _policy()
        assert p.is_temporal("As of my last training update, X was true", stale_context=False) is True
        assert p.is_temporal("The newest data released this year shows Y", stale_context=False) is True
        assert p.is_temporal("Water boils at 100C", stale_context=True) is True
        assert p.is_temporal("Water boils at 100C", stale_context=False) is False

    def test_type_route_targets_evasion_and_ignorance(self):
        p = _policy()
        assert p.type_route('[EVASION] The user asked: "x"') is EscalationTier.WEB
        assert p.type_route("I don't have information about that company's revenue") is EscalationTier.WEB
        assert p.type_route("Python was released in 1991") is None

    def test_factory_defaults_entailment_from_config(self):
        p = get_escalation_policy(verified_threshold=_VERIFIED_THRESHOLD)
        assert p.entailment_threshold == config.NLI_ENTAILMENT_THRESHOLD
        assert p.verified_threshold == _VERIFIED_THRESHOLD


# ---------------------------------------------------------------------------
# Evasion / ignorance routing — the behavior-changing fix (Phase 3.5)
# ---------------------------------------------------------------------------
class TestTypeAwareRouting:
    @pytest.mark.asyncio
    @patch("core.agents.hallucination.verification._verify_claim_externally", new_callable=AsyncMock)
    @patch("core.agents.hallucination.verification._query_memories", new_callable=AsyncMock, return_value=[])
    @patch("core.agents.query_agent.lightweight_kb_query", new_callable=AsyncMock)
    async def test_ignorance_claim_bypasses_kb_grounding(
        self, mock_kb, _mock_mem, mock_ext, mock_chroma, mock_neo4j, mock_redis
    ):
        """An ignorance claim must not be graded by KB similarity — it routes
        straight to the type-aware external verifier."""
        mock_ext.return_value = {
            "status": "unverified", "confidence": 0.9,
            "reason": "Response was factually inadequate — the information exists",
            "verification_method": "web_search", "source_urls": [],
        }
        result = await verify_claim(
            "I don't have information about who wrote Romeo and Juliet.",
            mock_chroma[0], mock_neo4j[0], mock_redis,
        )
        mock_ext.assert_awaited_once()
        mock_kb.assert_not_called()  # KB grounding bypassed
        assert result["status"] == "unverified"

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.verification._verify_claim_externally", new_callable=AsyncMock)
    @patch("core.agents.hallucination.verification._query_memories", new_callable=AsyncMock, return_value=[])
    @patch("core.agents.query_agent.lightweight_kb_query", new_callable=AsyncMock)
    async def test_evasion_claim_bypasses_kb_grounding(
        self, mock_kb, _mock_mem, mock_ext, mock_chroma, mock_neo4j, mock_redis
    ):
        mock_ext.return_value = {
            "status": "unverified", "confidence": 0.85,
            "reason": "Model evaded answering — data is available",
            "verification_method": "web_search", "source_urls": [],
        }
        result = await verify_claim(
            '[EVASION] The user asked: "What is the boiling point of water at sea level?" '
            "— the model hedged instead of answering.",
            mock_chroma[0], mock_neo4j[0], mock_redis,
        )
        mock_ext.assert_awaited_once()
        mock_kb.assert_not_called()
        assert result["status"] == "unverified"

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.verification._independent_search_evidence_urls",
           new_callable=AsyncMock, return_value=[])
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_inappropriate_ignorance_inverts_to_unverified_end_to_end(
        self, mock_llm, _mock_search, mock_chroma, mock_neo4j, mock_redis
    ):
        """Through the REAL external verifier: a well-documented fact the model
        claimed ignorance of → verifier finds it exists → inverted to unverified."""
        mock_llm.return_value = {
            "choices": [{"message": {
                "content": '{"verdict": "supported", "confidence": 0.95, '
                           '"reasoning": "Shakespeare wrote Romeo and Juliet."}',
            }}]
        }
        result = await verify_claim(
            "I don't have any information about who wrote the play Romeo and Juliet.",
            mock_chroma[0], mock_neo4j[0], mock_redis,
        )
        assert result["status"] == "unverified"

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.verification._independent_search_evidence_urls",
           new_callable=AsyncMock, return_value=[])
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_appropriate_ignorance_inverts_to_verified_end_to_end(
        self, mock_llm, _mock_search, mock_chroma, mock_neo4j, mock_redis
    ):
        """Private, unobtainable info the model declined → verifier confirms it
        can't be found → inverted to verified (the caution was appropriate)."""
        mock_llm.return_value = {
            "choices": [{"message": {
                "content": '{"verdict": "refuted", "confidence": 0.9, '
                           '"reasoning": "A private bank balance is not publicly obtainable."}',
            }}]
        }
        result = await verify_claim(
            "I don't have information about your personal bank account balance.",
            mock_chroma[0], mock_neo4j[0], mock_redis,
        )
        assert result["status"] == "verified"


# ---------------------------------------------------------------------------
# Named supported-gate constant (Phase 3.5, HELD at 0.5)
# ---------------------------------------------------------------------------
class TestSupportedGate:
    def test_constant_is_half(self):
        assert _SUPPORTED_MIN_CONFIDENCE == 0.5

    def test_supported_at_gate_is_verified(self):
        raw = f'{{"verdict": "supported", "confidence": {_SUPPORTED_MIN_CONFIDENCE}, "reasoning": "ok"}}'
        assert _parse_verification_verdict(raw)["status"] == "verified"

    def test_supported_just_below_gate_is_uncertain(self):
        raw = f'{{"verdict": "supported", "confidence": {_SUPPORTED_MIN_CONFIDENCE - 0.01}, "reasoning": "ok"}}'
        assert _parse_verification_verdict(raw)["status"] == "uncertain"


# ---------------------------------------------------------------------------
# NLI premise no longer double-truncated to 512 chars (Phase 3.1)
# ---------------------------------------------------------------------------
class TestNliPremiseCeiling:
    @pytest.mark.asyncio
    async def test_grounding_premise_exceeds_legacy_512_char_slice(self):
        long_content = "the knowledge base evidence sentence. " * 40  # ~1500 chars > 512
        assert len(long_content) > 512
        captured: dict[str, str] = {}

        async def _capture(premise: str, hypothesis: str):
            captured["premise"] = premise
            return {"entailment": 0.9, "contradiction": 0.0, "neutral": 0.1, "label": "entailment"}

        top = {"relevance": 0.8, "content": long_content, "artifact_id": ""}
        with patch("core.utils.nli.nli_score_async", side_effect=_capture):
            await _score_kb_grounding("some claim", [top], top, 0.8, None)

        # Pre-3.1 the caller sliced content to 512 chars before NLI; now the full
        # evidence (up to NLI_PREMISE_CHAR_LIMIT) reaches the tokenizer.
        assert len(captured["premise"]) > 512
        assert captured["premise"].startswith("the knowledge base evidence")
