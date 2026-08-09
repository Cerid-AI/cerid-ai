# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for utils/embeddings.py and deps._EmbeddingAwareClient."""

import math
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# OnnxEmbeddingFunction unit tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_provider_env(monkeypatch):
    """Ensure the fast-path env vars don't leak from prior tests.

    The settings router PATCH handler mutates `os.environ` directly to make
    provider flips take effect in-process without a restart (see
    `app.routers.settings.update_settings_endpoint`). Pytest's monkeypatch
    doesn't track those mutations, so a settings test that flips
    `EMBEDDINGS_PROVIDER=quenchforge` permanently taints the process env.
    When a later embeddings test runs with that env still set, the
    OnnxEmbeddingFunction fast-path fires and bypasses the mocked ONNX
    session → mock breakage. Clearing these per-test guarantees the test
    body sees the default (sidecar/none) provider state.
    """
    for var in (
        "EMBEDDINGS_PROVIDER",
        "RERANK_PROVIDER",
        "INTERNAL_LLM_PROVIDER",
        "QUENCHFORGE_EMBED_MODEL",
        "QUENCHFORGE_RERANK_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


class TestOnnxEmbeddingFunction:
    def test_empty_input_returns_empty(self):
        from core.utils.embeddings import OnnxEmbeddingFunction

        ef = OnnxEmbeddingFunction(model_id="test-model")
        assert ef([]) == []

    @patch("core.utils.embeddings.hf_hub_download")
    @patch("core.utils.embeddings.ort.InferenceSession")
    @patch("core.utils.embeddings.Tokenizer.from_file")
    def test_mean_pooling_and_normalization(self, mock_tok_cls, mock_session_cls, mock_dl):
        """Verify mean pooling + L2 normalization produces unit-length vectors."""
        from core.utils.embeddings import OnnxEmbeddingFunction

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
        from core.utils.embeddings import OnnxEmbeddingFunction

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
# chromadb 1.x EmbeddingFunction contract (forward-compat — no-op on 0.5)
# ---------------------------------------------------------------------------


class TestEmbeddingFunctionContract:
    def test_name_is_stable_identifier(self):
        from core.utils.embeddings import OnnxEmbeddingFunction

        # Static method — callable on the class itself, not just instances.
        assert OnnxEmbeddingFunction.name() == "cerid-onnx"

    def test_get_config_round_trips_constructor_args(self):
        from core.utils.embeddings import OnnxEmbeddingFunction

        ef = OnnxEmbeddingFunction(
            model_id="org/some-model",
            onnx_filename="onnx/model_quantized.onnx",
            cache_dir="/tmp/cache",
            dimensions=512,
        )
        config = ef.get_config()
        assert config == {
            "model_id": "org/some-model",
            "onnx_filename": "onnx/model_quantized.onnx",
            "cache_dir": "/tmp/cache",
            "dimensions": 512,
        }

    def test_build_from_config_reconstructs_equivalent_instance(self):
        from core.utils.embeddings import OnnxEmbeddingFunction

        original = OnnxEmbeddingFunction(
            model_id="org/some-model",
            onnx_filename="onnx/model_quantized.onnx",
            cache_dir="/tmp/cache",
            dimensions=512,
        )
        rebuilt = OnnxEmbeddingFunction.build_from_config(original.get_config())

        assert isinstance(rebuilt, OnnxEmbeddingFunction)
        # The rebuilt instance must round-trip its config too — proves the
        # contract is closed under serialise→deserialise.
        assert rebuilt.get_config() == original.get_config()

    def test_build_from_config_tolerates_missing_optional_keys(self):
        """Future-config-schema evolution must not break collection load."""
        from core.utils.embeddings import OnnxEmbeddingFunction

        rebuilt = OnnxEmbeddingFunction.build_from_config({"model_id": "org/x"})
        assert rebuilt.get_config()["model_id"] == "org/x"
        assert rebuilt.get_config()["onnx_filename"] == "onnx/model.onnx"
        assert rebuilt.get_config()["cache_dir"] is None
        assert rebuilt.get_config()["dimensions"] is None


# ---------------------------------------------------------------------------
# Fast-path return-shape contract — chromadb expects List[ndarray], not
# List[list[float]]. Both Quenchforge and sidecar branches were silently
# violating that contract pre-v0.93.8, which crashed retrieval with
# "'list' object has no attribute 'tolist'" inside chromadb's
# convert_np_embeddings_to_list. These tests pin the contract.
# ---------------------------------------------------------------------------


class TestFastPathReturnShape:
    def _make_ef(self):
        """Build an OnnxEmbeddingFunction without triggering tokenizer/ONNX load.

        We only exercise __call__'s fast-path branches; constructor args
        are placeholders that never get touched because the fast-path
        short-circuits before _load() runs.
        """
        from core.utils.embeddings import OnnxEmbeddingFunction

        return OnnxEmbeddingFunction(model_id="org/placeholder")

    def test_quenchforge_branch_returns_list_of_ndarrays(self, monkeypatch):
        """Quenchforge fast-path must return List[ndarray[float32]] rows."""
        import numpy as np

        ef = self._make_ef()
        plain_python_lists = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        monkeypatch.setattr(
            ef, "_maybe_embed_via_quenchforge", lambda inp: plain_python_lists,
        )
        out = ef(["doc1", "doc2"])
        assert len(out) == 2
        for row in out:
            assert isinstance(row, np.ndarray), (
                f"chromadb expects ndarray rows; got {type(row).__name__}"
            )
            assert row.dtype == np.float32
            # ndarray.tolist() must work (chromadb's
            # convert_np_embeddings_to_list calls this).
            assert isinstance(row.tolist(), list)

    def test_sidecar_branch_returns_list_of_ndarrays(self, monkeypatch):
        """Sidecar fast-path must satisfy the same chromadb contract."""
        import numpy as np

        ef = self._make_ef()
        # Force the sidecar branch by stubbing both:
        # - quenchforge short-circuit returns None (skip)
        # - sidecar returns plain lists (the contract-violation case)
        monkeypatch.setattr(ef, "_maybe_embed_via_quenchforge", lambda inp: None)
        plain_python_lists = [[0.7, 0.8, 0.9]]
        monkeypatch.setattr(
            ef, "_maybe_embed_via_sidecar", lambda inp: plain_python_lists,
        )
        out = ef(["doc1"])
        assert len(out) == 1
        assert isinstance(out[0], np.ndarray)
        assert out[0].dtype == np.float32

    def test_empty_input_short_circuits(self, monkeypatch):
        """Empty input returns empty list, fast-paths never consulted."""
        ef = self._make_ef()
        called: list[bool] = []

        def _should_not_be_called(inp):
            called.append(True)
            return None

        monkeypatch.setattr(
            ef, "_maybe_embed_via_quenchforge", _should_not_be_called,
        )
        monkeypatch.setattr(ef, "_maybe_embed_via_sidecar", _should_not_be_called)
        assert ef([]) == []
        assert not called


# ---------------------------------------------------------------------------
# Embedding cache integration
# ---------------------------------------------------------------------------


class TestEmbeddingCacheIntegration:
    """The LRU cache wraps the routing chain so identical texts don't
    re-embed across the network. Targeted by LongMemEval haystacks where
    ~30% of embed calls within a run are exact repeats of earlier sessions.
    """

    @pytest.fixture(autouse=True)
    def _isolate_cache(self):
        from core.utils.embedding_cache import _reset_singleton_for_testing

        _reset_singleton_for_testing()
        yield
        _reset_singleton_for_testing()

    def _make_ef(self):
        from core.utils.embeddings import OnnxEmbeddingFunction

        return OnnxEmbeddingFunction(model_id="org/placeholder")

    def test_second_call_with_same_texts_skips_backend(self, monkeypatch):
        ef = self._make_ef()
        backend_calls: list[list[str]] = []

        def _stub_backend(texts):
            backend_calls.append(list(texts))
            return [[float(len(t)), 0.0, 0.0] for t in texts]

        monkeypatch.setattr(ef, "_embed_uncached", lambda inp: [
            np.asarray(row, dtype=np.float32) for row in _stub_backend(inp)
        ])
        first = ef(["alpha", "beta"])
        second = ef(["alpha", "beta"])
        assert len(backend_calls) == 1, "second call should be served from cache"
        np.testing.assert_array_equal(first[0], second[0])
        np.testing.assert_array_equal(first[1], second[1])

    def test_mixed_hit_miss_preserves_order(self, monkeypatch):
        ef = self._make_ef()

        def _stub(texts):
            return [np.asarray([float(len(t))], dtype=np.float32) for t in texts]

        backend_calls: list[list[str]] = []

        def _tracked(inp):
            backend_calls.append(list(inp))
            return _stub(inp)

        monkeypatch.setattr(ef, "_embed_uncached", _tracked)

        # Warm cache for two texts.
        ef(["a", "bb"])
        backend_calls.clear()

        # Mixed batch: cached, new, cached, new.
        out = ef(["a", "ccc", "bb", "dddd"])
        assert len(out) == 4
        # Only the misses hit the backend, in input order.
        assert backend_calls == [["ccc", "dddd"]]
        # Output order matches input order, by-length vectors prove it.
        assert out[0][0] == 1.0  # "a"
        assert out[1][0] == 3.0  # "ccc"
        assert out[2][0] == 2.0  # "bb"
        assert out[3][0] == 4.0  # "dddd"

    def test_namespace_isolates_quenchforge_from_onnx(self, monkeypatch):
        ef = self._make_ef()
        backend_calls: list[str] = []

        def _stub(inp):
            backend_calls.append(_current_namespace_for_test(ef))
            return [np.asarray([1.0], dtype=np.float32) for _ in inp]

        monkeypatch.setattr(ef, "_embed_uncached", _stub)

        # First call under ONNX namespace.
        monkeypatch.delenv("EMBEDDINGS_PROVIDER", raising=False)
        ef(["hello"])
        # Flip to Quenchforge — same text, different vector space, must
        # re-embed instead of returning the ONNX-cached entry.
        monkeypatch.setenv("EMBEDDINGS_PROVIDER", "quenchforge")
        monkeypatch.setenv("QUENCHFORGE_EMBED_MODEL", "nomic-embed-text-v1.5")
        ef(["hello"])

        assert backend_calls == [
            "onnx:org/placeholder",
            "qf:nomic-embed-text-v1.5",
        ]

    def test_cache_disabled_via_env(self, monkeypatch):
        from core.utils.embedding_cache import _reset_singleton_for_testing

        monkeypatch.setenv("CERID_EMBED_CACHE_SIZE", "0")
        _reset_singleton_for_testing()
        ef = self._make_ef()
        calls = [0]

        def _stub(inp):
            calls[0] += 1
            return [np.asarray([1.0], dtype=np.float32) for _ in inp]

        monkeypatch.setattr(ef, "_embed_uncached", _stub)
        ef(["x"])
        ef(["x"])
        assert calls[0] == 2, "disabled cache must not memoize"


def _current_namespace_for_test(ef):
    """Helper used by the namespace-isolation test."""
    return ef._active_namespace()


# ---------------------------------------------------------------------------
# get_embedding_function tests
# ---------------------------------------------------------------------------


class TestGetEmbeddingFunction:
    def test_server_default_returns_none(self):
        """When EMBEDDING_MODEL is all-MiniLM-L6-v2, return None (server handles it)."""
        import core.utils.embeddings as mod

        # Reset singleton
        mod._embedding_fn = None
        with patch.object(mod.config, "EMBEDDING_MODEL", "all-MiniLM-L6-v2"):
            assert mod.get_embedding_function() is None

    def test_custom_model_returns_function(self):
        """When EMBEDDING_MODEL differs, return an OnnxEmbeddingFunction."""
        import core.utils.embeddings as mod

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
        with patch("core.utils.embeddings.get_embedding_function", return_value=fake_ef):
            wrapper.get_or_create_collection(name="test_coll")

        call_kwargs = mock_client.get_or_create_collection.call_args[1]
        assert call_kwargs["embedding_function"] is fake_ef

    def test_no_injection_when_server_default(self):
        """Wrapper passes through without ef when using server default model."""
        from app.deps import _EmbeddingAwareClient

        mock_client = MagicMock()
        wrapper = _EmbeddingAwareClient(mock_client)

        with patch("core.utils.embeddings.get_embedding_function", return_value=None):
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
        with patch("core.utils.embeddings.get_embedding_function", return_value=fake_ef):
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


# ---------------------------------------------------------------------------
# embedding_stamp unit tests (Phase 4.4)
# ---------------------------------------------------------------------------


class TestEmbeddingStamp:
    """embedding_stamp() is the single source of truth for chunk-metadata
    version stamping — used by both the ingest chunk-write path and the
    managed re-embed job."""

    def test_returns_model_and_version_keys(self):
        from core.utils.embeddings import embedding_stamp

        stamp = embedding_stamp("code")
        assert set(stamp.keys()) == {"embedding_model", "embedding_model_version"}

    def test_model_sourced_from_config(self):
        import config as cfg
        from core.utils.embeddings import embedding_stamp

        stamp = embedding_stamp("code")
        assert stamp["embedding_model"] == cfg.EMBEDDING_MODEL

    def test_version_falls_back_to_global_when_no_override(self):
        import config as cfg
        from core.utils.embeddings import embedding_stamp

        stamp = embedding_stamp("nonexistent_domain")
        assert stamp["embedding_model_version"] == cfg.EMBEDDING_MODEL_VERSION

    def test_version_honors_per_domain_override(self, monkeypatch):
        from config import settings as _settings
        from core.utils.embeddings import embedding_stamp

        monkeypatch.setitem(
            _settings.EMBEDDING_MODEL_VERSIONS_PER_DOMAIN,
            "code",
            "arctic-embed-l-v2.0",
        )
        stamp = embedding_stamp("code")
        assert stamp["embedding_model_version"] == "arctic-embed-l-v2.0"
        # An untouched domain still gets the global version.
        other = embedding_stamp("finance")
        assert other["embedding_model_version"] == _settings.EMBEDDING_MODEL_VERSION
