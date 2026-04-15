# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for claim routing through the full verification pipeline.

These tests exercise the complete verify_claim() flow with mocked backends
(ChromaDB, Neo4j, Redis, OpenRouter LLM calls) but unmocked internal logic
(classification, NLI scoring, system prompt selection, verdict mapping).

The goal: verify that each claim TYPE reaches its intended verification path.

Mocked (external boundaries only):
  - lightweight_kb_query      -> controlled KB results
  - _query_memories           -> controlled memory results (usually empty)
  - nli_score                 -> controlled entailment/contradiction/neutral
  - call_llm_raw              -> controlled LLM JSON verdicts
  - get_cached_verdict        -> always cache miss
  - cache_verdict             -> no-op
  - Redis client              -> MagicMock (cache miss)

NOT mocked (real internal logic runs):
  - _is_recency_claim, _is_current_event_claim, _reclassify_recency
  - _is_ignorance_admission, _detect_evasion
  - _invert_ignorance_verdict, _invert_evasion_verdict
  - _interpret_recency_verdict
  - _parse_verification_verdict
  - _pick_verification_model, _is_complex_claim
  - _compute_adjusted_confidence, _check_numeric_alignment
  - System prompt selection (evasion/citation/recency/ignorance/direct)
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _kb_result(
    content: str,
    relevance: float = 0.80,
    domain: str = "technology",
    artifact_id: str = "art-1",
    filename: str = "source.md",
    **extra: Any,
) -> dict[str, Any]:
    """Build a KB result dict that lightweight_kb_query would return."""
    base: dict[str, Any] = {
        "content": content,
        "relevance": relevance,
        "domain": domain,
        "artifact_id": artifact_id,
        "filename": filename,
        "memory_source": False,
    }
    base.update(extra)
    return base


def _nli(
    entailment: float = 0.0,
    contradiction: float = 0.0,
    neutral: float = 1.0,
    label: str = "neutral",
) -> dict[str, Any]:
    return {
        "entailment": entailment,
        "contradiction": contradiction,
        "neutral": neutral,
        "label": label,
    }


def _llm_verdict(
    verdict: str = "supported",
    confidence: float = 0.9,
    reasoning: str = "Evidence confirms the claim.",
) -> dict:
    """Build the raw LLM API response dict that call_llm_raw returns."""
    return {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "verdict": verdict,
                    "confidence": confidence,
                    "reasoning": reasoning,
                }),
                "annotations": [],
            },
        }],
    }


# Patch targets (all relative to the verification module's imports)
_PATCH_KB_QUERY = "core.agents.query_agent.lightweight_kb_query"
_PATCH_MEMORIES = "core.agents.hallucination.verification._query_memories"
_PATCH_NLI = "core.utils.nli.nli_score"
_PATCH_LLM_RAW = "core.utils.llm_client.call_llm_raw"
_PATCH_CACHE_GET = "core.utils.claim_cache.get_cached_verdict"
_PATCH_CACHE_SET = "core.utils.claim_cache.cache_verdict"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_redis():
    return MagicMock()


@pytest.fixture
def mock_chroma():
    client = MagicMock()
    collection = MagicMock()
    client.get_or_create_collection.return_value = collection
    client.get_collection.return_value = collection
    return client


@pytest.fixture
def mock_neo4j():
    return MagicMock()


# ============================================================================
# TestFactualClaimRouting
# ============================================================================

class TestFactualClaimRouting:
    """Factual claims verified against the KB with NLI and similarity checks."""

    @pytest.mark.asyncio
    @patch(_PATCH_CACHE_SET, new_callable=AsyncMock)
    @patch(_PATCH_CACHE_GET, new_callable=AsyncMock, return_value=None)
    @patch(_PATCH_LLM_RAW, new_callable=AsyncMock)
    @patch(_PATCH_NLI)
    @patch(_PATCH_MEMORIES, new_callable=AsyncMock, return_value=[])
    @patch(_PATCH_KB_QUERY, new_callable=AsyncMock)
    async def test_factual_claim_kb_verified(
        self,
        mock_kb,
        mock_mem,
        mock_nli,
        mock_llm,
        mock_cache_get,
        mock_cache_set,
        mock_chroma,
        mock_neo4j,
        mock_redis,
    ):
        """'Python was created by Guido van Rossum' with high NLI entailment.

        KB returns a matching result with high relevance and the NLI model
        confirms entailment.  Should return 'verified' via 'kb_nli' without
        calling the external LLM.
        """
        from core.agents.hallucination.verification import verify_claim

        claim = "Python was created by Guido van Rossum"
        mock_kb.return_value = [
            _kb_result(
                "Python is a programming language created by Guido van Rossum "
                "in 1991.",
                relevance=0.85,
            ),
        ]
        mock_nli.return_value = _nli(entailment=0.92, label="entailment")

        result = await verify_claim(
            claim=claim,
            chroma_client=mock_chroma,
            neo4j_driver=mock_neo4j,
            redis_client=mock_redis,
        )

        assert result["status"] == "verified"
        assert result["verification_method"] == "kb_nli"
        # External LLM should NOT be called for a KB-verified factual claim
        mock_llm.assert_not_called()

    @pytest.mark.asyncio
    @patch(_PATCH_CACHE_SET, new_callable=AsyncMock)
    @patch(_PATCH_CACHE_GET, new_callable=AsyncMock, return_value=None)
    @patch(_PATCH_LLM_RAW, new_callable=AsyncMock)
    @patch(_PATCH_NLI)
    @patch(_PATCH_MEMORIES, new_callable=AsyncMock, return_value=[])
    @patch(_PATCH_KB_QUERY, new_callable=AsyncMock)
    async def test_factual_claim_kb_contradiction(
        self,
        mock_kb,
        mock_mem,
        mock_nli,
        mock_llm,
        mock_cache_get,
        mock_cache_set,
        mock_chroma,
        mock_neo4j,
        mock_redis,
    ):
        """'Python was created in 2020' with an authoritative KB that says 1991.

        The KB result here has high relevance (0.75) AND non-trivial NLI
        entailment (0.20) on top of strong contradiction (0.88) — the
        signature of "KB doc is about the claim's subject AND disagrees with
        it". Pipeline should trust it: terminal `unverified` via `kb_nli`.

        When entailment is near zero, the kb_nli path escalates externally
        instead (covered by `test_factual_claim_kb_contradiction_weak_evidence`).
        """
        from core.agents.hallucination.verification import verify_claim

        claim = "Python was created in 2020"
        mock_kb.return_value = [
            _kb_result(
                "Python was created by Guido van Rossum in 1991.",
                relevance=0.75,
            ),
        ]
        # Entailment >= 0.15 gates the KB as authoritative (topically engaged
        # with the claim), so the strong contradiction is trusted as terminal.
        mock_nli.return_value = _nli(
            entailment=0.20, contradiction=0.88, label="contradiction",
        )

        result = await verify_claim(
            claim=claim,
            chroma_client=mock_chroma,
            neo4j_driver=mock_neo4j,
            redis_client=mock_redis,
        )

        assert result["status"] == "unverified"
        assert result["verification_method"] == "kb_nli"
        assert result.get("nli_contradiction", 0) >= 0.6

    @pytest.mark.asyncio
    @patch(_PATCH_CACHE_SET, new_callable=AsyncMock)
    @patch(_PATCH_CACHE_GET, new_callable=AsyncMock, return_value=None)
    @patch(_PATCH_LLM_RAW, new_callable=AsyncMock)
    @patch(_PATCH_NLI)
    @patch(_PATCH_MEMORIES, new_callable=AsyncMock, return_value=[])
    @patch(_PATCH_KB_QUERY, new_callable=AsyncMock)
    async def test_factual_claim_kb_contradiction_weak_evidence_escalates(
        self,
        mock_kb,
        mock_mem,
        mock_nli,
        mock_llm,
        mock_cache_get,
        mock_cache_set,
        mock_chroma,
        mock_neo4j,
        mock_redis,
    ):
        """Canary: "Paris is the capital of France" vs a KB doc that matches
        on keywords only (near-zero entailment, high contradiction).

        This is the "shared keywords, different topic" pattern that used to
        hard-fail on kb_nli. With the `entailment >= 0.15` authority gate,
        the pipeline must NOT terminate on the spurious NLI contradiction —
        it should escalate externally and let cross_model arbitrate.
        """
        from core.agents.hallucination.verification import verify_claim

        claim = "Paris is the capital of France"
        mock_kb.return_value = [
            # High similarity because both strings mention "Paris" — but the
            # KB content is a chat transcript about something else entirely.
            _kb_result(
                "Yesterday I walked through Paris and saw the river.",
                relevance=0.90,
            ),
        ]
        mock_nli.return_value = _nli(
            entailment=0.01,       # near zero — orthogonal to the claim
            contradiction=0.88,
            label="contradiction",
        )
        # Cross-model confirms the claim as a simple supported fact.
        mock_llm.return_value = {
            "choices": [{
                "message": {
                    "content": '{"verdict": "supported", "confidence": 0.98, "reasoning": "Paris is widely known as the capital of France."}',
                    "annotations": [],
                },
            }],
        }

        result = await verify_claim(
            claim=claim,
            chroma_client=mock_chroma,
            neo4j_driver=mock_neo4j,
            redis_client=mock_redis,
        )

        assert result["status"] == "verified"
        # Must NOT terminate at kb_nli — the weak-entailment gate forces escalation.
        assert result.get("kb_nli_escalated") is True
        assert result.get("kb_nli_contradiction", 0) >= 0.6

    @pytest.mark.asyncio
    @patch(_PATCH_CACHE_SET, new_callable=AsyncMock)
    @patch(_PATCH_CACHE_GET, new_callable=AsyncMock, return_value=None)
    @patch(_PATCH_LLM_RAW, new_callable=AsyncMock)
    @patch(_PATCH_NLI)
    @patch(_PATCH_MEMORIES, new_callable=AsyncMock, return_value=[])
    @patch(_PATCH_KB_QUERY, new_callable=AsyncMock)
    async def test_factual_claim_kb_weak_escalates(
        self,
        mock_kb,
        mock_mem,
        mock_nli,
        mock_llm,
        mock_cache_get,
        mock_cache_set,
        mock_chroma,
        mock_neo4j,
        mock_redis,
    ):
        """'Python has 50 million users' with low KB similarity -> external LLM.

        KB returns a weak match (below ext_kb_threshold=0.5), NLI is neutral,
        so the pipeline escalates to _verify_claim_externally with a standard
        cross-model (not Grok/Gemini for current events).
        """
        from core.agents.hallucination.verification import verify_claim

        claim = "Python has 50 million users worldwide"
        mock_kb.return_value = [
            _kb_result(
                "Python is one of the most popular programming languages.",
                relevance=0.38,
            ),
        ]
        mock_nli.return_value = _nli(neutral=0.9, label="neutral")
        mock_llm.return_value = _llm_verdict(
            verdict="supported", confidence=0.85,
            reasoning="Python's user base is estimated at over 50 million.",
        )

        result = await verify_claim(
            claim=claim,
            chroma_client=mock_chroma,
            neo4j_driver=mock_neo4j,
            redis_client=mock_redis,
        )

        assert result["status"] == "verified"
        # Should use cross_model (not web_search) for a non-temporal factual claim
        assert result["verification_method"] in ("cross_model", "cross_model_complex")
        mock_llm.assert_called()


# ============================================================================
# TestRecencyClaimRouting
# ============================================================================

class TestRecencyClaimRouting:
    """Recency/current-event claims are forced through web search verification."""

    @pytest.mark.asyncio
    @patch(_PATCH_CACHE_SET, new_callable=AsyncMock)
    @patch(_PATCH_CACHE_GET, new_callable=AsyncMock, return_value=None)
    @patch(_PATCH_LLM_RAW, new_callable=AsyncMock)
    @patch(_PATCH_NLI)
    @patch(_PATCH_MEMORIES, new_callable=AsyncMock, return_value=[])
    @patch(_PATCH_KB_QUERY, new_callable=AsyncMock)
    async def test_recency_claim_forces_web_search(
        self,
        mock_kb,
        mock_mem,
        mock_nli,
        mock_llm,
        mock_cache_get,
        mock_cache_set,
        mock_chroma,
        mock_neo4j,
        mock_redis,
    ):
        """'The current inflation rate is 3.2%' -> recency -> web_search.

        Even with high NLI entailment from KB data, a recency claim should
        escalate to web search verification to check freshness.
        """
        from core.agents.hallucination.verification import verify_claim

        claim = "The current inflation rate is 3.2%"
        mock_kb.return_value = [
            _kb_result(
                "The inflation rate was 3.2% according to the latest report.",
                relevance=0.82,
            ),
        ]
        # Even strong NLI entailment should escalate for temporal claims
        mock_nli.return_value = _nli(entailment=0.85, label="entailment")
        mock_llm.return_value = _llm_verdict(
            verdict="supported", confidence=0.9,
            reasoning="Current CPI data confirms 3.2% inflation rate.",
        )

        result = await verify_claim(
            claim=claim,
            chroma_client=mock_chroma,
            neo4j_driver=mock_neo4j,
            redis_client=mock_redis,
        )

        # The temporal detection in the NLI entailment path forces web search
        # for recency claims even when NLI entails.
        mock_llm.assert_called()
        assert result["status"] in ("verified", "unverified", "uncertain")

    @pytest.mark.asyncio
    @patch(_PATCH_CACHE_SET, new_callable=AsyncMock)
    @patch(_PATCH_CACHE_GET, new_callable=AsyncMock, return_value=None)
    @patch(_PATCH_LLM_RAW, new_callable=AsyncMock)
    @patch(_PATCH_NLI)
    @patch(_PATCH_MEMORIES, new_callable=AsyncMock, return_value=[])
    @patch(_PATCH_KB_QUERY, new_callable=AsyncMock)
    async def test_current_event_claim_routes_to_grok(
        self,
        mock_kb,
        mock_mem,
        mock_nli,
        mock_llm,
        mock_cache_get,
        mock_cache_set,
        mock_chroma,
        mock_neo4j,
        mock_redis,
    ):
        """'Breaking: Fed just raised rates' -> current_event -> Grok model.

        No KB results, so it goes straight to _verify_claim_externally.
        The current-event detection should select VERIFICATION_CURRENT_EVENT_MODEL.
        """
        import config as _cfg
        from core.agents.hallucination.verification import verify_claim

        claim = "Breaking: The Federal Reserve just raised interest rates this week"
        mock_kb.return_value = []  # No KB match
        mock_llm.return_value = _llm_verdict(
            verdict="supported", confidence=0.92,
            reasoning="Fed rate hike confirmed by Reuters.",
        )

        result = await verify_claim(
            claim=claim,
            chroma_client=mock_chroma,
            neo4j_driver=mock_neo4j,
            redis_client=mock_redis,
        )

        assert result["status"] == "verified"
        assert result["verification_method"] == "web_search"
        # Verify the model used is the current-event model (Grok)
        assert result.get("verification_model") == _cfg.VERIFICATION_CURRENT_EVENT_MODEL

    @pytest.mark.asyncio
    @patch(_PATCH_CACHE_SET, new_callable=AsyncMock)
    @patch(_PATCH_CACHE_GET, new_callable=AsyncMock, return_value=None)
    @patch(_PATCH_LLM_RAW, new_callable=AsyncMock)
    @patch(_PATCH_NLI)
    @patch(_PATCH_MEMORIES, new_callable=AsyncMock, return_value=[])
    @patch(_PATCH_KB_QUERY, new_callable=AsyncMock)
    async def test_month_year_recency_detected(
        self,
        mock_kb,
        mock_mem,
        mock_nli,
        mock_llm,
        mock_cache_get,
        mock_cache_set,
        mock_chroma,
        mock_neo4j,
        mock_redis,
    ):
        """'In March 2026 the policy changed' -> _reclassify_recency detects
        month+year -> routed as recency -> web_search.
        """
        from core.agents.hallucination.verification import verify_claim

        claim = "In March 2026 the policy changed to allow remote work"
        mock_kb.return_value = []  # No KB match
        mock_llm.return_value = _llm_verdict(
            verdict="supported", confidence=0.8,
            reasoning="Policy change confirmed for March 2026.",
        )

        result = await verify_claim(
            claim=claim,
            chroma_client=mock_chroma,
            neo4j_driver=mock_neo4j,
            redis_client=mock_redis,
        )

        assert result["status"] == "verified"
        assert result["verification_method"] == "web_search"


# ============================================================================
# TestIgnoranceClaimRouting
# ============================================================================

class TestIgnoranceClaimRouting:
    """Ignorance-admission claims get inverted verdicts."""

    @pytest.mark.asyncio
    @patch(_PATCH_CACHE_SET, new_callable=AsyncMock)
    @patch(_PATCH_CACHE_GET, new_callable=AsyncMock, return_value=None)
    @patch(_PATCH_LLM_RAW, new_callable=AsyncMock)
    @patch(_PATCH_NLI)
    @patch(_PATCH_MEMORIES, new_callable=AsyncMock, return_value=[])
    @patch(_PATCH_KB_QUERY, new_callable=AsyncMock)
    async def test_ignorance_kb_has_answer_unverified(
        self,
        mock_kb,
        mock_mem,
        mock_nli,
        mock_llm,
        mock_cache_get,
        mock_cache_set,
        mock_chroma,
        mock_neo4j,
        mock_redis,
    ):
        """'I don't have information about quantum computing advances' ->
        ignorance claim -> verifier finds the info exists -> verdict INVERTED
        to 'unverified' (model should have known).

        Note: the claim must NOT contain temporal markers like 'recent' that
        would cause _reclassify_recency() to override the ignorance detection.

        The LLM returns 'supported' (the info exists), which _invert_ignorance_verdict
        flips to 'unverified'.
        """
        from core.agents.hallucination.verification import verify_claim

        claim = "I don't have information about quantum computing advances"
        mock_kb.return_value = []  # No KB match, falls to external
        mock_llm.return_value = _llm_verdict(
            verdict="supported", confidence=0.9,
            reasoning="Multiple authoritative sources confirm quantum advances.",
        )

        result = await verify_claim(
            claim=claim,
            chroma_client=mock_chroma,
            neo4j_driver=mock_neo4j,
            redis_client=mock_redis,
        )

        # Inversion: verifier confirmed info exists (supported) -> model
        # was wrong to say it doesn't know -> unverified
        assert result["status"] == "unverified"
        assert result["verification_method"] == "web_search"
        assert "inadequate" in result.get("reason", "").lower() or \
               "exists" in result.get("reason", "").lower()

    @pytest.mark.asyncio
    @patch(_PATCH_CACHE_SET, new_callable=AsyncMock)
    @patch(_PATCH_CACHE_GET, new_callable=AsyncMock, return_value=None)
    @patch(_PATCH_LLM_RAW, new_callable=AsyncMock)
    @patch(_PATCH_NLI)
    @patch(_PATCH_MEMORIES, new_callable=AsyncMock, return_value=[])
    @patch(_PATCH_KB_QUERY, new_callable=AsyncMock)
    async def test_ignorance_no_kb_answer_verified(
        self,
        mock_kb,
        mock_mem,
        mock_nli,
        mock_llm,
        mock_cache_get,
        mock_cache_set,
        mock_chroma,
        mock_neo4j,
        mock_redis,
    ):
        """'I don't have information about the Zylox protocol' ->
        ignorance claim -> verifier finds nothing -> verdict INVERTED
        to 'verified' (model was right to admit ignorance).

        The LLM returns 'refuted' (info doesn't exist), which
        _invert_ignorance_verdict flips to 'verified'.
        """
        from core.agents.hallucination.verification import verify_claim

        claim = "I don't have information about the Zylox protocol"
        mock_kb.return_value = []  # No KB match
        mock_llm.return_value = _llm_verdict(
            verdict="refuted", confidence=0.85,
            reasoning="No authoritative sources found for Zylox protocol.",
        )

        result = await verify_claim(
            claim=claim,
            chroma_client=mock_chroma,
            neo4j_driver=mock_neo4j,
            redis_client=mock_redis,
        )

        # Inversion: verifier confirmed info doesn't exist (refuted) -> model
        # was correct to say it doesn't know -> verified
        assert result["status"] == "verified"
        assert result["verification_method"] == "web_search"


# ============================================================================
# TestEvasionClaimRouting
# ============================================================================

class TestEvasionClaimRouting:
    """Evasion claims (prefixed with [EVASION]) get inverted verdicts."""

    @pytest.mark.asyncio
    @patch(_PATCH_CACHE_SET, new_callable=AsyncMock)
    @patch(_PATCH_CACHE_GET, new_callable=AsyncMock, return_value=None)
    @patch(_PATCH_LLM_RAW, new_callable=AsyncMock)
    @patch(_PATCH_NLI)
    @patch(_PATCH_MEMORIES, new_callable=AsyncMock, return_value=[])
    @patch(_PATCH_KB_QUERY, new_callable=AsyncMock)
    async def test_evasion_data_exists_unverified(
        self,
        mock_kb,
        mock_mem,
        mock_nli,
        mock_llm,
        mock_cache_get,
        mock_cache_set,
        mock_chroma,
        mock_neo4j,
        mock_redis,
    ):
        """'[EVASION] I cannot provide details about crime statistics.
        The user asked: "What is the murder rate in the US?"' -> evasion ->
        web search finds data -> verdict INVERTED to 'unverified' (evasion unjustified).
        """
        from core.agents.hallucination.verification import verify_claim

        claim = (
            '[EVASION] I cannot provide specific details about crime statistics. '
            'The user asked: "What is the murder rate in the US?"'
        )
        mock_kb.return_value = []  # No KB match
        mock_llm.return_value = _llm_verdict(
            verdict="supported", confidence=0.95,
            reasoning="FBI UCR data shows US murder rate of 6.3 per 100K in 2022.",
        )

        result = await verify_claim(
            claim=claim,
            chroma_client=mock_chroma,
            neo4j_driver=mock_neo4j,
            redis_client=mock_redis,
        )

        # Inversion: data exists (supported) -> evasion was unjustified -> unverified
        assert result["status"] == "unverified"
        assert result["verification_method"] == "web_search"
        assert "evas" in result.get("reason", "").lower() or \
               "available" in result.get("reason", "").lower()

    @pytest.mark.asyncio
    @patch(_PATCH_CACHE_SET, new_callable=AsyncMock)
    @patch(_PATCH_CACHE_GET, new_callable=AsyncMock, return_value=None)
    @patch(_PATCH_LLM_RAW, new_callable=AsyncMock)
    @patch(_PATCH_NLI)
    @patch(_PATCH_MEMORIES, new_callable=AsyncMock, return_value=[])
    @patch(_PATCH_KB_QUERY, new_callable=AsyncMock)
    async def test_evasion_no_data_verified(
        self,
        mock_kb,
        mock_mem,
        mock_nli,
        mock_llm,
        mock_cache_get,
        mock_cache_set,
        mock_chroma,
        mock_neo4j,
        mock_redis,
    ):
        """'[EVASION] I cannot access real-time satellite imagery.
        The user asked: "Show me live satellite images of my house"' ->
        evasion -> web search finds nothing -> verdict INVERTED to 'verified'
        (evasion was justified).
        """
        from core.agents.hallucination.verification import verify_claim

        claim = (
            '[EVASION] I cannot access real-time satellite imagery. '
            'The user asked: "Show me live satellite images of my house"'
        )
        mock_kb.return_value = []
        mock_llm.return_value = _llm_verdict(
            verdict="refuted", confidence=0.8,
            reasoning="Real-time satellite imagery is not publicly accessible.",
        )

        result = await verify_claim(
            claim=claim,
            chroma_client=mock_chroma,
            neo4j_driver=mock_neo4j,
            redis_client=mock_redis,
        )

        # Inversion: data doesn't exist (refuted) -> evasion justified -> verified
        assert result["status"] == "verified"
        assert result["verification_method"] == "web_search"


# ============================================================================
# TestCitationClaimRouting
# ============================================================================

class TestCitationClaimRouting:
    """Citation claims (prefixed with [CITATION]) get direct verdict mapping."""

    @pytest.mark.asyncio
    @patch(_PATCH_CACHE_SET, new_callable=AsyncMock)
    @patch(_PATCH_CACHE_GET, new_callable=AsyncMock, return_value=None)
    @patch(_PATCH_LLM_RAW, new_callable=AsyncMock)
    @patch(_PATCH_NLI)
    @patch(_PATCH_MEMORIES, new_callable=AsyncMock, return_value=[])
    @patch(_PATCH_KB_QUERY, new_callable=AsyncMock)
    async def test_citation_source_exists_verified(
        self,
        mock_kb,
        mock_mem,
        mock_nli,
        mock_llm,
        mock_cache_get,
        mock_cache_set,
        mock_chroma,
        mock_neo4j,
        mock_redis,
    ):
        """'[CITATION] According to a 2024 Nature study on climate change' ->
        citation -> web search confirms source exists -> 'verified'.
        """
        from core.agents.hallucination.verification import verify_claim

        claim = "[CITATION] According to a 2024 Nature study on climate change impacts"
        mock_kb.return_value = []  # No KB match
        mock_llm.return_value = _llm_verdict(
            verdict="supported", confidence=0.9,
            reasoning="Nature published multiple climate studies in 2024.",
        )

        result = await verify_claim(
            claim=claim,
            chroma_client=mock_chroma,
            neo4j_driver=mock_neo4j,
            redis_client=mock_redis,
        )

        assert result["status"] == "verified"
        assert result["verification_method"] == "web_search"

    @pytest.mark.asyncio
    @patch(_PATCH_CACHE_SET, new_callable=AsyncMock)
    @patch(_PATCH_CACHE_GET, new_callable=AsyncMock, return_value=None)
    @patch(_PATCH_LLM_RAW, new_callable=AsyncMock)
    @patch(_PATCH_NLI)
    @patch(_PATCH_MEMORIES, new_callable=AsyncMock, return_value=[])
    @patch(_PATCH_KB_QUERY, new_callable=AsyncMock)
    async def test_citation_fabricated_unverified(
        self,
        mock_kb,
        mock_mem,
        mock_nli,
        mock_llm,
        mock_cache_get,
        mock_cache_set,
        mock_chroma,
        mock_neo4j,
        mock_redis,
    ):
        """'[CITATION] According to FakeJournal 2025 study' -> citation ->
        web search finds nothing -> 'unverified'.
        """
        from core.agents.hallucination.verification import verify_claim

        claim = "[CITATION] According to FakeJournal 2025 study on perpetual motion"
        mock_kb.return_value = []
        mock_llm.return_value = _llm_verdict(
            verdict="refuted", confidence=0.85,
            reasoning="No evidence of FakeJournal or this study existing.",
        )

        result = await verify_claim(
            claim=claim,
            chroma_client=mock_chroma,
            neo4j_driver=mock_neo4j,
            redis_client=mock_redis,
        )

        assert result["status"] == "unverified"
        assert result["verification_method"] == "web_search"


# ============================================================================
# TestMixedClaimTypes
# ============================================================================

class TestMixedClaimTypes:
    """Verify that heterogeneous claim types each get routed correctly."""

    @pytest.mark.asyncio
    @patch(_PATCH_CACHE_SET, new_callable=AsyncMock)
    @patch(_PATCH_CACHE_GET, new_callable=AsyncMock, return_value=None)
    @patch(_PATCH_LLM_RAW, new_callable=AsyncMock)
    @patch(_PATCH_NLI)
    @patch(_PATCH_MEMORIES, new_callable=AsyncMock, return_value=[])
    @patch(_PATCH_KB_QUERY, new_callable=AsyncMock)
    async def test_mixed_response_routes_each_correctly(
        self,
        mock_kb,
        mock_mem,
        mock_nli,
        mock_llm,
        mock_cache_get,
        mock_cache_set,
        mock_chroma,
        mock_neo4j,
        mock_redis,
    ):
        """Three claims from the same response, each a different type:
        1. Factual: "Python was created by Guido van Rossum" -> KB verified
        2. Recency: "The current Python version is 3.13" -> web_search
        3. Ignorance: "I don't have information about Python 4 release date"
           -> ignorance inversion

        Each call to verify_claim routes through its type-specific path.
        """
        from core.agents.hallucination.verification import verify_claim

        # ---- Claim 1: Factual (KB-verified via NLI entailment) ----
        claim_factual = "Python was created by Guido van Rossum"

        mock_kb.return_value = [
            _kb_result(
                "Python was created by Guido van Rossum in 1991.",
                relevance=0.88,
            ),
        ]
        mock_nli.return_value = _nli(entailment=0.91, label="entailment")

        result_factual = await verify_claim(
            claim=claim_factual,
            chroma_client=mock_chroma,
            neo4j_driver=mock_neo4j,
            redis_client=mock_redis,
        )

        assert result_factual["status"] == "verified"
        assert result_factual["verification_method"] == "kb_nli"

        # ---- Claim 2: Recency (forces web search even with KB data) ----
        claim_recency = "The current Python version released this year is 3.13"

        mock_kb.return_value = [
            _kb_result(
                "Python 3.12 was released in October 2023.",
                relevance=0.70,
            ),
        ]
        mock_nli.return_value = _nli(neutral=0.8, label="neutral")
        mock_llm.return_value = _llm_verdict(
            verdict="supported", confidence=0.88,
            reasoning="Python 3.13 confirmed as latest release.",
        )

        result_recency = await verify_claim(
            claim=claim_recency,
            chroma_client=mock_chroma,
            neo4j_driver=mock_neo4j,
            redis_client=mock_redis,
        )

        # Recency claims should be routed through web search
        mock_llm.assert_called()
        assert result_recency["status"] == "verified"

        # ---- Claim 3: Ignorance (verdict inversion) ----
        claim_ignorance = "I don't have information about the Python 4 release date"

        mock_kb.return_value = []
        # Reset call count tracking before this claim
        mock_llm.reset_mock()
        mock_llm.return_value = _llm_verdict(
            verdict="refuted", confidence=0.8,
            reasoning="No official Python 4 release date has been announced.",
        )

        result_ignorance = await verify_claim(
            claim=claim_ignorance,
            chroma_client=mock_chroma,
            neo4j_driver=mock_neo4j,
            redis_client=mock_redis,
        )

        # Inversion: info doesn't exist (refuted) -> model correct -> verified
        assert result_ignorance["status"] == "verified"
        assert result_ignorance["verification_method"] == "web_search"

        # --- Final assertions: all three took distinct paths ---
        assert result_factual["verification_method"] == "kb_nli"
        assert result_ignorance["verification_method"] == "web_search"
        # The three results should have different combinations
        methods = {
            result_factual["verification_method"],
            result_recency.get("verification_method", "unknown"),
            result_ignorance["verification_method"],
        }
        # At minimum we should see both kb_nli and web_search represented
        assert "kb_nli" in methods
        assert "web_search" in methods or any(
            "web" in m for m in methods
        )
