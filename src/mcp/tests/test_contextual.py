# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for utils/contextual.py — LLM-generated contextual chunk enrichment.

The contextualization call routes through ``core.utils.internal_llm.call_internal_llm``
(provider + model chosen by the per-stage registry, ``stage="contextual_chunks"``),
so these tests patch THAT call site rather than the OpenRouter client underneath —
the code is provider-agnostic and so are the tests.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch


def _config(
    *,
    enabled: bool = True,
    batch_size: int = 5,
    max_per_artifact: int = 200,
    timeout: float = 30.0,
) -> MagicMock:
    """A MagicMock ``config`` with the contextual knobs pinned to real values.

    A bare MagicMock would hand back child mocks for the cost-guard knobs and
    ``int()/float()`` of a mock raises — so every test sets concrete numbers.
    """
    cfg = MagicMock()
    cfg.ENABLE_CONTEXTUAL_CHUNKS = enabled
    cfg.CONTEXTUAL_CHUNK_BATCH_SIZE = batch_size
    cfg.CONTEXTUAL_CHUNKS_MAX_PER_ARTIFACT = max_per_artifact
    cfg.CONTEXTUAL_CHUNK_LLM_TIMEOUT = timeout
    return cfg


# ---------------------------------------------------------------------------
# Tests — contextualize_chunks
# ---------------------------------------------------------------------------


class TestContextualizeChunks:
    """Tests for the main contextualize_chunks function."""

    @patch("core.utils.contextual.config")
    def test_disabled_returns_original(self, mock_config):
        """Flag OFF → chunks pass through byte-identical (regression guard)."""
        mock_config.ENABLE_CONTEXTUAL_CHUNKS = False
        from core.utils.contextual import contextualize_chunks

        chunks = ["chunk one", "chunk two"]
        result = contextualize_chunks(chunks, "full doc text")
        assert result == chunks
        # Same object contents, no prefixes introduced.
        assert all("[" not in c[:1] for c in result)

    @patch("core.utils.contextual.config")
    def test_empty_chunks_returns_empty(self, mock_config):
        """Empty input returns empty output."""
        mock_config.ENABLE_CONTEXTUAL_CHUNKS = True
        from core.utils.contextual import contextualize_chunks

        result = contextualize_chunks([], "full text")
        assert result == []

    @patch("core.utils.internal_llm.call_internal_llm", new_callable=AsyncMock)
    @patch("core.utils.contextual.config", new_callable=lambda: _config())
    def test_successful_enrichment(self, mock_config, mock_llm):
        """Successful LLM call prepends context to each chunk."""
        contexts = ["revenue discussion in Q3 report", "API auth setup guide"]
        mock_llm.return_value = json.dumps(contexts)

        from core.utils.contextual import contextualize_chunks

        chunks = ["Revenue increased 15%", "Set up API key in config.yaml"]
        result = contextualize_chunks(chunks, "Full document text here", {"filename": "report.pdf"})

        assert len(result) == 2
        assert result[0] == "[revenue discussion in Q3 report]\nRevenue increased 15%"
        assert result[1] == "[API auth setup guide]\nSet up API key in config.yaml"

    @patch("core.utils.internal_llm.call_internal_llm", new_callable=AsyncMock)
    @patch("core.utils.contextual.config", new_callable=lambda: _config())
    def test_batching(self, mock_config, mock_llm):
        """Chunks are processed in batches of CONTEXTUAL_CHUNK_BATCH_SIZE."""
        chunks = [f"chunk {i}" for i in range(7)]
        batch1 = [f"ctx {i}" for i in range(5)]
        batch2 = [f"ctx {i}" for i in range(5, 7)]
        mock_llm.side_effect = [json.dumps(batch1), json.dumps(batch2)]

        from core.utils.contextual import contextualize_chunks

        result = contextualize_chunks(chunks, "doc text")
        assert len(result) == 7
        assert mock_llm.call_count == 2  # 7 chunks / batch 5 → 2 calls
        assert result[0] == "[ctx 0]\nchunk 0"
        assert result[6] == "[ctx 6]\nchunk 6"

    @patch("core.utils.contextual.log_swallowed_error")
    @patch("core.utils.internal_llm.call_internal_llm", new_callable=AsyncMock)
    @patch("core.utils.contextual.config", new_callable=lambda: _config())
    def test_llm_error_returns_originals_and_logs(self, mock_config, mock_llm, mock_log):
        """On LLM transport error: chunks pass through un-prefixed AND the
        swallow is recorded via log_swallowed_error (mechanical rule)."""
        import httpx as real_httpx
        mock_llm.side_effect = real_httpx.ConnectError("Connection refused")

        from core.utils.contextual import contextualize_chunks

        chunks = ["chunk one", "chunk two"]
        result = contextualize_chunks(chunks, "doc text")
        assert result == chunks  # graceful degrade — never a failed ingest
        assert mock_log.called

    @patch("core.utils.contextual.log_swallowed_error")
    @patch("core.utils.internal_llm.call_internal_llm", new_callable=AsyncMock)
    @patch("core.utils.contextual.config", new_callable=lambda: _config(timeout=0.05))
    def test_per_call_timeout_skips_gracefully(self, mock_config, mock_llm, mock_log):
        """A call slower than CONTEXTUAL_CHUNK_LLM_TIMEOUT is abandoned; the
        affected chunks are ingested un-prefixed and the timeout is logged."""

        async def _slow(*_args, **_kwargs):
            await asyncio.sleep(5)
            return json.dumps(["never"])

        mock_llm.side_effect = _slow

        from core.utils.contextual import contextualize_chunks

        chunks = ["chunk one", "chunk two"]
        result = contextualize_chunks(chunks, "doc text")
        assert result == chunks  # timed out → no prefix
        assert mock_log.called

    @patch("core.utils.internal_llm.call_internal_llm", new_callable=AsyncMock)
    @patch("core.utils.contextual.config", new_callable=lambda: _config(max_per_artifact=2))
    def test_cost_cap_bounds_llm_calls(self, mock_config, mock_llm):
        """Only the first CONTEXTUAL_CHUNKS_MAX_PER_ARTIFACT chunks pay an LLM
        call; the tail passes through un-prefixed (bounds ingest cost)."""
        mock_llm.return_value = json.dumps(["c0", "c1"])

        from core.utils.contextual import contextualize_chunks

        chunks = [f"chunk {i}" for i in range(5)]
        result = contextualize_chunks(chunks, "doc text")

        assert len(result) == 5
        # cap=2 → a single batch of 2 → exactly one LLM call
        assert mock_llm.call_count == 1
        assert result[0] == "[c0]\nchunk 0"
        assert result[1] == "[c1]\nchunk 1"
        # Tail beyond the cap is un-prefixed and identical to the input.
        assert result[2:] == ["chunk 2", "chunk 3", "chunk 4"]

    @patch("core.utils.internal_llm.call_internal_llm", new_callable=AsyncMock)
    @patch("core.utils.contextual.config", new_callable=lambda: _config(max_per_artifact=0))
    def test_cap_disabled_enriches_all(self, mock_config, mock_llm):
        """max_per_artifact <= 0 disables the cap — all chunks enriched."""
        mock_llm.return_value = json.dumps([f"c{i}" for i in range(3)])

        from core.utils.contextual import contextualize_chunks

        chunks = [f"chunk {i}" for i in range(3)]
        result = contextualize_chunks(chunks, "doc text")
        assert result == ["[c0]\nchunk 0", "[c1]\nchunk 1", "[c2]\nchunk 2"]

    @patch("core.utils.internal_llm.call_internal_llm", new_callable=AsyncMock)
    @patch("core.utils.contextual.config", new_callable=lambda: _config())
    def test_mismatched_count_returns_no_context(self, mock_config, mock_llm):
        """When the LLM returns the wrong number of contexts, chunks pass through."""
        mock_llm.return_value = json.dumps(["only one context"])

        from core.utils.contextual import contextualize_chunks

        chunks = ["chunk 1", "chunk 2", "chunk 3"]
        result = contextualize_chunks(chunks, "doc text")
        assert result == ["chunk 1", "chunk 2", "chunk 3"]

    @patch("core.utils.internal_llm.call_internal_llm", new_callable=AsyncMock)
    @patch("core.utils.contextual.config", new_callable=lambda: _config())
    def test_markdown_code_block_stripped(self, mock_config, mock_llm):
        """LLM responses wrapped in ```json code blocks are handled."""
        mock_llm.return_value = '```json\n["ctx for chunk 0", "ctx for chunk 1"]\n```'

        from core.utils.contextual import contextualize_chunks

        chunks = ["chunk 0", "chunk 1"]
        result = contextualize_chunks(chunks, "doc text")
        assert result[0] == "[ctx for chunk 0]\nchunk 0"
        assert result[1] == "[ctx for chunk 1]\nchunk 1"

    @patch("core.utils.internal_llm.call_internal_llm", new_callable=AsyncMock)
    @patch("core.utils.contextual.config", new_callable=lambda: _config())
    def test_metadata_passed_to_prompt(self, mock_config, mock_llm):
        """Filename and domain from metadata are included in the LLM prompt."""
        mock_llm.return_value = json.dumps(["ctx"])

        from core.utils.contextual import contextualize_chunks

        contextualize_chunks(
            ["chunk"], "doc text",
            metadata={"filename": "report.pdf", "domain": "finance"},
        )

        # First positional arg to call_internal_llm is the messages list.
        messages = mock_llm.call_args[0][0]
        prompt = messages[0]["content"]
        assert "report.pdf" in prompt
        assert "finance" in prompt

    @patch("core.utils.internal_llm.call_internal_llm", new_callable=AsyncMock)
    @patch("core.utils.contextual.config", new_callable=lambda: _config())
    def test_stage_breadcrumb_passed(self, mock_config, mock_llm):
        """The call is attributed to stage='contextual_chunks' (routing + obs)."""
        mock_llm.return_value = json.dumps(["ctx"])

        from core.utils.contextual import contextualize_chunks

        contextualize_chunks(["chunk"], "doc text")
        assert mock_llm.call_args.kwargs["stage"] == "contextual_chunks"

    @patch("core.utils.internal_llm.call_internal_llm", new_callable=AsyncMock)
    @patch("core.utils.contextual.config", new_callable=lambda: _config())
    def test_doc_preview_truncated(self, mock_config, mock_llm):
        """Full text is truncated to ~3000 chars in the LLM prompt."""
        mock_llm.return_value = json.dumps(["ctx"])

        from core.utils.contextual import contextualize_chunks

        long_text = "x" * 5000
        contextualize_chunks(["chunk"], long_text)

        messages = mock_llm.call_args[0][0]
        prompt = messages[0]["content"]
        assert "[... document continues ...]" in prompt
        assert prompt.count("x") <= 3100


# ---------------------------------------------------------------------------
# Tests — _generate_contexts (internal helper)
# ---------------------------------------------------------------------------


class TestGenerateContexts:
    """Tests for the internal _generate_contexts function."""

    @patch("core.utils.contextual.log_swallowed_error")
    @patch("core.utils.internal_llm.call_internal_llm", new_callable=AsyncMock)
    @patch("core.utils.contextual.config", new_callable=lambda: _config())
    def test_json_decode_error_returns_empty_and_logs(self, mock_config, mock_llm, mock_log):
        """Invalid JSON from the LLM returns empty strings AND is logged."""
        mock_llm.return_value = "not valid json at all"

        from core.utils.contextual import _generate_contexts

        result = _generate_contexts(["chunk"], "doc preview", "file.txt", "")
        assert result == [""]
        assert mock_log.called

    @patch("core.utils.internal_llm.call_internal_llm", new_callable=AsyncMock)
    @patch("core.utils.contextual.config", new_callable=lambda: _config())
    def test_chunk_preview_truncated(self, mock_config, mock_llm):
        """Individual chunk previews are truncated to 300 chars in the prompt."""
        mock_llm.return_value = json.dumps(["ctx"])

        from core.utils.contextual import _generate_contexts

        long_chunk = "é" * 500
        _generate_contexts([long_chunk], "doc preview", "file.txt", "")

        messages = mock_llm.call_args[0][0]
        prompt = messages[0]["content"]
        assert prompt.count("é") == 300
