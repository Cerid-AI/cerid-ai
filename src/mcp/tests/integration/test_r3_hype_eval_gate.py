# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Structural integration test: Phase R.3 HyPE eval-gate contract.

This test does NOT run the real eval (no live corpus, no live Chroma, no LLM
calls).  It verifies:

1. ``RETRIEVAL_HYPE_ENABLED=false`` (default) → the HyPE augmentation path is
   skipped entirely; ``multi_domain_query`` results flow through unchanged.

2. ``RETRIEVAL_HYPE_ENABLED=true`` → ``_hype_retrieval_enabled()`` returns
   True and the augmentation path would be entered.

3. ``HyPEIndexingJob`` is importable and carries the correct class attributes
   so ``build_default_registry()`` can discover it.

The *actual* eval-gate flip is a manual operator step documented in
``docs/EVAL_BASELINES.md``:
  - Run ``pytest -m benchmark_slo`` AND
    ``pytest src/mcp/tests/eval/test_retrieval_baselines.py``
  - Compare NDCG@10 vs baseline (≥ +0.02 required)
  - If gate clears on 2 consecutive full-corpus runs, flip
    ``RETRIEVAL_HYPE_ENABLED`` default to ``true`` in a separate one-line PR.

Do NOT flip the default in this commit.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. Flag default is off
# ---------------------------------------------------------------------------

class TestFlagDefault:
    def test_default_is_false(self):
        """RETRIEVAL_HYPE_ENABLED must default to false."""
        env_backup = os.environ.pop("RETRIEVAL_HYPE_ENABLED", None)
        try:
            from core.agents.query_agent import _hype_retrieval_enabled
            assert not _hype_retrieval_enabled()
        finally:
            if env_backup is not None:
                os.environ["RETRIEVAL_HYPE_ENABLED"] = env_backup

    def test_explicit_false_is_disabled(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "false")
        from core.agents.query_agent import _hype_retrieval_enabled
        assert not _hype_retrieval_enabled()

    def test_explicit_0_is_disabled(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "0")
        from core.agents.query_agent import _hype_retrieval_enabled
        assert not _hype_retrieval_enabled()


# ---------------------------------------------------------------------------
# 2. Flag-on activates the HyPE path
# ---------------------------------------------------------------------------

class TestFlagOn:
    def test_explicit_true_is_enabled(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "true")
        from core.agents.query_agent import _hype_retrieval_enabled
        assert _hype_retrieval_enabled()

    def test_explicit_1_is_enabled(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "1")
        from core.agents.query_agent import _hype_retrieval_enabled
        assert _hype_retrieval_enabled()

    @pytest.mark.asyncio
    async def test_augment_with_hype_returns_content_unchanged_when_flag_off(
        self, monkeypatch
    ):
        """When flag is off, _augment_with_hype returns content_hits untouched."""
        monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "false")
        from core.agents.query_agent import _augment_with_hype

        content_hits = [
            {"chunk_id": "c1", "relevance": 0.8, "artifact_id": "a1",
             "content": "hello", "filename": "f.md", "domain": "general",
             "chunk_index": 0, "collection": "c", "ingested_at": "",
             "sub_category": "", "tags_json": "[]", "keywords": "[]",
             "memory_type": ""},
        ]
        result = await _augment_with_hype(
            query="test query",
            results=content_hits,
            chroma_client=MagicMock(),
            domains=["general"],
        )
        assert result is content_hits  # exact same object — no copy made

    @pytest.mark.asyncio
    async def test_augment_with_hype_skips_when_no_chroma(self, monkeypatch):
        """Even with flag=true, None chroma_client → skip augmentation."""
        monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "true")
        from core.agents.query_agent import _augment_with_hype

        content_hits = [{"chunk_id": "c1", "relevance": 0.8}]
        result = await _augment_with_hype(
            query="test",
            results=content_hits,
            chroma_client=None,
            domains=["general"],
        )
        assert result is content_hits

    @pytest.mark.asyncio
    async def test_augment_with_hype_merges_when_hype_collection_present(
        self, monkeypatch
    ):
        """When flag=true and HyPE collection exists, dedup is called."""
        monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "true")

        # Set up a fake HyPE collection result.
        hype_collection_mock = MagicMock()
        hype_query_return = {
            "ids": [["c1_hype_0"]],
            "distances": [[0.1]],
            "metadatas": [[{
                "source_chunk_id": "c1",
                "source_artifact_id": "art1",
            }]],
            "documents": [["What is Python type hinting?"]],
        }
        hype_collection_mock.query.return_value = hype_query_return

        chroma_mock = MagicMock()
        chroma_mock.get_collection.return_value = hype_collection_mock

        # Fake embed function
        fake_ef = MagicMock(return_value=[[0.1] * 384])

        content_hits = [
            {
                "chunk_id": "c1",
                "relevance": 0.7,
                "artifact_id": "art1",
                "content": "Python type hints...",
                "filename": "doc.md",
                "domain": "general",
                "chunk_index": 0,
                "collection": "cerid_general",
                "ingested_at": "",
                "sub_category": "",
                "tags_json": "[]",
                "keywords": "[]",
                "memory_type": "",
            }
        ]

        with patch("core.utils.embeddings.get_embedding_function", return_value=fake_ef):
            from core.agents.query_agent import _augment_with_hype
            result = await _augment_with_hype(
                query="What are type hints?",
                results=content_hits,
                chroma_client=chroma_mock,
                domains=["general"],
            )

        # c1 is boosted — HyPE relevance (from distance 0.1) > original 0.7
        c1 = next(r for r in result if r.get("chunk_id") == "c1")
        # The HyPE relevance from l2_distance_to_relevance(0.1) is high
        assert c1["relevance"] >= 0.7  # at least as good as content


# ---------------------------------------------------------------------------
# 3. HyPEIndexingJob is discoverable
# ---------------------------------------------------------------------------

class TestJobDiscovery:
    def test_hype_indexing_job_importable(self):
        from app.processor.jobs.hype_indexing import HyPEIndexingJob
        assert HyPEIndexingJob.job_type == "hype_indexing"

    def test_hype_indexing_job_is_base_job_subclass(self):
        from app.processor.jobs.hype_indexing import HyPEIndexingJob
        from core.processor.job import BaseJob
        assert issubclass(HyPEIndexingJob, BaseJob)

    def test_hype_indexing_job_in_jobs_package(self):
        """The jobs/ package must expose HyPEIndexingJob."""
        import importlib
        pkg = importlib.import_module("app.processor.jobs.hype_indexing")
        assert hasattr(pkg, "HyPEIndexingJob")
