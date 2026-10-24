# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The cross-encoder scores only the top candidates. A row it never scored must
stay on the same scale as the rows it did, or a later sort lifts the unvetted
tail above everything the cross-encoder rejected: for "boiling point of
ethanol", a forgetting-curve article outranked the rejected top 15."""
from __future__ import annotations

import pytest

from core.retrieval import reranker


@pytest.fixture(autouse=True)
def _weights(monkeypatch):
    monkeypatch.setattr(reranker.config, "ENABLE_CASCADE_RERANK", False, raising=False)
    monkeypatch.setattr(reranker.config, "QUERY_RERANK_CANDIDATES", 2, raising=False)
    monkeypatch.setattr(reranker.config, "RERANK_CE_WEIGHT", 0.4, raising=False)
    monkeypatch.setattr(reranker.config, "RERANK_ORIGINAL_WEIGHT", 0.6, raising=False)


def test_an_unscored_row_never_outranks_a_rejected_one_with_a_higher_retrieval_score(monkeypatch):
    monkeypatch.setattr(reranker, "_score_pairs", lambda q, docs: [0.0] * len(docs))
    results = [{"content": "a", "relevance": 0.75}, {"content": "b", "relevance": 0.70},
               {"content": "tail", "relevance": 0.60}]
    out = reranker.rerank("q", results)
    assert [r["content"] for r in sorted(out, key=lambda r: r["relevance"], reverse=True)] == ["a", "b", "tail"]
    assert out[-1]["relevance"] == pytest.approx(0.6 * 0.60)


def test_a_relevant_row_still_rises_above_the_tail(monkeypatch):
    monkeypatch.setattr(reranker, "_score_pairs", lambda q, docs: [0.0, 1.0][: len(docs)])
    results = [{"content": "a", "relevance": 0.75}, {"content": "answer", "relevance": 0.50},
               {"content": "tail", "relevance": 0.70}]
    out = sorted(reranker.rerank("q", results), key=lambda r: r["relevance"], reverse=True)
    assert out[0]["content"] == "answer"


def test_the_external_gate_sits_between_rejected_and_accepted_chunks():
    """A strong retrieval match the cross-encoder rejects must not hold the CRAG
    gate closed; a modest one it accepts must."""
    import config

    rejected = config.RERANK_CE_WEIGHT * 0.0 + config.RERANK_ORIGINAL_WEIGHT * 0.9
    accepted = config.RERANK_CE_WEIGHT * 0.9 + config.RERANK_ORIGINAL_WEIGHT * 0.5
    assert rejected < config.RETRIEVAL_QUALITY_THRESHOLD <= accepted
