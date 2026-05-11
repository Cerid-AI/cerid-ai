# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for app.services.hype_indexer — orchestration layer."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _stub_embed_fn(text: str) -> list[float]:
    return [0.1] * 384


def _make_chroma_mock():
    """Return a minimal ChromaDB mock that satisfies hype_indexer's needs."""
    collection_mock = MagicMock()
    collection_mock.upsert = MagicMock()
    client_mock = MagicMock()
    client_mock.get_or_create_collection.return_value = collection_mock
    return client_mock, collection_mock


# ---------------------------------------------------------------------------
# Flag-off path
# ---------------------------------------------------------------------------

class TestFlagOff:
    @pytest.mark.asyncio
    async def test_returns_disabled_when_flag_false(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "false")
        from app.services.hype_indexer import index_chunk_with_hype

        result = await index_chunk_with_hype(
            chunk_id="chunk1",
            content="Some content here.",
            collection_name="cerid_general",
            artifact_id="art1",
        )
        assert result == {"enabled": False}

    @pytest.mark.asyncio
    async def test_no_llm_call_when_disabled(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "false")

        mock_llm = AsyncMock()
        with patch("core.retrieval.hype_index.default_hype_llm_caller", mock_llm):
            from app.services.hype_indexer import index_chunk_with_hype
            await index_chunk_with_hype(
                chunk_id="c1",
                content="content",
                collection_name="cerid_general",
                artifact_id="a1",
            )
            mock_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_embed_call_when_disabled(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "false")
        called = []

        async def _embed(t: str) -> list[float]:
            called.append(t)
            return [0.0] * 384

        from app.services.hype_indexer import index_chunk_with_hype
        await index_chunk_with_hype(
            chunk_id="c1",
            content="content",
            collection_name="cerid_general",
            artifact_id="a1",
            embed_fn=_embed,
        )
        assert called == []

    @pytest.mark.asyncio
    async def test_flag_default_is_off(self):
        """Env var not set → disabled."""
        env_backup = os.environ.pop("RETRIEVAL_HYPE_ENABLED", None)
        try:
            from app.services.hype_indexer import _hype_enabled
            assert not _hype_enabled()
        finally:
            if env_backup is not None:
                os.environ["RETRIEVAL_HYPE_ENABLED"] = env_backup


# ---------------------------------------------------------------------------
# Flag-on path
# ---------------------------------------------------------------------------

class TestFlagOn:
    @pytest.mark.asyncio
    async def test_returns_metadata_with_counts(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "true")

        chroma_mock, coll_mock = _make_chroma_mock()

        llm_response = "\n".join(f"{i+1}. Question {i+1}?" for i in range(5))
        mock_llm = AsyncMock(return_value=llm_response)

        with (
            patch("core.retrieval.hype_index.default_hype_llm_caller", mock_llm),
        ):
            from app.services.hype_indexer import index_chunk_with_hype
            result = await index_chunk_with_hype(
                chunk_id="c1",
                content="Python type hints allow annotating variables.",
                collection_name="cerid_general",
                artifact_id="art1",
                chroma=chroma_mock,
                embed_fn=_stub_embed_fn,
            )

        assert result["enabled"] is True
        assert result["n_prompts"] == 5
        assert result["total_tokens"] > 0

    @pytest.mark.asyncio
    async def test_chroma_upsert_called(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "true")

        chroma_mock, coll_mock = _make_chroma_mock()
        llm_response = "1. Q1?\n2. Q2?\n3. Q3?"
        mock_llm = AsyncMock(return_value=llm_response)

        with patch("core.retrieval.hype_index.default_hype_llm_caller", mock_llm):
            from app.services.hype_indexer import index_chunk_with_hype
            await index_chunk_with_hype(
                chunk_id="c1",
                content="Some content.",
                collection_name="cerid_general",
                artifact_id="art1",
                chroma=chroma_mock,
                embed_fn=_stub_embed_fn,
                n=3,
            )

        coll_mock.upsert.assert_called_once()
        call_kwargs = coll_mock.upsert.call_args[1]
        assert len(call_kwargs["ids"]) == 3
        assert len(call_kwargs["documents"]) == 3
        assert len(call_kwargs["embeddings"]) == 3

    @pytest.mark.asyncio
    async def test_empty_content_returns_zero_prompts(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "true")
        chroma_mock, _ = _make_chroma_mock()

        from app.services.hype_indexer import index_chunk_with_hype
        result = await index_chunk_with_hype(
            chunk_id="c1",
            content="",
            collection_name="cerid_general",
            artifact_id="art1",
            chroma=chroma_mock,
            embed_fn=_stub_embed_fn,
        )
        assert result["enabled"] is True
        assert result["n_prompts"] == 0

    @pytest.mark.asyncio
    async def test_hype_collection_name_is_parallel(self, monkeypatch):
        """Chroma must be asked for the parallel _hype collection."""
        monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "true")
        chroma_mock, _ = _make_chroma_mock()

        llm_response = "1. Q1?\n2. Q2?\n3. Q3?"
        mock_llm = AsyncMock(return_value=llm_response)

        with patch("core.retrieval.hype_index.default_hype_llm_caller", mock_llm):
            from app.services.hype_indexer import index_chunk_with_hype
            await index_chunk_with_hype(
                chunk_id="c1",
                content="Some content.",
                collection_name="cerid_general",
                artifact_id="art1",
                chroma=chroma_mock,
                embed_fn=_stub_embed_fn,
                n=3,
            )

        chroma_mock.get_or_create_collection.assert_called_once_with("cerid_general_hype")
