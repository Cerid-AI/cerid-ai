# Copyright 2026 Cerid AI. Apache-2.0 license.
"""Tests for agents/query_agent.py — RAG retrieval pipeline."""

import sys
from unittest.mock import MagicMock, patch

import pytest

# Clear stubs from other tests so real modules load
for _mod_name in ("agents.query_agent", "agents.decomposer", "agents.assembler"):
    _existing = sys.modules.get(_mod_name)
    if _existing is not None and not hasattr(_existing, "__file__"):
        del sys.modules[_mod_name]

from agents.assembler import deduplicate_results, rerank_results
from agents.decomposer import _get_adjacent_domains

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

    def test_empty_list_returns_empty(self):
        result = _get_adjacent_domains([])
        assert result == {}

    def test_values_are_floats(self):
        result = _get_adjacent_domains(["coding"])
        for v in result.values():
            assert isinstance(v, float)
            assert 0.0 <= v <= 1.0


# ---------------------------------------------------------------------------
# Tests: deduplicate_results (pure function)
# ---------------------------------------------------------------------------

class TestDeduplicateResults:
    def test_removes_exact_duplicates(self):
        results = [
            _make_result(artifact_id="a", chunk_index=0),
            _make_result(artifact_id="a", chunk_index=0),
        ]
        deduped = deduplicate_results(results)
        assert len(deduped) == 1

    def test_keeps_different_chunks(self):
        results = [
            _make_result(artifact_id="a", chunk_index=0),
            _make_result(artifact_id="a", chunk_index=1),
        ]
        deduped = deduplicate_results(results)
        assert len(deduped) == 2

    def test_empty_input(self):
        assert deduplicate_results([]) == []


# ---------------------------------------------------------------------------
# Tests: rerank_results (pure function)
# ---------------------------------------------------------------------------

class TestRerankResults:
    def test_sorts_by_relevance(self):
        results = [
            _make_result(relevance=0.5),
            _make_result(artifact_id="a2", relevance=0.9),
            _make_result(artifact_id="a3", relevance=0.7),
        ]
        reranked = rerank_results(results, "test query")
        scores = [r["relevance"] for r in reranked]
        assert scores == sorted(scores, reverse=True)

    def test_limits_top_k(self):
        results = [_make_result(artifact_id=f"a{i}", relevance=0.5 + i * 0.01) for i in range(20)]
        reranked = rerank_results(results, "test", top_k=5)
        assert len(reranked) <= 5


# ---------------------------------------------------------------------------
# Tests: agent_query (integration, mocked backends)
# ---------------------------------------------------------------------------

class TestAgentQuery:
    @pytest.mark.asyncio
    @patch("agents.query_agent.get_chroma")
    @patch("agents.query_agent.get_neo4j")
    @patch("agents.query_agent.get_redis")
    async def test_query_returns_results(self, mock_redis, mock_neo4j, mock_chroma):
        """agent_query should return a result dict with 'results' key."""
        collection = MagicMock()
        collection.query.return_value = {
            "ids": [["doc-1"]],
            "documents": [["Python uses a GIL"]],
            "metadatas": [[{"domain": "coding", "filename": "test.py"}]],
            "distances": [[0.1]],
        }
        mock_chroma.return_value.get_or_create_collection.return_value = collection
        mock_neo4j.return_value = None
        mock_redis.return_value = MagicMock(get=MagicMock(return_value=None))

        from agents.query_agent import agent_query

        result = await agent_query("What is a GIL?", domains=["coding"])
        assert isinstance(result, dict)
        assert "results" in result
