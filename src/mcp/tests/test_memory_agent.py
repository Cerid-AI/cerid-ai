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
    # Seed the parent package rather than importing it — src/mcp/routers/ is
    # internal_only and absent from the public mirror, where this test also runs.
    if "routers" not in sys.modules:
        sys.modules["routers"] = ModuleType("routers")
    sys.modules["routers"].ingestion = _stub

from app.agents.memory import extract_memories

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
    @patch("core.agents.memory.call_internal_llm", new_callable=AsyncMock)
    async def test_successful_extraction(self, mock_llm):
        """Valid LLM response should parse into memory list."""
        mock_llm.return_value = '[{"content":"Python uses GIL","memory_type":"fact","summary":"Python GIL"}]'

        result = await extract_memories("x" * 200, "conv-123")
        assert len(result) == 1
        # Legacy "fact" type is migrated to "empirical" at extraction time
        assert result[0]["memory_type"] == "empirical"
        assert "GIL" in result[0]["content"]

    @pytest.mark.asyncio
    @patch("core.agents.memory.call_internal_llm", new_callable=AsyncMock)
    async def test_invalid_memory_type_defaults_to_empirical(self, mock_llm):
        """Unknown memory_type should default to 'empirical'."""
        mock_llm.return_value = '[{"content":"test","memory_type":"invalid_type","summary":"test"}]'

        result = await extract_memories("x" * 200, "conv-123")
        assert result[0]["memory_type"] == "empirical"

    @pytest.mark.asyncio
    @patch("core.agents.memory.call_internal_llm", new_callable=AsyncMock)
    async def test_observation_date_grounds_prompt(self, mock_llm):
        """observation_date injects absolute-date grounding + transition capture
        instructions into the extraction prompt (R3)."""
        mock_llm.return_value = "[]"

        await extract_memories(
            "x" * 200, "conv-123", observation_date="2023/05/15",
        )

        prompt = mock_llm.call_args.args[0][0]["content"]
        assert "2023/05/15" in prompt
        assert "ABSOLUTE date" in prompt
        assert "transition" in prompt.lower()

    @pytest.mark.asyncio
    @patch("core.agents.memory.call_internal_llm", new_callable=AsyncMock)
    async def test_no_observation_date_leaves_prompt_ungrounded(self, mock_llm):
        """Without observation_date the date-grounding block is absent
        (backward-compatible default)."""
        mock_llm.return_value = "[]"

        await extract_memories("x" * 200, "conv-123")

        prompt = mock_llm.call_args.args[0][0]["content"]
        assert "ABSOLUTE date" not in prompt

    @pytest.mark.asyncio
    @patch("core.agents.memory.call_internal_llm", new_callable=AsyncMock)
    async def test_event_date_emitted_from_llm(self, mock_llm):
        """A structured event_date from the LLM is carried on the memory dict."""
        mock_llm.return_value = (
            '[{"content":"Visited MoMA","memory_type":"fact",'
            '"summary":"MoMA","event_date":"2023-01-08"}]'
        )
        result = await extract_memories(
            "x" * 200, "conv-1", observation_date="2023-01-15",
        )
        assert result[0]["event_date"] == "2023-01-08"

    @pytest.mark.asyncio
    @patch("core.agents.memory.call_internal_llm", new_callable=AsyncMock)
    async def test_event_date_falls_back_to_observation_date(self, mock_llm):
        """When the LLM omits event_date, the session/observation date is used."""
        mock_llm.return_value = (
            '[{"content":"I like oat milk","memory_type":"preference","summary":"oat"}]'
        )
        result = await extract_memories(
            "x" * 200, "conv-1", observation_date="2023-01-15",
        )
        assert result[0]["event_date"] == "2023-01-15"

    @pytest.mark.asyncio
    @patch("core.agents.memory.call_internal_llm", new_callable=AsyncMock)
    async def test_llm_returns_empty_json(self, mock_llm):
        """LLM returning empty array should return empty list."""
        mock_llm.return_value = "[]"

        result = await extract_memories("x" * 200, "conv-123")
        assert result == []

    @pytest.mark.asyncio
    @patch("core.agents.memory.call_internal_llm", new_callable=AsyncMock)
    async def test_llm_failure_returns_empty(self, mock_llm):
        """LLM failure should return empty list gracefully."""
        async def _raise(*args, **kwargs):
            raise Exception("LLM unavailable")  # noqa: TRY002

        mock_llm.side_effect = _raise

        result = await extract_memories("x" * 200, "conv-123")
        assert result == []


# ---------------------------------------------------------------------------
# Tests: extract_and_store_memories
# ---------------------------------------------------------------------------

class TestExtractAndStoreMemories:
    @pytest.mark.asyncio
    @patch("core.agents.memory.config")
    async def test_disabled_returns_skipped(self, mock_config):
        """Should skip when ENABLE_MEMORY_EXTRACTION is False."""
        from app.agents.memory import extract_and_store_memories

        mock_config.ENABLE_MEMORY_EXTRACTION = False
        result = await extract_and_store_memories("text", "conv-123")
        assert result["status"] == "skipped"

    @pytest.mark.asyncio
    @patch("core.agents.memory.config")
    @patch("core.agents.memory.extract_memories", new_callable=AsyncMock)
    async def test_no_memories_extracted(self, mock_extract, mock_config):
        """When no memories are extracted, should report zero."""
        from app.agents.memory import extract_and_store_memories

        mock_config.ENABLE_MEMORY_EXTRACTION = True
        mock_extract.return_value = []

        result = await extract_and_store_memories("x" * 200, "conv-123")
        assert result["memories_extracted"] == 0
