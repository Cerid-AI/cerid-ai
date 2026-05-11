# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for core.retrieval.hype_index — HyPE question generation + embedding."""

from __future__ import annotations

import pytest

from core.retrieval.hype_index import (
    HyPEPrompt,
    build_hype_doc_id,
    build_hype_metadata,
    embed_hype_prompts,
    generate_hype_prompts,
    hype_collection_name,
)

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

async def _stub_llm_caller_n(n: int):
    """Return a stub LLM caller that generates n numbered questions."""
    async def _caller(messages: list[dict]) -> str:
        # Verify stage param is in the messages (content should reference hype)
        return "\n".join(f"{i+1}. Question number {i+1} about the passage?" for i in range(n))
    return _caller


async def _stub_llm_caller_bad_response(messages: list[dict]) -> str:
    """Return a response that cannot be parsed into questions."""
    return "This is a plain sentence without any numbered questions."


async def _stub_embed_fn(text: str) -> list[float]:
    """Return a deterministic fake embedding."""
    return [float(i % 10) for i in range(384)]


# ---------------------------------------------------------------------------
# generate_hype_prompts
# ---------------------------------------------------------------------------

class TestGenerateHypePrompts:
    @pytest.mark.asyncio
    async def test_returns_n_questions(self):
        caller = await _stub_llm_caller_n(5)
        questions = await generate_hype_prompts(
            "Python type hints allow you to annotate variables and function signatures.",
            n=5,
            llm_caller=caller,
        )
        assert len(questions) == 5
        for q in questions:
            assert isinstance(q, str)
            assert len(q) > 0

    @pytest.mark.asyncio
    async def test_returns_fewer_than_n_when_clipped(self):
        """Extra lines are silently dropped; we only take n."""
        async def _verbose_caller(messages: list[dict]) -> str:
            # Returns 7 lines for n=3 — extra should be dropped.
            return "\n".join(
                f"{i+1}. Question {i+1}?" for i in range(7)
            )

        questions = await generate_hype_prompts(
            "Some content here.",
            n=3,
            llm_caller=_verbose_caller,
        )
        assert len(questions) == 3

    @pytest.mark.asyncio
    async def test_empty_content_returns_empty_list(self):
        async def _caller(messages: list[dict]) -> str:
            raise AssertionError("LLM should not be called for empty content")

        result = await generate_hype_prompts("", n=5, llm_caller=_caller)
        assert result == []

    @pytest.mark.asyncio
    async def test_whitespace_only_content_returns_empty_list(self):
        async def _caller(messages: list[dict]) -> str:
            raise AssertionError("LLM should not be called for blank content")

        result = await generate_hype_prompts("   \t\n", n=5, llm_caller=_caller)
        assert result == []

    @pytest.mark.asyncio
    async def test_malformed_response_raises(self):
        """Fewer than n parseable lines must raise ValueError."""
        with pytest.raises(ValueError, match="parseable question"):
            await generate_hype_prompts(
                "Some content that yields a bad LLM response.",
                n=5,
                llm_caller=_stub_llm_caller_bad_response,
            )

    @pytest.mark.asyncio
    async def test_stage_label_in_llm_call(self):
        """Verify that the stage keyword is used in the default caller.

        Contract test: the production ``default_hype_llm_caller`` must pass
        ``stage="hype_index/generate"`` to ``call_internal_llm``.  We test
        the *default* caller indirectly by checking the call_internal_llm
        invocation.
        """
        from unittest.mock import AsyncMock, patch

        mock_llm = AsyncMock(return_value="1. What is this about?\n2. How does it work?\n3. Why is it useful?\n4. When to use it?\n5. What are the limitations?")
        with patch("core.utils.internal_llm.call_internal_llm", mock_llm):
            from core.retrieval.hype_index import default_hype_llm_caller
            await generate_hype_prompts(
                "Some passage about Python type hints.",
                n=5,
                llm_caller=default_hype_llm_caller,
            )
            assert mock_llm.called
            call_kwargs = mock_llm.call_args[1]
            assert call_kwargs.get("stage") == "hype_index/generate"

    @pytest.mark.asyncio
    async def test_n_must_be_at_least_1(self):
        async def _caller(messages: list[dict]) -> str:
            return "1. Question?"

        with pytest.raises(ValueError):
            await generate_hype_prompts("content", n=0, llm_caller=_caller)

    @pytest.mark.asyncio
    async def test_llm_exception_propagates(self):
        """LLM call exceptions propagate — caller decides to swallow or retry."""
        async def _failing_caller(messages: list[dict]) -> str:
            raise RuntimeError("Ollama not available")

        with pytest.raises(RuntimeError, match="Ollama not available"):
            await generate_hype_prompts(
                "Some content.",
                n=3,
                llm_caller=_failing_caller,
            )


# ---------------------------------------------------------------------------
# embed_hype_prompts
# ---------------------------------------------------------------------------

class TestEmbedHypePrompts:
    @pytest.mark.asyncio
    async def test_returns_one_prompt_per_question(self):
        questions = ["What is type hinting?", "How do you annotate a function?", "What is mypy?"]
        prompts = await embed_hype_prompts(questions, embed_fn=_stub_embed_fn)
        assert len(prompts) == 3
        for p in prompts:
            assert isinstance(p, HyPEPrompt)
            assert p.embedding is not None
            assert len(p.embedding) == 384
            assert p.question in questions

    @pytest.mark.asyncio
    async def test_embed_fn_exception_propagates(self):
        async def _bad_embed(text: str) -> list[float]:
            raise ConnectionError("embedding service down")

        with pytest.raises(ConnectionError):
            await embed_hype_prompts(["Question?"], embed_fn=_bad_embed)

    @pytest.mark.asyncio
    async def test_empty_prompt_list_returns_empty(self):
        prompts = await embed_hype_prompts([], embed_fn=_stub_embed_fn)
        assert prompts == []

    @pytest.mark.asyncio
    async def test_model_stamped_on_prompt(self):
        questions = ["What is RAG?"]
        prompts = await embed_hype_prompts(
            questions, embed_fn=_stub_embed_fn, model="ollama/llama3.1"
        )
        assert prompts[0].model == "ollama/llama3.1"

    @pytest.mark.asyncio
    async def test_generated_at_is_iso_string(self):
        questions = ["What is RAG?"]
        prompts = await embed_hype_prompts(questions, embed_fn=_stub_embed_fn)
        from datetime import datetime
        # Should parse without error.
        ts = datetime.fromisoformat(prompts[0].generated_at)
        assert ts is not None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_hype_collection_name(self):
        assert hype_collection_name("cerid_general") == "cerid_general_hype"
        assert hype_collection_name("cerid_finance") == "cerid_finance_hype"

    def test_build_hype_doc_id(self):
        assert build_hype_doc_id("abc123_chunk_0", 2) == "abc123_chunk_0_hype_2"

    def test_build_hype_metadata(self):
        from datetime import datetime, timezone

        p = HyPEPrompt(
            question="What is this?",
            embedding=[0.1, 0.2],
            generated_at=datetime.now(tz=timezone.utc).isoformat(),
            model="ollama/local",
        )
        meta = build_hype_metadata(
            source_chunk_id="cid",
            source_artifact_id="aid",
            prompt=p,
            question_index=1,
        )
        assert meta["source_chunk_id"] == "cid"
        assert meta["source_artifact_id"] == "aid"
        assert meta["hype_question_index"] == 1
        assert meta["hype_model"] == "ollama/local"
