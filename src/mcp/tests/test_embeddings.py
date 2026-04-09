# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for utils/embeddings.py and deps._EmbeddingAwareClient."""

import math
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# OnnxEmbeddingFunction unit tests
# ---------------------------------------------------------------------------


class TestOnnxEmbeddingFunction:
    def test_empty_input_returns_empty(self):
        from utils.embeddings import OnnxEmbeddingFunction

        ef = OnnxEmbeddingFunction(model_id="test-model")
        assert ef([]) == []

    @patch("core.utils.embeddings.hf_hub_download")
    @patch("core.utils.embeddings.ort.InferenceSession")
    @patch("core.utils.embeddings.Tokenizer.from_file")
    def test_mean_pooling_and_normalization(self, mock_tok_cls, mock_session_cls, mock_dl):
        """Verify mean pooling + L2 normalization produces unit-length vectors."""
        from utils.embeddings import OnnxEmbeddingFunction

        mock_dl.return_value = "/fake/model.onnx"

        # Mock tokenizer
        mock_tok = MagicMock()
        encoding = MagicMock()
        encoding.ids = [101, 2003, 102]
        encoding.attention_mask = [1, 1, 1]
        encoding.type_ids = [0, 0, 0]
        mock_tok.encode_batch.return_value = [encoding]
        mock_tok_cls.return_value = mock_tok

        # Mock ONNX session — output shape (1, 3, 4) → 3 tokens, 4 dims
        hidden = np.array([[[1.0, 0.0, 0.0, 0.0],
                            [0.0, 1.0, 0.0, 0.0],
                            [0.0, 0.0, 1.0, 0.0]]], dtype=np.float32)
        mock_session = MagicMock()
        mock_session.run.return_value = [hidden]
        mock_session.get_inputs.return_value = [
            MagicMock(name="input_ids"),
            MagicMock(name="attention_mask"),
        ]
        mock_session.get_outputs.return_value = [MagicMock(shape=[None, None, 4])]
        mock_session_cls.return_value = mock_session

        ef = OnnxEmbeddingFunction(model_id="test-model")
        result = ef(["test sentence"])

        assert len(result) == 1
        vec = np.array(result[0])
        # Mean of [[1,0,0,0],[0,1,0,0],[0,0,1,0]] = [1/3, 1/3, 1/3, 0]
        # L2 norm → unit vector
        assert abs(np.linalg.norm(vec) - 1.0) < 1e-5, "Output should be L2-normalized"
        assert vec[3] == pytest.approx(0.0, abs=1e-5), "Fourth dim should be ~0"

    @patch("core.utils.embeddings.hf_hub_download")
    @patch("core.utils.embeddings.ort.InferenceSession")
    @patch("core.utils.embeddings.Tokenizer.from_file")
    def test_matryoshka_truncation(self, mock_tok_cls, mock_session_cls, mock_dl):
        """Matryoshka truncation reduces dimensions and re-normalizes."""
        from utils.embeddings import OnnxEmbeddingFunction

        mock_dl.return_value = "/fake/model.onnx"

        mock_tok = MagicMock()
        encoding = MagicMock()
        encoding.ids = [101, 102]
        encoding.attention_mask = [1, 1]
        encoding.type_ids = [0, 0]
        mock_tok.encode_batch.return_value = [encoding]
        mock_tok_cls.return_value = mock_tok

        # Output 8 dims, truncate to 4
        hidden = np.random.randn(1, 2, 8).astype(np.float32)
        mock_session = MagicMock()
        mock_session.run.return_value = [hidden]
        mock_session.get_inputs.return_value = [MagicMock(name="input_ids")]
        mock_session.get_outputs.return_value = [MagicMock(shape=[None, None, 8])]
        mock_session_cls.return_value = mock_session

        ef = OnnxEmbeddingFunction(model_id="test-model", dimensions=4)
        result = ef(["test"])

        assert len(result[0]) == 4, "Should truncate to 4 dims"
        assert abs(np.linalg.norm(result[0]) - 1.0) < 1e-5, "Re-normalized after truncation"


# ---------------------------------------------------------------------------
# get_embedding_function tests
# ---------------------------------------------------------------------------


class TestGetEmbeddingFunction:
    def test_server_default_returns_none(self):
        """When EMBEDDING_MODEL is all-MiniLM-L6-v2, return None (server handles it)."""
        import utils.embeddings as mod

        # Reset singleton
        mod._embedding_fn = None
        with patch.object(mod.config, "EMBEDDING_MODEL", "all-MiniLM-L6-v2"):
            assert mod.get_embedding_function() is None

    def test_custom_model_returns_function(self):
        """When EMBEDDING_MODEL differs, return an OnnxEmbeddingFunction."""
        import utils.embeddings as mod

        mod._embedding_fn = None
        with patch.object(mod.config, "EMBEDDING_MODEL", "Snowflake/snowflake-arctic-embed-m-v1.5"):
            with patch.object(mod.config, "EMBEDDING_ONNX_FILENAME", "onnx/model.onnx"):
                with patch.object(mod.config, "EMBEDDING_DIMENSIONS", 0):
                    with patch.object(mod.config, "EMBEDDING_MODEL_CACHE_DIR", ""):
                        ef = mod.get_embedding_function()
                        assert ef is not None
                        assert isinstance(ef, mod.OnnxEmbeddingFunction)
        # Clean up
        mod._embedding_fn = None


# ---------------------------------------------------------------------------
# _EmbeddingAwareClient tests
# ---------------------------------------------------------------------------


class TestEmbeddingAwareClient:
    def test_injects_embedding_function(self):
        """Wrapper injects ef when model differs from server default."""
        from app.deps import _EmbeddingAwareClient

        mock_client = MagicMock()
        mock_collection = MagicMock()
        mock_client.get_or_create_collection.return_value = mock_collection

        wrapper = _EmbeddingAwareClient(mock_client)

        fake_ef = MagicMock()
        with patch("utils.embeddings.get_embedding_function", return_value=fake_ef):
            wrapper.get_or_create_collection(name="test_coll")

        call_kwargs = mock_client.get_or_create_collection.call_args[1]
        assert call_kwargs["embedding_function"] is fake_ef

    def test_no_injection_when_server_default(self):
        """Wrapper passes through without ef when using server default model."""
        from app.deps import _EmbeddingAwareClient

        mock_client = MagicMock()
        wrapper = _EmbeddingAwareClient(mock_client)

        with patch("utils.embeddings.get_embedding_function", return_value=None):
            wrapper.get_or_create_collection(name="test_coll")

        call_kwargs = mock_client.get_or_create_collection.call_args[1]
        assert "embedding_function" not in call_kwargs

    def test_passthrough_other_methods(self):
        """Other methods (heartbeat, list_collections, etc.) pass through."""
        from app.deps import _EmbeddingAwareClient

        mock_client = MagicMock()
        mock_client.heartbeat.return_value = 12345
        wrapper = _EmbeddingAwareClient(mock_client)

        assert wrapper.heartbeat() == 12345
        mock_client.heartbeat.assert_called_once()

    def test_get_collection_also_injects(self):
        """get_collection (read path) also gets the embedding function."""
        from app.deps import _EmbeddingAwareClient

        mock_client = MagicMock()
        wrapper = _EmbeddingAwareClient(mock_client)

        fake_ef = MagicMock()
        with patch("utils.embeddings.get_embedding_function", return_value=fake_ef):
            wrapper.get_collection(name="test_coll")

        call_kwargs = mock_client.get_collection.call_args[1]
        assert call_kwargs["embedding_function"] is fake_ef


# ---------------------------------------------------------------------------
# l2_distance_to_relevance unit tests
# ---------------------------------------------------------------------------


class TestL2DistanceToRelevance:
    """Direct tests for the L2→cosine-similarity conversion function.

    Formula: relevance = clamp(1 − d²/2, 0, 1)

    Valid for unit-norm embeddings where L2² = 2·(1 − cos_sim).
    """

    def _fn(self, distance: float) -> float:
        from core.utils.embeddings import l2_distance_to_relevance
        return l2_distance_to_relevance(distance)

    def test_identical_vectors(self):
        """Distance 0 → relevance 1.0 (cosine similarity = 1)."""
        assert self._fn(0.0) == 1.0

    def test_orthogonal_vectors(self):
        """Distance √2 → relevance 0.0 (cosine similarity = 0)."""
        assert self._fn(math.sqrt(2)) == pytest.approx(0.0, abs=1e-9)

    def test_opposite_vectors(self):
        """Distance 2.0 → clamped to 0.0 (cosine similarity = −1)."""
        assert self._fn(2.0) == 0.0

    def test_high_similarity(self):
        """Distance 0.2 → 0.98 (used in test_query_agent mock)."""
        assert self._fn(0.2) == pytest.approx(0.98, abs=1e-9)

    def test_moderate_similarity(self):
        """Distance 1.0 → 0.5 (cosine similarity = 0.5)."""
        assert self._fn(1.0) == pytest.approx(0.5, abs=1e-9)

    def test_low_similarity(self):
        """Distance 1.2 → 0.28 (the range that was broken before the fix)."""
        assert self._fn(1.2) == pytest.approx(0.28, abs=1e-9)

    def test_typical_chroma_distances(self):
        """Real-world ChromaDB distances that were being zeroed out."""
        assert self._fn(1.1183) == pytest.approx(0.3747, abs=0.001)
        assert self._fn(1.1768) == pytest.approx(0.3076, abs=0.001)
        assert self._fn(1.2089) == pytest.approx(0.2693, abs=0.001)

    def test_negative_distance_clamped(self):
        """Negative input (invalid) is clamped to max 1.0."""
        assert self._fn(-0.1) <= 1.0

    def test_very_large_distance_clamped(self):
        """Distances beyond 2.0 clamp to 0.0."""
        assert self._fn(3.0) == 0.0
        assert self._fn(100.0) == 0.0

    def test_monotonically_decreasing(self):
        """Relevance decreases as distance increases (in valid range)."""
        distances = [0.0, 0.2, 0.5, 0.8, 1.0, 1.2, 1.414]
        relevances = [self._fn(d) for d in distances]
        for i in range(len(relevances) - 1):
            assert relevances[i] >= relevances[i + 1], (
                f"Not monotonic: rel({distances[i]})={relevances[i]} < "
                f"rel({distances[i+1]})={relevances[i+1]}"
            )
