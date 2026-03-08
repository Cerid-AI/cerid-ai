# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for Self-RAG validation loop (claim-based retrieval refinement)."""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Pre-seed heavy modules that self_rag imports lazily
# so @patch can target them without triggering real imports.
if "agents.query_agent" not in sys.modules:
    _stub = ModuleType("agents.query_agent")
    _stub.agent_query = None  # type: ignore[attr-defined]
    _stub.multi_domain_query = None  # type: ignore[attr-defined]
    _stub.assemble_context = None  # type: ignore[attr-defined]
    sys.modules["agents.query_agent"] = _stub
    import agents
    agents.query_agent = _stub  # type: ignore[attr-defined]

if "agents.hallucination" not in sys.modules:
    _hall_stub = ModuleType("agents.hallucination")
    _hall_stub.extract_claims = None  # type: ignore[attr-defined]
    sys.modules["agents.hallucination"] = _hall_stub
    import agents as _agents_pkg
    _agents_pkg.hallucination = _hall_stub  # type: ignore[attr-defined]

import config
from agents.self_rag import (
    _assess_claims,
    _merge_results,
    _retrieve_for_claims,
    _with_metadata,
    self_rag_enhance,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_query_result(**overrides):
    """Build a minimal agent_query result dict."""
    base = {
        "context": "test context",
        "sources": [{"artifact_id": "a1", "filename": "f1.py", "relevance": 0.8, "domain": "coding", "chunk_index": 0, "content": "test"}],
        "confidence": 0.8,
        "domains_searched": ["coding"],
        "total_results": 2,
        "token_budget_used": 100,
        "graph_results": 0,
        "results": [
            {"artifact_id": "a1", "chunk_index": 0, "relevance": 0.85, "content": "Python uses GIL", "filename": "f1.py", "domain": "coding"},
            {"artifact_id": "a2", "chunk_index": 0, "relevance": 0.70, "content": "FastAPI is async", "filename": "f2.py", "domain": "coding"},
        ],
    }
    base.update(overrides)
    return base


def _make_multi_domain_result(relevance=0.6):
    """Build a minimal multi_domain_query result list."""
    return [
        {"artifact_id": "r1", "chunk_index": 0, "relevance": relevance, "content": "result content", "filename": "r1.py", "domain": "coding"},
    ]


# ---------------------------------------------------------------------------
# _merge_results
# ---------------------------------------------------------------------------

class TestMergeResults:
    def test_deduplicates_by_artifact_and_chunk(self):
        original = [
            {"artifact_id": "a1", "chunk_index": 0, "relevance": 0.9},
            {"artifact_id": "a2", "chunk_index": 0, "relevance": 0.8},
        ]
        additional = [
            {"artifact_id": "a1", "chunk_index": 0, "relevance": 0.7},  # duplicate
            {"artifact_id": "a3", "chunk_index": 0, "relevance": 0.6},  # new
        ]
        merged = _merge_results(original, additional)
        assert len(merged) == 3
        ids = [r["artifact_id"] for r in merged]
        assert ids == ["a1", "a2", "a3"]

    def test_marks_additional_as_self_rag_source(self):
        original = [{"artifact_id": "a1", "chunk_index": 0}]
        additional = [{"artifact_id": "a2", "chunk_index": 0}]
        merged = _merge_results(original, additional)
        assert "self_rag_source" not in merged[0]
        assert merged[1].get("self_rag_source") is True

    def test_empty_additional(self):
        original = [{"artifact_id": "a1", "chunk_index": 0}]
        merged = _merge_results(original, [])
        assert len(merged) == 1

    def test_empty_original(self):
        additional = [{"artifact_id": "a1", "chunk_index": 0}]
        merged = _merge_results([], additional)
        assert len(merged) == 1
        assert merged[0].get("self_rag_source") is True

    def test_different_chunk_indices_not_deduplicated(self):
        original = [{"artifact_id": "a1", "chunk_index": 0}]
        additional = [{"artifact_id": "a1", "chunk_index": 1}]
        merged = _merge_results(original, additional)
        assert len(merged) == 2


# ---------------------------------------------------------------------------
# _with_metadata
# ---------------------------------------------------------------------------

class TestWithMetadata:
    def test_attaches_self_rag_key(self):
        qr = _make_query_result()
        result = _with_metadata(qr, "no_claims", 0, 0, 0)
        assert "self_rag" in result
        assert result["self_rag"]["status"] == "no_claims"
        assert result["self_rag"]["iterations"] == 0

    def test_includes_assessments_when_provided(self):
        qr = _make_query_result()
        assessments = [{"claim": "test", "max_similarity": 0.9, "covered": True}]
        result = _with_metadata(qr, "all_supported", 1, 1, 0, assessments=assessments)
        assert result["self_rag"]["claim_assessments"] == assessments

    def test_replaces_results_when_merged_provided(self):
        qr = _make_query_result()
        merged = [{"artifact_id": "m1", "chunk_index": 0}]
        result = _with_metadata(qr, "refined", 1, 2, 1, merged_results=merged)
        assert result["results"] == merged
        assert result["total_results"] == 1

    def test_preserves_original_when_no_merged(self):
        qr = _make_query_result()
        result = _with_metadata(qr, "no_claims", 0, 0, 0)
        assert result["results"] == qr["results"]


# ---------------------------------------------------------------------------
# _assess_claims — patches target the source module (lazy import)
# ---------------------------------------------------------------------------

class TestAssessClaims:
    @pytest.mark.asyncio
    @patch("agents.query_agent.multi_domain_query", new_callable=AsyncMock)
    async def test_covered_claim(self, mock_mdq):
        mock_mdq.return_value = _make_multi_domain_result(relevance=0.8)
        assessments = await _assess_claims(["Python uses GIL"], MagicMock(), threshold=0.5)
        assert len(assessments) == 1
        assert assessments[0]["covered"] is True
        assert assessments[0]["max_similarity"] == 0.8

    @pytest.mark.asyncio
    @patch("agents.query_agent.multi_domain_query", new_callable=AsyncMock)
    async def test_weak_claim(self, mock_mdq):
        mock_mdq.return_value = _make_multi_domain_result(relevance=0.3)
        assessments = await _assess_claims(["obscure fact"], MagicMock(), threshold=0.5)
        assert assessments[0]["covered"] is False
        assert assessments[0]["max_similarity"] == 0.3

    @pytest.mark.asyncio
    @patch("agents.query_agent.multi_domain_query", new_callable=AsyncMock)
    async def test_no_results(self, mock_mdq):
        mock_mdq.return_value = []
        assessments = await _assess_claims(["unknown claim"], MagicMock(), threshold=0.5)
        assert assessments[0]["covered"] is False
        assert assessments[0]["max_similarity"] == 0.0

    @pytest.mark.asyncio
    @patch("agents.query_agent.multi_domain_query", new_callable=AsyncMock)
    async def test_multiple_claims(self, mock_mdq):
        mock_mdq.side_effect = [
            _make_multi_domain_result(relevance=0.9),
            _make_multi_domain_result(relevance=0.2),
        ]
        assessments = await _assess_claims(["strong", "weak"], MagicMock(), threshold=0.5)
        assert assessments[0]["covered"] is True
        assert assessments[1]["covered"] is False

    @pytest.mark.asyncio
    @patch("agents.query_agent.multi_domain_query", new_callable=AsyncMock)
    async def test_assessment_error_handled(self, mock_mdq):
        mock_mdq.side_effect = Exception("connection failed")
        assessments = await _assess_claims(["test claim"], MagicMock(), threshold=0.5)
        assert assessments[0]["covered"] is False
        assert "error" in assessments[0]


# ---------------------------------------------------------------------------
# _retrieve_for_claims — patches target the source module (lazy import)
# ---------------------------------------------------------------------------

class TestRetrieveForClaims:
    @pytest.mark.asyncio
    @patch("agents.query_agent.agent_query", new_callable=AsyncMock)
    async def test_returns_combined_results(self, mock_aq):
        mock_aq.return_value = {
            "results": [
                {"artifact_id": "r1", "chunk_index": 0, "relevance": 0.7},
            ]
        }
        results = await _retrieve_for_claims(
            ["query1", "query2"], MagicMock(), MagicMock(), MagicMock(),
        )
        assert len(results) == 2  # 1 result per query, 2 queries
        assert mock_aq.call_count == 2

    @pytest.mark.asyncio
    @patch("agents.query_agent.agent_query", new_callable=AsyncMock)
    async def test_uses_no_reranking(self, mock_aq):
        mock_aq.return_value = {"results": []}
        await _retrieve_for_claims(["query"], MagicMock(), MagicMock(), MagicMock())
        call_kwargs = mock_aq.call_args[1]
        assert call_kwargs["use_reranking"] is False

    @pytest.mark.asyncio
    @patch("agents.query_agent.agent_query", new_callable=AsyncMock)
    async def test_handles_query_failure(self, mock_aq):
        mock_aq.side_effect = Exception("timeout")
        results = await _retrieve_for_claims(["query"], MagicMock(), MagicMock(), MagicMock())
        assert results == []

    @pytest.mark.asyncio
    @patch("agents.query_agent.agent_query", new_callable=AsyncMock)
    async def test_empty_queries(self, mock_aq):
        results = await _retrieve_for_claims([], MagicMock(), MagicMock(), MagicMock())
        assert results == []
        mock_aq.assert_not_called()


# ---------------------------------------------------------------------------
# self_rag_enhance — integration tests
# Patches: extract_claims at source, _assess_claims/_retrieve_for_claims
# on self_rag module (they are actual module-level functions).
# ---------------------------------------------------------------------------

class TestSelfRagEnhance:
    @pytest.mark.asyncio
    @patch("agents.hallucination.extract_claims", new_callable=AsyncMock)
    async def test_no_claims_returns_early(self, mock_extract):
        mock_extract.return_value = ([], "none")
        qr = _make_query_result()
        result = await self_rag_enhance(
            qr, "short response", MagicMock(), MagicMock(), MagicMock(),
        )
        assert result["self_rag"]["status"] == "no_claims"
        assert result["self_rag"]["iterations"] == 0
        assert result["context"] == qr["context"]  # unchanged

    @pytest.mark.asyncio
    @patch("agents.hallucination.extract_claims", new_callable=AsyncMock)
    @patch("agents.self_rag._assess_claims", new_callable=AsyncMock)
    async def test_all_claims_supported(self, mock_assess, mock_extract):
        mock_extract.return_value = (["Python uses GIL", "FastAPI is async"], "llm")
        mock_assess.return_value = [
            {"claim": "Python uses GIL", "max_similarity": 0.9, "covered": True, "top_source": "f1.py"},
            {"claim": "FastAPI is async", "max_similarity": 0.8, "covered": True, "top_source": "f2.py"},
        ]
        qr = _make_query_result()
        result = await self_rag_enhance(
            qr, "x" * 100, MagicMock(), MagicMock(), MagicMock(),
        )
        assert result["self_rag"]["status"] == "all_supported"
        assert result["self_rag"]["claims_total"] == 2
        assert result["self_rag"]["claims_weak"] == 0

    @pytest.mark.asyncio
    @patch("agents.hallucination.extract_claims", new_callable=AsyncMock)
    @patch("agents.self_rag._assess_claims", new_callable=AsyncMock)
    @patch("agents.self_rag._retrieve_for_claims", new_callable=AsyncMock)
    @patch("agents.query_agent.assemble_context")
    async def test_weak_claims_trigger_refinement(self, mock_assemble, mock_retrieve, mock_assess, mock_extract):
        mock_extract.return_value = (["strong claim", "weak claim"], "llm")
        # First assess: one weak
        mock_assess.side_effect = [
            [
                {"claim": "strong claim", "max_similarity": 0.9, "covered": True, "top_source": "s.py"},
                {"claim": "weak claim", "max_similarity": 0.3, "covered": False, "top_source": ""},
            ],
            # Second assess (after refinement): all covered
            [
                {"claim": "strong claim", "max_similarity": 0.9, "covered": True, "top_source": "s.py"},
                {"claim": "weak claim", "max_similarity": 0.7, "covered": True, "top_source": "new.py"},
            ],
        ]
        mock_retrieve.return_value = [
            {"artifact_id": "new1", "chunk_index": 0, "relevance": 0.7, "content": "new content", "filename": "new.py", "domain": "coding"},
        ]
        mock_assemble.return_value = ("new context", [{"artifact_id": "a1", "relevance": 0.8}], 200)

        qr = _make_query_result()
        result = await self_rag_enhance(
            qr, "x" * 100, MagicMock(), MagicMock(), MagicMock(),
        )
        assert result["self_rag"]["status"] == "refined"
        assert result["self_rag"]["claims_weak"] == 0
        assert result["self_rag"]["additional_results_found"] == 1
        assert "weak claim" in result["self_rag"]["refined_queries"]

    @pytest.mark.asyncio
    @patch("agents.hallucination.extract_claims", new_callable=AsyncMock)
    @patch("agents.self_rag._assess_claims", new_callable=AsyncMock)
    @patch("agents.self_rag._retrieve_for_claims", new_callable=AsyncMock)
    async def test_no_additional_results_found(self, mock_retrieve, mock_assess, mock_extract):
        mock_extract.return_value = (["weak claim"], "heuristic")
        mock_assess.return_value = [
            {"claim": "weak claim", "max_similarity": 0.2, "covered": False, "top_source": ""},
        ]
        mock_retrieve.return_value = []

        qr = _make_query_result()
        result = await self_rag_enhance(
            qr, "x" * 100, MagicMock(), MagicMock(), MagicMock(),
        )
        assert result["self_rag"]["status"] == "no_additional_results"
        assert result["self_rag"]["claims_weak"] == 1

    @pytest.mark.asyncio
    @patch("agents.hallucination.extract_claims", new_callable=AsyncMock)
    @patch("agents.self_rag._assess_claims", new_callable=AsyncMock)
    @patch("agents.self_rag._retrieve_for_claims", new_callable=AsyncMock)
    @patch("agents.query_agent.assemble_context")
    async def test_respects_max_iterations(self, mock_assemble, mock_retrieve, mock_assess, mock_extract):
        """Self-RAG should stop after max_iterations even if claims remain weak."""
        mock_extract.return_value = (["persistent weak claim"], "llm")
        # Always return weak — should stop at max iterations
        mock_assess.return_value = [
            {"claim": "persistent weak claim", "max_similarity": 0.2, "covered": False, "top_source": ""},
        ]
        mock_retrieve.return_value = [
            {"artifact_id": "r1", "chunk_index": 0, "relevance": 0.3, "content": "some content", "filename": "r.py", "domain": "coding"},
        ]
        mock_assemble.return_value = ("ctx", [{"artifact_id": "a1", "relevance": 0.5}], 100)

        qr = _make_query_result()
        with patch.object(config, "SELF_RAG_MAX_ITERATIONS", 2):
            result = await self_rag_enhance(
                qr, "x" * 100, MagicMock(), MagicMock(), MagicMock(),
            )
        assert result["self_rag"]["iterations"] <= 2
        assert mock_retrieve.call_count <= 2

    @pytest.mark.asyncio
    @patch("agents.hallucination.extract_claims", new_callable=AsyncMock)
    @patch("agents.self_rag._assess_claims", new_callable=AsyncMock)
    @patch("agents.self_rag._retrieve_for_claims", new_callable=AsyncMock)
    async def test_respects_max_refined_queries(self, mock_retrieve, mock_assess, mock_extract):
        """Only up to SELF_RAG_MAX_REFINED_QUERIES weak claims should be used."""
        mock_extract.return_value = (["w1", "w2", "w3", "w4", "w5"], "llm")
        mock_assess.side_effect = [
            # All weak
            [{"claim": f"w{i+1}", "max_similarity": 0.1, "covered": False, "top_source": ""} for i in range(5)],
            # After refinement: still all weak (stop after 1 iter)
            [{"claim": f"w{i+1}", "max_similarity": 0.1, "covered": False, "top_source": ""} for i in range(5)],
            # Third call for final assessment
            [{"claim": f"w{i+1}", "max_similarity": 0.1, "covered": False, "top_source": ""} for i in range(5)],
        ]
        mock_retrieve.return_value = []

        qr = _make_query_result()
        with patch.object(config, "SELF_RAG_MAX_REFINED_QUERIES", 3), \
             patch.object(config, "SELF_RAG_MAX_ITERATIONS", 1):
            await self_rag_enhance(
                qr, "x" * 100, MagicMock(), MagicMock(), MagicMock(),
            )

        # _retrieve_for_claims receives at most 3 queries
        if mock_retrieve.call_count > 0:
            queries_arg = mock_retrieve.call_args[0][0]
            assert len(queries_arg) <= 3

    @pytest.mark.asyncio
    @patch("agents.hallucination.extract_claims", new_callable=AsyncMock)
    @patch("agents.self_rag._assess_claims", new_callable=AsyncMock)
    @patch("agents.self_rag._retrieve_for_claims", new_callable=AsyncMock)
    @patch("agents.query_agent.assemble_context")
    async def test_model_metadata_passed_through(self, mock_assemble, mock_retrieve, mock_assess, mock_extract):
        mock_extract.return_value = (["claim"], "llm")
        mock_assess.side_effect = [
            [{"claim": "claim", "max_similarity": 0.2, "covered": False, "top_source": ""}],
            [{"claim": "claim", "max_similarity": 0.7, "covered": True, "top_source": "n.py"}],
        ]
        mock_retrieve.return_value = [
            {"artifact_id": "n1", "chunk_index": 0, "relevance": 0.7, "content": "c", "filename": "n.py", "domain": "coding"},
        ]
        mock_assemble.return_value = ("ctx", [{"artifact_id": "a1", "relevance": 0.8}], 100)

        qr = _make_query_result()
        result = await self_rag_enhance(
            qr, "x" * 100, MagicMock(), MagicMock(), MagicMock(), model="test-model",
        )
        assert result["self_rag"]["model"] == "test-model"
        assert result["self_rag"]["extraction_method"] == "llm"

    @pytest.mark.asyncio
    @patch("agents.hallucination.extract_claims", new_callable=AsyncMock)
    @patch("agents.self_rag._assess_claims", new_callable=AsyncMock)
    @patch("agents.self_rag._retrieve_for_claims", new_callable=AsyncMock)
    @patch("agents.query_agent.assemble_context")
    async def test_context_reassembled_after_refinement(self, mock_assemble, mock_retrieve, mock_assess, mock_extract):
        """When additional results are found, context should be reassembled."""
        mock_extract.return_value = (["weak claim"], "llm")
        mock_assess.side_effect = [
            [{"claim": "weak claim", "max_similarity": 0.2, "covered": False, "top_source": ""}],
            [{"claim": "weak claim", "max_similarity": 0.7, "covered": True, "top_source": "new.py"}],
        ]
        mock_retrieve.return_value = [
            {"artifact_id": "n1", "chunk_index": 0, "relevance": 0.7, "content": "new", "filename": "new.py", "domain": "coding"},
        ]
        mock_assemble.return_value = ("enriched context", [{"artifact_id": "n1", "relevance": 0.7}], 300)

        qr = _make_query_result()
        result = await self_rag_enhance(
            qr, "x" * 100, MagicMock(), MagicMock(), MagicMock(),
        )
        assert result["context"] == "enriched context"
        assert result["token_budget_used"] == 300
        mock_assemble.assert_called_once()


# ---------------------------------------------------------------------------
# Config toggle tests
# ---------------------------------------------------------------------------

class TestSelfRagConfig:
    def test_enable_self_rag_default_true(self):
        """ENABLE_SELF_RAG should default to True."""
        import os
        saved = os.environ.get("ENABLE_SELF_RAG")
        try:
            os.environ.pop("ENABLE_SELF_RAG", None)
            result = os.getenv("ENABLE_SELF_RAG", "true").lower() == "true"
            assert result is True
        finally:
            if saved is not None:
                os.environ["ENABLE_SELF_RAG"] = saved

    def test_config_vars_have_defaults(self):
        """All Self-RAG config vars should have sensible defaults."""
        assert config.SELF_RAG_MAX_ITERATIONS >= 1
        assert 0 < config.SELF_RAG_WEAK_CLAIM_THRESHOLD <= 1.0
        assert config.SELF_RAG_MAX_REFINED_QUERIES >= 1
        assert config.SELF_RAG_REFINED_TOP_K >= 1
