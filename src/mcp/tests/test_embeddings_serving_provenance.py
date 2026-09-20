# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Embedding provenance must describe the model that PRODUCED the vector.

F145: the cache namespace was bound from the CONFIGURED provider before any
routing, so a Quenchforge failure wrote local-ONNX vectors under the
``qf:<model>`` namespace — and, with CERID_EMBED_CACHE_PATH set, persisted them.
F165: ``embedding_stamp()`` returned ``config.EMBEDDING_MODEL`` unconditionally,
so every chunk ingested since the Quenchforge cutover claims arctic-embed while
holding nomic vectors.
F166: the fallback comment claimed store-level namespacing that does not exist —
the ONLY namespacing is the in-process LRU.
"""

from __future__ import annotations

import numpy as np
import pytest

from core.utils import embedding_cache as ec
from core.utils import embeddings as emb

QF_MODEL = "nomic-embed-text-v1.5"
ONNX_MODEL = "Snowflake/snowflake-arctic-embed-m-v1.5"


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(ec, "_singleton", None, raising=False)
    yield
    monkeypatch.setattr(ec, "_singleton", None, raising=False)


def _fn():
    return emb.OnnxEmbeddingFunction(
        model_id=ONNX_MODEL,
        onnx_filename="onnx/model.onnx",
        dimensions=768,
    )


class TestCacheNamespaceFollowsTheServingProvider:
    def test_quenchforge_failure_does_not_cache_onnx_vectors_under_qf(
        self, monkeypatch
    ):
        """The poisoning path: configured=quenchforge, serving=onnx."""
        monkeypatch.setenv("EMBEDDINGS_PROVIDER", "quenchforge")
        monkeypatch.setenv("QUENCHFORGE_EMBED_MODEL", QF_MODEL)

        fn = _fn()
        local_vec = np.ones(768, dtype=np.float32)
        monkeypatch.setattr(
            emb.OnnxEmbeddingFunction, "_maybe_embed_via_quenchforge",
            lambda self, texts: None,
        )
        monkeypatch.setattr(
            emb.OnnxEmbeddingFunction, "_maybe_embed_via_sidecar",
            lambda self, texts: [local_vec for _ in texts],
        )

        fn(["hello"])

        cache = ec.get_embedding_cache()
        assert cache.get(f"qf:{QF_MODEL}", "hello") is None, (
            "local vector was cached under the quenchforge namespace"
        )
        assert cache.get(f"onnx:{ONNX_MODEL}", "hello") is not None

    def test_quenchforge_success_caches_under_the_qf_namespace(self, monkeypatch):
        monkeypatch.setenv("EMBEDDINGS_PROVIDER", "quenchforge")
        monkeypatch.setenv("QUENCHFORGE_EMBED_MODEL", QF_MODEL)

        fn = _fn()
        monkeypatch.setattr(
            emb.OnnxEmbeddingFunction, "_maybe_embed_via_quenchforge",
            lambda self, texts: [[0.5] * 768 for _ in texts],
        )
        fn(["hello"])

        cache = ec.get_embedding_cache()
        assert cache.get(f"qf:{QF_MODEL}", "hello") is not None


class TestFallbackAcrossVectorSpacesFailsClosed:
    def test_mismatched_fallback_raises_instead_of_mixing_spaces(self, monkeypatch):
        """Live config: quenchforge serves nomic, local ONNX is arctic. Both
        768-dim, so no dimension guard catches the substitution."""
        monkeypatch.setenv("EMBEDDINGS_PROVIDER", "quenchforge")
        monkeypatch.setenv("QUENCHFORGE_EMBED_MODEL", QF_MODEL)

        fn = _fn()

        def _boom(texts, is_query=False):
            raise RuntimeError("quenchforge down")

        monkeypatch.setattr(
            "utils.quenchforge_client.quenchforge_embed", _boom, raising=False,
        )
        monkeypatch.setattr(
            "core.utils.async_bridge.run_async",
            lambda coro, timeout=None: (_ for _ in ()).throw(
                RuntimeError("quenchforge down")
            ),
        )
        with pytest.raises(RuntimeError, match="vector space"):
            fn(["hello"])

    def test_matching_fallback_still_degrades_gracefully(self, monkeypatch):
        """When both legs serve the same model identity, falling through is
        genuinely safe — availability wins."""
        monkeypatch.setenv("EMBEDDINGS_PROVIDER", "quenchforge")
        monkeypatch.setenv("QUENCHFORGE_EMBED_MODEL", QF_MODEL)
        monkeypatch.setattr(emb.config, "EMBEDDING_MODEL", QF_MODEL)

        fn = _fn()
        monkeypatch.setattr(
            "core.utils.async_bridge.run_async",
            lambda coro, timeout=None: (_ for _ in ()).throw(
                RuntimeError("quenchforge down")
            ),
        )
        monkeypatch.setattr(
            emb.OnnxEmbeddingFunction, "_maybe_embed_via_sidecar",
            lambda self, texts: [np.ones(768, dtype=np.float32) for _ in texts],
        )
        assert len(fn(["hello"])) == 1


class TestEmbeddingStampNamesTheServingModel:
    def test_stamp_reports_the_quenchforge_model_when_it_serves(self, monkeypatch):
        monkeypatch.setenv("EMBEDDINGS_PROVIDER", "quenchforge")
        monkeypatch.setenv("QUENCHFORGE_EMBED_MODEL", QF_MODEL)
        stamp = emb.embedding_stamp("code")
        assert stamp["embedding_model"] == QF_MODEL

    def test_stamp_reports_the_local_model_when_it_serves(self, monkeypatch):
        import config as cfg

        monkeypatch.setenv("EMBEDDINGS_PROVIDER", "sidecar")
        stamp = emb.embedding_stamp("code")
        assert stamp["embedding_model"] == cfg.EMBEDDING_MODEL
