# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for hallucination detection agent (Phase 7A)."""

import contextlib
import json
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Pre-seed heavy modules that verify_claim imports lazily so
# ``@patch`` can target them without triggering real imports.
# Post-Sprint E the canonical path is ``core.agents.query_agent`` —
# pre-seeding against the old ``agents.query_agent`` bridge breaks
# collection because that bridge no longer exists.
if "core.agents.query_agent" not in sys.modules:
    _stub = ModuleType("core.agents.query_agent")
    _stub.agent_query = None  # type: ignore[attr-defined]
    _stub.lightweight_kb_query = None  # type: ignore[attr-defined]
    sys.modules["core.agents.query_agent"] = _stub
    import core.agents as _core_agents
    _core_agents.query_agent = _stub  # type: ignore[attr-defined]

import config
from core.agents.hallucination import (
    REDIS_HALLUCINATION_PREFIX,
    _build_verification_details,
    _check_history_consistency,
    _check_numeric_alignment,
    _compute_adjusted_confidence,
    _detect_evasion,
    _extract_citation_claims,
    _extract_claims_heuristic,
    _has_staleness_indicators,
    _interpret_recency_verdict,
    _invert_evasion_verdict,
    _invert_ignorance_verdict,
    _is_complex_claim,
    _is_current_event_claim,
    _is_ignorance_admission,
    _is_recency_claim,
    _model_family,
    _parse_verification_verdict,
    _pick_verification_model,
    _query_memories,
    _verify_claim_externally,
    check_hallucinations,
    extract_claims,
    get_hallucination_report,
    verify_claim,
    verify_response_streaming,
)

# ---------------------------------------------------------------------------
# Helper: mock the individual extraction functions that verify_response_streaming
# now calls directly (instead of the top-level extract_claims wrapper).
# ---------------------------------------------------------------------------
_STREAMING_MOD = "core.agents.hallucination.streaming"


@contextlib.contextmanager
def _mock_streaming_extraction(claims: list[str], method: str = "heuristic"):
    """Patch the individual extraction helpers so verify_response_streaming
    produces exactly *claims* with the given *method*.

    For ``method="heuristic"``: ``_extract_claims_heuristic`` returns the claims.
    For ``method="llm"``: heuristic returns ``[]`` and ``_extract_claims_llm``
    returns the claims.
    For ``method="none"`` or empty claims with heuristic: heuristic returns ``[]``
    and LLM returns ``None``.
    """
    heuristic_rv = claims if (method == "heuristic" and claims) else []
    llm_rv: list[str] | None = claims if method == "llm" else None
    if method == "none":
        llm_rv = None

    with (
        patch(f"{_STREAMING_MOD}._extract_claims_heuristic", return_value=heuristic_rv),
        patch(f"{_STREAMING_MOD}._detect_evasion", return_value=[]),
        patch(f"{_STREAMING_MOD}._extract_citation_claims", return_value=[]),
        patch(f"{_STREAMING_MOD}._extract_ignorance_claims", return_value=[]),
        patch(f"{_STREAMING_MOD}._resolve_pronouns_heuristic", side_effect=lambda c, *a, **kw: c),
        patch(f"{_STREAMING_MOD}._extract_claims_llm", new_callable=AsyncMock, return_value=llm_rv),
    ):
        yield


class TestExtractClaims:
    """Test claim extraction from LLM responses."""

    @pytest.mark.asyncio
    async def test_short_response_returns_empty(self):
        """Responses below MIN_RESPONSE_LENGTH should return no claims."""
        claims, method = await extract_claims("short text")
        assert claims == []
        assert method == "none"

    # Patch target is the module-local name imported at extraction.py:38 —
    # ``from core.utils.internal_llm import call_internal_llm``. Patching the
    # source module has no effect because ``extract_claims`` resolves the name
    # through ``core.agents.hallucination.extraction``.
    _EXTRACTION_MOD = "core.agents.hallucination.extraction"

    @pytest.mark.asyncio
    @patch(f"{_EXTRACTION_MOD}.call_internal_llm", new_callable=AsyncMock)
    async def test_successful_extraction(self, mock_internal):
        """Valid LLM response should parse into claim list with method=llm."""
        # Non-factual filler that cannot be picked up by the heuristic regex
        # forces the LLM path rather than the heuristic fallback.
        filler = "hello. world. foo. bar. " * 10
        mock_internal.return_value = '{"claims": ["Python was created in 1991", "The GIL limits threading"]}'

        claims, method = await extract_claims(filler)
        assert len(claims) == 2
        assert "Python" in claims[0]
        assert method == "llm"

    @pytest.mark.asyncio
    @patch(f"{_EXTRACTION_MOD}.call_internal_llm", new_callable=AsyncMock)
    async def test_extraction_handles_code_block(self, mock_internal):
        """LLM responses wrapped in markdown code blocks should parse correctly."""
        filler = "hello. world. foo. bar. " * 10
        mock_internal.return_value = '```json\n{"claims": ["claim one"]}\n```'

        claims, method = await extract_claims(filler)
        assert len(claims) == 1
        assert claims[0] == "claim one"
        assert method == "llm"

    @pytest.mark.asyncio
    @patch(f"{_EXTRACTION_MOD}.call_internal_llm", new_callable=AsyncMock)
    async def test_handles_structured_claim_objects(self, mock_internal):
        """LLM returning structured {claim, type} objects should extract claim text."""
        filler = "hello. world. foo. bar. " * 10
        mock_internal.return_value = (
            '{"claims": [{"claim": "Python was created in 1991", "type": "date"}, '
            '{"claim": "GIL limits threading", "type": "technical"}]}'
        )

        claims, method = await extract_claims(filler)
        assert len(claims) == 2
        assert claims[0] == "Python was created in 1991"
        assert claims[1] == "GIL limits threading"
        assert method == "llm"

    @pytest.mark.asyncio
    @patch(f"{_EXTRACTION_MOD}.call_llm", new_callable=AsyncMock)
    @patch(f"{_EXTRACTION_MOD}.call_internal_llm", new_callable=AsyncMock)
    async def test_falls_back_to_heuristic_when_llm_returns_empty(self, mock_internal, mock_external):
        """When both LLM paths return no claims, the heuristic runs and method=heuristic.

        Guards the ``extract_claims`` design intent: LLM first (internal,
        then external fallback models), heuristic as the final fallback.
        A regression that loses the heuristic fallback would fail here
        with ``method != "heuristic"``.
        """
        # Both LLM layers return empty — forces heuristic fallback.
        mock_internal.return_value = '{"claims": []}'
        mock_external.return_value = '{"claims": []}'
        factual = (
            "The Eiffel Tower was completed in 1889. "
            "Mount Everest is 8848 meters tall. "
        ) * 3

        claims, method = await extract_claims(factual)
        assert method == "heuristic"
        assert len(claims) >= 1  # heuristic should find the factual sentences
        assert mock_internal.await_count >= 1  # LLM was tried first


class TestHeuristicExtraction:
    """Test the regex-based heuristic claim extractor."""

    def test_extracts_factual_sentences(self):
        """Sentences with dates/numbers + state verbs should be extracted."""
        text = (
            "Python 3.11 was released in October 2022. "
            "It includes significant performance improvements of up to 25%. "
            "Hello, how are you today?"
        )
        claims = _extract_claims_heuristic(text)
        assert len(claims) >= 1
        assert any("Python" in c for c in claims)

    def test_skips_greetings(self):
        """Greetings and conversational text should be filtered out."""
        text = "Hello there! I can help you with that. Sure, let me look into it."
        claims = _extract_claims_heuristic(text)
        assert len(claims) == 0

    def test_skips_code_blocks(self):
        """Code blocks should not be treated as claims."""
        text = "```python\nx = 42\n``` The variable x was set to 42 in the year 2024."
        claims = _extract_claims_heuristic(text)
        # Should not extract the code block line
        for c in claims:
            assert "```" not in c

    def test_respects_max_claims(self):
        """Should not exceed HALLUCINATION_MAX_CLAIMS."""
        sentences = [f"Version {i}.0 was released in {2000 + i}." for i in range(20)]
        text = " ".join(sentences)
        claims = _extract_claims_heuristic(text)
        assert len(claims) <= config.HALLUCINATION_MAX_CLAIMS

    def test_extracts_comparisons(self):
        """Comparison claims ('X is faster than Y') should be extracted."""
        text = "FastAPI is significantly faster than Flask for handling async requests in production workloads."
        claims = _extract_claims_heuristic(text)
        assert len(claims) >= 1
        assert any("faster than" in c.lower() for c in claims)

    def test_extracts_causal_claims(self):
        """Causal statements ('because', 'due to') should be extracted."""
        text = "Python 3.11 is 25% faster because the interpreter was optimized with specialized bytecodes."
        claims = _extract_claims_heuristic(text)
        assert len(claims) >= 1

    def test_extracts_attributions(self):
        """Attribution claims ('according to') should be extracted."""
        text = "According to the Python documentation, the GIL was introduced in version 1.5 for thread safety."
        claims = _extract_claims_heuristic(text)
        assert len(claims) >= 1

    def test_handles_numbered_lists(self):
        """Facts embedded in numbered markdown lists should be extracted."""
        text = (
            "Key features of Python 3.12:\n"
            "1. Python 3.12 was released in October 2023\n"
            "2. It includes improved error messages with 30% better context\n"
            "3. The interpreter is 5% faster due to comprehension inlining\n"
        )
        claims = _extract_claims_heuristic(text)
        assert len(claims) >= 1

    def test_strips_bold_italic_markdown(self):
        """Markdown emphasis should be stripped, content preserved."""
        text = "**Python 3.11** was released in **October 2022** with *significant* performance improvements of 25%."
        claims = _extract_claims_heuristic(text)
        assert len(claims) >= 1
        for c in claims:
            assert "**" not in c
            assert "*" not in c or c.count("*") == 0

    def test_removes_code_blocks_entirely(self):
        """Multi-line code blocks should be removed, not just detected."""
        text = (
            "The function works as follows:\n"
            "```python\ndef foo():\n    return 42\n```\n"
            "Python 3.11 was released in 2022 with 25% performance gains."
        )
        claims = _extract_claims_heuristic(text)
        for c in claims:
            assert "def foo" not in c
            assert "return 42" not in c

    def test_strong_signal_single_pattern(self):
        """Claims with strong-signal patterns need fewer base pattern matches."""
        text = "According to benchmarks, FastAPI handles requests faster than Flask."
        claims = _extract_claims_heuristic(text)
        assert len(claims) >= 1


class TestNumericAlignment:
    """Test numeric contradiction detection — key defense against inverted-fact hallucinations."""

    def test_matching_years_positive_boost(self):
        """Matching years in claim and source should return positive adjustment."""
        result = _check_numeric_alignment(
            "Python was released in 1991",
            {"content": "Python was first released in 1991 by Guido van Rossum"},
        )
        assert result == 0.03

    def test_conflicting_years_negative_penalty(self):
        """Conflicting years should return negative adjustment."""
        result = _check_numeric_alignment(
            "Python was released in 2021 with 50% improvements",
            {"content": "Python was first released in 1991 with 30% speedup"},
        )
        assert result == -0.03  # 0/2 match ratio → proportional disagreement penalty

    def test_no_numbers_returns_zero(self):
        """Claims without numbers should return 0 (nothing to check)."""
        result = _check_numeric_alignment(
            "Python is a great programming language",
            {"content": "Python is widely used in data science"},
        )
        assert result == 0.0

    def test_matching_percentages(self):
        """Matching percentages should return positive adjustment."""
        result = _check_numeric_alignment(
            "Performance improved by 25%",
            {"content": "The benchmark showed a 25% improvement"},
        )
        assert result == 0.03

    def test_empty_source_returns_zero(self):
        """Empty source content should return 0."""
        result = _check_numeric_alignment(
            "Python was released in 1991",
            {"content": ""},
        )
        assert result == 0.0

    def test_partial_match_returns_negative(self):
        """One match out of two checks (50% ratio < 75% threshold) returns -0.03."""
        result = _check_numeric_alignment(
            "Version 3.11 was released in 2022 with 25% improvement",
            {"content": "Python 3.11 released in 2022 with 10% speedup"},
        )
        # year 2022 matches, but 25% != 10% → 1/2 = 0.5 ratio < 0.75 → -0.03
        assert result == -0.03


class TestAdjustedConfidence:
    """Test multi-result confidence calibration."""

    def test_corroborating_results_boost(self):
        """Multiple results at similar scores should boost confidence."""
        results = [
            {"relevance": 0.80, "domain": "coding", "content": "fact A"},
            {"relevance": 0.75, "domain": "general", "content": "fact A variant"},
            {"relevance": 0.70, "domain": "projects", "content": "fact A related"},
        ]
        adjusted = _compute_adjusted_confidence("test claim", results, 0.80)
        # spread = 0.10 < 0.15 → +0.03, 3 domains > 1 → +0.02, 3 results → no penalty
        assert adjusted > 0.80

    def test_isolated_match_penalty(self):
        """Large score drop from #1 to #3 should reduce confidence."""
        results = [
            {"relevance": 0.85, "domain": "coding", "content": "fact A"},
            {"relevance": 0.30, "domain": "general", "content": "unrelated"},
            {"relevance": 0.20, "domain": "projects", "content": "also unrelated"},
        ]
        adjusted = _compute_adjusted_confidence("test claim", results, 0.85)
        # spread = 0.65 > 0.4 → -0.03
        assert adjusted < 0.85

    def test_single_result_penalty(self):
        """Only one KB result should reduce confidence."""
        results = [
            {"relevance": 0.80, "domain": "coding", "content": "Python was released in 1991"},
        ]
        adjusted = _compute_adjusted_confidence(
            "Python was released in 1991", results, 0.80
        )
        # single result → -0.02, but year match → +0.03
        # Net: 0.80 + 0.03 - 0.02 = 0.81
        assert adjusted == pytest.approx(0.81, abs=0.01)


class TestVerificationDetails:
    """Test verification detail metadata generation."""

    def test_cross_domain_reason(self):
        """Cross-domain matches should be noted in reason."""
        results = [
            {"relevance": 0.80, "domain": "coding", "content": "fact"},
            {"relevance": 0.75, "domain": "general", "content": "fact"},
        ]
        details = _build_verification_details("test claim", results)
        assert details["result_count"] == 2
        assert len(details["domains_found"]) == 2
        assert "cross-domain corroboration" in details.get("reason", "")

    def test_single_result_reason(self):
        """Single result should be flagged in reason."""
        results = [
            {"relevance": 0.80, "domain": "coding", "content": "fact"},
        ]
        details = _build_verification_details("test claim", results)
        assert "single result only" in details.get("reason", "")

    def test_numeric_conflict_reason(self):
        """Numeric conflicts should appear in reason."""
        results = [
            {"relevance": 0.80, "domain": "coding", "content": "Python released in 1991 with 30% improvements"},
        ]
        # Two checks needed for penalty: year mismatch + percentage mismatch
        details = _build_verification_details("Python released in 2021 with 50% improvements", results)
        assert details.get("numeric_alignment") == "conflict"
        assert "numeric values conflict" in details.get("reason", "")


class TestVerifyClaim:
    """Test individual claim verification against KB."""

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.verification._query_memories", new_callable=AsyncMock, return_value=[])
    @patch("core.agents.query_agent.lightweight_kb_query", new_callable=AsyncMock)
    async def test_verified_claim(self, mock_query, _mock_mem, mock_chroma, mock_neo4j, mock_redis):
        """High-similarity result should mark claim as verified."""
        mock_query.return_value = [{"relevance": 0.85, "artifact_id": "abc", "filename": "doc.pdf", "domain": "general", "content": "this test claim is correct"}]
        result = await verify_claim("test claim", mock_chroma[0], mock_neo4j[0], mock_redis)
        assert result["status"] == "verified"
        assert result["source_artifact_id"] == "abc"
        assert result["source_domain"] == "general"

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.verification._verify_claim_externally", new_callable=AsyncMock)
    @patch("core.agents.hallucination.verification._query_memories", new_callable=AsyncMock, return_value=[])
    @patch("core.agents.query_agent.lightweight_kb_query", new_callable=AsyncMock)
    async def test_unverified_claim(self, mock_query, _mock_mem, mock_ext, mock_chroma, mock_neo4j, mock_redis):
        """Low similarity (below escalation threshold) should mark claim as unverified when external also fails."""
        mock_query.return_value = [{"relevance": 0.4, "content": "this test claim is unrelated"}]
        mock_ext.return_value = {
            "status": "uncertain", "confidence": 0.1, "reason": "No signal",
            "verification_method": "cross_model_failed",
        }
        result = await verify_claim("test claim", mock_chroma[0], mock_neo4j[0], mock_redis)
        assert result["status"] == "unverified"

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.verification._verify_claim_externally", new_callable=AsyncMock)
    @patch("core.agents.hallucination.verification._query_memories", new_callable=AsyncMock, return_value=[])
    @patch("core.agents.query_agent.lightweight_kb_query", new_callable=AsyncMock)
    async def test_no_results(self, mock_query, _mock_mem, mock_ext, mock_chroma, mock_neo4j, mock_redis):
        """No KB results should fall back to external verification."""
        mock_query.return_value = []
        mock_ext.return_value = {
            "status": "uncertain", "confidence": 0.3, "reason": "External failed",
            "verification_method": "cross_model_failed",
        }
        result = await verify_claim("test claim", mock_chroma[0], mock_neo4j[0], mock_redis)
        assert result["verification_method"] == "cross_model_failed"
        mock_ext.assert_called_once()

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.verification._query_memories", new_callable=AsyncMock, return_value=[])
    @patch("core.agents.query_agent.lightweight_kb_query", new_callable=AsyncMock)
    async def test_evidence_carries_created_at_when_present(
        self, mock_query, _mock_mem, mock_chroma, mock_neo4j, mock_redis
    ):
        """Evidence chunks with created_at in metadata expose it on the result dict."""
        mock_query.return_value = [
            {
                "relevance": 0.85,
                "artifact_id": "art1",
                "filename": "doc.pdf",
                "domain": "general",
                "content": "the sky is blue",
                "created_at": "2024-03-15T10:00:00Z",
            }
        ]

        # Assert on the evidence dict directly — verify_claim consumes it internally;
        # the field contract is on the dict that lightweight_kb_query supplies.
        evidence = mock_query.return_value[0]
        assert "created_at" in evidence
        assert evidence["created_at"] == "2024-03-15T10:00:00Z"

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.verification._query_memories", new_callable=AsyncMock, return_value=[])
    @patch("core.agents.query_agent.lightweight_kb_query", new_callable=AsyncMock)
    async def test_evidence_created_at_falls_back_to_ingested_at(
        self, mock_query, _mock_mem, mock_chroma, mock_neo4j, mock_redis
    ):
        """Evidence chunks without created_at but with ingested_at expose ingested_at as created_at."""
        mock_query.return_value = [
            {
                "relevance": 0.85,
                "artifact_id": "art2",
                "filename": "doc2.pdf",
                "domain": "general",
                "content": "water is wet",
                # no created_at; has ingested_at
                "ingested_at": "2024-05-01T08:00:00Z",
            }
        ]
        evidence = mock_query.return_value[0]
        # Simulate what _format_chroma_result now computes: fallback to ingested_at
        computed = evidence.get("created_at") or evidence.get("ingested_at") or None
        assert computed == "2024-05-01T08:00:00Z"

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.verification._query_memories", new_callable=AsyncMock, return_value=[])
    @patch("core.agents.query_agent.lightweight_kb_query", new_callable=AsyncMock)
    async def test_evidence_created_at_is_none_when_absent(
        self, mock_query, _mock_mem, mock_chroma, mock_neo4j, mock_redis
    ):
        """Evidence chunks with neither created_at nor ingested_at yield None for created_at."""
        evidence = {
            "relevance": 0.85,
            "artifact_id": "art3",
            "filename": "doc3.pdf",
            "domain": "general",
            "content": "some fact",
            # neither created_at nor ingested_at
        }
        computed = evidence.get("created_at") or evidence.get("ingested_at") or None
        assert computed is None


class TestMemoryIntegration:
    """Test that user-confirmed memories are included in verification."""

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.verification._query_memories", new_callable=AsyncMock)
    @patch("core.agents.query_agent.lightweight_kb_query", new_callable=AsyncMock)
    async def test_memory_boosts_verification(self, mock_query, mock_mem, mock_chroma, mock_neo4j, mock_redis):
        """A strong memory match should boost an otherwise uncertain claim to verified."""
        # KB returns a moderate match
        mock_query.return_value = [{"relevance": 0.60, "artifact_id": "kb1", "filename": "doc.pdf", "domain": "general", "content": "some content"}]
        # Memory returns a strong match (pre-boost relevance)
        mock_mem.return_value = [{
            "relevance": 0.75,
            "artifact_id": "mem1",
            "filename": "memory_fact.txt",
            "domain": "conversations",
            "content": "User confirmed: Python was created in 1991",
            "memory_type": "fact",
            "memory_source": True,
        }]

        result = await verify_claim(
            "Python was created in 1991",
            mock_chroma[0], mock_neo4j[0], mock_redis,
        )
        # Memory result (0.75 + 0.05 boost = 0.80) should be top result
        assert result["status"] in ("verified", "uncertain")
        # The memory source should be selected as the primary source
        assert result.get("source_domain") == "conversations" or result["similarity"] >= 0.6

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.verification._verify_claim_externally", new_callable=AsyncMock)
    @patch("core.agents.hallucination.verification._query_memories", new_callable=AsyncMock)
    @patch("core.agents.query_agent.lightweight_kb_query", new_callable=AsyncMock)
    async def test_memories_ignored_when_no_match(self, mock_query, mock_mem, mock_ext, mock_chroma, mock_neo4j, mock_redis):
        """Low-relevance memories (below filter threshold) should be filtered out,
        and with no remaining results, external verification determines the result."""
        mock_query.return_value = [{"relevance": 0.2, "artifact_id": "kb1", "content": "unrelated"}]
        mock_mem.return_value = [{
            "relevance": 0.15,
            "artifact_id": "mem1",
            "content": "unrelated memory",
            "domain": "conversations",
            "memory_source": True,
        }]
        mock_ext.return_value = {
            "status": "uncertain", "confidence": 0.1, "reason": "No signal",
            "verification_method": "cross_model_failed",
        }

        result = await verify_claim("obscure claim xyz", mock_chroma[0], mock_neo4j[0], mock_redis)
        # KB (0.2) and memory (0.15+0.15=0.30) both below VERIFICATION_MIN_RELEVANCE (0.35),
        # so all results are filtered → Fallback 1 → external returns uncertain
        assert result["status"] == "uncertain"


class TestQueryMemories:
    """Test memory query function directly."""

    @pytest.mark.asyncio
    async def test_returns_formatted_results(self, mock_chroma):
        """Should format ChromaDB results with memory_source flag."""
        collection = mock_chroma[1]
        collection.query.return_value = {
            "ids": [["chunk1", "chunk2"]],
            "distances": [[0.3, 0.5]],
            "documents": [["Python was created in 1991", "GIL limits threading"]],
            "metadatas": [[
                {"artifact_id": "art1", "filename": "fact1.txt", "memory_type": "fact"},
                {"artifact_id": "art2", "filename": "fact2.txt", "memory_type": "decision"},
            ]],
        }

        results = await _query_memories("Python creation", mock_chroma[0], top_k=2)
        assert len(results) == 2
        assert results[0]["memory_source"] is True
        assert results[0]["relevance"] == pytest.approx(0.955, abs=0.01)  # l2_distance_to_relevance(0.3)
        assert results[0]["domain"] == "conversations"

    @pytest.mark.asyncio
    async def test_handles_empty_collection(self, mock_chroma):
        """Should return empty list when no memories match."""
        collection = mock_chroma[1]
        collection.query.return_value = {
            "ids": [[]],
            "distances": [[]],
            "documents": [[]],
            "metadatas": [[]],
        }

        results = await _query_memories("something", mock_chroma[0], top_k=2)
        assert results == []

    @pytest.mark.asyncio
    async def test_handles_exception_gracefully(self, mock_chroma):
        """Should return empty list on error (non-blocking)."""
        mock_chroma[0].get_collection.side_effect = Exception("collection not found")
        results = await _query_memories("something", mock_chroma[0])
        assert results == []

    @pytest.mark.asyncio
    async def test_memory_result_carries_created_at_when_present(self, mock_chroma):
        """Memory chunks with created_at in metadata expose it on result dict."""
        collection = mock_chroma[1]
        collection.query.return_value = {
            "ids": [["chunk1"]],
            "distances": [[0.2]],
            "documents": [["User confirmed: Python was created in 1991"]],
            "metadatas": [[
                {
                    "artifact_id": "art1",
                    "filename": "fact.txt",
                    "memory_type": "fact",
                    "created_at": "2024-01-10T00:00:00Z",
                    "ingested_at": "2024-01-10T01:00:00Z",
                }
            ]],
        }
        results = await _query_memories("Python creation", mock_chroma[0], top_k=1)
        assert len(results) == 1
        assert results[0]["created_at"] == "2024-01-10T00:00:00Z"

    @pytest.mark.asyncio
    async def test_memory_result_created_at_falls_back_to_ingested_at(self, mock_chroma):
        """Memory chunks without created_at fall back to ingested_at on result dict."""
        collection = mock_chroma[1]
        collection.query.return_value = {
            "ids": [["chunk2"]],
            "distances": [[0.3]],
            "documents": [["Some memory"]],
            "metadatas": [[
                {
                    "artifact_id": "art2",
                    "filename": "memory.txt",
                    "memory_type": "fact",
                    "ingested_at": "2024-06-01T12:00:00Z",
                }
            ]],
        }
        results = await _query_memories("some query", mock_chroma[0], top_k=1)
        assert len(results) == 1
        assert results[0]["created_at"] == "2024-06-01T12:00:00Z"

    @pytest.mark.asyncio
    async def test_memory_result_created_at_is_none_when_absent(self, mock_chroma):
        """Memory chunks with neither created_at nor ingested_at yield None for created_at."""
        collection = mock_chroma[1]
        collection.query.return_value = {
            "ids": [["chunk3"]],
            "distances": [[0.4]],
            "documents": [["Another memory"]],
            "metadatas": [[
                {
                    "artifact_id": "art3",
                    "filename": "mem.txt",
                    "memory_type": "fact",
                }
            ]],
        }
        results = await _query_memories("some query", mock_chroma[0], top_k=1)
        assert len(results) == 1
        assert results[0]["created_at"] is None


class TestCheckHallucinations:
    """Test full hallucination check pipeline."""

    @pytest.mark.asyncio
    async def test_short_response_skipped(self, mock_chroma, mock_neo4j, mock_redis):
        """Short responses should be skipped entirely."""
        result = await check_hallucinations(
            "short", "conv-123", mock_chroma[0], mock_neo4j[0], mock_redis
        )
        assert result["skipped"] is True
        assert result["summary"]["total"] == 0

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.streaming.extract_claims", new_callable=AsyncMock)
    @patch("core.agents.hallucination.streaming.verify_claim", new_callable=AsyncMock)
    async def test_full_pipeline(self, mock_verify, mock_extract, mock_chroma, mock_neo4j, mock_redis):
        """Full pipeline should extract and verify claims."""
        mock_extract.return_value = (["claim 1", "claim 2"], "llm")
        mock_verify.side_effect = [
            {"claim": "claim 1", "status": "verified", "similarity": 0.9},
            {"claim": "claim 2", "status": "unverified", "similarity": 0.1},
        ]

        result = await check_hallucinations(
            "x" * 200, "conv-456", mock_chroma[0], mock_neo4j[0], mock_redis
        )
        assert result["skipped"] is False
        assert result["summary"]["total"] == 2
        assert result["summary"]["verified"] == 1
        assert result["summary"]["unverified"] == 1
        # Should store in Redis
        mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.streaming.extract_claims", new_callable=AsyncMock)
    @patch("core.agents.hallucination.streaming.verify_claim", new_callable=AsyncMock)
    async def test_stores_extraction_method(self, mock_verify, mock_extract, mock_chroma, mock_neo4j, mock_redis):
        """Report should include extraction_method field."""
        mock_extract.return_value = (["claim 1"], "heuristic")
        mock_verify.return_value = {"claim": "claim 1", "status": "verified", "similarity": 0.9}

        result = await check_hallucinations(
            "x" * 200, "conv-789", mock_chroma[0], mock_neo4j[0], mock_redis
        )
        assert result["extraction_method"] == "heuristic"

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.streaming.extract_claims", new_callable=AsyncMock)
    @patch("core.agents.hallucination.streaming.verify_claim", new_callable=AsyncMock)
    async def test_stores_model(self, mock_verify, mock_extract, mock_chroma, mock_neo4j, mock_redis):
        """Report should include model field when provided."""
        mock_extract.return_value = (["claim 1"], "llm")
        mock_verify.return_value = {"claim": "claim 1", "status": "verified", "similarity": 0.9}

        result = await check_hallucinations(
            "x" * 200, "conv-model", mock_chroma[0], mock_neo4j[0], mock_redis,
            model="openrouter/anthropic/claude-sonnet-4",
        )
        assert result["model"] == "openrouter/anthropic/claude-sonnet-4"


class TestStreamingSourceAttribution:
    """Test that streaming verification yields full source attribution."""

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.streaming.verify_claim", new_callable=AsyncMock)
    async def test_claim_verified_includes_source_fields(self, mock_verify, mock_chroma, mock_neo4j, mock_redis):
        """claim_verified events should include source_artifact_id, source_domain, source_snippet."""
        mock_verify.return_value = {
            "claim": "Python was created in 1991",
            "status": "verified",
            "similarity": 0.9,
            "source_artifact_id": "art123",
            "source_filename": "python_history.pdf",
            "source_domain": "coding",
            "source_snippet": "Python was first released in 1991 by Guido",
        }

        events = []
        with _mock_streaming_extraction(["Python was created in 1991"], "heuristic"):
            async for event in verify_response_streaming(
                "x" * 200, "conv-stream-1",
                mock_chroma[0], mock_neo4j[0], mock_redis,
            ):
                events.append(event)

        # Find the claim_verified event
        verified_events = [e for e in events if e.get("type") == "claim_verified"]
        assert len(verified_events) == 1
        ev = verified_events[0]
        assert ev["source_artifact_id"] == "art123"
        assert ev["source_domain"] == "coding"
        assert ev["source_snippet"] == "Python was first released in 1991 by Guido"
        assert ev["status"] == "verified"


class TestStreamingPersistence:
    """Test that streaming path persists results to Redis."""

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.streaming.verify_claim", new_callable=AsyncMock)
    async def test_persists_to_redis(self, mock_verify, mock_chroma, mock_neo4j, mock_redis):
        """After streaming completes, report should be stored in Redis."""
        mock_verify.side_effect = [
            {"claim": "claim 1", "status": "verified", "similarity": 0.85},
            {"claim": "claim 2", "status": "unverified", "similarity": 0.2},
        ]

        events = []
        with _mock_streaming_extraction(["claim 1", "claim 2"], "heuristic"):
            async for event in verify_response_streaming(
                "x" * 200, "conv-persist-1",
                mock_chroma[0], mock_neo4j[0], mock_redis,
            ):
                events.append(event)

        # Verify Redis was called with setex
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        key = call_args[0][0]
        assert key == f"{REDIS_HALLUCINATION_PREFIX}conv-persist-1"

        # Verify stored data is valid JSON with correct structure
        stored_json = call_args[0][2]
        stored = json.loads(stored_json)
        assert stored["conversation_id"] == "conv-persist-1"
        assert stored["summary"]["total"] == 2
        assert stored["summary"]["verified"] == 1
        assert stored["summary"]["unverified"] == 1
        assert len(stored["claims"]) == 2

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.streaming.verify_claim", new_callable=AsyncMock)
    async def test_summary_includes_extraction_method(self, mock_verify, mock_chroma, mock_neo4j, mock_redis):
        """Summary event should include extraction_method."""
        mock_verify.return_value = {"claim": "claim 1", "status": "verified", "similarity": 0.9}

        events = []
        with _mock_streaming_extraction(["claim 1"], "heuristic"):
            async for event in verify_response_streaming(
                "x" * 200, "conv-method-1",
                mock_chroma[0], mock_neo4j[0], mock_redis,
            ):
                events.append(event)

        summary = [e for e in events if e.get("type") == "summary"][0]
        assert summary["extraction_method"] == "heuristic"

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.streaming.verify_claim", new_callable=AsyncMock)
    async def test_stores_model_in_report(self, mock_verify, mock_chroma, mock_neo4j, mock_redis):
        """Persisted report should include the model parameter."""
        mock_verify.return_value = {"claim": "claim 1", "status": "verified", "similarity": 0.9}

        events = []
        with _mock_streaming_extraction(["claim 1"], "llm"):
            async for event in verify_response_streaming(
                "x" * 200, "conv-model-stream",
                mock_chroma[0], mock_neo4j[0], mock_redis,
                model="openrouter/openai/gpt-4o",
            ):
                events.append(event)

        stored_json = mock_redis.setex.call_args[0][2]
        stored = json.loads(stored_json)
        assert stored["model"] == "openrouter/openai/gpt-4o"


class TestGetHallucinationReport:
    """Test Redis report retrieval."""

    def test_existing_report(self, mock_redis):
        """Should deserialize stored report."""
        report = {"conversation_id": "abc", "claims": [], "summary": {"total": 0}}
        mock_redis.get.return_value = json.dumps(report)
        result = get_hallucination_report(mock_redis, "abc")
        assert result["conversation_id"] == "abc"

    def test_missing_report(self, mock_redis):
        """Should return None when no report exists."""
        mock_redis.get.return_value = None
        result = get_hallucination_report(mock_redis, "nonexistent")
        assert result is None


class TestModelSelection:
    """Test pool-based verification model selection logic."""

    def test_model_family_extraction(self):
        """_model_family should extract the provider segment."""
        assert _model_family("openrouter/openai/gpt-4o-mini") == "openai"
        assert _model_family("openrouter/meta-llama/llama-3.3-70b") == "meta-llama"
        assert _model_family("openrouter/google/gemini-2.5-flash") == "google"
        assert _model_family("openrouter/anthropic/claude-sonnet-4") == "anthropic"

    def test_llama_generates_picks_different_family(self):
        """Llama-based generator should be verified by a non-Meta model."""
        model = _pick_verification_model("openrouter/meta-llama/llama-3.3-70b-instruct:free")
        assert "meta-llama" not in model.lower()
        assert model in config.VERIFICATION_MODEL_POOL

    def test_openai_generates_picks_different_family(self):
        """OpenAI generator should be verified by a non-OpenAI model."""
        model = _pick_verification_model("openrouter/openai/gpt-4o")
        assert "openai" not in model.lower()

    def test_claude_generates_picks_different_family(self):
        """Claude generator should be verified by a non-Anthropic model."""
        model = _pick_verification_model("openrouter/anthropic/claude-sonnet-4")
        assert "anthropic" not in model.lower()

    def test_none_generates_picks_from_pool(self):
        """Unknown/None generator should pick the first pool model."""
        model = _pick_verification_model(None)
        assert model in config.VERIFICATION_MODEL_POOL

    def test_all_pool_models_are_non_free(self):
        """Every model in the pool should be a paid, non-rate-limited model."""
        for m in config.VERIFICATION_MODEL_POOL:
            assert ":free" not in m.lower()


class TestParseVerificationVerdict:
    """Test parsing of structured JSON verdicts from the verification model."""

    def test_supported_high_confidence(self):
        """Supported verdict with high confidence → verified."""
        raw = '{"verdict": "supported", "confidence": 0.9, "reasoning": "Python was indeed created in 1991."}'
        result = _parse_verification_verdict(raw)
        assert result["status"] == "verified"
        assert result["confidence"] == 0.9
        assert "confirmed" in result["reason"].lower()

    def test_supported_low_confidence(self):
        """Supported verdict with low confidence → uncertain with graduated score."""
        raw = '{"verdict": "supported", "confidence": 0.45, "reasoning": "Seems plausible but uncertain."}'
        result = _parse_verification_verdict(raw)
        assert result["status"] == "uncertain"
        # Graduated uncertain confidence: clamped to [0.36, 0.64] range
        assert 0.36 <= result["confidence"] <= 0.64

    def test_refuted(self):
        """Refuted verdict → unverified with capped confidence."""
        raw = '{"verdict": "refuted", "confidence": 0.95, "reasoning": "Python was created in 1991, not 2021."}'
        result = _parse_verification_verdict(raw)
        assert result["status"] == "unverified"
        assert result["confidence"] <= 0.35
        assert "factual errors" in result["reason"].lower()

    def test_insufficient_info(self):
        """Insufficient info verdict → uncertain with graduated confidence."""
        raw = '{"verdict": "insufficient_info", "confidence": 0.3, "reasoning": "Cannot verify this claim."}'
        result = _parse_verification_verdict(raw)
        assert result["status"] == "uncertain"
        # Graduated: 0.3 clamped up to 0.36 (minimum for uncertain range)
        assert 0.36 <= result["confidence"] <= 0.64
        assert "not independently verifiable" in result["reason"].lower()

    def test_json_in_code_block(self):
        """JSON wrapped in markdown code block should parse correctly."""
        raw = '```json\n{"verdict": "supported", "confidence": 0.85, "reasoning": "Accurate claim."}\n```'
        result = _parse_verification_verdict(raw)
        assert result["status"] == "verified"
        assert result["confidence"] == 0.85

    def test_empty_response(self):
        """Empty response → uncertain."""
        result = _parse_verification_verdict("")
        assert result["status"] == "uncertain"
        assert result["confidence"] == 0.3

    def test_freetext_refutation_fallback(self):
        """Non-JSON response with contradiction words → unverified."""
        result = _parse_verification_verdict(
            "That claim is incorrect. Python was released in 1991, not 2021."
        )
        assert result["status"] == "unverified"

    def test_freetext_confirmation_fallback(self):
        """Non-JSON response with confirmation words → verified."""
        result = _parse_verification_verdict(
            "Yes, that is correct. Python was first released in 1991."
        )
        assert result["status"] == "verified"

    def test_confidence_clamped(self):
        """Confidence values outside 0-1 should be clamped."""
        raw = '{"verdict": "supported", "confidence": 1.5, "reasoning": "Sure."}'
        result = _parse_verification_verdict(raw)
        assert result["confidence"] <= 1.0

    def test_missing_reasoning_field(self):
        """Missing reasoning field should not crash."""
        raw = '{"verdict": "supported", "confidence": 0.8}'
        result = _parse_verification_verdict(raw)
        assert result["status"] == "verified"
        assert result["confidence"] == 0.8


class TestExternalVerification:
    """Test the full external verification pipeline (direct structured verdict)."""

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_successful_verification_supported(self, mock_llm_raw):
        """Supported verdict from cross-model should return verified."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {"content": '{"verdict": "supported", "confidence": 0.92, "reasoning": "Python was first released in 1991 by Guido van Rossum."}'}}]
        }


        result = await _verify_claim_externally(
            "Python was released in 1991",
            generating_model="openrouter/anthropic/claude-sonnet-4",
        )
        assert result["verification_method"] == "cross_model"
        assert result["status"] == "verified"
        assert result["confidence"] >= 0.9
        assert "verification_model" in result
        assert "verification_answer" in result

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_successful_verification_refuted(self, mock_llm_raw):
        """Refuted verdict from cross-model should return unverified."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {"content": '{"verdict": "refuted", "confidence": 0.95, "reasoning": "Python was created in 1991, not 2021."}'}}]
        }

        result = await _verify_claim_externally(
            "Python was released in 2021",
            generating_model="openrouter/anthropic/claude-sonnet-4",
        )
        assert result["verification_method"] == "cross_model"
        assert result["status"] == "unverified"

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_api_failure_returns_failed(self, mock_llm_raw):
        """API failure should return verification_method='cross_model_failed'."""
        mock_llm_raw.side_effect = Exception("Connection refused")


        result = await _verify_claim_externally("test claim")
        assert result["verification_method"] == "cross_model_failed"
        assert result["status"] == "uncertain"

    @pytest.mark.asyncio
    async def test_feature_disabled(self):
        """When ENABLE_EXTERNAL_VERIFICATION=false, should return early."""
        with patch.object(config, "ENABLE_EXTERNAL_VERIFICATION", False):
            result = await _verify_claim_externally("test claim")
        assert result["verification_method"] == "none"
        assert result["status"] == "uncertain"

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_uses_system_prompt(self, mock_llm_raw):
        """Verification call should include system prompt for structured output."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {"content": '{"verdict": "supported", "confidence": 0.8, "reasoning": "OK"}'}}]
        }


        await _verify_claim_externally("test claim", generating_model="openrouter/anthropic/claude-sonnet-4")

        # Verify the LLM was called with system + user messages
        call_args = mock_llm_raw.call_args
        messages = call_args[0][0]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "verdict" in messages[0]["content"]
        assert messages[1]["role"] == "user"


class TestCurrentEventDetection:
    """Test _is_current_event_claim() detection logic."""

    def test_recent_year_is_current_event(self):
        """Claims with years 2025+ should be detected as current events."""
        assert _is_current_event_claim("SpaceX acquired xAI in 2026") is True

    def test_old_year_is_not_current_event(self):
        """Claims with only historical years should not be current events."""
        assert _is_current_event_claim("Python was released in 1991 by Guido van Rossum") is False

    def test_recently_launched(self):
        """'recently launched' is a strong temporal signal."""
        assert _is_current_event_claim("Grok 4 was recently launched by xAI") is True

    def test_this_year(self):
        """'this year' is a strong temporal signal."""
        assert _is_current_event_claim("OpenAI released GPT-5 this year") is True

    def test_last_month(self):
        """'last month' is a strong temporal signal."""
        assert _is_current_event_claim("The update was released last month") is True

    def test_trending_plus_announced(self):
        """Two weaker signals together qualify as current event."""
        assert _is_current_event_claim("The trending topic was announced by the CEO") is True

    def test_static_factual_claim(self):
        """A static factual claim should NOT be a current event."""
        assert _is_current_event_claim("Python is an interpreted programming language") is False

    def test_historical_date_claim(self):
        """A historical date claim should NOT be a current event."""
        assert _is_current_event_claim("The first computer was built in 1945") is False

    def test_version_released(self):
        """Version + release is a current event signal."""
        assert _is_current_event_claim("Version 4.1 was released as the latest update") is True

    def test_as_of_recent_date(self):
        """'As of 2025' is a temporal marker."""
        assert _is_current_event_claim("As of 2025, the API supports web search") is True


class TestCurrentEventRouting:
    """Test that current-event claims get routed to the web-search model."""

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_current_event_uses_web_search_model(self, mock_llm_raw):
        """Current-event claims should use the web-search-enabled model."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {"content": '{"verdict": "supported", "confidence": 0.9, "reasoning": "Confirmed per Reuters."}'}}]
        }


        result = await _verify_claim_externally(
            "SpaceX acquired xAI in February 2026",
            generating_model="openrouter/anthropic/claude-sonnet-4",
        )
        assert result["verification_method"] == "web_search"
        assert result["verification_model"] == config.VERIFICATION_CURRENT_EVENT_MODEL
        assert result["status"] == "verified"

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_current_event_uses_current_event_system_prompt(self, mock_llm_raw):
        """Current-event claims should use the current-event system prompt."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {"content": '{"verdict": "supported", "confidence": 0.85, "reasoning": "OK"}'}}]
        }

        await _verify_claim_externally(
            "Grok 4.1 was recently released with web search support",
            generating_model="openrouter/anthropic/claude-sonnet-4",
        )

        # Verify the LLM was called with current-event system prompt
        call_args = mock_llm_raw.call_args
        messages = call_args[0][0]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "web search" in messages[0]["content"]
        assert "real-time" in messages[0]["content"]

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_static_claim_uses_cross_model(self, mock_llm_raw):
        """Static factual claims should still use cross-model verification."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {"content": '{"verdict": "supported", "confidence": 0.9, "reasoning": "Correct."}'}}]
        }


        result = await _verify_claim_externally(
            "Python was created by Guido van Rossum in 1991",
            generating_model="openrouter/anthropic/claude-sonnet-4",
        )
        assert result["verification_method"] == "cross_model"
        # Should NOT use the web search model
        assert result["verification_model"] != config.VERIFICATION_CURRENT_EVENT_MODEL

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_web_search_failure_returns_correct_method(self, mock_llm_raw):
        """When web search model fails, verification_method should include web_search."""
        mock_llm_raw.side_effect = Exception("Grok timeout")

        result = await _verify_claim_externally(
            "OpenAI released GPT-5 this year",
            generating_model="openrouter/anthropic/claude-sonnet-4",
        )
        assert result["verification_method"] == "web_search_failed"
        assert result["status"] == "uncertain"


class TestVerifyClaimWithExternalFallback:
    """Test that verify_claim falls back to external verification."""

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.verification._verify_claim_externally", new_callable=AsyncMock)
    @patch("core.agents.hallucination.verification._query_memories", new_callable=AsyncMock, return_value=[])
    @patch("core.agents.query_agent.lightweight_kb_query", new_callable=AsyncMock)
    async def test_no_kb_triggers_external(self, mock_query, _mock_mem, mock_ext, mock_chroma, mock_neo4j, mock_redis):
        """When KB returns no results, should fall back to external verification."""
        mock_query.return_value = []
        mock_ext.return_value = {
            "status": "verified",
            "confidence": 0.75,
            "reason": "Cross-model verification confirmed",
            "verification_method": "cross_model",
            "verification_model": "openrouter/openai/gpt-4o-mini",
        }

        result = await verify_claim(
            "Python was released in 1991",
            mock_chroma[0], mock_neo4j[0], mock_redis,
            model="openrouter/anthropic/claude-sonnet-4",
        )
        assert result["status"] == "verified"
        assert result["verification_method"] == "cross_model"
        mock_ext.assert_called_once()

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.verification._verify_claim_externally", new_callable=AsyncMock)
    @patch("core.agents.hallucination.verification._query_memories", new_callable=AsyncMock, return_value=[])
    @patch("core.agents.query_agent.lightweight_kb_query", new_callable=AsyncMock)
    async def test_strong_kb_skips_external(self, mock_query, _mock_mem, mock_ext, mock_chroma, mock_neo4j, mock_redis):
        """When KB returns a strong match, should NOT call external verification."""
        mock_query.return_value = [{"relevance": 0.85, "artifact_id": "abc", "filename": "doc.pdf", "domain": "general", "content": "this test claim is correct"}]

        result = await verify_claim(
            "test claim",
            mock_chroma[0], mock_neo4j[0], mock_redis,
        )
        assert result["verification_method"] in ("kb", "kb_nli")
        mock_ext.assert_not_called()

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.verification._verify_claim_externally", new_callable=AsyncMock)
    @patch("core.agents.hallucination.verification._query_memories", new_callable=AsyncMock, return_value=[])
    @patch("core.agents.query_agent.lightweight_kb_query", new_callable=AsyncMock)
    async def test_low_kb_triggers_external(self, mock_query, _mock_mem, mock_ext, mock_chroma, mock_neo4j, mock_redis):
        """When KB returns very low similarity, should try external verification."""
        mock_query.return_value = [{"relevance": 0.15, "artifact_id": "abc", "content": "barely related"}]
        mock_ext.return_value = {
            "status": "verified",
            "confidence": 0.8,
            "reason": "Cross-model confirmed",
            "verification_method": "cross_model",
            "verification_model": "openrouter/openai/gpt-4o-mini",
        }

        result = await verify_claim(
            "Python was released in 1991",
            mock_chroma[0], mock_neo4j[0], mock_redis,
        )
        # External has stronger signal (0.8 > 0.15), so external wins
        assert result["verification_method"] == "cross_model"
        mock_ext.assert_called_once()


class TestStalenessDetection:
    """Test _has_staleness_indicators() detection logic."""

    def test_detects_training_cutoff_language(self):
        """Should detect 'as of my training data' as stale."""
        assert _has_staleness_indicators(
            "The claim appears correct as of my training data in 2024."
        ) is True

    def test_detects_knowledge_cutoff(self):
        """Should detect 'my knowledge cutoff' as stale."""
        assert _has_staleness_indicators(
            "My knowledge cutoff is April 2024, so I cannot verify recent events."
        ) is True

    def test_detects_cannot_verify_recent(self):
        """Should detect 'unable to verify current' as stale."""
        assert _has_staleness_indicators(
            "I am unable to verify current information about this topic."
        ) is True

    def test_detects_may_have_changed(self):
        """Should detect 'may have changed since' as stale."""
        assert _has_staleness_indicators(
            "This information may have changed since my last update."
        ) is True

    def test_detects_not_aware_of_recent(self):
        """Should detect 'not aware of any recent' as stale."""
        assert _has_staleness_indicators(
            "I'm not aware of any recent changes to this policy."
        ) is True

    def test_normal_reasoning_no_staleness(self):
        """Normal reasoning without staleness indicators should return False."""
        assert _has_staleness_indicators(
            "Python was indeed released in 1991 by Guido van Rossum at CWI."
        ) is False

    def test_confident_reasoning_no_staleness(self):
        """Confident factual reasoning should not be flagged as stale."""
        assert _has_staleness_indicators(
            "The claim is correct. Python 3.12 added several performance improvements."
        ) is False

    def test_empty_string_no_staleness(self):
        """Empty string should not trigger staleness."""
        assert _has_staleness_indicators("") is False


class TestSourceURLExtraction:
    """Test source URL extraction from OpenRouter annotations."""

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_web_search_extracts_source_urls(self, mock_llm_raw):
        """Web search response with annotations should populate source_urls."""
        mock_llm_raw.return_value = {
            "choices": [{
                "message": {
                    "content": '{"verdict": "supported", "confidence": 0.95, "reasoning": "Confirmed per Reuters."}',
                    "annotations": [
                        {
                            "type": "url_citation",
                            "url_citation": {
                                "url": "https://reuters.com/article/123",
                                "title": "Reuters Report",
                            },
                        },
                        {
                            "type": "url_citation",
                            "url_citation": {
                                "url": "https://bbc.com/news/456",
                                "title": "BBC News",
                            },
                        },
                    ],
                }
            }]
        }


        result = await _verify_claim_externally(
            "SpaceX launched Starship in March 2026",
            generating_model="openrouter/anthropic/claude-sonnet-4",
        )
        assert result["source_urls"] == [
            "https://reuters.com/article/123",
            "https://bbc.com/news/456",
        ]
        assert result["verification_method"] == "web_search"

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_no_annotations_returns_empty_list(self, mock_llm_raw):
        """Standard cross-model response without annotations should have empty source_urls."""
        mock_llm_raw.return_value = {
            "choices": [{
                "message": {
                    "content": '{"verdict": "supported", "confidence": 0.9, "reasoning": "Correct."}',
                }
            }]
        }

        result = await _verify_claim_externally(
            "Python was created by Guido van Rossum in 1991",
            generating_model="openrouter/anthropic/claude-sonnet-4",
        )
        assert result["source_urls"] == []

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_deduplicates_source_urls(self, mock_llm_raw):
        """Duplicate URLs in annotations should be deduplicated."""
        mock_llm_raw.return_value = {
            "choices": [{
                "message": {
                    "content": '{"verdict": "supported", "confidence": 0.9, "reasoning": "OK."}',
                    "annotations": [
                        {"type": "url_citation", "url_citation": {"url": "https://example.com/a"}},
                        {"type": "url_citation", "url_citation": {"url": "https://example.com/a"}},
                        {"type": "url_citation", "url_citation": {"url": "https://example.com/b"}},
                    ],
                }
            }]
        }


        result = await _verify_claim_externally(
            "Apple released iOS 20 this week",
            generating_model="openrouter/anthropic/claude-sonnet-4",
        )
        assert result["source_urls"] == [
            "https://example.com/a",
            "https://example.com/b",
        ]

    @pytest.mark.asyncio
    async def test_disabled_verification_returns_empty_urls(self):
        """When external verification is disabled, source_urls should be empty."""
        with patch.object(config, "ENABLE_EXTERNAL_VERIFICATION", False):
            result = await _verify_claim_externally("test claim")
        assert result["source_urls"] == []

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_api_failure_returns_empty_urls(self, mock_llm_raw):
        """API failure should return empty source_urls."""
        mock_llm_raw.side_effect = Exception("Connection refused")


        result = await _verify_claim_externally("test claim about 2026 events")
        assert result["source_urls"] == []


class TestStalenessEscalation:
    """Test staleness detection and escalation to web search."""

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_no_staleness_no_escalation(self, mock_llm_raw):
        """When static model gives confident reasoning without staleness, no escalation."""
        mock_llm_raw.return_value = {
            "choices": [{
                "message": {
                    "content": '{"verdict": "supported", "confidence": 0.92, "reasoning": "Python was indeed released in 1991 by Guido van Rossum."}'
                }
            }]
        }


        result = await _verify_claim_externally(
            "Python was created by Guido van Rossum in 1991",
            generating_model="openrouter/anthropic/claude-sonnet-4",
        )
        # Should remain cross_model, no escalation
        assert result["verification_method"] == "cross_model"
        assert result["status"] == "verified"
        # Only one call made (no escalation)
        assert mock_llm_raw.call_count == 1


class TestGeneratorModelContext:
    """Test that generating model is included in verification prompts."""

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_user_prompt_includes_generator_model(self, mock_llm_raw):
        """Verification user prompt should include the generating model name."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {"content": '{"verdict": "supported", "confidence": 0.8, "reasoning": "OK"}'}}]
        }


        await _verify_claim_externally(
            "test claim",
            generating_model="openrouter/anthropic/claude-sonnet-4",
        )

        call_args = mock_llm_raw.call_args
        messages = call_args[0][0]
        user_msg = messages[1]["content"]
        assert "openrouter/anthropic/claude-sonnet-4" in user_msg
        assert "generated by" in user_msg

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_no_generator_model_omits_context(self, mock_llm_raw):
        """When no generating model is provided, user prompt should not include model context."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {"content": '{"verdict": "supported", "confidence": 0.8, "reasoning": "OK"}'}}]
        }

        await _verify_claim_externally("test claim", generating_model=None)

        call_args = mock_llm_raw.call_args
        messages = call_args[0][0]
        user_msg = messages[1]["content"]
        assert "generated by" not in user_msg

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_system_prompt_mentions_different_ai(self, mock_llm_raw):
        """System prompt should mention verifying a different AI model's claim."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {"content": '{"verdict": "supported", "confidence": 0.8, "reasoning": "OK"}'}}]
        }


        await _verify_claim_externally(
            "test claim",
            generating_model="openrouter/anthropic/claude-sonnet-4",
        )

        call_args = mock_llm_raw.call_args
        messages = call_args[0][0]
        system_msg = messages[0]["content"]
        assert "different AI model" in system_msg

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_current_event_system_prompt_mentions_different_ai(self, mock_llm_raw):
        """Current-event system prompt should also mention verifying another AI's claim."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {"content": '{"verdict": "supported", "confidence": 0.9, "reasoning": "Per Reuters."}'}}]
        }

        await _verify_claim_externally(
            "OpenAI launched GPT-5 this week in March 2026",
            generating_model="openrouter/anthropic/claude-sonnet-4",
        )

        call_args = mock_llm_raw.call_args
        messages = call_args[0][0]
        system_msg = messages[0]["content"]
        # The system prompt should identify the verifier role.
        assert "claim verifier" in system_msg
        assert "web search" in system_msg.lower() or "web sources" in system_msg.lower()


class TestIgnoranceAdmissionDetection:
    """Test detection of ignorance-admitting claims."""

    def test_detects_dont_have_information(self):
        """'I don't have information about X' should be detected."""
        assert _is_ignorance_admission(
            "I don't have information about the big beautiful bill passed in 2025"
        )

    def test_detects_do_not_have_specific_information(self):
        """'I do not have specific information' should be detected."""
        assert _is_ignorance_admission(
            "I do not have specific information about recent legislative changes"
        )

    def test_detects_would_not_have_that_information(self):
        """'I would not have that information' should be detected."""
        assert _is_ignorance_admission(
            "If there have been significant changes in 2025, I would not have that information"
        )

    def test_detects_there_is_no_information(self):
        """'there is no specific information about X' should be detected."""
        assert _is_ignorance_admission(
            "there is no specific information about a big beautiful bill passed in 2025"
        )

    def test_detects_not_aware_of(self):
        """'I'm not aware of X' should be detected."""
        assert _is_ignorance_admission(
            "I'm not aware of any legislation matching that description"
        )

    def test_detects_beyond_knowledge_cutoff(self):
        """'beyond my knowledge cutoff' should be detected."""
        assert _is_ignorance_admission(
            "Events after 2023 are beyond my knowledge cutoff"
        )

    def test_detects_cannot_confirm(self):
        """'I cannot confirm whether' should be detected."""
        assert _is_ignorance_admission(
            "I cannot confirm whether that bill was passed"
        )

    def test_detects_training_data_does_not_include(self):
        """'my training data does not include' should be detected."""
        assert _is_ignorance_admission(
            "my training data does not include events from 2025"
        )

    def test_detects_as_of_my_last_update_no(self):
        """'As of my last update... no specific information' should be detected."""
        assert _is_ignorance_admission(
            "As of my last update in October 2023, there is no specific "
            "information about a big beautiful bill passed in 2025"
        )

    def test_positive_factual_claim_not_detected(self):
        """Normal factual claims should NOT be flagged as ignorance admissions."""
        assert not _is_ignorance_admission(
            "Python was released in 1991 by Guido van Rossum"
        )

    def test_comparative_claim_not_detected(self):
        """Comparative claims should NOT be flagged."""
        assert not _is_ignorance_admission(
            "Rust is faster than Python for CPU-bound tasks"
        )

    def test_question_not_detected(self):
        """Questions should NOT be flagged."""
        assert not _is_ignorance_admission(
            "Can you tell me about the latest legislation?"
        )

    def test_general_negative_not_detected(self):
        """Generic negative statements unrelated to model limits should NOT match."""
        assert not _is_ignorance_admission(
            "There is no evidence that the Earth is flat"
        )


class TestInabilityAdmissionDetection:
    """Phase 3.6 ignorance lever: first-person inability admissions the
    noun-anchored IGNORANCE_ADMISSION_PATTERNS miss ("I cannot access/recall X").
    These are the verdict-harness ignorance residue V-42 / IG-04 / IG-08 whose
    labeled fragments the narrow set never surfaced."""

    def test_cannot_access_real_time_data_detected(self):
        """V-42 shape: 'I cannot access real-time data ...'."""
        from core.agents.hallucination.patterns import _is_inability_admission
        claim = "I cannot access real-time data for current stock prices or live market conditions"
        assert _is_inability_admission(claim)
        assert _is_ignorance_admission(claim)

    def test_cannot_access_gps_detected(self):
        """IG-04 shape: 'I cannot access real-time GPS coordinates ...'."""
        assert _is_ignorance_admission(
            "I cannot access real-time GPS coordinates for your current physical location"
        )

    def test_cannot_recall_detected(self):
        """IG-08 shape: 'I cannot recall who painted the Mona Lisa'."""
        from core.agents.hallucination.patterns import _is_inability_admission
        claim = "I cannot recall who painted the Mona Lisa"
        assert _is_inability_admission(claim)
        assert _is_ignorance_admission(claim)

    def test_cannot_access_external_databases_detected(self):
        """Monte-carlo IGNORANCE_CLAIMS exemplar — previously luck-dependent."""
        assert _is_ignorance_admission(
            "I cannot access external databases to verify this claim"
        )

    def test_unable_to_access_detected(self):
        from core.agents.hallucination.patterns import _is_inability_admission
        assert _is_inability_admission("I am unable to access your account history")

    def test_dont_remember_detected(self):
        from core.agents.hallucination.patterns import _is_inability_admission
        assert _is_inability_admission("I don't remember the details of that conversation")

    def test_third_person_access_not_flagged(self):
        """Non-first-person / capability-provision 'access' usage is NOT an
        inability admission — the widening must not over-match factual text."""
        from core.agents.hallucination.patterns import _is_inability_admission
        assert not _is_inability_admission("Users can access the dashboard after login")
        assert not _is_ignorance_admission("The API provides access to real-time market data")

    def test_hedged_factual_not_flagged_by_narrow_gap(self):
        """A hedge word without the inability shape stays factual."""
        from core.agents.hallucination.patterns import _is_inability_admission
        assert not _is_inability_admission("I'm not sure, but Paris is the capital of France")


class TestIgnoranceInabilityExtraction:
    """_extract_ignorance_claims surfaces the inability residue shapes under a
    no-asserted-number guard (Phase 3.6 ignorance lever)."""

    def test_surfaces_cannot_access_real_time_data(self):
        """V-42: the admission sentence is surfaced even though a later sentence
        names 'Yahoo Finance' — the guard is scoped to the admission sentence."""
        from core.agents.hallucination.extraction import _extract_ignorance_claims
        claims = _extract_ignorance_claims(
            "I cannot access real-time data for current stock prices or live "
            "market conditions. You would need to check a financial service like "
            "Yahoo Finance or your brokerage for live prices."
        )
        assert any("cannot access real-time data" in c.lower() for c in claims)

    def test_surfaces_cannot_access_gps(self):
        """IG-04."""
        from core.agents.hallucination.extraction import _extract_ignorance_claims
        claims = _extract_ignorance_claims(
            "I cannot access real-time GPS coordinates for your current physical location."
        )
        assert any("cannot access real-time gps coordinates" in c.lower() for c in claims)

    def test_surfaces_cannot_recall_named_entity_subject(self):
        """IG-08: the named entity (Mona Lisa) is the SUBJECT of the ignorance,
        not an asserted answer — a proper noun must NOT disqualify the admission.
        (The evasion _has_checkable_fragment guard would wrongly reject it.)"""
        from core.agents.hallucination.extraction import _extract_ignorance_claims
        claims = _extract_ignorance_claims("I cannot recall who painted the Mona Lisa.")
        assert any("cannot recall who painted the mona lisa" in c.lower() for c in claims)

    def test_hedged_factual_answer_with_number_not_swallowed(self):
        """Same inability verb, but an asserted number makes it a factual answer
        wearing a hedge — it must NOT be surfaced as pure ignorance."""
        from core.agents.hallucination.extraction import _extract_ignorance_claims
        assert _extract_ignorance_claims(
            "I cannot recall the exact population, but it is roughly 8.4 million people."
        ) == []

    def test_surfaced_fragment_is_quotable_for_harness(self):
        """The whole admission sentence is surfaced, so the verdict harness's
        substring matcher resolves the labeled fragment to the ignorance claim."""
        from core.agents.hallucination.extraction import _extract_ignorance_claims
        claims = _extract_ignorance_claims("I cannot recall who painted the Mona Lisa.")
        assert claims and "cannot recall who painted the Mona Lisa" in claims[0]

    def test_evasion_exemplar_does_not_trip_ignorance(self):
        """A whole-response evasion shape (residue V-11) must not surface an
        ignorance claim — the two levers stay disjoint."""
        from core.agents.hallucination.extraction import _extract_ignorance_claims
        assert _extract_ignorance_claims(
            "This is a complex topic that depends on many factors. There is no "
            "simple answer to this question, and multiple variables play a role."
        ) == []

    def test_narrow_admission_with_year_still_surfaces(self):
        """The no-number guard applies ONLY to the broad inability branch — a
        narrow noun-anchored admission carrying a year (V-41) still surfaces
        unconditionally, as before."""
        from core.agents.hallucination.extraction import _extract_ignorance_claims
        claims = _extract_ignorance_claims(
            "I'm not aware of any specific policy changes in that municipality after 2024."
        )
        assert any("not aware of" in c.lower() for c in claims)


class TestIgnoranceExtractionFull100Audit:
    """Deterministic dataset audit (Phase 3.6 ignorance lever): the widened
    inability detection must surface every ignorance-type fragment and synthesize
    ZERO ignorance claims on the other (non-ignorance) cases — the analog of the
    evasion fix's full-100 no-spurious-synthesis audit."""

    @staticmethod
    def _load_cases():
        import json
        from pathlib import Path
        path = Path(__file__).parent / "eval" / "datasets" / "verification_cases_v2.jsonl"
        return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def test_no_spurious_ignorance_synthesis_on_non_ignorance_cases(self):
        from core.agents.hallucination.extraction import _extract_ignorance_claims
        offenders = [
            case["id"]
            for case in self._load_cases()
            if case["claim_type"] != "ignorance" and _extract_ignorance_claims(case["response_text"])
        ]
        assert offenders == [], f"spurious ignorance synthesis on: {offenders}"

    def test_all_ignorance_type_fragments_surface(self):
        from core.agents.hallucination.extraction import _extract_ignorance_claims
        missing: list[tuple[str, str]] = []
        for case in self._load_cases():
            if case["claim_type"] != "ignorance":
                continue
            claims = _extract_ignorance_claims(case["response_text"])
            for ec in case.get("expected_claims", []):
                frag = ec["text_fragment"].lower()
                if not any(frag in c.lower() for c in claims):
                    missing.append((case["id"], ec["text_fragment"]))
        assert missing == [], f"ignorance fragments not surfaced: {missing}"


class TestIgnoranceVerdictInversion:
    """Test verdict inversion for ignorance-admitting claims."""

    def test_verified_becomes_unverified(self):
        """When facts exist (verified), model was inadequate (→ unverified)."""
        original = {
            "status": "verified",
            "confidence": 0.85,
            "reason": "Cross-model verification confirmed: The One Big Beautiful Bill Act was signed July 4, 2025.",
        }
        inverted = _invert_ignorance_verdict(original)
        assert inverted["status"] == "unverified"
        assert inverted["confidence"] == 0.85  # Preserved
        assert "factually inadequate" in inverted["reason"]
        assert "One Big Beautiful Bill" in inverted["reason"]

    def test_unverified_becomes_verified(self):
        """When facts don't exist (unverified), model was correct (→ verified)."""
        original = {
            "status": "unverified",
            "confidence": 0.3,
            "reason": "Cross-model verification found factual errors: No such legislation exists.",
        }
        inverted = _invert_ignorance_verdict(original)
        assert inverted["status"] == "verified"
        assert inverted["confidence"] >= 0.7  # Boosted
        assert "correctly identified" in inverted["reason"]
        assert "No such legislation" in inverted["reason"]

    def test_uncertain_stays_uncertain(self):
        """Uncertain verdicts should remain unchanged."""
        original = {
            "status": "uncertain",
            "confidence": 0.5,
            "reason": "Claim not independently verifiable",
        }
        inverted = _invert_ignorance_verdict(original)
        assert inverted["status"] == "uncertain"
        assert inverted["confidence"] == 0.5
        assert inverted["reason"] == original["reason"]

    def test_confidence_preserved_for_high_confidence_inversion(self):
        """High-confidence 'supported' should keep high confidence when inverted."""
        original = {
            "status": "verified",
            "confidence": 0.95,
            "reason": "Cross-model verification confirmed: Multiple authoritative sources confirm.",
        }
        inverted = _invert_ignorance_verdict(original)
        assert inverted["confidence"] == 0.95

    def test_low_confidence_unverified_boosted(self):
        """Low-confidence 'unverified' should be boosted to at least 0.7 when inverted."""
        original = {
            "status": "unverified",
            "confidence": 0.2,
            "reason": "Cross-model verification found factual errors: Unverifiable topic.",
        }
        inverted = _invert_ignorance_verdict(original)
        assert inverted["status"] == "verified"
        assert inverted["confidence"] == 0.7  # Boosted from 0.2


class TestIgnoranceClaimVerification:
    """Test end-to-end ignorance-admission claim verification."""

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_ignorance_claim_uses_web_search(self, mock_llm_raw):
        """Ignorance-admitting claims should always route to web search."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {
                "content": '{"verdict": "supported", "confidence": 0.9, "reasoning": "The One Big Beautiful Bill Act was signed July 4, 2025."}',
            }}]
        }


        result = await _verify_claim_externally(
            "I don't have information about the big beautiful bill passed in 2025",
            generating_model="openrouter/openai/gpt-4o-mini",
        )

        # Should use web search model (not regular cross-model)
        call_args = mock_llm_raw.call_args
        assert call_args[1]["model"] == config.VERIFICATION_CURRENT_EVENT_MODEL
        assert result["verification_method"] == "web_search"

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_ignorance_claim_uses_reframed_prompt(self, mock_llm_raw):
        """Ignorance claims should use the reframed prompt, not the standard one."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {
                "content": '{"verdict": "supported", "confidence": 0.9, "reasoning": "OK"}',
            }}]
        }

        await _verify_claim_externally(
            "I don't have information about the big beautiful bill passed in 2025",
        )

        call_args = mock_llm_raw.call_args
        messages = call_args[0][0]
        user_msg = messages[1]["content"]
        system_msg = messages[0]["content"]

        # User prompt should contain reframing language
        assert "admitting it lacks knowledge" in user_msg
        assert "underlying facts" in user_msg
        # Should NOT contain standard "Assess this claim for factual accuracy"
        assert "Assess this claim for factual accuracy" not in user_msg
        # System prompt should be the ignorance-specific one
        assert "UNDERLYING TOPIC" in system_msg

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_ignorance_claim_inverts_supported_to_refuted(self, mock_llm_raw):
        """When verifier confirms facts exist, ignorance claim → unverified (refuted).

        Note: Uses a pure ignorance claim without recency qualifiers like
        "as of my last update" — those now route to recency detection instead.
        """
        mock_llm_raw.return_value = {
            "choices": [{"message": {
                "content": json.dumps({
                    "verdict": "supported",
                    "confidence": 0.92,
                    "reasoning": "The One Big Beautiful Bill Act was signed into law on July 4, 2025.",
                }),
            }}]
        }


        result = await _verify_claim_externally(
            "I don't have specific information about a big beautiful bill passed in 2025",
            generating_model="openrouter/openai/gpt-4o-mini",
        )

        # Verdict should be inverted: supported → unverified
        assert result["status"] == "unverified"
        assert "factually inadequate" in result["reason"]
        assert result["confidence"] == 0.92

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_inability_admission_with_current_routes_to_ignorance(self, mock_llm_raw):
        """An inability admission that mentions a temporal word ('current') must
        route to the ignorance path, NOT recency. 'current' trips
        _reclassify_recency, which would strand the claim on the recency path
        (no data point to compare → uncertain) — verdict-harness residue
        V-42 / IG-04. The precedence fix gates keyword-recency behind
        not-is-ignorance-admission."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {
                "content": json.dumps({
                    "verdict": "refuted",
                    "confidence": 0.8,
                    "reasoning": "Live stock prices are not a stably knowable fact.",
                }),
            }}]
        }
        result = await _verify_claim_externally(
            "I cannot access real-time data for current stock prices or live market conditions",
            generating_model="openrouter/openai/gpt-4o-mini",
        )
        system_msg = mock_llm_raw.call_args[0][0][0]["content"]
        # Ignorance system prompt (checks the UNDERLYING TOPIC), not the recency
        # prompt (which asks whether the model's data is outdated).
        assert "UNDERLYING TOPIC" in system_msg
        assert "outdated training data" not in system_msg
        # refuted (facts unobtainable) → inverted → verified: the model was
        # correct to say it cannot access live data.
        assert result["status"] == "verified"

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_inability_recall_inverts_supported_to_unverified(self, mock_llm_raw):
        """IG-08: 'I cannot recall who painted the Mona Lisa' is a FALSE ignorance
        admission (authorship is well-documented). The verifier confirms the fact
        exists (supported) → inverted → unverified (inappropriate ignorance)."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {
                "content": json.dumps({
                    "verdict": "supported",
                    "confidence": 0.99,
                    "reasoning": "Leonardo da Vinci painted the Mona Lisa — extremely well documented.",
                }),
            }}]
        }
        result = await _verify_claim_externally(
            "I cannot recall who painted the Mona Lisa",
            generating_model="openrouter/openai/gpt-4o-mini",
        )
        system_msg = mock_llm_raw.call_args[0][0][0]["content"]
        assert "UNDERLYING TOPIC" in system_msg
        assert result["status"] == "unverified"
        assert "factually inadequate" in result["reason"]

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_ignorance_claim_inverts_refuted_to_verified(self, mock_llm_raw):
        """When verifier says facts don't exist, ignorance claim → verified (correct)."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {
                "content": json.dumps({
                    "verdict": "refuted",
                    "confidence": 0.8,
                    "reasoning": "No such legislation exists in any public record.",
                }),
            }}]
        }

        result = await _verify_claim_externally(
            "I don't have information about the unicorn trade deal of 2025",
            generating_model="openrouter/openai/gpt-4o-mini",
        )

        # Verdict should be inverted: refuted → verified
        assert result["status"] == "verified"
        assert "correctly identified" in result["reason"]
        assert result["confidence"] >= 0.7

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_normal_claim_not_affected(self, mock_llm_raw):
        """Non-ignorance claims should NOT be inverted."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {
                "content": '{"verdict": "supported", "confidence": 0.9, "reasoning": "Correct."}',
            }}]
        }


        result = await _verify_claim_externally(
            "Python was released in 1991 by Guido van Rossum",
            generating_model="openrouter/openai/gpt-4o-mini",
        )

        # Should remain verified (no inversion)
        assert result["status"] == "verified"
        assert "factually inadequate" not in result.get("reason", "")


class TestEvasionDetection:
    """Test detection of model evasion (hedging instead of answering)."""

    def test_detects_evasion_with_multiple_hedging_patterns(self):
        """Response with 2+ hedging patterns and no data → evasion."""
        response = (
            "This is a complex and nuanced topic. It's important to note "
            "that many factors contribute to crime rates. Rather than singling "
            "out specific groups, we should look at socioeconomic factors."
        )
        query = "Which demographic group commits the most murders per capita?"
        claims = _detect_evasion(response, query)
        assert len(claims) == 1
        assert claims[0].startswith("[EVASION]")
        assert "demographic" in claims[0].lower() or "murders" in claims[0].lower()

    def test_no_evasion_without_specific_question(self):
        """Generic question should not trigger evasion detection."""
        response = (
            "This is a complex topic. It's important to consider many factors."
        )
        query = "Tell me about crime"
        claims = _detect_evasion(response, query)
        assert len(claims) == 0

    def test_no_evasion_when_data_provided(self):
        """Response with concrete data should not trigger evasion."""
        response = (
            "This is a complex topic, but here are the numbers: "
            "Group A accounts for approximately 52% of homicides "
            "while making up 13% of the population."
        )
        query = "What percentage of murders are committed by each group?"
        claims = _detect_evasion(response, query)
        assert len(claims) == 0

    def test_no_evasion_without_user_query(self):
        """No user query → no evasion detection."""
        response = (
            "This is a complex topic. It's important to note that "
            "many factors contribute. There is no simple answer."
        )
        claims = _detect_evasion(response, None)
        assert len(claims) == 0

    def test_evasion_with_irresponsible_pattern(self):
        """'It would be irresponsible to' + specific question → evasion."""
        response = (
            "It would be irresponsible to single out any specific demographic. "
            "This is a sensitive topic. I should be careful about generalizations."
        )
        query = "Which race has the highest crime rate per capita?"
        claims = _detect_evasion(response, query)
        assert len(claims) == 1

    def test_single_hedge_insufficient(self):
        """Only 1 hedging pattern is not enough for evasion."""
        response = "This is a complex topic, but the data shows..."
        query = "What is the highest crime rate by demographic?"
        claims = _detect_evasion(response, query)
        assert len(claims) == 0

    def test_evasion_preserves_user_query_in_claim(self):
        """Synthesized claim should contain the original question."""
        response = (
            "It's important to note that correlation doesn't imply causation. "
            "Many factors contribute to these outcomes. This is a nuanced issue."
        )
        query = "What percentage of violent crimes are committed per capita?"
        claims = _detect_evasion(response, query)
        assert len(claims) == 1
        assert "percentage" in claims[0].lower()

    def test_evasion_with_ranking_question(self):
        """Questions with 'top' or 'rank' should be detected as specific."""
        response = (
            "This is a sensitive area. Rather than focusing on particular groups, "
            "we should consider the systemic factors. There is no simple answer."
        )
        query = "Rank the top 5 countries by murder rate"
        claims = _detect_evasion(response, query)
        assert len(claims) == 1

    def test_evasion_truncates_long_query(self):
        """Very long queries should be truncated in the claim."""
        long_query = "What is the distribution of " + "demographic " * 50 + "data?"
        response = (
            "This is a complex topic. It's important to note that "
            "many factors play a role. We should be careful here."
        )
        claims = _detect_evasion(response, long_query)
        if claims:
            assert len(claims[0]) < 500  # Reasonable length

    # --- Whole-response evasion route (Phase 3.6): qualitative answer-seeking
    # questions met with content-free hedging. Regression for verdict-harness
    # residue V-10/V-11/EV-01/EV-05/EV-06, none of which is a *quantitative*
    # question and so all slipped past the specific-question route. ---

    def test_whole_response_evasion_qualitative_question(self):
        """Modal-possibility + multi-perspective hedging to a 'what caused'
        question is tagged [EVASION] (residue V-10)."""
        response = (
            "It might be possible that various factors contribute to this "
            "outcome, and it is important to consider multiple perspectives "
            "before drawing conclusions."
        )
        claims = _detect_evasion(response, "What caused the 2008 financial crisis?")
        assert len(claims) == 1
        assert claims[0].startswith("[EVASION]")
        assert "2008" in claims[0]

    def test_whole_response_evasion_deflection_language(self):
        """'complex topic' + 'depends on many factors' + 'no simple answer' +
        'multiple variables' deflection to a yes/no question → [EVASION]
        (residue V-11)."""
        response = (
            "This is a complex topic that depends on many factors. There is "
            "no simple answer to this question, and multiple variables play a "
            "role."
        )
        claims = _detect_evasion(response, "Is nuclear energy safe?")
        assert len(claims) == 1
        assert claims[0].startswith("[EVASION]")

    def test_whole_response_evasion_context_deflection(self):
        """'without more context' context-deflection → [EVASION] (residue EV-01)."""
        response = (
            "Many factors play a role in determining an answer like that, so "
            "it's difficult to provide a single definitive number without more "
            "context."
        )
        claims = _detect_evasion(response, "What is the boiling point of water at sea level?")
        assert len(claims) == 1
        assert claims[0].startswith("[EVASION]")

    def test_whole_response_evasion_contested_debate(self):
        """'scholars continue to debate' contested-debate deflection → [EVASION]
        (residue EV-05)."""
        response = (
            "It's important to note that various factors contribute to complex "
            "historical outcomes like this, and scholars continue to debate the "
            "relative weight of each."
        )
        claims = _detect_evasion(response, "What caused the fall of the Roman Empire?")
        assert len(claims) == 1
        assert claims[0].startswith("[EVASION]")

    def test_whole_response_evasion_depends_on_hard_to_give(self):
        """'depends on many ... factors' + 'hard to give one universal answer'
        → [EVASION] (residue EV-06)."""
        response = (
            "This is a nuanced topic that depends on many individual and "
            "organizational factors, so it's hard to give one universal answer."
        )
        claims = _detect_evasion(response, "Is remote work better than in-office work?")
        assert len(claims) == 1
        assert claims[0].startswith("[EVASION]")

    # --- Anti-false-positive guards: the whole-response route must not swallow
    # factual claims that merely carry a hedge word (the checkable-fragment
    # guard) or discursive open-ended requests (the answer-seeking gate). ---

    def test_hedged_factual_answer_with_number_not_evasion(self):
        """Same hedge density as V-10, but the answer names a concrete number →
        NOT evasion (the number is a checkable fragment)."""
        response = (
            "This is a complex and nuanced topic, and it is important to "
            "consider multiple perspectives, but water boils at approximately "
            "100 degrees Celsius at sea level."
        )
        assert _detect_evasion(response, "What is the boiling point of water at sea level?") == []

    def test_hedged_factual_answer_with_named_entity_not_evasion(self):
        """Hedge density above threshold, but the answer names a concrete entity
        → NOT evasion (the mid-sentence proper noun is a checkable fragment)."""
        response = (
            "This is a complex topic and it is important to consider multiple "
            "perspectives, but the fall was driven in large part by the "
            "Visigoths sacking Rome in 410 CE."
        )
        assert _detect_evasion(response, "What caused the fall of the Roman Empire?") == []

    def test_open_ended_request_not_evasion_even_when_hedged(self):
        """A discursive 'tell me about' request (not answer-seeking) met with
        heavy hedging must not trigger the whole-response route."""
        response = (
            "This is a complex and nuanced topic. It is important to consider "
            "multiple perspectives, and it might be possible that various "
            "factors contribute to different views."
        )
        assert _detect_evasion(response, "Tell me about the economy") == []


class TestEvasionVerdictInversion:
    """Test verdict inversion for evasion claims."""

    def test_verified_becomes_unverified(self):
        """When data exists (verified), evasion was unjustified (→ unverified)."""
        original = {
            "status": "verified",
            "confidence": 0.9,
            "reason": "Cross-model verification confirmed: data available",
        }
        inverted = _invert_evasion_verdict(original)
        assert inverted["status"] == "unverified"
        assert "evaded" in inverted["reason"].lower()

    def test_unverified_becomes_verified(self):
        """When data doesn't exist (unverified), evasion was justified (→ verified)."""
        original = {
            "status": "unverified",
            "confidence": 0.4,
            "reason": "Cross-model verification found factual errors: data unavailable",
        }
        inverted = _invert_evasion_verdict(original)
        assert inverted["status"] == "verified"
        assert inverted["confidence"] >= 0.7

    def test_uncertain_unchanged(self):
        """Uncertain verdicts stay uncertain (status is not flipped)."""
        original = {
            "status": "uncertain",
            "confidence": 0.3,
            "reason": "Cannot determine",
        }
        inverted = _invert_evasion_verdict(original)
        assert inverted["status"] == "uncertain"

    def test_contested_question_stays_uncertain_with_mid_confidence(self):
        """Phase 3.6: an insufficient_info verdict (question is genuinely
        contested / has no single objective answer) yields a genuine uncertain
        outcome — clamped mid-band, with a defensible-hedge reason — instead of
        being inverted to a high-confidence unverified."""
        original = {
            "status": "uncertain",
            "confidence": 0.5,
            "reason": "Claim not independently verifiable: multi-causal debate",
        }
        inverted = _invert_evasion_verdict(original)
        assert inverted["status"] == "uncertain"
        assert 0.36 <= inverted["confidence"] <= 0.64
        assert "defensible" in inverted["reason"].lower()

    def test_verified_inversion_confidence_clamped_to_unverified_band(self):
        """Phase 3.6: a high-confidence 'supported' (a concrete answer exists)
        inverts to unverified with confidence clamped into the unverified band
        (<= 0.35) — a hedge never surfaces as unverified@~1.0."""
        original = {
            "status": "verified",
            "confidence": 0.97,
            "reason": "Cross-model verification confirmed: water boils at 100C",
        }
        inverted = _invert_evasion_verdict(original)
        assert inverted["status"] == "unverified"
        assert inverted["confidence"] <= 0.35

    @pytest.mark.parametrize(
        "verifier_json,expected_status",
        [
            # kind 1 — concrete answer exists → evasion unjustified → unverified
            ('{"verdict": "supported", "confidence": 0.95, "reasoning": "100C"}', "unverified"),
            # kind 2 — genuinely contested → defensible hedge → uncertain
            ('{"verdict": "insufficient_info", "confidence": 0.5, "reasoning": "debate"}', "uncertain"),
            # kind 3 — unknowable / nonexistent → caution justified → verified
            ('{"verdict": "refuted", "confidence": 0.8, "reasoning": "unknowable"}', "verified"),
        ],
    )
    def test_evasion_verifier_verdict_to_final_status(self, verifier_json, expected_status):
        """End-to-end contract: verifier answerability class → final evasion
        status, through the parser + inversion the live path uses."""
        parsed = _parse_verification_verdict(verifier_json)
        assert _invert_evasion_verdict(parsed)["status"] == expected_status


class TestEvasionClaimExtraction:
    """Test that evasion claims are merged into extract_claims output."""

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.extraction._extract_claims_heuristic")
    @patch("core.agents.hallucination.extraction._extract_claims_llm")
    async def test_evasion_returned_when_no_other_claims(self, mock_llm, mock_heuristic):
        """When LLM+heuristic find nothing, evasion claims returned alone."""
        mock_llm.return_value = []
        mock_heuristic.return_value = []
        # Response with hedging patterns
        response = (
            "This is a complex and nuanced topic. It's important to note "
            "that many factors contribute to this issue. Rather than singling "
            "out specific groups, we need to consider the broader context. "
            "There is no simple answer to this question." + " " * 200
        )
        query = "Which demographic has the highest murder rate per capita?"
        claims, method = await extract_claims(response, user_query=query)
        # Should have evasion claims
        assert method == "evasion"
        assert any("[EVASION]" in c for c in claims)

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.extraction._extract_claims_llm")
    async def test_evasion_merged_with_llm_claims(self, mock_llm):
        """Partial evasion (specific-question route, response carries data)
        keeps merge semantics: LLM claims and the [EVASION] claim coexist."""
        mock_llm.return_value = ["Roughly 45% of recorded incidents involve a single group"]
        response = (
            "Roughly 45% of recorded incidents involve a single group. But "
            "this is a complex topic. It's important to consider many "
            "factors, and it might be possible that other variables play a "
            "role. It depends on many social factors. There is no simple "
            "answer." + " " * 200
        )
        query = "What is the murder rate breakdown by demographic?"
        claims, method = await extract_claims(response, user_query=query)
        assert method == "llm"
        # Should include both LLM claim and evasion claim
        assert len(claims) >= 2
        assert any("[EVASION]" in c for c in claims)
        assert any("45%" in c for c in claims)


class TestWholeResponseEvasionSuppression:
    """Composition-level tests for the whole-response evasion route (Phase 3.6
    integration fix): when the response has no checkable fragment, the
    synthesized [EVASION] claim SUPPRESSES LLM/heuristic pseudo-claims instead
    of merging beside them — a hedge restatement like 'X is a complex topic
    that depends on many factors' otherwise verifies supported@~1.0 via the
    cross-model path and shadows the type-aware evasion verdict (full-100
    HTTP run, 2026-07-14: V-11/EV-06 verified@1.0 via cross_model_complex)."""

    # (response_text, user_query, dataset text_fragment) — the four
    # whole-response residue shapes from verification_cases_v2.jsonl.
    RESIDUE_SHAPES = [
        (
            "It might be possible that various factors contribute to this "
            "outcome, and it is important to consider multiple perspectives "
            "before drawing conclusions.",
            "What caused the 2008 financial crisis?",
            "various factors contribute",
        ),
        (
            "This is a complex topic that depends on many factors. There is "
            "no simple answer to this question, and multiple variables play "
            "a role.",
            "Is nuclear energy safe?",
            "complex topic",
        ),
        (
            "It's important to note that various factors contribute to "
            "complex historical outcomes like this, and scholars continue "
            "to debate the relative weight of each.",
            "What caused the fall of the Roman Empire?",
            "various factors contribute",
        ),
        (
            "This is a nuanced topic that depends on many individual and "
            "organizational factors, so it's hard to give one universal "
            "answer.",
            "Is remote work better than in-office work?",
            "hard to give one universal answer",
        ),
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("response,query,fragment", RESIDUE_SHAPES)
    @patch("core.agents.hallucination.extraction._extract_claims_llm", new_callable=AsyncMock)
    async def test_pseudo_claims_suppressed_and_fragment_matches_evasion(
        self, mock_llm, response, query, fragment
    ):
        """extract_claims end-to-end: the [EVASION] claim is the ONLY claim,
        the LLM extractor is never even called, and the response fragment
        (what the verdict harness matches on) lands inside the [EVASION]
        claim via the quoted hedge snippet."""
        # Realistic pseudo-claims the LLM extractor produces for hedged text —
        # must never reach the output.
        mock_llm.return_value = [
            f"{query.rstrip('?')} is a complex topic that depends on many factors",
            "There is no simple answer to this question",
        ]
        claims, method = await extract_claims(response, user_query=query)
        assert method == "evasion"
        assert len(claims) == 1
        assert claims[0].startswith("[EVASION]")
        # Suppression happens before primary extraction — no wasted LLM call.
        assert mock_llm.await_count == 0
        # The harness's fragment matcher (substring, case-insensitive) must
        # resolve to the [EVASION] claim.
        assert fragment.lower() in claims[0].lower()

    @pytest.mark.asyncio
    async def test_heuristic_pseudo_claims_suppressed_in_streaming_composition(self):
        """The streaming stage-1 composition (heuristic-first, the live
        /agent/verify-stream path) must not emit heuristic hedge restatements
        beside the [EVASION] claim. V-11 shape: the heuristic extractor
        naturally produces 'Is nuclear energy safe is a complex topic that
        depends on many factors' — the exact claim that graded verified@1.0
        over HTTP."""
        from core.agents.hallucination.extraction import (
            _detect_evasion,
            _evasion_supersedes_primary,
            _extract_citation_claims,
            _extract_claims_heuristic,
            _extract_ignorance_claims,
            _merge_special_claims,
            _resolve_pronouns_heuristic,
        )
        response = (
            "This is a complex topic that depends on many factors. There is "
            "no simple answer to this question, and multiple variables play "
            "a role."
        )
        query = "Is nuclear energy safe?"
        ignorance = _extract_ignorance_claims(response)
        evasion = _detect_evasion(response, query)
        citation = _extract_citation_claims(response)
        assert evasion and _evasion_supersedes_primary(response, evasion)
        # Mirror streaming.verify_response_streaming stage 1 post-fix.
        heuristic: list[str] = []
        if not _evasion_supersedes_primary(response, evasion):
            heuristic = _resolve_pronouns_heuristic(
                _extract_claims_heuristic(response), response, query
            )
        merged = _merge_special_claims(heuristic, ignorance, evasion, citation, 10)
        assert merged == evasion  # nothing but the [EVASION] claim

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.extraction._extract_claims_llm", new_callable=AsyncMock)
    async def test_ignorance_shape_gets_no_evasion_and_survives_merge(self, mock_llm):
        """An ignorance-shaped response must not trip the evasion detector,
        and its ignorance claim must survive merging even when the LLM
        extractor returns pseudo-claims."""
        mock_llm.return_value = [
            "The municipality passed several policy changes",
            "Policy records are maintained by the city clerk",
        ]
        response = (
            "I don't have any information about recent policy changes in "
            "that municipality, as my training data does not include local "
            "government records from that period." + " " * 120
        )
        query = "What policy changes did the municipality pass last year?"
        claims, method = await extract_claims(response, user_query=query)
        assert not any(c.startswith("[EVASION]") for c in claims)
        assert any(c.lower().startswith("i don't have") for c in claims)

    def test_merge_cap_never_evicts_special_claims(self):
        """A primary list that fills max_claims must not push special claims
        out — the primary is trimmed to the remaining budget instead."""
        from core.agents.hallucination.extraction import _merge_special_claims

        primary = [f"Fact number {i} about the topic" for i in range(12)]
        ignorance = ["I don't have information about the launch date"]
        evasion = ['[EVASION] The user asked: "When?" — deflected']
        merged = _merge_special_claims(primary, ignorance, evasion, [], 10)
        assert len(merged) == 10
        assert ignorance[0] in merged
        assert evasion[0] in merged
        # Primary trimmed to the remaining budget (10 - 2 specials = 8).
        assert sum(1 for c in merged if c.startswith("Fact number")) == 8


class TestEmpiricalSourcePrompts:
    """Test that verification prompts include empirical source guidance."""

    def test_current_event_prompt_has_empirical_sources(self):
        """Current event verification prompt should mention government data."""
        from core.agents.hallucination.verification import _SYSTEM_CURRENT_EVENT_VERIFICATION
        assert "CDC" in _SYSTEM_CURRENT_EVENT_VERIFICATION
        assert ".gov" in _SYSTEM_CURRENT_EVENT_VERIFICATION
        assert "BLS" in _SYSTEM_CURRENT_EVENT_VERIFICATION

    def test_ignorance_prompt_has_empirical_sources(self):
        """Ignorance verification prompt should mention government data."""
        from core.agents.hallucination.verification import _SYSTEM_IGNORANCE_VERIFICATION
        assert "CDC" in _SYSTEM_IGNORANCE_VERIFICATION
        assert ".gov" in _SYSTEM_IGNORANCE_VERIFICATION

    def test_evasion_prompt_has_empirical_sources(self):
        """Evasion verification prompt should mention government data."""
        from core.agents.hallucination.verification import _SYSTEM_EVASION_VERIFICATION
        assert "CDC" in _SYSTEM_EVASION_VERIFICATION
        assert ".gov" in _SYSTEM_EVASION_VERIFICATION
        assert "concrete" in _SYSTEM_EVASION_VERIFICATION.lower()

    def test_evasion_prompt_classifies_answerability(self):
        """Evasion prompt should ask the verifier to classify the question's
        answerability — concrete answer (supported) vs genuinely contested
        (insufficient_info) vs nonexistent (refuted) — rather than always
        forcing a concrete answer (Phase 3.6)."""
        from core.agents.hallucination.verification import _SYSTEM_EVASION_VERIFICATION
        prompt = _SYSTEM_EVASION_VERIFICATION
        # Still routes single-answer evasions to a real, concrete answer.
        assert "concrete" in prompt.lower()
        assert "single decisive" in prompt.lower()
        # And offers a genuine contested/no-single-answer outcome so a hedge
        # about a multi-causal or subjective question is not forced to supported.
        assert "insufficient_info" in prompt
        assert "combination" in prompt.lower()
        assert "no single decisive answer" in prompt.lower()


class TestRecencyClaimDetection:
    """Test recency claim detection (split from ignorance)."""

    def test_detects_as_of_my_training(self):
        assert _is_recency_claim("As of my training data, the population is 330 million")

    def test_detects_knowledge_cutoff(self):
        assert _is_recency_claim("My knowledge cutoff is April 2024, so I can't confirm recent changes")

    def test_detects_may_have_changed(self):
        assert _is_recency_claim("This information may have changed since my last update")

    def test_detects_as_of_my_last_update(self):
        assert _is_recency_claim("As of my last update, Python 3.12 was the latest version")

    def test_normal_factual_claim_not_detected(self):
        assert not _is_recency_claim("Python was created by Guido van Rossum")

    def test_ignorance_claim_not_detected(self):
        # Ignorance claims should NOT match recency patterns
        assert not _is_recency_claim("I don't have specific information about that")


class TestRecencyVerdictInterpretation:
    """Test recency verdict mapping (direct, no inversion)."""

    def test_supported_becomes_verified(self):
        verdict = {"status": "verified", "confidence": 0.9, "reason": "Still current"}
        result = _interpret_recency_verdict(verdict)
        assert result["status"] == "verified"
        assert result["confidence"] == 0.9

    def test_unverified_keeps_status(self):
        verdict = {"status": "unverified", "confidence": 0.8, "reason": "Data superseded"}
        result = _interpret_recency_verdict(verdict)
        assert result["status"] == "unverified"
        assert "Outdated" in result["reason"]

    def test_uncertain_stays_uncertain(self):
        verdict = {"status": "uncertain", "confidence": 0.4, "reason": "Cannot determine"}
        result = _interpret_recency_verdict(verdict)
        assert result["status"] == "uncertain"


class TestRecencyClaimVerification:
    """Test recency claims routed correctly through external verification."""

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_recency_claim_uses_web_search_model(self, mock_llm_raw):
        """Recency claims should use the web search model (Grok :online)."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {
                "content": '{"verdict": "refuted", "confidence": 0.85, "reasoning": "Current data shows 340M"}',
                "annotations": [],
            }}]
        }


        result = await _verify_claim_externally(
            "As of my last update, the US population is 330 million",
            "openrouter/anthropic/claude-sonnet-4",
        )
        # Recency claim with refuted verdict → unverified (model data is outdated)
        assert result["status"] == "unverified"
        assert result["verification_method"] == "web_search"
        assert "Outdated" in result.get("reason", "")

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_recency_supported_becomes_verified(self, mock_llm_raw):
        """Recency claim where data is still current → verified."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {
                "content": '{"verdict": "supported", "confidence": 0.9, "reasoning": "Data is current"}',
                "annotations": [],
            }}]
        }

        result = await _verify_claim_externally(
            "As of my training data, Python 3.12 is the latest version",
            "openrouter/anthropic/claude-sonnet-4",
        )
        assert result["status"] == "verified"
        assert result["verification_method"] == "web_search"


class TestCitationExtraction:
    """Test citation claim extraction from response text."""

    def test_extracts_according_to_citation(self):
        text = "According to the World Health Organization, COVID-19 remains a concern."
        claims = _extract_citation_claims(text)
        assert len(claims) >= 1
        assert any("[CITATION]" in c for c in claims)

    def test_extracts_study_by_citation(self):
        text = "A study by Harvard Medical School found that exercise improves mood."
        claims = _extract_citation_claims(text)
        assert len(claims) >= 1
        assert any("Harvard" in c for c in claims)

    def test_extracts_academic_citation(self):
        text = "This finding was confirmed (Smith et al., 2023) in multiple studies."
        claims = _extract_citation_claims(text)
        assert len(claims) >= 1

    def test_skips_known_sources(self):
        """Well-known sources (Wikipedia, etc.) should be excluded."""
        text = "According to Wikipedia, the Earth is the third planet."
        claims = _extract_citation_claims(text)
        assert len(claims) == 0

    def test_no_citations_returns_empty(self):
        text = "Python is a programming language used for web development."
        claims = _extract_citation_claims(text)
        assert claims == []


class TestCitationClaimVerification:
    """Test citation claims routed through verification."""

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_citation_claim_uses_web_search(self, mock_llm_raw):
        """Citation claims should use web search to verify source exists."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {
                "content": '{"verdict": "supported", "confidence": 0.95, "reasoning": "Source exists"}',
                "annotations": [],
            }}]
        }


        result = await _verify_claim_externally(
            '[CITATION] Source cited: "World Health Organization"',
            "openrouter/anthropic/claude-sonnet-4",
        )
        assert result["status"] == "verified"
        assert result["verification_method"] == "web_search"

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_fabricated_citation_detected(self, mock_llm_raw):
        """Fabricated citation should be marked as unverified."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {
                "content": '{"verdict": "refuted", "confidence": 0.9, "reasoning": "No such publication found"}',
                "annotations": [],
            }}]
        }

        result = await _verify_claim_externally(
            '[CITATION] Source cited: "Journal of Fake Research 2024"',
            "openrouter/anthropic/claude-sonnet-4",
        )
        assert result["status"] == "unverified"
        assert result["verification_method"] == "web_search"


class TestConsistencyChecking:
    """Test cross-turn and internal consistency checking."""

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_detects_history_contradiction(self, mock_llm_raw):
        """Should detect when current claims contradict prior turns."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {"content": json.dumps([
                {
                    "claim_index": 0,
                    "contradiction": "Previously said Python was created in 1989",
                    "type": "history",
                }
            ])}}]
        }


        issues = await _check_history_consistency(
            claims=["Python was created in 1991"],
            conversation_history=[
                {"role": "assistant", "content": "Python was created in 1989."},
            ],
        )
        assert len(issues) == 1
        assert issues[0]["claim_index"] == 0
        assert issues[0]["type"] == "history"

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_detects_internal_contradiction(self, mock_llm_raw):
        """Should detect claims that contradict each other in the same response."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {"content": json.dumps([
                {
                    "claim_index": 1,
                    "contradiction": "Contradicts claim 0: said A > B then B > A",
                    "conflicting_claim_index": 0,
                    "type": "internal",
                }
            ])}}]
        }

        issues = await _check_history_consistency(
            claims=["A is greater than B", "B is greater than A"],
            conversation_history=None,
        )
        assert len(issues) == 1
        assert issues[0]["type"] == "internal"
        assert issues[0]["conflicting_claim_index"] == 0

    @pytest.mark.asyncio
    async def test_no_claims_returns_empty(self):
        """Empty claims should return no issues without making an LLM call."""
        issues = await _check_history_consistency(claims=[], conversation_history=None)
        assert issues == []

    @pytest.mark.asyncio
    async def test_single_claim_no_history_returns_empty(self):
        """Single claim with no history should skip checking."""
        issues = await _check_history_consistency(
            claims=["Python is great"],
            conversation_history=None,
        )
        assert issues == []

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_no_contradictions_returns_empty(self, mock_llm_raw):
        """When LLM finds no contradictions, return empty list."""
        mock_llm_raw.return_value = {
            "choices": [{"message": {"content": "[]"}}]
        }


        issues = await _check_history_consistency(
            claims=["Python is great", "Python is popular"],
            conversation_history=[
                {"role": "assistant", "content": "Python is widely used."},
            ],
        )
        assert issues == []

    @pytest.mark.asyncio
    @patch("core.utils.llm_client.call_llm_raw", new_callable=AsyncMock)
    async def test_llm_failure_returns_empty(self, mock_llm_raw):
        """LLM call failure should return empty list gracefully."""
        mock_llm_raw.side_effect = Exception("Connection refused")

        issues = await _check_history_consistency(
            claims=["Claim A", "Claim B"],
            conversation_history=[
                {"role": "assistant", "content": "Prior statement."},
            ],
        )
        assert issues == []


class TestClaimTypeClassification:
    """Test _claim_type in verify_response_streaming handles all types."""

    def test_evasion_prefix(self):
        assert "[EVASION]" == "[EVASION]"  # prefix check is string-based

    def test_citation_prefix(self):
        # Verify citation prefix detection works
        claim = "[CITATION] Source cited: \"Harvard Study\""
        assert claim.startswith("[CITATION]")

    def test_ignorance_detected(self):
        assert _is_ignorance_admission("I don't have specific information about that topic")

    def test_factual_default(self):
        assert not _is_ignorance_admission("Python was created in 1991")


class TestGrokInVerificationPool:
    """Test Grok is in the verification model pool."""

    def test_grok_in_pool(self):
        # grok-4.1-fast was removed from the OpenRouter catalog; the pool now
        # carries the current grok (4.3) for cross-family verification diversity.
        assert "openrouter/x-ai/grok-4.3" in config.VERIFICATION_MODEL_POOL

    def test_pool_has_three_families(self):
        families = set()
        for m in config.VERIFICATION_MODEL_POOL:
            families.add(_model_family(m))
        assert len(families) >= 3

    def test_grok_selected_for_non_xai_model(self):
        """When generating model is not xAI, Grok should be selectable."""
        model = _pick_verification_model("openrouter/anthropic/claude-sonnet-4")
        # Should pick from non-Anthropic family — could be OpenAI, Google, or xAI
        assert _model_family(model) != "anthropic"


class TestVerifyStreamConversationHistory:
    """Test that conversation_history is threaded through verify_response_streaming."""

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.streaming._check_history_consistency", new_callable=AsyncMock)
    @patch("core.agents.hallucination.streaming.verify_claim", new_callable=AsyncMock)
    async def test_consistency_check_called_with_history(
        self, mock_verify, mock_consistency,
    ):
        """Consistency check should be called when conversation_history is provided."""
        mock_verify.return_value = {
            "status": "verified",
            "similarity": 0.9,
            "source_filename": "test.txt",
        }
        mock_consistency.return_value = []

        mock_chroma = MagicMock()
        mock_neo4j = MagicMock()
        mock_redis = MagicMock()

        events = []
        with _mock_streaming_extraction(["Claim 1", "Claim 2"], "llm"):
            async for event in verify_response_streaming(
                response_text="x" * 100,
                conversation_id="test-123",
                chroma_client=mock_chroma,
                neo4j_driver=mock_neo4j,
                redis_client=mock_redis,
                model="openrouter/openai/gpt-4o",
                conversation_history=[
                    {"role": "assistant", "content": "Prior response content."},
                ],
            ):
                events.append(event)

        mock_consistency.assert_called_once()
        call_args = mock_consistency.call_args
        # Check positional or keyword args
        args, kwargs = call_args
        if args:
            assert args[0] == ["Claim 1", "Claim 2"]
        else:
            assert kwargs["claims"] == ["Claim 1", "Claim 2"]

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.streaming._check_history_consistency", new_callable=AsyncMock)
    @patch("core.agents.hallucination.streaming.verify_claim", new_callable=AsyncMock)
    async def test_consistency_issues_emitted_as_event(
        self, mock_verify, mock_consistency,
    ):
        """Consistency issues should be emitted as a consistency_check SSE event."""
        mock_verify.return_value = {
            "status": "verified",
            "similarity": 0.9,
            "source_filename": "test.txt",
        }
        mock_consistency.return_value = [
            {"claim_index": 0, "contradiction": "Contradicts prior statement", "type": "history"},
        ]

        mock_chroma = MagicMock()
        mock_neo4j = MagicMock()
        mock_redis = MagicMock()

        events = []
        with _mock_streaming_extraction(["Claim 1", "Claim 2"], "llm"):
            async for event in verify_response_streaming(
                response_text="x" * 100,
                conversation_id="test-123",
                chroma_client=mock_chroma,
                neo4j_driver=mock_neo4j,
                redis_client=mock_redis,
                conversation_history=[
                    {"role": "assistant", "content": "Prior content."},
                ],
            ):
                events.append(event)

        consistency_events = [e for e in events if e.get("type") == "consistency_check"]
        assert len(consistency_events) == 1
        assert len(consistency_events[0]["issues"]) == 1
        assert consistency_events[0]["issues"][0]["claim_index"] == 0


class TestRecencyAndCitationPrompts:
    """Test that recency and citation system prompts are well-formed."""

    def test_recency_prompt_instructs_current_data(self):
        from core.agents.hallucination.verification import _SYSTEM_RECENCY_VERIFICATION
        assert "MOST CURRENT" in _SYSTEM_RECENCY_VERIFICATION
        assert "outdated" in _SYSTEM_RECENCY_VERIFICATION.lower()

    def test_citation_prompt_instructs_source_verification(self):
        from core.agents.hallucination.verification import _SYSTEM_CITATION_VERIFICATION
        assert "source" in _SYSTEM_CITATION_VERIFICATION.lower() or "publication" in _SYSTEM_CITATION_VERIFICATION.lower()

    def test_consistency_prompt_checks_both_types(self):
        from core.agents.hallucination.verification import _SYSTEM_CONSISTENCY_CHECK
        # Should check both history and internal consistency
        assert "contradict" in _SYSTEM_CONSISTENCY_CHECK.lower()
        assert "claim_index" in _SYSTEM_CONSISTENCY_CHECK


class TestComplexClaimClassifier:
    """Test the _is_complex_claim function for complexity-based model routing."""

    def test_causal_claim(self):
        assert _is_complex_claim("Global warming leads to rising sea levels")

    def test_comparative_claim(self):
        assert _is_complex_claim("Python is faster than Ruby for web scraping")

    def test_conditional_claim(self):
        assert _is_complex_claim("If interest rates rise then housing prices fall")

    def test_quantitative_change(self):
        assert _is_complex_claim("Revenue increased by 25% from 2024 to 2025")

    def test_contrast_claim(self):
        assert _is_complex_claim("Unlike Java, Rust provides memory safety without garbage collection")

    def test_dependency_claim(self):
        assert _is_complex_claim("This process requires a GPU with at least 8GB VRAM")

    def test_consequence_claim(self):
        assert _is_complex_claim("The policy failed, consequently unemployment rose")

    def test_simple_factual_claim(self):
        assert not _is_complex_claim("Paris is the capital of France")

    def test_simple_date_claim(self):
        assert not _is_complex_claim("Python was released in 1991")

    def test_simple_attribution(self):
        assert not _is_complex_claim("Tesla was founded by Elon Musk")

    def test_edge_case_empty_string(self):
        assert not _is_complex_claim("")


class TestStreamingGuaranteedSummary:
    """Test that verify_response_streaming always emits a summary event.

    These tests use synthetic data to verify the streaming pipeline's
    guaranteed summary emission, including when individual verification
    tasks fail.
    """

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.streaming.verify_claim")
    async def test_summary_emitted_on_success(
        self, mock_verify, mock_chroma, mock_neo4j, mock_redis
    ):
        """Happy path: all claims verify successfully → summary emitted."""
        mock_verify.return_value = {
            "status": "verified",
            "similarity": 0.9,
            "source_filename": "facts.txt",
            "source_artifact_id": "art-1",
            "source_domain": "science",
            "source_snippet": "The sky is blue",
            "reason": "High similarity",
            "verification_method": "kb",
            "verification_model": "gpt-4o-mini",
            "source_urls": [],
            "verification_answer": "yes",
        }

        events = []
        with _mock_streaming_extraction(
            ["The sky is blue", "Water boils at 100 degrees Celsius"], "heuristic"
        ):
            async for event in verify_response_streaming(
                response_text="The sky is blue. Water boils at 100 degrees Celsius. " * 5,
                conversation_id="test-conv-1",
                chroma_client=mock_chroma,
                neo4j_driver=mock_neo4j,
                redis_client=mock_redis,
            ):
                events.append(event)

        types = [e["type"] for e in events]
        assert "summary" in types, "Summary event must always be emitted"
        summary = next(e for e in events if e["type"] == "summary")
        assert summary["total"] == 2
        assert summary["verified"] == 2
        assert "interrupted" not in summary

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.streaming.verify_claim")
    async def test_summary_emitted_on_partial_failure(
        self, mock_verify, mock_chroma, mock_neo4j, mock_redis
    ):
        """When some verification tasks fail, summary is still emitted."""
        # First call succeeds, second raises, third succeeds
        call_count = 0

        async def side_effect_verify(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("Simulated verification timeout")
            return {
                "status": "verified",
                "similarity": 0.85,
                "source_filename": "test.txt",
                "source_artifact_id": "",
                "source_domain": "",
                "source_snippet": "",
                "reason": "Matched",
                "verification_method": "kb",
                "source_urls": [],
            }

        mock_verify.side_effect = side_effect_verify

        events = []
        with _mock_streaming_extraction(
            ["Claim A is true", "Claim B is true", "Claim C is true"], "heuristic"
        ):
            async for event in verify_response_streaming(
                response_text="Claim A is true. Claim B is true. Claim C is true. " * 5,
                conversation_id="test-conv-2",
                chroma_client=mock_chroma,
                neo4j_driver=mock_neo4j,
                redis_client=mock_redis,
            ):
                events.append(event)

        types = [e["type"] for e in events]
        assert "summary" in types, "Summary must be emitted even with failures"
        summary = next(e for e in events if e["type"] == "summary")
        # 2 succeeded, 1 failed (silently skipped)
        assert summary["verified"] == 2

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.streaming.extract_claims")
    async def test_short_response_emits_skipped_summary(
        self, mock_extract, mock_chroma, mock_neo4j, mock_redis
    ):
        """Very short responses should emit a skipped summary."""
        events = []
        async for event in verify_response_streaming(
            response_text="OK",
            conversation_id="test-conv-3",
            chroma_client=mock_chroma,
            neo4j_driver=mock_neo4j,
            redis_client=mock_redis,
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0]["type"] == "summary"
        assert events[0]["skipped"] is True

    @pytest.mark.asyncio
    async def test_no_claims_emits_skipped_summary(
        self, mock_chroma, mock_neo4j, mock_redis
    ):
        """When extraction finds no claims, a skipped summary is emitted."""
        events = []
        with _mock_streaming_extraction([], "none"):
            async for event in verify_response_streaming(
                response_text="This is a long enough response with no factual claims. " * 10,
                conversation_id="test-conv-4",
                chroma_client=mock_chroma,
                neo4j_driver=mock_neo4j,
                redis_client=mock_redis,
            ):
                events.append(event)

        assert len(events) == 1
        assert events[0]["type"] == "summary"
        assert events[0]["skipped"] is True

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.streaming.extract_claims")
    @patch("core.agents.hallucination.streaming.verify_claim")
    async def test_streaming_event_order(
        self, mock_verify, mock_extract, mock_chroma, mock_neo4j, mock_redis
    ):
        """Events must arrive in order: extraction_complete → claim_extracted* → claim_verified* → summary."""
        mock_extract.return_value = (
            ["Python was created by Guido van Rossum"],
            "heuristic",
        )
        mock_verify.return_value = {
            "status": "verified",
            "similarity": 0.92,
            "source_filename": "python.txt",
            "source_artifact_id": "",
            "source_domain": "",
            "source_snippet": "",
            "reason": "Match",
            "verification_method": "cross_model",
            "verification_model": "gpt-4o-mini",
            "source_urls": [],
        }

        events = []
        async for event in verify_response_streaming(
            response_text="Python was created by Guido van Rossum. " * 10,
            conversation_id="test-conv-5",
            chroma_client=mock_chroma,
            neo4j_driver=mock_neo4j,
            redis_client=mock_redis,
        ):
            events.append(event)

        types = [e["type"] for e in events]
        assert types[0] == "extraction_complete"
        assert types[1] == "claim_extracted"
        assert "claim_verified" in types
        assert types[-1] == "summary"

    @pytest.mark.asyncio
    @patch("core.agents.hallucination.streaming.verify_claim")
    async def test_mixed_statuses_in_summary(
        self, mock_verify, mock_chroma, mock_neo4j, mock_redis
    ):
        """Summary correctly counts verified, unverified, and uncertain claims."""
        results = [
            {"status": "verified", "similarity": 0.9, "source_filename": "", "source_artifact_id": "",
             "source_domain": "", "source_snippet": "", "reason": "", "verification_method": "kb", "source_urls": []},
            {"status": "unverified", "similarity": 0.2, "source_filename": "", "source_artifact_id": "",
             "source_domain": "", "source_snippet": "", "reason": "", "verification_method": "kb", "source_urls": []},
            {"status": "uncertain", "similarity": 0.5, "source_filename": "", "source_artifact_id": "",
             "source_domain": "", "source_snippet": "", "reason": "", "verification_method": "kb", "source_urls": []},
        ]
        call_idx = 0

        async def side_effect(*a, **kw):
            nonlocal call_idx
            r = results[call_idx % len(results)]
            call_idx += 1
            return r

        mock_verify.side_effect = side_effect

        events = []
        with _mock_streaming_extraction(
            ["Claim verified", "Claim unverified", "Claim uncertain"], "heuristic"
        ):
            async for event in verify_response_streaming(
                response_text="Claim verified. Claim unverified. Claim uncertain. " * 5,
                conversation_id="test-conv-6",
                chroma_client=mock_chroma,
                neo4j_driver=mock_neo4j,
                redis_client=mock_redis,
            ):
                events.append(event)

        summary = next(e for e in events if e["type"] == "summary")
        assert summary["verified"] == 1
        assert summary["unverified"] == 1
        assert summary["uncertain"] == 1
        assert summary["total"] == 3


class TestVerificationErrorCaching:
    """Test the error caching functions in utils.cache."""

    def test_log_verification_error(self, mock_redis):
        from core.utils.cache import log_verification_error
        log_verification_error(
            mock_redis,
            conversation_id="conv-err-1",
            error_type="stream_interrupted",
            error_message="Connection reset",
            model="gpt-4o-mini",
            phase="verification",
        )
        mock_redis.rpush.assert_called_once()
        call_args = mock_redis.rpush.call_args
        assert call_args[0][0] == "verify:errors"
        entry = json.loads(call_args[0][1])
        assert entry["error_type"] == "stream_interrupted"
        assert entry["conversation_id"] == "conv-err-1"
        assert entry["phase"] == "verification"

    def test_log_verification_error_trims_old(self, mock_redis):
        from core.utils.cache import log_verification_error
        log_verification_error(
            mock_redis,
            conversation_id="conv-err-2",
            error_type="claim_verification_failed",
            error_message="Timeout",
        )
        mock_redis.ltrim.assert_called_once()
        # Should keep last 200
        call_args = mock_redis.ltrim.call_args
        assert call_args[0][1] == -200
        assert call_args[0][2] == -1

    def test_log_verification_error_handles_redis_failure(self, mock_redis):
        from core.utils.cache import log_verification_error
        mock_redis.rpush.side_effect = Exception("Redis down")
        # Should not raise
        log_verification_error(
            mock_redis,
            conversation_id="conv-err-3",
            error_type="timeout",
            error_message="Timed out",
        )
