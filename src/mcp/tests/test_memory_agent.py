# Copyright 2026 Cerid AI. Apache-2.0 license.
"""Tests for memory extraction and recall agent."""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Pre-seed heavy modules to avoid real imports
if "routers.ingestion" not in sys.modules:
    _stub = ModuleType("routers.ingestion")
    _stub.ingest_content = None
    _stub.ingest_batch = None
    _stub.router = MagicMock()
    sys.modules["routers.ingestion"] = _stub
    import routers
    routers.ingestion = _stub

from agents.memory import extract_memories


# ---------------------------------------------------------------------------
# Tests: extract_memories
# ---------------------------------------------------------------------------

class TestExtractMemories:
    @pytest.mark.asyncio
    async def test_short_response_returns_empty(self):
        """Responses below minimum length should return no memories."""
        result = await extract_memories("short", "conv-123")
        assert result == []

    @pytest.mark.asyncio
    @patch("agents.memory.call_internal_llm", new_callable=AsyncMock)
    async def test_successful_extraction(self, mock_llm):
        """Valid LLM response should parse into memory list."""
        mock_llm.return_value = '[{"content":"Python uses GIL","memory_type":"fact","summary":"Python GIL"}]'

        result = await extract_memories("x" * 200, "conv-123")
        assert len(result) == 1
        # Legacy "fact" type is migrated to "empirical" at extraction time
        assert result[0]["memory_type"] == "empirical"
        assert "GIL" in result[0]["content"]

    @pytest.mark.asyncio
    @patch("agents.memory.call_internal_llm", new_callable=AsyncMock)
    async def test_invalid_memory_type_defaults_to_empirical(self, mock_llm):
        """Unknown memory_type should default to 'empirical'."""
        mock_llm.return_value = '[{"content":"test","memory_type":"invalid_type","summary":"test"}]'

        result = await extract_memories("x" * 200, "conv-123")
        assert result[0]["memory_type"] == "empirical"

    @pytest.mark.asyncio
    @patch("agents.memory.call_internal_llm", new_callable=AsyncMock)
    async def test_llm_returns_empty_json(self, mock_llm):
        """LLM returning empty array should return empty list."""
        mock_llm.return_value = "[]"

        result = await extract_memories("x" * 200, "conv-123")
        assert result == []

    @pytest.mark.asyncio
    @patch("agents.memory.call_internal_llm", new_callable=AsyncMock)
    async def test_llm_failure_returns_empty(self, mock_llm):
        """LLM failure should return empty list gracefully."""
        mock_llm.side_effect = Exception("LLM unavailable")

        result = await extract_memories("x" * 200, "conv-123")
        assert result == []


# ---------------------------------------------------------------------------
# Tests: extract_and_store_memories
# ---------------------------------------------------------------------------

class TestExtractAndStoreMemories:
    @pytest.mark.asyncio
    @patch("agents.memory.config")
    async def test_disabled_returns_skipped(self, mock_config):
        """Should skip when ENABLE_MEMORY_EXTRACTION is False."""
        from agents.memory import extract_and_store_memories

        mock_config.ENABLE_MEMORY_EXTRACTION = False
        result = await extract_and_store_memories("text", "conv-123")
        assert result["status"] == "skipped"

    @pytest.mark.asyncio
    @patch("agents.memory.config")
    @patch("agents.memory.extract_memories", new_callable=AsyncMock)
    async def test_no_memories_extracted(self, mock_extract, mock_config):
        """When no memories are extracted, should report zero."""
        from agents.memory import extract_and_store_memories

        mock_config.ENABLE_MEMORY_EXTRACTION = True
        mock_extract.return_value = []

        result = await extract_and_store_memories("x" * 200, "conv-123")
        assert result["memories_extracted"] == 0
