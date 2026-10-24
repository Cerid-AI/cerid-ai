# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The in-process nomic leg stands in for a stalled model server (audit F20).

The local model server builds nomic-embed-text-v1.5 indexes with CLS pooling
over up to 2048 tokens. The in-process ONNX leg mean-pooled at 512 tokens and
the stack pinned it to Snowflake, so when the server stalled the fallback had
to refuse — a different vector space — and retrieval had no embedder at all.
Only the ONNX session, tokenizer and weight download are faked.
"""
from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

NOMIC = "nomic-ai/nomic-embed-text-v1.5"
# Three tokens; CLS is a 3-4-5 vector, the rest would pull a mean elsewhere.
HIDDEN = np.array([[[3.0, 4.0, 0.0, 0.0], [0.0, 0.0, 9.0, 0.0], [0.0, 0.0, 0.0, 9.0]]], dtype=np.float32)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from core.utils.embedding_cache import _reset_singleton_for_testing

    for var in ("EMBEDDINGS_PROVIDER", "QUENCHFORGE_EMBED_MODEL"):
        monkeypatch.delenv(var, raising=False)
    _reset_singleton_for_testing()
    yield
    _reset_singleton_for_testing()


@pytest.fixture
def onnx_leg():
    tok = MagicMock()
    enc = MagicMock(ids=[101, 7, 102], attention_mask=[1, 1, 1], type_ids=[0, 0, 0])
    tok.encode_batch.return_value = [enc]
    session = MagicMock()
    session.run.return_value = [HIDDEN]
    session.get_inputs.return_value = [MagicMock(name="input_ids"), MagicMock(name="attention_mask")]
    session.get_outputs.return_value = [MagicMock(shape=[None, None, 4])]
    with (
        patch("core.utils.embeddings.resolve_hf_file", return_value="/fake/model.onnx"),
        patch("core.utils.embeddings.ort.InferenceSession", return_value=session),
        patch("core.utils.embeddings.Tokenizer.from_file", return_value=tok),
    ):
        yield tok


def test_the_nomic_leg_pools_on_cls_over_2048_tokens(onnx_leg):
    from core.utils.embeddings import OnnxEmbeddingFunction

    [vec] = OnnxEmbeddingFunction(model_id=NOMIC)(["a passage"])

    assert np.allclose(vec, [0.6, 0.8, 0.0, 0.0], atol=1e-6)
    onnx_leg.enable_truncation.assert_called_once_with(max_length=2048)


def test_other_models_keep_mean_pooling_at_512(onnx_leg):
    from core.utils.embeddings import OnnxEmbeddingFunction

    [vec] = OnnxEmbeddingFunction(model_id="Snowflake/snowflake-arctic-embed-m-v1.5")(["a passage"])

    assert vec[2] > 0 and vec[3] > 0
    onnx_leg.enable_truncation.assert_called_once_with(max_length=512)


@patch("core.utils.hf_cache.hf_hub_download", return_value="/fake/model.onnx")
@patch("core.utils.embeddings.ort.InferenceSession")
@patch("core.utils.embeddings.Tokenizer.from_file")
def test_the_nomic_weights_are_pinned_to_a_commit(_tok, session_cls, download):
    from core.utils.embeddings import OnnxEmbeddingFunction

    session_cls.return_value.get_outputs.return_value = [MagicMock(shape=[None, None, 768])]

    OnnxEmbeddingFunction(model_id=NOMIC)._load()

    revisions = {c.kwargs.get("revision") for c in download.call_args_list}
    assert len(revisions) == 1 and re.fullmatch(r"[0-9a-f]{40}", revisions.pop() or "")


def test_a_stalled_server_falls_through_to_the_nomic_leg(onnx_leg, monkeypatch):
    """The live configuration: the server names the model without its org."""
    from core.utils import embeddings as emb

    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "quenchforge")
    monkeypatch.setenv("QUENCHFORGE_EMBED_MODEL", "nomic-embed-text-v1.5")
    monkeypatch.setattr(emb.config, "EMBEDDING_MODEL", NOMIC)
    monkeypatch.setattr(emb.OnnxEmbeddingFunction, "_maybe_embed_via_sidecar", lambda self, texts: None)

    def stalled(coro, timeout=None):
        coro.close()
        raise TimeoutError()

    monkeypatch.setattr("core.utils.async_bridge.run_async", stalled)

    [vec] = emb.OnnxEmbeddingFunction(model_id=NOMIC).embed_query(["what does the reranker do"])

    assert np.allclose(vec, [0.6, 0.8, 0.0, 0.0], atol=1e-6)
