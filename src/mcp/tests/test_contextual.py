# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for utils/contextual.py — LLM-generated contextual chunk enrichment."""

import json
from unittest.mock import AsyncMock, patch

# ---------------------------------------------------------------------------
# Tests — contextualize_chunks
# ---------------------------------------------------------------------------

class TestContextualizeChunks:
    """Tests for the main contextualize_chunks function."""

    @patch("core.utils.contextual.config")
    def test_disabled_returns_original(self, mock_config):
        """When ENABLE_CONTEXTUAL_CHUNKS is False, chunks pass through unchanged."""
        mock_config.ENABLE_CONTEXTUAL_CHUNKS = False
        from utils.contextual import contextualize_chunks

        chunks = ["chunk one", "chunk two"]
        result = contextualize_chunks(chunks, "full doc text")
        assert result == chunks

    @patch("core.utils.contextual.config")
    def test_empty_chunks_returns_empty(self, mock_config):
        """Empty input returns empty output."""
        mock_config.ENABLE_CONTEXTUAL_CHUNKS = True
        from utils.contextual import contextualize_chunks

        result = contextualize_chunks([], "full text")
        assert result == []

    @patch("core.utils.llm_client.call_llm", new_callable=AsyncMock)
    @patch("core.utils.contextual.config")
    def test_successful_enrichment(self, mock_config, mock_call_llm):
        """Successful LLM call prepends context to each chunk."""
        mock_config.ENABLE_CONTEXTUAL_CHUNKS = True
        mock_config.CONTEXTUAL_CHUNKS_MODEL = "test-model"

        contexts = ["revenue discussion in Q3 report", "API auth setup guide"]
        mock_call_llm.return_value = json.dumps(contexts)

        from utils.contextual import contextualize_chunks

        chunks = ["Revenue increased 15%", "Set up API key in config.yaml"]
        result = contextualize_chunks(chunks, "Full document text here", {"filename": "report.pdf"})

        assert len(result) == 2
        assert result[0] == "[revenue discussion in Q3 report]\nRevenue increased 15%"
        assert result[1] == "[API auth setup guide]\nSet up API key in config.yaml"

    @patch("core.utils.llm_client.call_llm", new_callable=AsyncMock)
    @patch("core.utils.contextual.config")
    def test_batching(self, mock_config, mock_call_llm):
        """Chunks are processed in batches of 5."""
        mock_config.ENABLE_CONTEXTUAL_CHUNKS = True
        mock_config.CONTEXTUAL_CHUNKS_MODEL = "test-model"

        # 7 chunks → 2 batches (5 + 2)
        chunks = [f"chunk {i}" for i in range(7)]
        batch1_contexts = [f"ctx {i}" for i in range(5)]
        batch2_contexts = [f"ctx {i}" for i in range(5, 7)]

        mock_call_llm.side_effect = [
            json.dumps(batch1_contexts),
            json.dumps(batch2_contexts),
        ]

        from utils.contextual import contextualize_chunks

        result = contextualize_chunks(chunks, "doc text")
        assert len(result) == 7
        assert mock_call_llm.call_count == 2
        assert result[0] == "[ctx 0]\nchunk 0"
        assert result[6] == "[ctx 6]\nchunk 6"

    @patch("core.utils.llm_client.call_llm", new_callable=AsyncMock)
    @patch("core.utils.contextual.config")
    def test_http_error_returns_originals(self, mock_config, mock_call_llm):
        """On LLM error, returns original chunks unchanged."""
        mock_config.ENABLE_CONTEXTUAL_CHUNKS = True
        mock_config.CONTEXTUAL_CHUNKS_MODEL = "test-model"

        import httpx as real_httpx
        mock_call_llm.side_effect = real_httpx.ConnectError("Connection refused")

        from utils.contextual import contextualize_chunks

        chunks = ["chunk one", "chunk two"]
        result = contextualize_chunks(chunks, "doc text")
        # Should fall back — chunks pass through without context
        assert result == chunks

    @patch("core.utils.llm_client.call_llm", new_callable=AsyncMock)
    @patch("core.utils.contextual.config")
    def test_mismatched_count_returns_no_context(self, mock_config, mock_call_llm):
        """When LLM returns wrong number of contexts, chunks pass through."""
        mock_config.ENABLE_CONTEXTUAL_CHUNKS = True
        mock_config.CONTEXTUAL_CHUNKS_MODEL = "test-model"

        # Return 1 context for 3 chunks
        mock_call_llm.return_value = json.dumps(["only one context"])

        from utils.contextual import contextualize_chunks

        chunks = ["chunk 1", "chunk 2", "chunk 3"]
        result = contextualize_chunks(chunks, "doc text")
        # Mismatched count → no context prepended
        assert result == ["chunk 1", "chunk 2", "chunk 3"]

    @patch("core.utils.llm_client.call_llm", new_callable=AsyncMock)
    @patch("core.utils.contextual.config")
    def test_markdown_code_block_stripped(self, mock_config, mock_call_llm):
        """LLM responses wrapped in ```json code blocks are handled."""
        mock_config.ENABLE_CONTEXTUAL_CHUNKS = True
        mock_config.CONTEXTUAL_CHUNKS_MODEL = "test-model"

        # Simulate LLM wrapping output in markdown code block
        mock_call_llm.return_value = '```json\n["ctx for chunk 0", "ctx for chunk 1"]\n```'

        from utils.contextual import contextualize_chunks

        chunks = ["chunk 0", "chunk 1"]
        result = contextualize_chunks(chunks, "doc text")
        assert result[0] == "[ctx for chunk 0]\nchunk 0"
        assert result[1] == "[ctx for chunk 1]\nchunk 1"

    @patch("core.utils.llm_client.call_llm", new_callable=AsyncMock)
    @patch("core.utils.contextual.config")
    def test_metadata_passed_to_prompt(self, mock_config, mock_call_llm):
        """Filename and domain from metadata are included in the LLM prompt."""
        mock_config.ENABLE_CONTEXTUAL_CHUNKS = True
        mock_config.CONTEXTUAL_CHUNKS_MODEL = "test-model"

        mock_call_llm.return_value = json.dumps(["ctx"])

        from utils.contextual import contextualize_chunks

        contextualize_chunks(
            ["chunk"], "doc text",
            metadata={"filename": "report.pdf", "domain": "finance"},
        )

        # First positional arg to call_llm is the messages list
        messages = mock_call_llm.call_args[0][0]
        prompt = messages[0]["content"]
        assert "report.pdf" in prompt
        assert "finance" in prompt

    @patch("core.utils.llm_client.call_llm", new_callable=AsyncMock)
    @patch("core.utils.contextual.config")
    def test_doc_preview_truncated(self, mock_config, mock_call_llm):
        """Full text is truncated to ~3000 chars in the LLM prompt."""
        mock_config.ENABLE_CONTEXTUAL_CHUNKS = True
        mock_config.CONTEXTUAL_CHUNKS_MODEL = "test-model"

        mock_call_llm.return_value = json.dumps(["ctx"])

        from utils.contextual import contextualize_chunks

        long_text = "x" * 5000
        contextualize_chunks(["chunk"], long_text)

        messages = mock_call_llm.call_args[0][0]
        prompt = messages[0]["content"]
        # Should contain truncation marker, not the full 5000 chars
        assert "[... document continues ...]" in prompt
        # Prompt should contain ≤ 3000 chars of original text (plus a few in boilerplate)
        assert prompt.count("x") <= 3100


# ---------------------------------------------------------------------------
# Tests — _generate_contexts (internal helper)
# ---------------------------------------------------------------------------

class TestGenerateContexts:
    """Tests for the internal _generate_contexts function."""

    @patch("core.utils.llm_client.call_llm", new_callable=AsyncMock)
    @patch("core.utils.contextual.config")
    def test_json_decode_error_returns_empty(self, mock_config, mock_call_llm):
        """Invalid JSON from LLM returns empty strings."""
        mock_config.CONTEXTUAL_CHUNKS_MODEL = "test-model"

        mock_call_llm.return_value = "not valid json at all"

        from core.utils.contextual import _generate_contexts

        result = _generate_contexts(["chunk"], "doc preview", "file.txt", "")
        assert result == [""]

    @patch("core.utils.llm_client.call_llm", new_callable=AsyncMock)
    @patch("core.utils.contextual.config")
    def test_chunk_preview_truncated(self, mock_config, mock_call_llm):
        """Individual chunk previews are truncated to 300 chars in the prompt."""
        mock_config.CONTEXTUAL_CHUNKS_MODEL = "test-model"

        mock_call_llm.return_value = json.dumps(["ctx"])

        from core.utils.contextual import _generate_contexts

        # Use a character that won't appear in the prompt boilerplate
        long_chunk = "\u00e9" * 500
        _generate_contexts([long_chunk], "doc preview", "file.txt", "")

        messages = mock_call_llm.call_args[0][0]
        prompt = messages[0]["content"]
        # Chunk preview should be truncated to 300 chars
        assert prompt.count("\u00e9") == 300
