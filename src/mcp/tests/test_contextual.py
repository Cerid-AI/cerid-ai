# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for utils/contextual.py — LLM-generated contextual chunk enrichment."""

import json
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_config(**overrides):
    """Build a mock config with contextual chunking defaults."""
    cfg = MagicMock()
    cfg.ENABLE_CONTEXTUAL_CHUNKS = True
    cfg.CONTEXTUAL_CHUNKS_MODEL = "test-model"
    cfg.BIFROST_URL = "http://bifrost:8080/v1"
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _mock_response(contexts: list[str], status_code: int = 200):
    """Build a mock httpx response returning a JSON array of context strings."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "choices": [{
            "message": {"content": json.dumps(contexts)},
        }],
    }
    return resp


# ---------------------------------------------------------------------------
# Tests — contextualize_chunks
# ---------------------------------------------------------------------------

class TestContextualizeChunks:
    """Tests for the main contextualize_chunks function."""

    @patch("utils.contextual.config")
    def test_disabled_returns_original(self, mock_config):
        """When ENABLE_CONTEXTUAL_CHUNKS is False, chunks pass through unchanged."""
        mock_config.ENABLE_CONTEXTUAL_CHUNKS = False
        from utils.contextual import contextualize_chunks

        chunks = ["chunk one", "chunk two"]
        result = contextualize_chunks(chunks, "full doc text")
        assert result == chunks

    @patch("utils.contextual.config")
    def test_empty_chunks_returns_empty(self, mock_config):
        """Empty input returns empty output."""
        mock_config.ENABLE_CONTEXTUAL_CHUNKS = True
        from utils.contextual import contextualize_chunks

        result = contextualize_chunks([], "full text")
        assert result == []

    @patch("utils.contextual.httpx")
    @patch("utils.contextual.config")
    def test_successful_enrichment(self, mock_config, mock_httpx):
        """Successful LLM call prepends context to each chunk."""
        mock_config.ENABLE_CONTEXTUAL_CHUNKS = True
        mock_config.CONTEXTUAL_CHUNKS_MODEL = "test-model"
        mock_config.BIFROST_URL = "http://bifrost:8080/v1"

        contexts = ["revenue discussion in Q3 report", "API auth setup guide"]
        mock_httpx.post.return_value = _mock_response(contexts)

        from utils.contextual import contextualize_chunks

        chunks = ["Revenue increased 15%", "Set up API key in config.yaml"]
        result = contextualize_chunks(chunks, "Full document text here", {"filename": "report.pdf"})

        assert len(result) == 2
        assert result[0] == "[revenue discussion in Q3 report]\nRevenue increased 15%"
        assert result[1] == "[API auth setup guide]\nSet up API key in config.yaml"

    @patch("utils.contextual.httpx")
    @patch("utils.contextual.config")
    def test_batching(self, mock_config, mock_httpx):
        """Chunks are processed in batches of 5."""
        mock_config.ENABLE_CONTEXTUAL_CHUNKS = True
        mock_config.CONTEXTUAL_CHUNKS_MODEL = "test-model"
        mock_config.BIFROST_URL = "http://bifrost:8080/v1"

        # 7 chunks → 2 batches (5 + 2)
        chunks = [f"chunk {i}" for i in range(7)]
        batch1_contexts = [f"ctx {i}" for i in range(5)]
        batch2_contexts = [f"ctx {i}" for i in range(5, 7)]

        mock_httpx.post.side_effect = [
            _mock_response(batch1_contexts),
            _mock_response(batch2_contexts),
        ]

        from utils.contextual import contextualize_chunks

        result = contextualize_chunks(chunks, "doc text")
        assert len(result) == 7
        assert mock_httpx.post.call_count == 2
        assert result[0] == "[ctx 0]\nchunk 0"
        assert result[6] == "[ctx 6]\nchunk 6"

    @patch("utils.contextual.httpx.post")
    @patch("utils.contextual.config")
    def test_http_error_returns_originals(self, mock_config, mock_post):
        """On HTTP error, returns original chunks unchanged."""
        mock_config.ENABLE_CONTEXTUAL_CHUNKS = True
        mock_config.CONTEXTUAL_CHUNKS_MODEL = "test-model"
        mock_config.BIFROST_URL = "http://bifrost:8080/v1"

        import httpx as real_httpx
        mock_post.side_effect = real_httpx.ConnectError("Connection refused")

        from utils.contextual import contextualize_chunks

        chunks = ["chunk one", "chunk two"]
        result = contextualize_chunks(chunks, "doc text")
        # Should fall back — chunks pass through without context
        assert result == chunks

    @patch("utils.contextual.httpx")
    @patch("utils.contextual.config")
    def test_mismatched_count_returns_no_context(self, mock_config, mock_httpx):
        """When LLM returns wrong number of contexts, chunks pass through."""
        mock_config.ENABLE_CONTEXTUAL_CHUNKS = True
        mock_config.CONTEXTUAL_CHUNKS_MODEL = "test-model"
        mock_config.BIFROST_URL = "http://bifrost:8080/v1"

        # Return 1 context for 3 chunks
        mock_httpx.post.return_value = _mock_response(["only one context"])

        from utils.contextual import contextualize_chunks

        chunks = ["chunk 1", "chunk 2", "chunk 3"]
        result = contextualize_chunks(chunks, "doc text")
        # Mismatched count → no context prepended
        assert result == ["chunk 1", "chunk 2", "chunk 3"]

    @patch("utils.contextual.httpx")
    @patch("utils.contextual.config")
    def test_markdown_code_block_stripped(self, mock_config, mock_httpx):
        """LLM responses wrapped in ```json code blocks are handled."""
        mock_config.ENABLE_CONTEXTUAL_CHUNKS = True
        mock_config.CONTEXTUAL_CHUNKS_MODEL = "test-model"
        mock_config.BIFROST_URL = "http://bifrost:8080/v1"

        # Simulate LLM wrapping output in markdown code block
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "choices": [{
                "message": {
                    "content": '```json\n["ctx for chunk 0", "ctx for chunk 1"]\n```'
                },
            }],
        }
        mock_httpx.post.return_value = resp

        from utils.contextual import contextualize_chunks

        chunks = ["chunk 0", "chunk 1"]
        result = contextualize_chunks(chunks, "doc text")
        assert result[0] == "[ctx for chunk 0]\nchunk 0"
        assert result[1] == "[ctx for chunk 1]\nchunk 1"

    @patch("utils.contextual.httpx")
    @patch("utils.contextual.config")
    def test_metadata_passed_to_prompt(self, mock_config, mock_httpx):
        """Filename and domain from metadata are included in the LLM prompt."""
        mock_config.ENABLE_CONTEXTUAL_CHUNKS = True
        mock_config.CONTEXTUAL_CHUNKS_MODEL = "test-model"
        mock_config.BIFROST_URL = "http://bifrost:8080/v1"

        mock_httpx.post.return_value = _mock_response(["ctx"])

        from utils.contextual import contextualize_chunks

        contextualize_chunks(
            ["chunk"], "doc text",
            metadata={"filename": "report.pdf", "domain": "finance"},
        )

        call_args = mock_httpx.post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        prompt = body["messages"][0]["content"]
        assert "report.pdf" in prompt
        assert "finance" in prompt

    @patch("utils.contextual.httpx")
    @patch("utils.contextual.config")
    def test_doc_preview_truncated(self, mock_config, mock_httpx):
        """Full text is truncated to ~3000 chars in the LLM prompt."""
        mock_config.ENABLE_CONTEXTUAL_CHUNKS = True
        mock_config.CONTEXTUAL_CHUNKS_MODEL = "test-model"
        mock_config.BIFROST_URL = "http://bifrost:8080/v1"

        mock_httpx.post.return_value = _mock_response(["ctx"])

        from utils.contextual import contextualize_chunks

        long_text = "x" * 5000
        contextualize_chunks(["chunk"], long_text)

        call_args = mock_httpx.post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        prompt = body["messages"][0]["content"]
        # Should contain truncation marker, not the full 5000 chars
        assert "[... document continues ...]" in prompt
        # Prompt should contain ≤ 3000 chars of original text (plus a few in boilerplate)
        assert prompt.count("x") <= 3100


# ---------------------------------------------------------------------------
# Tests — _generate_contexts (internal helper)
# ---------------------------------------------------------------------------

class TestGenerateContexts:
    """Tests for the internal _generate_contexts function."""

    @patch("utils.contextual.httpx.post")
    @patch("utils.contextual.config")
    def test_json_decode_error_returns_empty(self, mock_config, mock_post):
        """Invalid JSON from LLM returns empty strings."""
        mock_config.CONTEXTUAL_CHUNKS_MODEL = "test-model"
        mock_config.BIFROST_URL = "http://bifrost:8080/v1"

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "choices": [{
                "message": {"content": "not valid json at all"},
            }],
        }
        mock_post.return_value = resp

        from utils.contextual import _generate_contexts

        result = _generate_contexts(["chunk"], "doc preview", "file.txt", "")
        assert result == [""]

    @patch("utils.contextual.httpx.post")
    @patch("utils.contextual.config")
    def test_chunk_preview_truncated(self, mock_config, mock_post):
        """Individual chunk previews are truncated to 300 chars in the prompt."""
        mock_config.CONTEXTUAL_CHUNKS_MODEL = "test-model"
        mock_config.BIFROST_URL = "http://bifrost:8080/v1"

        mock_post.return_value = _mock_response(["ctx"])

        from utils.contextual import _generate_contexts

        # Use a character that won't appear in the prompt boilerplate
        long_chunk = "\u00e9" * 500
        _generate_contexts([long_chunk], "doc preview", "file.txt", "")

        call_args = mock_post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        prompt = body["messages"][0]["content"]
        # Chunk preview should be truncated to 300 chars
        assert prompt.count("\u00e9") == 300
