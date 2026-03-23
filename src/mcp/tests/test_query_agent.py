# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for agents/query_agent.py — multi-domain search with reranking."""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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
    apply_metadata_boost,
    apply_quality_boost,
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
            rerank_results(results, "test", use_reranking=False)
        )
        assert reranked[0]["relevance"] >= reranked[1]["relevance"]
        assert reranked[1]["relevance"] >= reranked[2]["relevance"]

    def test_single_candidate_skips_rerank(self):
        results = [_make_result(relevance=0.5)]
        reranked = asyncio.get_event_loop().run_until_complete(
            rerank_results(results, "test", use_reranking=True)
        )
        assert len(reranked) == 1
        assert reranked[0]["relevance"] == 0.5

    @patch("utils.internal_llm.call_internal_llm", new_callable=AsyncMock)
    @patch("agents.query_agent.config")
    def test_llm_rerank_fallback_on_error(self, mock_config, mock_call_llm):
        """When LLM reranking fails, falls back to embedding sort."""
        mock_config.RERANK_MODE = "llm"
        mock_config.QUERY_RERANK_CANDIDATES = 15

        # Make the LLM call raise
        mock_call_llm.side_effect = httpx.HTTPStatusError(
            "Connection refused", request=MagicMock(), response=MagicMock(status_code=502)
        )

        results = [
            _make_result(relevance=0.3, artifact_id="a1"),
            _make_result(relevance=0.9, artifact_id="a2"),
        ]
        reranked = asyncio.get_event_loop().run_until_complete(
            rerank_results(results, "test", use_reranking=True)
        )
        # Fallback: sorted by relevance descending
        assert reranked[0]["relevance"] >= reranked[1]["relevance"]

    @patch("agents.query_agent.config")
    def test_cross_encoder_rerank(self, mock_config):
        """Cross-encoder mode dispatches to utils.reranker and blends scores."""
        mock_config.RERANK_MODE = "cross_encoder"
        mock_config.QUERY_RERANK_CANDIDATES = 15
        mock_config.RERANK_CE_WEIGHT = 0.6
        mock_config.RERANK_ORIGINAL_WEIGHT = 0.4

        results = [
            _make_result(relevance=0.3, artifact_id="a1", content="Python tutorial"),
            _make_result(relevance=0.9, artifact_id="a2", content="Java guide"),
            _make_result(relevance=0.6, artifact_id="a3", content="Python best practices"),
        ]

        # Mock the cross-encoder reranker to return controlled scores
        def fake_rerank(query, results_arg):
            # Simulate: reorder by cross-encoder (Python chunks score higher)
            for r in results_arg:
                if "Python" in r["content"]:
                    ce = 0.95
                else:
                    ce = 0.2
                original = r["relevance"]
                r["relevance"] = round(0.6 * ce + 0.4 * original, 4)
            results_arg.sort(key=lambda x: x["relevance"], reverse=True)
            return results_arg

        with patch("utils.reranker.rerank", side_effect=fake_rerank):
            reranked = asyncio.get_event_loop().run_until_complete(
                rerank_results(results, "Python programming", use_reranking=True)
            )

        # Python content should rank higher despite lower original score
        assert reranked[0]["artifact_id"] in ("a1", "a3")

    @patch("agents.query_agent.config")
    def test_cross_encoder_fallback_to_llm(self, mock_config):
        """When cross-encoder fails, falls back to LLM reranking."""
        mock_config.RERANK_MODE = "cross_encoder"
        mock_config.QUERY_RERANK_CANDIDATES = 15

        results = [
            _make_result(relevance=0.3, artifact_id="a1"),
            _make_result(relevance=0.9, artifact_id="a2"),
        ]

        with patch("utils.reranker.rerank", side_effect=RuntimeError("model not found")):
            with patch("agents.query_agent._rerank_llm", new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = sorted(
                    results, key=lambda x: x["relevance"], reverse=True,
                )
                reranked = asyncio.get_event_loop().run_until_complete(
                    rerank_results(results, "test", use_reranking=True)
                )
                mock_llm.assert_called_once()
        assert reranked[0]["relevance"] >= reranked[1]["relevance"]

    def test_rerank_mode_none_skips_reranking(self):
        """RERANK_MODE=none returns results in original sorted order."""
        results = [
            _make_result(relevance=0.3, artifact_id="a1"),
            _make_result(relevance=0.9, artifact_id="a2"),
        ]

        with patch("agents.query_agent.config") as mock_config:
            mock_config.RERANK_MODE = "none"
            reranked = asyncio.get_event_loop().run_until_complete(
                rerank_results(results, "test", use_reranking=True)
            )
        # mode=none: pre-sorted by relevance but no reranking applied
        assert reranked[0]["relevance"] == 0.9
        assert reranked[1]["relevance"] == 0.3


# ---------------------------------------------------------------------------
# Tests: cross-encoder reranker module
# ---------------------------------------------------------------------------

class TestRerankerModule:
    def test_rerank_empty_returns_empty(self):
        from utils.reranker import rerank
        assert rerank("test", []) == []

    def test_rerank_single_returns_unchanged(self):
        from utils.reranker import rerank
        results = [_make_result(relevance=0.5)]
        reranked = rerank("test", results)
        assert len(reranked) == 1
        assert reranked[0]["relevance"] == 0.5

    def test_sigmoid_clipping(self):
        import numpy as np

        from utils.reranker import _sigmoid
        # Extreme values should not overflow
        assert _sigmoid(np.array([100.0]))[0] == pytest.approx(1.0, abs=1e-6)
        assert _sigmoid(np.array([-100.0]))[0] == pytest.approx(0.0, abs=1e-6)
        assert _sigmoid(np.array([0.0]))[0] == pytest.approx(0.5, abs=1e-6)

    @patch("utils.reranker._load_model")
    def test_rerank_blends_scores(self, mock_load):
        """Verify score blending formula: CE_WEIGHT * ce + ORIGINAL_WEIGHT * original."""
        import numpy as np

        from utils.reranker import rerank

        # Mock ONNX session that returns known logits
        mock_session = MagicMock()
        # 3 candidates, logits where sigmoid gives ~0.73, ~0.27, ~0.95
        logits = np.array([[0.0, 1.0], [0.0, -1.0], [0.0, 3.0]], dtype=np.float32)
        mock_session.run.return_value = [logits]
        mock_session.get_inputs.return_value = [
            MagicMock(name="input_ids"),
            MagicMock(name="attention_mask"),
            MagicMock(name="token_type_ids"),
        ]

        mock_tokenizer = MagicMock()
        encoding = MagicMock()
        encoding.ids = [101, 2003, 102, 2023, 102]
        encoding.attention_mask = [1, 1, 1, 1, 1]
        encoding.type_ids = [0, 0, 0, 1, 1]
        mock_tokenizer.encode_batch.return_value = [encoding, encoding, encoding]

        mock_load.return_value = (mock_session, mock_tokenizer)

        results = [
            _make_result(relevance=0.8, artifact_id="a1", content="doc A"),
            _make_result(relevance=0.5, artifact_id="a2", content="doc B"),
            _make_result(relevance=0.3, artifact_id="a3", content="doc C"),
        ]

        reranked = rerank("test query", results)
        assert len(reranked) == 3
        # All scores should be blended (not raw originals)
        for r in reranked:
            assert r["relevance"] != 0.8
            assert r["relevance"] != 0.5
            assert r["relevance"] != 0.3


# ---------------------------------------------------------------------------
# Tests: agent_query (integration-level with mocks)
# ---------------------------------------------------------------------------

class TestAgentQuery:
    @patch("agents.query_agent.log_event")
    @patch("agents.query_agent.rerank_results")
    @patch("agents.query_agent.graph_expand_results")
    @patch("agents.query_agent.multi_domain_query")
    @patch("agents.query_agent.config")
    @patch("config.features.ENABLE_ADAPTIVE_RETRIEVAL", False)
    def test_basic_query_response_shape(
        self, mock_config, mock_mdq, mock_graph, mock_rerank, mock_log
    ):
        mock_config.DOMAINS = ["coding", "general"]
        mock_config.DOMAIN_AFFINITY = {}
        mock_config.CROSS_DOMAIN_DEFAULT_AFFINITY = 0.2
        mock_config.QUERY_CONTEXT_MAX_CHARS = 14000
        mock_config.QUALITY_BOOST_BASE = 0.8
        mock_config.QUALITY_BOOST_FACTOR = 0.2
        mock_config.QUALITY_METADATA_TAG_BOOST = 0.05
        mock_config.QUALITY_METADATA_SUBCAT_BOOST = 0.08
        mock_config.QUALITY_METADATA_MAX_BOOST = 0.15
        mock_config.QUALITY_MIN_RELEVANCE_THRESHOLD = 0.15
        mock_config.TEMPORAL_HALF_LIFE_DAYS = 30
        mock_config.TEMPORAL_RECENCY_WEIGHT = 0.1
        mock_config.CONTEXT_MAX_CHUNKS_PER_ARTIFACT = 2
        mock_config.QUERY_CONTEXT_MESSAGES = 5

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
    @patch("config.features.ENABLE_ADAPTIVE_RETRIEVAL", False)
    def test_logs_to_redis_when_client_provided(
        self, mock_config, mock_mdq, mock_graph, mock_rerank, mock_log
    ):
        mock_config.DOMAINS = ["coding", "general"]
        mock_config.DOMAIN_AFFINITY = {}
        mock_config.CROSS_DOMAIN_DEFAULT_AFFINITY = 0.2
        mock_config.QUERY_CONTEXT_MAX_CHARS = 14000
        mock_config.QUALITY_BOOST_BASE = 0.8
        mock_config.QUALITY_BOOST_FACTOR = 0.2
        mock_config.QUALITY_METADATA_TAG_BOOST = 0.05
        mock_config.QUALITY_METADATA_SUBCAT_BOOST = 0.08
        mock_config.QUALITY_METADATA_MAX_BOOST = 0.15
        mock_config.QUALITY_MIN_RELEVANCE_THRESHOLD = 0.15
        mock_config.TEMPORAL_HALF_LIFE_DAYS = 30
        mock_config.TEMPORAL_RECENCY_WEIGHT = 0.1
        mock_config.CONTEXT_MAX_CHUNKS_PER_ARTIFACT = 2
        mock_config.QUERY_CONTEXT_MESSAGES = 5

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
        mock_config.QUALITY_BOOST_BASE = 0.8
        mock_config.QUALITY_BOOST_FACTOR = 0.2
        mock_config.QUALITY_METADATA_TAG_BOOST = 0.05
        mock_config.QUALITY_METADATA_SUBCAT_BOOST = 0.08
        mock_config.QUALITY_METADATA_MAX_BOOST = 0.15
        mock_config.QUALITY_MIN_RELEVANCE_THRESHOLD = 0.15
        mock_config.TEMPORAL_HALF_LIFE_DAYS = 30
        mock_config.TEMPORAL_RECENCY_WEIGHT = 0.1
        mock_config.CONTEXT_MAX_CHUNKS_PER_ARTIFACT = 2
        mock_config.QUERY_CONTEXT_MESSAGES = 5

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


# ---------------------------------------------------------------------------
# Tests: apply_metadata_boost (Phase 14C)
# ---------------------------------------------------------------------------

class TestApplyMetadataBoost:
    def test_empty_results(self):
        """Empty input returns empty list."""
        assert apply_metadata_boost([], "python flask") == []

    def test_no_matching_terms(self):
        """No boost when query terms don't match any metadata."""
        results = [
            _make_result(
                relevance=0.8,
                sub_category="databases",
                tags_json='["sql", "postgres"]',
                keywords='["migration"]',
            ),
        ]
        boosted = apply_metadata_boost(results, "python flask routing")
        assert boosted[0]["relevance"] == 0.8
        assert "metadata_boost" not in boosted[0]

    def test_subcategory_match(self):
        """Sub-category match adds QUALITY_METADATA_SUBCAT_BOOST (0.08)."""
        results = [
            _make_result(
                relevance=0.7,
                sub_category="flask web framework",
                tags_json="[]",
                keywords="[]",
            ),
        ]
        boosted = apply_metadata_boost(results, "flask routing")
        assert boosted[0]["relevance"] == round(0.7 + 0.08, 4)
        assert boosted[0]["metadata_boost"] == 0.08

    def test_tag_match(self):
        """Each matching tag adds QUALITY_METADATA_TAG_BOOST (0.05)."""
        results = [
            _make_result(
                relevance=0.6,
                sub_category="",
                tags_json='["flask", "routing", "middleware"]',
                keywords="[]",
            ),
        ]
        # "flask" and "routing" match; "middleware" doesn't match query
        boosted = apply_metadata_boost(results, "flask routing setup")
        expected_boost = 0.05 * 2  # two matching tags
        assert boosted[0]["relevance"] == round(0.6 + expected_boost, 4)
        assert boosted[0]["metadata_boost"] == round(expected_boost, 4)

    def test_keyword_match(self):
        """Keyword matches add 0.02 each, capped at 0.06."""
        results = [
            _make_result(
                relevance=0.5,
                sub_category="",
                tags_json="[]",
                keywords='["flask", "routing", "blueprint", "middleware"]',
            ),
        ]
        # "flask", "routing", "blueprint", "middleware" — 4 keyword matches
        # 4 * 0.02 = 0.08, but capped at 0.06
        boosted = apply_metadata_boost(results, "flask routing blueprint middleware")
        assert boosted[0]["relevance"] == round(0.5 + 0.06, 4)
        assert boosted[0]["metadata_boost"] == 0.06

    def test_boost_capped(self):
        """Total boost capped at QUALITY_METADATA_MAX_BOOST (0.15)."""
        results = [
            _make_result(
                relevance=0.5,
                sub_category="flask web",  # +0.08
                tags_json='["flask", "routing", "blueprints"]',  # +0.05 * 3 = 0.15
                keywords='["flask", "routing"]',  # +0.02 * 2 = 0.04
            ),
        ]
        # Uncapped total: 0.08 + 0.15 + 0.04 = 0.27, capped to 0.15
        boosted = apply_metadata_boost(results, "flask routing blueprints")
        assert boosted[0]["relevance"] == round(0.5 + 0.15, 4)
        assert boosted[0]["metadata_boost"] == 0.15

    def test_stopwords_ignored(self):
        """Query terms that are stopwords don't trigger boosts."""
        results = [
            _make_result(
                relevance=0.7,
                sub_category="the very best",
                tags_json='["with", "from", "about"]',
                keywords='["have", "been"]',
            ),
        ]
        # "the", "very", "with", "from", "about", "have", "been" are all stopwords
        # "is" and "a" are <= 2 chars and also filtered
        boosted = apply_metadata_boost(results, "the very best is a thing")
        # Only "best" and "thing" pass the filter; "best" matches sub_category
        assert boosted[0]["relevance"] == round(0.7 + 0.08, 4)


# ---------------------------------------------------------------------------
# Tests: apply_quality_boost (Phase 14B)
# ---------------------------------------------------------------------------

class TestApplyQualityBoost:
    def test_no_driver(self):
        """Returns results unchanged when neo4j_driver is None."""
        results = [_make_result(relevance=0.8)]
        boosted = apply_quality_boost(results, neo4j_driver=None)
        assert boosted[0]["relevance"] == 0.8
        assert "quality_score" not in boosted[0]

    def test_empty_results(self):
        """Returns empty list for empty input."""
        driver = MagicMock()
        assert apply_quality_boost([], neo4j_driver=driver) == []

    @patch("db.neo4j.artifacts.get_quality_scores")
    def test_basic_multiplier_quality_1(self, mock_scores):
        """quality=1.0 → multiplier = 0.8 + 0.4*1.0 = 1.2 (20% boost)."""
        mock_scores.return_value = {"art-1": 1.0}
        results = [_make_result(relevance=0.8, artifact_id="art-1")]
        boosted = apply_quality_boost(results, neo4j_driver=MagicMock())
        # 0.8 * (0.8 + 0.4 * 1.0) = 0.8 * 1.2 = 0.96
        assert boosted[0]["relevance"] == round(0.8 * 1.2, 4)

    @patch("db.neo4j.artifacts.get_quality_scores")
    def test_basic_multiplier_quality_0(self, mock_scores):
        """quality=0.0 → multiplier = 0.8 + 0.4*0.0 = 0.8 (20% penalty)."""
        mock_scores.return_value = {"art-1": 0.0}
        results = [_make_result(relevance=0.8, artifact_id="art-1")]
        boosted = apply_quality_boost(results, neo4j_driver=MagicMock())
        # 0.8 * (0.8 + 0.4 * 0.0) = 0.8 * 0.8 = 0.64
        assert boosted[0]["relevance"] == round(0.8 * 0.8, 4)

    @patch("db.neo4j.artifacts.get_quality_scores")
    def test_basic_multiplier_quality_half(self, mock_scores):
        """quality=0.5 → multiplier = 0.8 + 0.4*0.5 = 1.0 (neutral)."""
        mock_scores.return_value = {"art-1": 0.5}
        results = [_make_result(relevance=0.8, artifact_id="art-1")]
        boosted = apply_quality_boost(results, neo4j_driver=MagicMock())
        # 0.8 * (0.8 + 0.4 * 0.5) = 0.8 * 1.0 = 0.8
        assert boosted[0]["relevance"] == round(0.8 * 1.0, 4)

    @patch("db.neo4j.artifacts.get_quality_scores")
    def test_unscored_default(self, mock_scores):
        """Artifacts not in scores dict get 0.5 default."""
        mock_scores.return_value = {}  # No scores returned
        results = [_make_result(relevance=0.8, artifact_id="art-1")]
        boosted = apply_quality_boost(results, neo4j_driver=MagicMock())
        # Default quality = 0.5 → multiplier = 0.8 + 0.4 * 0.5 = 1.0
        # 0.8 * 1.0 = 0.8
        assert boosted[0]["relevance"] == round(0.8 * 1.0, 4)
        assert boosted[0]["quality_score"] == 0.5

    @patch("db.neo4j.artifacts.get_quality_scores")
    def test_quality_score_attached(self, mock_scores):
        """quality_score field is added to result dict."""
        mock_scores.return_value = {"art-1": 0.75}
        results = [_make_result(relevance=0.8, artifact_id="art-1")]
        boosted = apply_quality_boost(results, neo4j_driver=MagicMock())
        assert boosted[0]["quality_score"] == 0.75

    @patch("db.neo4j.artifacts.get_quality_scores")
    def test_lookup_failure(self, mock_scores):
        """If get_quality_scores raises, returns results unchanged."""
        mock_scores.side_effect = Exception("Neo4j connection failed")
        results = [_make_result(relevance=0.8, artifact_id="art-1")]
        boosted = apply_quality_boost(results, neo4j_driver=MagicMock())
        assert boosted[0]["relevance"] == 0.8
        assert "quality_score" not in boosted[0]
