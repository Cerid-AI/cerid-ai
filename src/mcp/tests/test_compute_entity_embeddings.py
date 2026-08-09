# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for compute_entity_embeddings.py.

Covers:
- Mean-pool + L2-normalise path (two chunk vectors → correct mean)
- model name (JobResult metadata) sourced from config.EMBEDDING_MODEL, not a literal
- Name-embed fallback (entity with no chunk vectors → embed_fn called)
- Skip path (no chunk vectors AND embed_fn unavailable → entity skipped)
- Neo4j-unavailable guard (job returns skipped result)
- _write_embeddings sends a JSON-encoded embedding list (AF-063: no model provenance)
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import numpy as np

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job():
    from app.processor.jobs.compute_entity_embeddings import ComputeEntityEmbeddingsJob
    return ComputeEntityEmbeddingsJob(tenant_id="test")


# ---------------------------------------------------------------------------
# Unit tests: _l2_normalize
# ---------------------------------------------------------------------------

class TestL2Normalize:
    def test_unit_vector_unchanged(self):
        from app.processor.jobs.compute_entity_embeddings import ComputeEntityEmbeddingsJob
        v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        result = ComputeEntityEmbeddingsJob._l2_normalize(v)
        np.testing.assert_allclose(result, [1.0, 0.0, 0.0], atol=1e-6)

    def test_normalises_to_unit_norm(self):
        from app.processor.jobs.compute_entity_embeddings import ComputeEntityEmbeddingsJob
        v = np.array([3.0, 4.0], dtype=np.float32)
        result = ComputeEntityEmbeddingsJob._l2_normalize(v)
        assert abs(float(np.linalg.norm(result)) - 1.0) < 1e-6

    def test_zero_vector_passthrough(self):
        from app.processor.jobs.compute_entity_embeddings import ComputeEntityEmbeddingsJob
        v = np.zeros(4, dtype=np.float32)
        result = ComputeEntityEmbeddingsJob._l2_normalize(v)
        assert result.shape == (4,)
        assert np.allclose(result, [0, 0, 0, 0])


# ---------------------------------------------------------------------------
# Unit tests: _compute_embedding
# ---------------------------------------------------------------------------

class TestComputeEmbedding:
    def test_mean_pools_two_chunks_and_normalises(self):
        """Mean of two known vectors, then L2-normalised."""
        job = _make_job()
        v1 = np.array([1.0, 0.0], dtype=np.float32)
        v2 = np.array([0.0, 1.0], dtype=np.float32)
        chunk_index = {"c1": v1, "c2": v2}
        entity = {"id": "e1", "name": "E1", "chunk_ids": ["c1", "c2"]}

        result = job._compute_embedding(entity, chunk_index, embed_fn=None)

        assert result is not None
        # Mean = [0.5, 0.5]; normalised = [1/sqrt(2), 1/sqrt(2)]
        expected = np.array([0.5, 0.5], dtype=np.float32)
        expected /= np.linalg.norm(expected)
        np.testing.assert_allclose(result, expected, atol=1e-6)

    def test_uses_only_present_chunk_ids(self):
        """Chunk IDs absent from index are silently skipped."""
        job = _make_job()
        v1 = np.array([2.0, 0.0], dtype=np.float32)
        chunk_index = {"c1": v1}
        entity = {"id": "e1", "name": "E1", "chunk_ids": ["c1", "c_missing"]}

        result = job._compute_embedding(entity, chunk_index, embed_fn=None)

        assert result is not None
        # Only c1 used; normalise [2.0, 0.0] → [1.0, 0.0]
        np.testing.assert_allclose(result, [1.0, 0.0], atol=1e-6)

    def test_fallback_to_embed_fn_when_no_chunks(self):
        """No retrievable chunk vectors → embed_fn called with canonical name."""
        job = _make_job()
        fallback_vec = np.array([0.6, 0.8], dtype=np.float32)
        embed_fn = MagicMock(return_value=[fallback_vec])
        entity = {"id": "e1", "name": "Entity Name", "chunk_ids": []}

        result = job._compute_embedding(entity, {}, embed_fn=embed_fn)

        embed_fn.assert_called_once_with(["Entity Name"])
        assert result is not None
        # [0.6, 0.8] is already unit-norm (0.36+0.64=1.0); normalise is a no-op
        assert abs(float(np.linalg.norm(result)) - 1.0) < 1e-6

    def test_skip_when_no_chunks_and_no_embed_fn(self):
        """No chunk vectors and no embed_fn → None (skip)."""
        job = _make_job()
        entity = {"id": "e1", "name": "Entity Name", "chunk_ids": []}
        result = job._compute_embedding(entity, {}, embed_fn=None)
        assert result is None

    def test_skip_when_chunk_ids_missing_from_index_and_no_embed_fn(self):
        """All chunk IDs absent, no embed_fn → None (skip)."""
        job = _make_job()
        entity = {"id": "e1", "name": "E", "chunk_ids": ["ghost1", "ghost2"]}
        result = job._compute_embedding(entity, {}, embed_fn=None)
        assert result is None

    def test_embed_fn_exception_falls_through_to_skip(self):
        """If embed_fn raises, fall through to skip, don't propagate."""
        job = _make_job()
        embed_fn = MagicMock(side_effect=RuntimeError("sidecar down"))
        entity = {"id": "e1", "name": "Oops", "chunk_ids": []}
        result = job._compute_embedding(entity, {}, embed_fn=embed_fn)
        assert result is None


# ---------------------------------------------------------------------------
# Unit tests: embedding_model sourced from config
# ---------------------------------------------------------------------------

class TestModelNameFromConfig:
    def test_embedding_model_from_settings_not_literal(self):
        """The job must read config.EMBEDDING_MODEL at runtime, not a hardcoded literal.

        Monkeypatches config.EMBEDDING_MODEL to a distinctive sentinel, runs the
        job end-to-end against mocked Chroma+Neo4j, and asserts the model name
        reported in the JobResult metadata equals the sentinel — not the default
        model name. (AF-063: the model name is no longer stamped on the Entity
        node, so it is asserted via the job result, not the Neo4j write.)
        """
        import config as cfg

        sentinel = "sentinel-embed-model-xyz"
        original = cfg.EMBEDDING_MODEL

        job = _make_job()

        v1 = np.array([1.0, 0.0], dtype=np.float32)
        entity_rows = [{"id": "cfg_test_ent", "name": "Config Test", "chunk_ids": ["ck1"]}]
        chunk_index = {"ck1": v1}
        written_calls: list[list[dict]] = []

        def fake_write(driver: object, rows: list[dict]) -> None:
            written_calls.append(rows)

        neo4j_driver = MagicMock()

        async def _test():
            async def noop_progress(_pct: float) -> None:
                pass

            cfg.EMBEDDING_MODEL = sentinel
            try:
                with patch("app.deps.get_neo4j", return_value=neo4j_driver), \
                     patch.object(job, "_fetch_entities_with_chunks", return_value=entity_rows), \
                     patch.object(job, "_open_collections", return_value=(MagicMock(), [MagicMock()])), \
                     patch.object(job, "_fetch_chunk_embeddings", return_value=chunk_index), \
                     patch.object(job, "_get_embed_fn", return_value=None), \
                     patch.object(job, "_write_embeddings", side_effect=fake_write):
                    result = await job.run(noop_progress)
            finally:
                cfg.EMBEDDING_MODEL = original

            assert result.metadata["model"] == sentinel
            assert len(written_calls) == 1

        asyncio.run(_test())


# ---------------------------------------------------------------------------
# Integration-style tests: run() method
#
# Strategy: patch the job's own private methods (_fetch_entities_with_chunks,
# _open_collections, _fetch_chunk_embeddings, _get_embed_fn, _write_embeddings)
# at the class level so the async run() logic is tested without fighting the
# thread executor / Neo4j session mock layering.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Unit tests: _fetch_chunk_embeddings — numpy-safe extraction
# ---------------------------------------------------------------------------

class TestFetchChunkEmbeddings:
    def test_handles_numpy_ndarray_embeddings(self):
        """Chroma returns embeddings as ndarray; truthiness check must not raise."""
        job = _make_job()
        emb_array = np.array([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]], dtype=np.float32)
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["chunk_a", "chunk_b"],
            "embeddings": emb_array,
        }

        result = job._fetch_chunk_embeddings([mock_collection], ["chunk_a", "chunk_b"])

        assert set(result.keys()) == {"chunk_a", "chunk_b"}
        np.testing.assert_allclose(result["chunk_a"], [0.1, 0.2, 0.3], atol=1e-6)
        np.testing.assert_allclose(result["chunk_b"], [0.4, 0.5, 0.6], atol=1e-6)

    def test_handles_empty_numpy_array_embeddings(self):
        """Empty ndarray from Chroma must not raise ValueError."""
        job = _make_job()
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": [],
            "embeddings": np.array([], dtype=np.float32),
        }

        result = job._fetch_chunk_embeddings([mock_collection], ["chunk_x"])
        assert result == {}

    def test_handles_none_embeddings(self):
        """None embeddings field falls back to empty dict."""
        job = _make_job()
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": [],
            "embeddings": None,
        }

        result = job._fetch_chunk_embeddings([mock_collection], ["chunk_y"])
        assert result == {}


class TestRunMethod:
    def test_run_skips_when_neo4j_unavailable(self):
        """If get_neo4j() returns None, job returns status=skipped."""
        job = _make_job()

        async def _test():
            progress_calls: list[float] = []

            async def capture_progress(pct: float) -> None:
                progress_calls.append(pct)

            with patch("app.deps.get_neo4j", return_value=None):
                result = await job.run(capture_progress)

            assert result.metadata["status"] == "skipped"
            assert result.metadata["reason"] == "neo4j unavailable"

        asyncio.run(_test())

    def test_run_writes_mean_pooled_embedding_and_model_from_settings(self):
        """End-to-end run: two chunk vectors → written embedding matches mean-pool;
        JobResult metadata model matches config.EMBEDDING_MODEL (AF-063: model is
        no longer written to the Entity node)."""
        import config as cfg

        job = _make_job()

        v1 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        v2 = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        expected_mean = (v1 + v2) / 2.0
        expected_norm = (expected_mean / np.linalg.norm(expected_mean)).astype(np.float32)

        entity_rows = [{"id": "ent1", "name": "Test Entity", "chunk_ids": ["c1", "c2"]}]
        chunk_index = {"c1": v1, "c2": v2}
        written_calls: list[list[dict]] = []

        def fake_write(driver: object, rows: list[dict]) -> None:
            written_calls.append(rows)

        neo4j_driver = MagicMock()

        async def _test():
            progress_calls: list[float] = []

            async def capture_progress(pct: float) -> None:
                progress_calls.append(pct)

            with patch("app.deps.get_neo4j", return_value=neo4j_driver), \
                 patch.object(job, "_fetch_entities_with_chunks", return_value=entity_rows), \
                 patch.object(job, "_open_collections", return_value=(MagicMock(), [MagicMock()])), \
                 patch.object(job, "_fetch_chunk_embeddings", return_value=chunk_index), \
                 patch.object(job, "_get_embed_fn", return_value=None), \
                 patch.object(job, "_write_embeddings", side_effect=fake_write):
                result = await job.run(capture_progress)

            assert result.metadata["written"] == 1
            assert result.metadata["model"] == cfg.EMBEDDING_MODEL
            assert len(written_calls) == 1
            rows = written_calls[0]
            assert len(rows) == 1
            row = rows[0]
            assert row["id"] == "ent1"

            stored_vec = np.array(json.loads(row["embedding"]), dtype=np.float32)
            np.testing.assert_allclose(stored_vec, expected_norm, atol=1e-5)

        asyncio.run(_test())

    def test_run_name_embed_fallback_when_no_chunk_vectors(self):
        """Entity with no retrievable chunk vectors falls back to name embedding."""

        job = _make_job()

        fallback_vec = np.array([0.6, 0.0, 0.8], dtype=np.float32)
        # Already unit-norm: sqrt(0.36+0+0.64)=1.0

        entity_rows = [{"id": "ent2", "name": "Missing Chunks Entity", "chunk_ids": ["ghost1"]}]
        embed_fn = MagicMock(return_value=[fallback_vec])
        written_calls: list[list[dict]] = []

        def fake_write(driver: object, rows: list[dict]) -> None:
            written_calls.append(rows)

        neo4j_driver = MagicMock()

        async def _test():
            progress_calls: list[float] = []

            async def capture_progress(pct: float) -> None:
                progress_calls.append(pct)

            # chunk_index has no entries for ghost1 → falls back to embed_fn
            with patch("app.deps.get_neo4j", return_value=neo4j_driver), \
                 patch.object(job, "_fetch_entities_with_chunks", return_value=entity_rows), \
                 patch.object(job, "_open_collections", return_value=(MagicMock(), [MagicMock()])), \
                 patch.object(job, "_fetch_chunk_embeddings", return_value={}), \
                 patch.object(job, "_get_embed_fn", return_value=embed_fn), \
                 patch.object(job, "_write_embeddings", side_effect=fake_write):
                result = await job.run(capture_progress)

            assert result.metadata["written"] == 1
            assert len(written_calls) == 1
            stored_vec = np.array(json.loads(written_calls[0][0]["embedding"]), dtype=np.float32)
            expected_norm = fallback_vec / np.linalg.norm(fallback_vec)
            np.testing.assert_allclose(stored_vec, expected_norm, atol=1e-5)

        asyncio.run(_test())

    def test_run_skips_entity_when_no_vectors_and_no_embed_fn(self):
        """Entity with no chunk vectors and unavailable embed_fn is silently skipped."""
        job = _make_job()

        entity_rows = [{"id": "ent3", "name": "No Embedding Entity", "chunk_ids": []}]
        written_calls: list[list[dict]] = []

        def fake_write(driver: object, rows: list[dict]) -> None:
            written_calls.append(rows)

        neo4j_driver = MagicMock()

        async def _test():
            progress_calls: list[float] = []

            async def capture_progress(pct: float) -> None:
                progress_calls.append(pct)

            with patch("app.deps.get_neo4j", return_value=neo4j_driver), \
                 patch.object(job, "_fetch_entities_with_chunks", return_value=entity_rows), \
                 patch.object(job, "_open_collections", return_value=(MagicMock(), [MagicMock()])), \
                 patch.object(job, "_fetch_chunk_embeddings", return_value={}), \
                 patch.object(job, "_get_embed_fn", return_value=None), \
                 patch.object(job, "_write_embeddings", side_effect=fake_write):
                result = await job.run(capture_progress)

            assert result.metadata["written"] == 0
            assert result.metadata["skipped"] == 1
            assert len(written_calls) == 0

        asyncio.run(_test())
