# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for memory consolidation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.memory_consolidation import (
    SIMILARITY_THRESHOLD,
    MemoryAction,
    classify_memory,
    mark_superseded,
)

# ---------------------------------------------------------------------------
# classify_memory — no ChromaDB client
# ---------------------------------------------------------------------------

class TestClassifyMemoryNoClient:
    """When no ChromaDB client is available, should default to ADD."""

    @pytest.mark.asyncio
    async def test_no_chroma_client_returns_add(self):
        result = await classify_memory("some new fact", chroma_client=None)
        assert result.action == "ADD"
        assert "no ChromaDB" in result.reason

    @pytest.mark.asyncio
    async def test_no_chroma_preserves_memory_type(self):
        result = await classify_memory("preference info", chroma_client=None, memory_type="preference")
        assert result.action == "ADD"


# ---------------------------------------------------------------------------
# classify_memory — no similar candidates
# ---------------------------------------------------------------------------

class TestClassifyMemoryNoMatches:
    """When ChromaDB returns no close matches, should ADD."""

    @pytest.mark.asyncio
    async def test_empty_results_returns_add(self, mock_chroma):
        client, collection = mock_chroma
        collection.query.return_value = {
            "ids": [[]],
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }
        result = await classify_memory("brand new fact", chroma_client=client)
        assert result.action == "ADD"
        assert "no similar" in result.reason

    @pytest.mark.asyncio
    async def test_low_similarity_returns_add(self, mock_chroma):
        """Distances above threshold (similarity < 0.85) should be filtered out."""
        client, collection = mock_chroma
        collection.query.return_value = {
            "ids": [["chunk_1"]],
            "documents": [["some old fact"]],
            "metadatas": [[{"artifact_id": "art-old"}]],
            "distances": [[0.5]],  # similarity = 0.5 < 0.85
        }
        result = await classify_memory("somewhat related fact", chroma_client=client)
        assert result.action == "ADD"

    @pytest.mark.asyncio
    async def test_similarity_search_failure_returns_add(self, mock_chroma):
        client, collection = mock_chroma
        collection.query.side_effect = RuntimeError("ChromaDB unavailable")
        result = await classify_memory("some fact", chroma_client=client)
        assert result.action == "ADD"
        assert "similarity search failed" in result.reason


# ---------------------------------------------------------------------------
# classify_memory — with candidates → LLM classification
# ---------------------------------------------------------------------------

class TestClassifyMemoryWithCandidates:
    """When close matches are found, should call LLM for classification."""

    def _make_chroma_results(self, similarity: float = 0.95) -> dict:
        distance = 1.0 - similarity
        return {
            "ids": [["chunk_1"]],
            "documents": [["The user prefers dark mode"]],
            "metadatas": [[{"artifact_id": "art-existing-1"}]],
            "distances": [[distance]],
        }

    @pytest.mark.asyncio
    @patch("utils.memory_consolidation.call_internal_llm", new_callable=AsyncMock)
    async def test_llm_returns_noop(self, mock_llm, mock_chroma):
        client, collection = mock_chroma
        collection.query.return_value = self._make_chroma_results()
        mock_llm.return_value = '{"action":"NOOP","target_id":null,"reason":"duplicate info"}'
        result = await classify_memory("User prefers dark mode", chroma_client=client)
        assert result.action == "NOOP"
        assert result.target_id is None
        assert "duplicate" in result.reason

    @pytest.mark.asyncio
    @patch("utils.memory_consolidation.call_internal_llm", new_callable=AsyncMock)
    async def test_llm_returns_update(self, mock_llm, mock_chroma):
        client, collection = mock_chroma
        collection.query.return_value = self._make_chroma_results()
        mock_llm.return_value = '{"action":"UPDATE","target_id":"art-existing-1","reason":"updated preference"}'
        result = await classify_memory("User now prefers light mode", chroma_client=client)
        assert result.action == "UPDATE"
        assert result.target_id == "art-existing-1"

    @pytest.mark.asyncio
    @patch("utils.memory_consolidation.call_internal_llm", new_callable=AsyncMock)
    async def test_llm_returns_add(self, mock_llm, mock_chroma):
        client, collection = mock_chroma
        collection.query.return_value = self._make_chroma_results()
        mock_llm.return_value = '{"action":"ADD","target_id":null,"reason":"new info despite similar text"}'
        result = await classify_memory("User prefers dark mode in VS Code", chroma_client=client)
        assert result.action == "ADD"

    @pytest.mark.asyncio
    @patch("utils.memory_consolidation.call_internal_llm", new_callable=AsyncMock)
    async def test_invalid_llm_action_defaults_to_add(self, mock_llm, mock_chroma):
        client, collection = mock_chroma
        collection.query.return_value = self._make_chroma_results()
        mock_llm.return_value = '{"action":"INVALID","reason":"bad"}'
        result = await classify_memory("some fact", chroma_client=client)
        assert result.action == "ADD"

    @pytest.mark.asyncio
    @patch("utils.memory_consolidation.call_internal_llm", new_callable=AsyncMock)
    async def test_update_invalid_target_falls_back_to_most_similar(self, mock_llm, mock_chroma):
        """UPDATE with unknown target_id should fall back to most similar candidate."""
        client, collection = mock_chroma
        collection.query.return_value = self._make_chroma_results()
        mock_llm.return_value = '{"action":"UPDATE","target_id":"art-nonexistent","reason":"corrected"}'
        result = await classify_memory("corrected info", chroma_client=client)
        assert result.action == "UPDATE"
        assert result.target_id == "art-existing-1"  # fallback to first candidate

    @pytest.mark.asyncio
    @patch("utils.memory_consolidation.call_internal_llm", new_callable=AsyncMock)
    async def test_non_dict_llm_response_returns_add(self, mock_llm, mock_chroma):
        client, collection = mock_chroma
        collection.query.return_value = self._make_chroma_results()
        mock_llm.return_value = '"just a string"'
        result = await classify_memory("some fact", chroma_client=client)
        assert result.action == "ADD"
        assert "non-dict" in result.reason


# ---------------------------------------------------------------------------
# classify_memory — LLM failure modes
# ---------------------------------------------------------------------------

class TestClassifyMemoryLLMFailures:
    """LLM failures should gracefully default to ADD."""

    def _make_chroma_results(self) -> dict:
        return {
            "ids": [["chunk_1"]],
            "documents": [["existing fact"]],
            "metadatas": [[{"artifact_id": "art-1"}]],
            "distances": [[0.05]],  # similarity = 0.95
        }

    @pytest.mark.asyncio
    @patch("utils.memory_consolidation.call_internal_llm", new_callable=AsyncMock)
    async def test_bifrost_http_error(self, mock_llm, mock_chroma):
        import httpx
        client, collection = mock_chroma
        collection.query.return_value = self._make_chroma_results()
        mock_llm.side_effect = httpx.HTTPStatusError(
            "503", request=MagicMock(), response=MagicMock()
        )
        result = await classify_memory("some fact", chroma_client=client)
        assert result.action == "ADD"
        assert "LLM call failed" in result.reason

    @pytest.mark.asyncio
    @patch("utils.memory_consolidation.call_internal_llm", new_callable=AsyncMock)
    async def test_circuit_open_defaults_to_add(self, mock_llm, mock_chroma):
        from utils.circuit_breaker import CircuitOpenError
        client, collection = mock_chroma
        collection.query.return_value = self._make_chroma_results()
        mock_llm.side_effect = CircuitOpenError("bifrost-memory", retry_after=30.0)
        result = await classify_memory("some fact", chroma_client=client)
        assert result.action == "ADD"
        assert "circuit open" in result.reason


# ---------------------------------------------------------------------------
# classify_memory — multiple candidates
# ---------------------------------------------------------------------------

class TestClassifyMemoryMultipleCandidates:
    """Tests with multiple similar memories returned from ChromaDB."""

    @pytest.mark.asyncio
    @patch("utils.memory_consolidation.call_internal_llm", new_callable=AsyncMock)
    async def test_multiple_candidates_sent_to_llm(self, mock_llm, mock_chroma):
        client, collection = mock_chroma
        collection.query.return_value = {
            "ids": [["chunk_1", "chunk_2", "chunk_3"]],
            "documents": [["fact A", "fact B", "fact C"]],
            "metadatas": [[
                {"artifact_id": "art-1"},
                {"artifact_id": "art-2"},
                {"artifact_id": "art-3"},
            ]],
            "distances": [[0.05, 0.08, 0.12]],  # all above threshold
        }
        mock_llm.return_value = '{"action":"UPDATE","target_id":"art-2","reason":"supersedes fact B"}'
        result = await classify_memory("updated fact B", chroma_client=client)
        assert result.action == "UPDATE"
        assert result.target_id == "art-2"

    @pytest.mark.asyncio
    @patch("utils.memory_consolidation.call_internal_llm", new_callable=AsyncMock)
    async def test_mixed_similarity_filters_low(self, mock_llm, mock_chroma):
        """Only candidates above threshold should be sent to LLM."""
        client, collection = mock_chroma
        collection.query.return_value = {
            "ids": [["chunk_1", "chunk_2"]],
            "documents": [["close match", "distant match"]],
            "metadatas": [[{"artifact_id": "art-1"}, {"artifact_id": "art-2"}]],
            "distances": [[0.05, 0.5]],  # only first is above threshold
        }
        mock_llm.return_value = '{"action":"NOOP","reason":"duplicate"}'
        result = await classify_memory("close match variant", chroma_client=client)
        assert result.action == "NOOP"
        # Verify LLM prompt only includes the close match
        call_args = mock_llm.call_args
        prompt_text = call_args[0][0][0]["content"]
        assert "art-1" in prompt_text
        assert "art-2" not in prompt_text


# ---------------------------------------------------------------------------
# mark_superseded
# ---------------------------------------------------------------------------

class TestMarkSuperseded:
    """Tests for Neo4j supersession marking."""

    def test_marks_old_artifact(self, mock_neo4j):
        driver, session = mock_neo4j
        mark_superseded(driver, "art-old", "art-new")
        session.run.assert_called_once()
        cypher = session.run.call_args[0][0]
        assert "superseded_by" in cypher
        assert "SUPERSEDES" in cypher

    def test_neo4j_failure_logs_warning(self, mock_neo4j):
        driver, session = mock_neo4j
        session.run.side_effect = RuntimeError("Neo4j down")
        # Should not raise — logs warning instead
        mark_superseded(driver, "art-old", "art-new")

    def test_passes_correct_ids(self, mock_neo4j):
        driver, session = mock_neo4j
        mark_superseded(driver, "old-123", "new-456")
        kwargs = session.run.call_args[1]
        assert kwargs["old_id"] == "old-123"
        assert kwargs["new_id"] == "new-456"
        assert "now" in kwargs


# ---------------------------------------------------------------------------
# MemoryAction dataclass
# ---------------------------------------------------------------------------

class TestMemoryAction:
    """Tests for the MemoryAction dataclass."""

    def test_defaults(self):
        action = MemoryAction(action="ADD")
        assert action.target_id is None
        assert action.reason == ""

    def test_full_construction(self):
        action = MemoryAction(action="UPDATE", target_id="art-1", reason="corrected")
        assert action.action == "UPDATE"
        assert action.target_id == "art-1"
        assert action.reason == "corrected"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    """Verify configuration constants."""

    def test_similarity_threshold_range(self):
        assert 0.0 < SIMILARITY_THRESHOLD < 1.0

    def test_similarity_threshold_is_strict(self):
        """Threshold should be high enough to avoid false positives."""
        assert SIMILARITY_THRESHOLD >= 0.8


# ---------------------------------------------------------------------------
# Integration: memory.py with consolidation enabled
# ---------------------------------------------------------------------------

class TestMemoryConsolidationIntegration:
    """Test consolidation integration in extract_and_store_memories."""

    @pytest.mark.asyncio
    @patch("core.agents.memory.config")
    @patch("core.agents.memory.extract_memories", new_callable=AsyncMock)
    @patch("config.features.FEATURE_TOGGLES", {
        "enable_memory_consolidation": True,
    })
    @patch("utils.memory_consolidation.classify_memory", new_callable=AsyncMock)
    async def test_noop_skips_storage(
        self, mock_classify, mock_extract, mock_config, mock_chroma
    ):
        """NOOP classification should skip storage and count as skipped."""
        mock_config.ENABLE_MEMORY_EXTRACTION = True
        mock_extract.return_value = [
            {"content": "duplicate fact", "memory_type": "fact", "summary": "dup"},
        ]
        mock_classify.return_value = MemoryAction(
            action="NOOP", reason="already stored"
        )
        mock_ingest = MagicMock(return_value={"status": "success", "artifact_id": "art-1"})

        from agents.memory import extract_and_store_memories
        result = await extract_and_store_memories(
            "x" * 200, "conv-123", "claude",
            chroma_client=mock_chroma[0],
            ingest_fn=mock_ingest,
        )
        assert result["memories_extracted"] == 1
        assert result["memories_stored"] == 0
        assert result["skipped_duplicates"] == 1
        mock_ingest.assert_not_called()

    @pytest.mark.asyncio
    @patch("core.agents.memory.config")
    @patch("core.agents.memory.extract_memories", new_callable=AsyncMock)
    @patch("config.features.FEATURE_TOGGLES", {
        "enable_memory_consolidation": True,
    })
    @patch("utils.memory_consolidation.classify_memory", new_callable=AsyncMock)
    @patch("utils.memory_consolidation.mark_superseded")
    async def test_update_stores_and_marks_superseded(
        self, mock_mark, mock_classify, mock_extract, mock_config,
        mock_chroma, mock_neo4j,
    ):
        """UPDATE should store new memory and mark old one superseded."""
        mock_config.ENABLE_MEMORY_EXTRACTION = True
        mock_extract.return_value = [
            {"content": "updated pref", "memory_type": "preference", "summary": "pref update"},
        ]
        mock_classify.return_value = MemoryAction(
            action="UPDATE", target_id="art-old", reason="corrected"
        )
        mock_ingest = MagicMock(return_value={"status": "success", "artifact_id": "art-new"})

        from agents.memory import extract_and_store_memories
        result = await extract_and_store_memories(
            "x" * 200, "conv-123", "claude",
            chroma_client=mock_chroma[0],
            neo4j_driver=mock_neo4j[0],
            ingest_fn=mock_ingest,
        )
        assert result["memories_stored"] == 1
        assert result["results"][0]["consolidation_action"] == "UPDATE"
        mock_mark.assert_called_once_with(mock_neo4j[0], "art-old", "art-new")

    @pytest.mark.asyncio
    @patch("core.agents.memory.config")
    @patch("core.agents.memory.extract_memories", new_callable=AsyncMock)
    @patch("config.features.FEATURE_TOGGLES", {
        "enable_memory_consolidation": False,
    })
    async def test_consolidation_disabled_skips_classification(
        self, mock_extract, mock_config, mock_chroma,
    ):
        """When consolidation is disabled, should proceed directly to storage."""
        mock_config.ENABLE_MEMORY_EXTRACTION = True
        mock_extract.return_value = [
            {"content": "some fact", "memory_type": "fact", "summary": "fact"},
        ]
        mock_ingest = MagicMock(return_value={"status": "success", "artifact_id": "art-1"})

        from agents.memory import extract_and_store_memories
        result = await extract_and_store_memories(
            "x" * 200, "conv-123",
            chroma_client=mock_chroma[0],
            ingest_fn=mock_ingest,
        )
        assert result["memories_stored"] == 1
        assert result["skipped_duplicates"] == 0
