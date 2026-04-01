# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for memory extraction agent."""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Pre-seed heavy modules that extract_and_store_memories imports lazily
# so @patch can target them without triggering real imports.
if "routers.ingestion" not in sys.modules:
    _stub = ModuleType("routers.ingestion")
    _stub.ingest_content = None  # type: ignore[attr-defined]
    _stub.ingest_batch = None  # type: ignore[attr-defined]
    _stub.router = MagicMock()  # type: ignore[attr-defined]
    sys.modules["routers.ingestion"] = _stub
    # Also register as attribute on the parent package so _dot_lookup works.
    import routers
    routers.ingestion = _stub  # type: ignore[attr-defined]

from agents.memory import (
    archive_old_memories,
    extract_and_store_memories,
    extract_memories,
)


class TestExtractMemories:
    """Test memory extraction via LLM."""

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
        # Legacy "fact" is migrated to "empirical" at extraction time
        assert result[0]["memory_type"] == "empirical"
        assert "GIL" in result[0]["content"]

    @pytest.mark.asyncio
    @patch("agents.memory.call_internal_llm", new_callable=AsyncMock)
    async def test_invalid_memory_type_defaults_to_empirical(self, mock_llm):
        """Unknown memory_type should default to 'empirical'."""
        mock_llm.return_value = '[{"content":"test","memory_type":"invalid_type","summary":"test"}]'

        result = await extract_memories("x" * 200, "conv-123")
        assert result[0]["memory_type"] == "empirical"


class TestExtractAndStoreMemories:
    """Test full extraction + storage pipeline."""

    @pytest.mark.asyncio
    @patch("agents.memory.config")
    async def test_disabled_returns_skipped(self, mock_config):
        """Should skip when ENABLE_MEMORY_EXTRACTION is False."""
        mock_config.ENABLE_MEMORY_EXTRACTION = False
        result = await extract_and_store_memories("text", "conv-123")
        assert result["status"] == "skipped"

    @pytest.mark.asyncio
    @patch("agents.memory.config")
    @patch("agents.memory.extract_memories", new_callable=AsyncMock)
    @patch("services.ingestion.ingest_content")
    async def test_successful_storage(self, mock_ingest, mock_extract, mock_config, mock_redis, mock_neo4j):
        """Extracted memories should be ingested into conversations domain."""
        mock_config.ENABLE_MEMORY_EXTRACTION = True
        mock_extract.return_value = [
            {"content": "Python uses GIL", "memory_type": "fact", "summary": "GIL info"},
        ]
        mock_ingest.return_value = {"status": "success", "artifact_id": "art-123"}

        result = await extract_and_store_memories(
            "x" * 200, "conv-123", "claude",
            redis_client=mock_redis,
            neo4j_driver=mock_neo4j[0],
        )
        assert result["memories_extracted"] == 1
        assert result["memories_stored"] == 1
        mock_ingest.assert_called_once()


class TestArchiveOldMemories:
    """Test memory retention/archival."""

    @pytest.mark.asyncio
    async def test_archive_query(self, mock_neo4j):
        """Should run archival Cypher query and return count."""
        driver, session = mock_neo4j
        mock_result = MagicMock()
        mock_record = MagicMock()
        mock_record.__getitem__ = lambda self, key: 5  # 5 archived
        mock_result.single.return_value = mock_record
        session.run.return_value = mock_result

        result = await archive_old_memories(driver, retention_days=90)
        assert result["archived_count"] == 5
        assert result["retention_days"] == 90
        session.run.assert_called_once()
