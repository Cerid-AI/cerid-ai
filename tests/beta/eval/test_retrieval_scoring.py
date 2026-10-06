# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Offline checks over the retrieval benchmark's scoring and floors — no stack.

The live ``test_retrieval_metrics`` can only prove it gates something if the
same scoring function goes red on a ranker that is known to be broken; that
proof lives here, against the real seed, so it runs anywhere.
"""

from __future__ import annotations

import pytest
from metrics import retrieval_summary
from rag_benchmark import (
    MRR_THRESHOLD,
    NDCG_5_THRESHOLD,
    REFERENCE,
    SEED_CASES,
    assert_retrieval_floors,
)
from seed_reference import reference_scores, seed_documents


def _rankings(place_answer_at: int | None) -> list[tuple[list[str], set[str]]]:
    """Per case: the answer at 1-based position ``place_answer_at`` inside its
    own cluster (``None`` = never returned), the rest of the pool after."""
    all_ids = [doc_id for doc_id, _ in seed_documents(SEED_CASES)]
    out = []
    for i, case in enumerate(SEED_CASES):
        answer = f"{i}:answer"
        cluster = [f"{i}:d{j + 1}" for j in range(len(case["distractors"]))]
        if place_answer_at is not None:
            cluster.insert(place_answer_at - 1, answer)
        rest = [d for d in all_ids if d not in cluster and d != answer]
        out.append((cluster + rest, {answer}))
    return out


def test_every_case_has_near_topic_distractors():
    for case in SEED_CASES:
        assert 3 <= len(case["distractors"]) <= 4, case["query"]
        assert all(d != case["answer"] for d in case["distractors"])


def test_reference_numbers_match_the_docstring():
    ref = reference_scores(SEED_CASES)
    for ranker, expected in REFERENCE.items():
        for metric, value in expected.items():
            assert round(ref[ranker][metric], 3) == value, (ranker, metric, ref[ranker][metric])


def test_floors_sit_between_random_cluster_and_lexical():
    assert REFERENCE["random_cluster"]["ndcg"] < NDCG_5_THRESHOLD < REFERENCE["lexical"]["ndcg"]
    assert REFERENCE["random_cluster"]["mrr"] < MRR_THRESHOLD < REFERENCE["lexical"]["mrr"]


def test_perfect_ranker_passes():
    summary = retrieval_summary(_rankings(1))
    assert summary["ndcg"]["mean"] == 1.0 and summary["mrr"]["mean"] == 1.0
    assert summary["ndcg"]["non_discriminating"] is True
    assert_retrieval_floors(summary)


@pytest.mark.parametrize("place_answer_at", [3, 4, None], ids=["third", "last-in-cluster", "missing"])
def test_broken_ranker_fails_the_floor(place_answer_at):
    summary = retrieval_summary(_rankings(place_answer_at))
    with pytest.raises(AssertionError, match=r"(NDCG@5|MRR) [0-9.]+ < "):
        assert_retrieval_floors(summary)


def test_summary_reports_spread_and_per_query_rank():
    rankings = _rankings(1)
    rankings[0] = (rankings[0][0][1:] + rankings[0][0][:1], rankings[0][1])  # answer to the end
    summary = retrieval_summary(rankings)
    assert summary["mrr"]["min"] < summary["mrr"]["max"]
    assert summary["mrr"]["spread"] == pytest.approx(summary["mrr"]["max"] - summary["mrr"]["min"])
    assert summary["ndcg"]["non_discriminating"] is False
    assert summary["per_query"][0]["answer_rank"] == len(rankings[0][0])
    assert summary["per_query"][1]["answer_rank"] == 1


def test_an_empty_score_list_is_not_measured_rather_than_zero():
    from metrics import _distribution

    d = _distribution([])
    assert d["mean"] is None and d["n"] == 0 and d["non_discriminating"] is False
