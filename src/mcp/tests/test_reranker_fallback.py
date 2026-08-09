# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Task 27 / audit C-9: ONNX cross-encoder failure must not crash queries.

After Bifrost was retired, the old ``_rerank_llm`` fallback path was broken
(it routed through Bifrost). A single ONNX load failure would therefore take
every query down. The graceful path now returns results in their input order
and tags each with ``reranker_status = 'onnx_failed_no_fallback'`` so the
caller can surface the degraded state.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


def _fixture_results() -> list[dict]:
    # Pre-sorted by relevance (descending) so the "input order" check is
    # meaningful — reranking would reorder them.
    return [
        {"content": "alpha", "domain": "docs", "filename": "a.md", "relevance": 0.9},
        {"content": "bravo", "domain": "docs", "filename": "b.md", "relevance": 0.6},
        {"content": "charlie", "domain": "docs", "filename": "c.md", "relevance": 0.3},
    ]


@pytest.mark.asyncio
async def test_onnx_failure_returns_original_order_and_status():
    """When the ONNX reranker raises, results come back in the input order
    with each item tagged ``reranker_status=onnx_failed_no_fallback`` — no
    LLM fallback, no crash."""
    from core.agents import query_agent

    results = _fixture_results()
    original_ids = [r["filename"] for r in results]

    with patch(
        "core.retrieval.reranker.rerank",
        side_effect=RuntimeError("ONNX session not initialised"),
    ):
        out = await query_agent._rerank_cross_encoder(results, query="anything")

    assert [r["filename"] for r in out] == original_ids, (
        "ONNX failure must preserve input order (no silent reshuffle)"
    )
    assert all(
        r.get("reranker_status") == "onnx_failed_no_fallback" for r in out
    ), "Every result must be tagged so the caller can surface the degraded state"


@pytest.mark.asyncio
async def test_onnx_success_does_not_tag_status():
    """Happy path: successful ONNX rerank does not set the degraded flag."""
    from core.agents import query_agent

    results = _fixture_results()

    def _fake_rerank(query, docs):  # signature of core.retrieval.reranker.rerank
        # Reverse to make the reorder observable.
        return list(reversed(docs))

    with patch("core.retrieval.reranker.rerank", side_effect=_fake_rerank):
        out = await query_agent._rerank_cross_encoder(results, query="anything")

    assert [r["filename"] for r in out] == ["c.md", "b.md", "a.md"]
    assert not any("reranker_status" in r for r in out)


@pytest.mark.parametrize("max_length", [512, 1024])
def test_tokenizer_truncation_uses_configured_max_length(max_length):
    """The cross-encoder tokenizer truncates at RERANK_MAX_LENGTH, not a
    hardcoded 512 — so bge-reranker-v2-m3 can read a full 512-token parent
    chunk + query (1024) instead of silently clipping the chunk's tail."""
    from core.retrieval import reranker

    fake_tokenizer = MagicMock()

    # Reset the module singletons so _load_model runs its full body.
    with patch.object(reranker, "_session", None), patch.object(
        reranker, "_tokenizer", None,
    ), patch.object(reranker.config, "RERANK_MAX_LENGTH", max_length), patch(
        "core.retrieval.reranker.hf_hub_download", return_value="/tmp/fake",
    ), patch(
        "core.retrieval.reranker.ort.InferenceSession", return_value=MagicMock(),
    ), patch(
        "core.retrieval.reranker.Tokenizer.from_file", return_value=fake_tokenizer,
    ):
        reranker._load_model()

    fake_tokenizer.enable_truncation.assert_called_once_with(max_length=max_length)


def _fake_encoding(ids: list[int], overflowing: bool) -> SimpleNamespace:
    """Mimic a ``tokenizers.Encoding``. Verified against the real
    ``tokenizers`` library: ``enable_truncation`` always populates
    ``.overflowing`` with the dropped tail when a pair is truncated (even
    with the default ``stride=0``), so a non-empty list is an exact,
    zero-extra-cost truncation signal.
    """
    return SimpleNamespace(
        ids=ids,
        attention_mask=[1] * len(ids),
        type_ids=[0] * len(ids),
        overflowing=[SimpleNamespace()] if overflowing else [],
    )


def _fake_session() -> MagicMock:
    session = MagicMock()
    session.get_inputs.return_value = [
        SimpleNamespace(name="input_ids"),
        SimpleNamespace(name="attention_mask"),
        SimpleNamespace(name="token_type_ids"),
    ]
    session.run.return_value = [np.array([[0.1, 0.9], [0.2, 0.8]])]
    return session


def test_score_pairs_logs_debug_when_pair_truncated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A (query, chunk) pair that overflows RERANK_MAX_LENGTH is truncated
    silently by the tokenizer — _score_pairs must at least log a debug
    signal so the parent-chunk data loss isn't invisible."""
    from core.retrieval import reranker

    # Real (padded) batches always have equal-length ids across the batch —
    # only ``.overflowing`` distinguishes the truncated pair.
    fake_tokenizer = MagicMock()
    fake_tokenizer.encode_batch.return_value = [
        _fake_encoding([1, 2, 3], overflowing=True),
        _fake_encoding([1, 2, 0], overflowing=False),
    ]

    with patch.object(reranker, "_session", _fake_session()), patch.object(
        reranker, "_tokenizer", fake_tokenizer,
    ), caplog.at_level(logging.DEBUG, logger="ai-companion.reranker"):
        reranker._score_pairs("query", ["long doc", "short doc"])

    assert any(
        "truncat" in record.message.lower() for record in caplog.records
    ), "Truncation must be logged at debug level, not silently dropped"


def test_score_pairs_no_log_when_no_truncation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Happy path: no pair overflowed the budget — no truncation log."""
    from core.retrieval import reranker

    fake_tokenizer = MagicMock()
    fake_tokenizer.encode_batch.return_value = [
        _fake_encoding([1, 2], overflowing=False),
        _fake_encoding([1, 2], overflowing=False),
    ]

    with patch.object(reranker, "_session", _fake_session()), patch.object(
        reranker, "_tokenizer", fake_tokenizer,
    ), caplog.at_level(logging.DEBUG, logger="ai-companion.reranker"):
        reranker._score_pairs("query", ["doc1", "doc2"])

    assert not any(
        "truncat" in record.message.lower() for record in caplog.records
    )
