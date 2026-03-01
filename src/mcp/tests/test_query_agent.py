# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for agents/query_agent.py — multi-domain search with reranking."""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# test_hallucination.py may inject a stub agents.query_agent with only
# `agent_query` — clear it so the real module loads.
_existing = sys.modules.get("agents.query_agent")
if _existing is not None and not hasattr(_existing, "_get_adjacent_domains"):
    del sys.modules["agents.query_agent"]

from agents.query_agent import (  # noqa: E402
    _enrich_query,
    _get_adjacent_domains,
    agent_query,
    assemble_context,
    deduplicate_results,
    multi_domain_query,
    rerank_results,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(
    artifact_id="art-1",
    chunk_index=0,
    relevance=0.8,
    domain="coding",
    filename="test.py",
    content="some content",
    **extra,
):
    """Create a minimal result dict for testing."""
    return {
        "artifact_id": artifact_id,
        "chunk_index": chunk_index,
        "relevance": relevance,
        "domain": domain,
        "filename": filename,
        "content": content,
        "chunk_id": f"{artifact_id}_chunk_{chunk_index}",
        "collection": f"domain_{domain}",
        "ingested_at": "",
        **extra,
    }


# ---------------------------------------------------------------------------
# Tests: _get_adjacent_domains (pure function)
# ---------------------------------------------------------------------------

class TestGetAdjacentDomains:
    def test_returns_dict(self):
        result = _get_adjacent_domains(["coding"])
        assert isinstance(result, dict)

    def test_excludes_requested_domains(self):
        result = _get_adjacent_domains(["coding"])
        assert "coding" not in result

    def test_all_domains_returns_empty(self):
        from config import DOMAINS
        result = _get_adjacent_domains(DOMAINS)
        assert result == {}

    def test_empty_list_returns_empty(self):
        result = _get_adjacent_domains([])
        assert result == {}

    def test_single_domain_includes_others(self):
        from config import DOMAINS
        result = _get_adjacent_domains(["coding"])
        # Should include at least some other domains
        for d in result:
            assert d != "coding"
            assert d in DOMAINS

    def test_values_are_floats(self):
        result = _get_adjacent_domains(["coding"])
        for v in result.values():
            assert isinstance(v, float)
            assert 0.0 <= v <= 1.0


# ---------------------------------------------------------------------------
# Tests: deduplicate_results (pure function)
# ---------------------------------------------------------------------------

class TestDeduplicateResults:
    def test_no_duplicates_unchanged(self):
        results = [
            _make_result(artifact_id="a1", chunk_index=0),
            _make_result(artifact_id="a2", chunk_index=0),
        ]
        deduped = deduplicate_results(results)
        assert len(deduped) == 2

    def test_exact_duplicates_keep_best(self):
        results = [
            _make_result(artifact_id="a1", chunk_index=0, relevance=0.5),
            _make_result(artifact_id="a1", chunk_index=0, relevance=0.9),
        ]
        deduped = deduplicate_results(results)
        assert len(deduped) == 1
        assert deduped[0]["relevance"] == 0.9

    def test_different_chunk_indices_kept(self):
        results = [
            _make_result(artifact_id="a1", chunk_index=0),
            _make_result(artifact_id="a1", chunk_index=1),
        ]
        deduped = deduplicate_results(results)
        assert len(deduped) == 2

    def test_empty_input(self):
        assert deduplicate_results([]) == []

    def test_triple_duplicates(self):
        results = [
            _make_result(artifact_id="a1", chunk_index=0, relevance=0.3),
            _make_result(artifact_id="a1", chunk_index=0, relevance=0.7),
            _make_result(artifact_id="a1", chunk_index=0, relevance=0.5),
        ]
        deduped = deduplicate_results(results)
        assert len(deduped) == 1
        assert deduped[0]["relevance"] == 0.7


# ---------------------------------------------------------------------------
# Tests: assemble_context (pure function)
# ---------------------------------------------------------------------------

class TestAssembleContext:
    def test_single_result(self):
        results = [_make_result(content="hello world")]
        context, sources, chars = assemble_context(results)
        assert "hello world" in context
        assert len(sources) == 1
        assert chars == len("hello world")

    def test_respects_max_chars(self):
        results = [
            _make_result(content="a" * 100),
            _make_result(content="b" * 100, artifact_id="a2"),
        ]
        context, sources, chars = assemble_context(results, max_chars=150)
        assert len(sources) == 1  # Only first fits
        assert chars == 100

    def test_empty_results(self):
        context, sources, chars = assemble_context([])
        assert context == ""
        assert sources == []
        assert chars == 0

    def test_source_preview_truncated(self):
        long_content = "x" * 500
        results = [_make_result(content=long_content)]
        _, sources, _ = assemble_context(results)
        assert len(sources[0]["content"]) == 200  # Preview truncated to 200

    def test_source_has_required_fields(self):
        results = [_make_result()]
        _, sources, _ = assemble_context(results)
        source = sources[0]
        required = {"content", "relevance", "artifact_id", "filename", "domain", "chunk_index"}
        assert required.issubset(set(source.keys()))

    def test_multiple_results_joined(self):
        results = [
            _make_result(content="part one", artifact_id="a1"),
            _make_result(content="part two", artifact_id="a2"),
        ]
        context, sources, _ = assemble_context(results, max_chars=50000)
        assert "part one" in context
        assert "part two" in context
        assert len(sources) == 2


# ---------------------------------------------------------------------------
# Tests: multi_domain_query
# ---------------------------------------------------------------------------

class TestMultiDomainQuery:
    def test_invalid_domain_raises(self):
        with pytest.raises(ValueError, match="Invalid domains"):
            asyncio.get_event_loop().run_until_complete(
                multi_domain_query("test", domains=["nonexistent_domain_xyz"])
            )

    @patch("agents.query_agent.config")
    def test_query_single_domain(self, mock_config):
        mock_config.DOMAINS = ["coding", "general"]
        mock_config.collection_name = lambda d: f"domain_{d}"
        mock_config.HYBRID_VECTOR_WEIGHT = 0.6
        mock_config.HYBRID_KEYWORD_WEIGHT = 0.4

        collection = MagicMock()
        collection.query.return_value = {
            "ids": [["chunk_1"]],
            "distances": [[0.2]],
            "documents": [["test content"]],
            "metadatas": [[{"artifact_id": "a1", "filename": "test.py", "chunk_index": 0}]],
        }

        chroma_client = MagicMock()
        chroma_client.get_collection.return_value = collection

        # Mock BM25 as unavailable
        with patch("utils.bm25.is_available", return_value=False):
            results = asyncio.get_event_loop().run_until_complete(
                multi_domain_query("test query", domains=["coding"], chroma_client=chroma_client)
            )

        assert len(results) == 1
        assert results[0]["domain"] == "coding"
        assert results[0]["content"] == "test content"
        assert results[0]["relevance"] == 0.8  # 1.0 - 0.2

    @patch("agents.query_agent.config")
    def test_domain_error_returns_empty(self, mock_config):
        mock_config.DOMAINS = ["coding", "general"]
        mock_config.collection_name = lambda d: f"domain_{d}"

        chroma_client = MagicMock()
        chroma_client.get_collection.side_effect = Exception("Collection not found")

        with patch("utils.bm25.is_available", return_value=False):
            results = asyncio.get_event_loop().run_until_complete(
                multi_domain_query("test", domains=["coding"], chroma_client=chroma_client)
            )

        assert results == []


# ---------------------------------------------------------------------------
# Tests: rerank_results
# ---------------------------------------------------------------------------

class TestRerankResults:
    def test_empty_results(self):
        results = asyncio.get_event_loop().run_until_complete(
            rerank_results([], "test query")
        )
        assert results == []

    def test_no_llm_sorts_by_relevance(self):
        results = [
            _make_result(relevance=0.3),
            _make_result(relevance=0.9, artifact_id="a2"),
            _make_result(relevance=0.6, artifact_id="a3"),
        ]
        reranked = asyncio.get_event_loop().run_until_complete(
            rerank_results(results, "test", use_llm=False)
        )
        assert reranked[0]["relevance"] >= reranked[1]["relevance"]
        assert reranked[1]["relevance"] >= reranked[2]["relevance"]

    def test_single_candidate_skips_rerank(self):
        results = [_make_result(relevance=0.5)]
        reranked = asyncio.get_event_loop().run_until_complete(
            rerank_results(results, "test", use_llm=True)
        )
        assert len(reranked) == 1
        assert reranked[0]["relevance"] == 0.5

    @patch("agents.query_agent.httpx")
    @patch("agents.query_agent.parse_llm_json")
    @patch("agents.query_agent.config")
    def test_llm_rerank_fallback_on_error(self, mock_config, mock_parse, mock_httpx):
        """When LLM reranking fails, falls back to embedding sort."""
        mock_config.QUERY_RERANK_CANDIDATES = 15
        mock_config.BIFROST_TIMEOUT = 30
        mock_config.BIFROST_URL = "http://bifrost:8080/v1"
        mock_config.LLM_INTERNAL_MODEL = "meta-llama/llama-3.3-70b-instruct"

        # Make the async client raise
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        results = [
            _make_result(relevance=0.3, artifact_id="a1"),
            _make_result(relevance=0.9, artifact_id="a2"),
        ]
        reranked = asyncio.get_event_loop().run_until_complete(
            rerank_results(results, "test", use_llm=True)
        )
        # Fallback: sorted by relevance descending
        assert reranked[0]["relevance"] >= reranked[1]["relevance"]


# ---------------------------------------------------------------------------
# Tests: agent_query (integration-level with mocks)
# ---------------------------------------------------------------------------

class TestAgentQuery:
    @patch("agents.query_agent.log_event")
    @patch("agents.query_agent.rerank_results")
    @patch("agents.query_agent.graph_expand_results")
    @patch("agents.query_agent.multi_domain_query")
    @patch("agents.query_agent.config")
    def test_basic_query_response_shape(
        self, mock_config, mock_mdq, mock_graph, mock_rerank, mock_log
    ):
        mock_config.DOMAINS = ["coding", "general"]
        mock_config.DOMAIN_AFFINITY = {}
        mock_config.CROSS_DOMAIN_DEFAULT_AFFINITY = 0.2
        mock_config.QUERY_CONTEXT_MAX_CHARS = 14000

        result_item = _make_result(content="test content", relevance=0.8)
        # Use side_effect to return fresh lists (avoids aliasing when extend() mutates)
        mock_mdq.side_effect = [[result_item], []]
        mock_graph.return_value = [result_item]
        mock_rerank.return_value = [result_item]

        with patch("utils.temporal.parse_temporal_intent", return_value=None), \
             patch("utils.temporal.recency_score", return_value=0.0):
            response = asyncio.get_event_loop().run_until_complete(
                agent_query(
                    "test query",
                    domains=["coding"],
                    chroma_client=MagicMock(),
                )
            )

        required = {"context", "sources", "confidence", "domains_searched",
                     "total_results", "token_budget_used", "graph_results", "results"}
        assert required.issubset(set(response.keys()))
        assert response["total_results"] == 1
        assert isinstance(response["confidence"], float)

    @patch("agents.query_agent.log_event")
    @patch("agents.query_agent.rerank_results")
    @patch("agents.query_agent.graph_expand_results")
    @patch("agents.query_agent.multi_domain_query")
    @patch("agents.query_agent.config")
    def test_logs_to_redis_when_client_provided(
        self, mock_config, mock_mdq, mock_graph, mock_rerank, mock_log
    ):
        mock_config.DOMAINS = ["coding", "general"]
        mock_config.DOMAIN_AFFINITY = {}
        mock_config.CROSS_DOMAIN_DEFAULT_AFFINITY = 0.2
        mock_config.QUERY_CONTEXT_MAX_CHARS = 14000

        mock_mdq.return_value = []
        mock_graph.return_value = []
        mock_rerank.return_value = []

        redis = MagicMock()

        with patch("utils.temporal.parse_temporal_intent", return_value=None):
            asyncio.get_event_loop().run_until_complete(
                agent_query("test", redis_client=redis, chroma_client=MagicMock())
            )

        mock_log.assert_called_once()

    @patch("agents.query_agent.log_event")
    @patch("agents.query_agent.rerank_results")
    @patch("agents.query_agent.graph_expand_results")
    @patch("agents.query_agent.multi_domain_query")
    @patch("agents.query_agent.config")
    def test_no_results_returns_zero_confidence(
        self, mock_config, mock_mdq, mock_graph, mock_rerank, mock_log
    ):
        mock_config.DOMAINS = ["coding"]
        mock_config.DOMAIN_AFFINITY = {}
        mock_config.CROSS_DOMAIN_DEFAULT_AFFINITY = 0.2
        mock_config.QUERY_CONTEXT_MAX_CHARS = 14000

        mock_mdq.return_value = []
        mock_graph.return_value = []
        mock_rerank.return_value = []

        with patch("utils.temporal.parse_temporal_intent", return_value=None):
            response = asyncio.get_event_loop().run_until_complete(
                agent_query("test", domains=["coding"], chroma_client=MagicMock())
            )

        assert response["confidence"] == 0.0
        assert response["total_results"] == 0
        assert response["context"] == ""


# ---------------------------------------------------------------------------
# Tests: _enrich_query (pure function)
# ---------------------------------------------------------------------------

class TestEnrichQuery:
    def test_empty_conversation(self):
        assert _enrich_query("middleware", []) == "middleware"

    def test_none_like_empty(self):
        assert _enrich_query("middleware", []) == "middleware"

    def test_adds_context_terms(self):
        messages = [
            {"role": "user", "content": "I am building a FastAPI application"},
        ]
        enriched = _enrich_query("how do I add middleware", messages)
        assert "fastapi" in enriched.lower()
        assert "application" in enriched.lower()
        assert "how do I add middleware" in enriched

    def test_skips_assistant_messages(self):
        messages = [
            {"role": "assistant", "content": "Here is how to use Django"},
            {"role": "user", "content": "I want Flask instead"},
        ]
        enriched = _enrich_query("routing setup", messages)
        assert "flask" in enriched.lower()
        assert "django" not in enriched.lower()

    def test_skips_system_messages(self):
        messages = [
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "Tell me about PostgreSQL"},
        ]
        enriched = _enrich_query("database", messages)
        assert "postgresql" in enriched.lower()
        assert "helpful" not in enriched.lower()

    def test_removes_stopwords(self):
        messages = [
            {"role": "user", "content": "I have been working with Python"},
        ]
        enriched = _enrich_query("testing", messages)
        # "have", "been", "with" are stopwords; "python" and "working" are not
        assert "python" in enriched.lower()
        words = enriched.lower().split()
        assert "have" not in words[1:]  # after the original query
        assert "been" not in words[1:]

    def test_skips_short_words(self):
        messages = [
            {"role": "user", "content": "Go is a great language"},
        ]
        enriched = _enrich_query("concurrency", messages)
        # "go" and "is" and "a" are <= 2 chars, should be skipped
        words = enriched.split()
        assert "go" not in [w.lower() for w in words[1:]]

    def test_deduplicates_with_query_terms(self):
        messages = [
            {"role": "user", "content": "I need help with middleware for my app"},
        ]
        enriched = _enrich_query("middleware setup", messages)
        # "middleware" is already in query, should not be duplicated
        assert enriched.lower().count("middleware") == 1

    def test_max_terms_limit(self):
        # Create a message with many unique words
        words = [f"word{i}" for i in range(50)]
        messages = [{"role": "user", "content": " ".join(words)}]
        enriched = _enrich_query("query", messages, max_terms=5)
        # Original query + at most 5 context terms
        parts = enriched.split()
        assert len(parts) <= 1 + 5  # "query" + 5 terms

    def test_max_context_messages(self):
        messages = [
            {"role": "user", "content": f"topic{i} content"} for i in range(10)
        ]
        enriched = _enrich_query("query", messages, max_context_messages=2)
        # Should only use last 2 messages
        assert "topic9" in enriched.lower() or "topic8" in enriched.lower()

    def test_most_recent_first(self):
        messages = [
            {"role": "user", "content": "ancient topic forgotten"},
            {"role": "user", "content": "recent topic important"},
        ]
        enriched = _enrich_query("query", messages, max_terms=3)
        # Recent message terms should appear (processed first from reversed)
        assert "recent" in enriched.lower()

    def test_preserves_original_query(self):
        messages = [{"role": "user", "content": "Python FastAPI"}]
        enriched = _enrich_query("middleware setup", messages)
        assert enriched.startswith("middleware setup")


# ---------------------------------------------------------------------------
# Tests: assemble_context — artifact chunk limiting (Phase 13C)
# ---------------------------------------------------------------------------

class TestAssembleContextArtifactLimiting:
    def test_limits_chunks_per_artifact(self):
        """Three chunks from same artifact → only 2 included (default limit)."""
        results = [
            _make_result(artifact_id="a1", chunk_index=0, content="chunk0", relevance=0.9),
            _make_result(artifact_id="a1", chunk_index=1, content="chunk1", relevance=0.8),
            _make_result(artifact_id="a1", chunk_index=2, content="chunk2", relevance=0.7),
        ]
        _, sources, _ = assemble_context(results, max_chars=50000, max_chunks_per_artifact=2)
        assert len(sources) == 2
        assert all(s["artifact_id"] == "a1" for s in sources)

    def test_different_artifacts_not_limited(self):
        """Chunks from different artifacts are each allowed their own quota."""
        results = [
            _make_result(artifact_id="a1", chunk_index=0, content="a1c0"),
            _make_result(artifact_id="a1", chunk_index=1, content="a1c1"),
            _make_result(artifact_id="a2", chunk_index=0, content="a2c0"),
            _make_result(artifact_id="a2", chunk_index=1, content="a2c1"),
        ]
        _, sources, _ = assemble_context(results, max_chars=50000, max_chunks_per_artifact=2)
        assert len(sources) == 4

    def test_skipped_chunks_allow_others(self):
        """When artifact A is capped, chunks from artifact B still fill budget."""
        results = [
            _make_result(artifact_id="a1", chunk_index=0, content="x" * 10, relevance=0.9),
            _make_result(artifact_id="a1", chunk_index=1, content="x" * 10, relevance=0.85),
            _make_result(artifact_id="a1", chunk_index=2, content="x" * 10, relevance=0.8),
            _make_result(artifact_id="a2", chunk_index=0, content="y" * 10, relevance=0.7),
        ]
        _, sources, _ = assemble_context(results, max_chars=50000, max_chunks_per_artifact=2)
        assert len(sources) == 3  # 2 from a1 + 1 from a2
        artifact_ids = [s["artifact_id"] for s in sources]
        assert artifact_ids.count("a1") == 2
        assert artifact_ids.count("a2") == 1

    def test_char_budget_still_respected(self):
        """Char budget takes precedence even when artifact limit allows more."""
        results = [
            _make_result(artifact_id="a1", chunk_index=0, content="a" * 100),
            _make_result(artifact_id="a2", chunk_index=0, content="b" * 100),
        ]
        _, sources, chars = assemble_context(results, max_chars=150, max_chunks_per_artifact=5)
        assert len(sources) == 1
        assert chars == 100

    def test_continue_past_budget_overflow(self):
        """Large chunk that exceeds budget is skipped; smaller later chunk fits."""
        results = [
            _make_result(artifact_id="a1", chunk_index=0, content="a" * 50, relevance=0.9),
            _make_result(artifact_id="a2", chunk_index=0, content="b" * 200, relevance=0.8),
            _make_result(artifact_id="a3", chunk_index=0, content="c" * 40, relevance=0.7),
        ]
        _, sources, chars = assemble_context(results, max_chars=100, max_chunks_per_artifact=5)
        # a1 fits (50), a2 too big (200), a3 fits (40)
        assert len(sources) == 2
        assert sources[0]["artifact_id"] == "a1"
        assert sources[1]["artifact_id"] == "a3"
        assert chars == 90
