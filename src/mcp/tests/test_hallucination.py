"""Tests for hallucination detection agent (Phase 7A)."""

import json
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Pre-seed heavy modules that verify_claim imports lazily
# so @patch can target them without triggering real imports.
if "agents.query_agent" not in sys.modules:
    _stub = ModuleType("agents.query_agent")
    _stub.agent_query = None  # type: ignore[attr-defined]
    sys.modules["agents.query_agent"] = _stub
    # Also register as attribute on the parent package so _dot_lookup works.
    import agents
    agents.query_agent = _stub  # type: ignore[attr-defined]

from agents.hallucination import (
    MIN_RESPONSE_LENGTH,
    check_hallucinations,
    extract_claims,
    get_hallucination_report,
    verify_claim,
)


class TestExtractClaims:
    """Test claim extraction from LLM responses."""

    @pytest.mark.asyncio
    async def test_short_response_returns_empty(self):
        """Responses below MIN_RESPONSE_LENGTH should return no claims."""
        result = await extract_claims("short text")
        assert result == []

    @pytest.mark.asyncio
    @patch("agents.hallucination.httpx.AsyncClient")
    async def test_successful_extraction(self, mock_client_cls):
        """Valid LLM response should parse into claim list."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '["Python was created in 1991", "The GIL limits threading"]'}}]
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await extract_claims("x" * (MIN_RESPONSE_LENGTH + 1))
        assert len(result) == 2
        assert "Python" in result[0]

    @pytest.mark.asyncio
    @patch("agents.hallucination.httpx.AsyncClient")
    async def test_extraction_handles_code_block(self, mock_client_cls):
        """LLM responses wrapped in markdown code blocks should parse correctly."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '```json\n["claim one"]\n```'}}]
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await extract_claims("x" * (MIN_RESPONSE_LENGTH + 1))
        assert len(result) == 1
        assert result[0] == "claim one"


class TestVerifyClaim:
    """Test individual claim verification against KB."""

    @pytest.mark.asyncio
    @patch("agents.query_agent.agent_query", new_callable=AsyncMock)
    async def test_verified_claim(self, mock_query, mock_chroma, mock_neo4j, mock_redis):
        """High-similarity result should mark claim as verified."""
        mock_query.return_value = {
            "results": [{"relevance": 0.85, "artifact_id": "abc", "filename": "doc.pdf", "domain": "general", "content": "matching text"}]
        }
        result = await verify_claim("test claim", mock_chroma[0], mock_neo4j[0], mock_redis)
        assert result["status"] == "verified"
        assert result["similarity"] == 0.85

    @pytest.mark.asyncio
    @patch("agents.query_agent.agent_query", new_callable=AsyncMock)
    async def test_unverified_claim(self, mock_query, mock_chroma, mock_neo4j, mock_redis):
        """Very low similarity should mark claim as unverified."""
        mock_query.return_value = {
            "results": [{"relevance": 0.2, "content": "unrelated"}]
        }
        result = await verify_claim("test claim", mock_chroma[0], mock_neo4j[0], mock_redis)
        assert result["status"] == "unverified"

    @pytest.mark.asyncio
    @patch("agents.query_agent.agent_query", new_callable=AsyncMock)
    async def test_no_results(self, mock_query, mock_chroma, mock_neo4j, mock_redis):
        """No KB results should mark claim as unverified."""
        mock_query.return_value = {"results": []}
        result = await verify_claim("test claim", mock_chroma[0], mock_neo4j[0], mock_redis)
        assert result["status"] == "unverified"
        assert result["similarity"] == 0.0


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
    @patch("agents.hallucination.extract_claims", new_callable=AsyncMock)
    @patch("agents.hallucination.verify_claim", new_callable=AsyncMock)
    async def test_full_pipeline(self, mock_verify, mock_extract, mock_chroma, mock_neo4j, mock_redis):
        """Full pipeline should extract and verify claims."""
        mock_extract.return_value = ["claim 1", "claim 2"]
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
