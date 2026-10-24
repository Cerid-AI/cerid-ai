# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The CRAG gate asks the cross-encoder whether the KB answers (audit F07).

Relevance after reranking is 0.4 x cross-encoder + 0.6 x retrieval. Live probe
2026-10-10: "Who wrote the novel Dune?" met a chunk about authors writing on
the Great Wall at cosine 0.84; the cross-encoder gave it 0.50, the blend 0.71,
and the 0.6 gate held external sources back. A compiled wiki page enters at
retrieval 1.0, so its blend never falls below 0.6 whatever the cross-encoder
says. The cross-encoder model is faked; the blend and the gate are real.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from core.agents.crag import crag_wants_external, kb_low_confidence
from core.retrieval.reranker import rerank

THRESHOLD = 0.6


def _row(content: str, relevance: float, **extra) -> dict:
    return {"content": content, "relevance": relevance, "artifact_id": content[:8], **extra}


def _reranked(rows: list[dict], scores: dict[str, float]) -> list[dict]:
    with (
        patch("core.retrieval.reranker._score_pairs", lambda _q, docs: [scores[d] for d in docs]),
        patch("config.ENABLE_CASCADE_RERANK", False),
    ):
        return rerank("who wrote the novel Dune?", rows)


def test_a_row_the_cross_encoder_doubts_does_not_close_the_gate():
    rows = _reranked(
        [_row("Authors such as John Man have written on the Great Wall", 0.844), _row("The forgetting curve", 0.67)],
        {"Authors such as John Man have written on the Great Wall": 0.50, "The forgetting curve": 0.0},
    )
    assert rows[0]["relevance"] > THRESHOLD, "the blend alone would read this as answered"

    assert crag_wants_external({"results": rows}, "who wrote the novel Dune?", THRESHOLD)
    assert kb_low_confidence({"results": rows}, THRESHOLD)


def test_a_compiled_page_the_cross_encoder_rejects_does_not_close_the_gate():
    rows = _reranked(
        [_row("Compiled summary: the reranker blends scores", 1.0, source_type="wiki"), _row("unrelated", 0.7)],
        {"Compiled summary: the reranker blends scores": 0.05, "unrelated": 0.0},
    )

    assert crag_wants_external({"results": rows}, "what is the tallest building in the world?", THRESHOLD)


def test_a_row_the_cross_encoder_accepts_keeps_the_kb_answer():
    rows = _reranked(
        [_row("Frank Herbert wrote Dune, published in 1965", 0.55), _row("unrelated", 0.5)],
        {"Frank Herbert wrote Dune, published in 1965": 0.97, "unrelated": 0.0},
    )

    assert not crag_wants_external({"results": rows}, "who wrote the novel Dune?", THRESHOLD)
    assert not kb_low_confidence({"results": rows}, THRESHOLD)


@pytest.mark.parametrize(("relevance", "fires"), [(0.8, False), (0.3, True)])
def test_without_a_reranker_the_gate_reads_relevance(relevance, fires):
    rows = [_row("a chunk nobody reranked", relevance)]

    assert crag_wants_external({"results": rows}, "a question", THRESHOLD) is fires


@pytest.mark.parametrize("leg", ["quenchforge", "sidecar"])
def test_a_remote_cross_encoder_is_asked_the_same_way(leg):
    from core.agents.query_agent import _apply_rerank_scores

    rows = _apply_rerank_scores([_row("Authors such as John Man", 0.844)], [0.5], leg)

    assert rows[0]["relevance"] > THRESHOLD
    assert crag_wants_external({"results": rows}, "who wrote the novel Dune?", THRESHOLD)
