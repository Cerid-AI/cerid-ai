# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The LLM-judge parser and the mean built on it — offline, no stack.

The shapes mirror ``app/eval/ragas_metrics.MetricResult``: a judge reply that
cannot be parsed is an instrument failure, not a score of zero, and an answer
that abstains is a retrieval-coverage signal, not a hallucination. Neither
belongs in the faithfulness mean.
"""

from __future__ import annotations

from dataclasses import asdict

import pytest
from rag_benchmark import (
    FAITHFULNESS_ANSWER_THRESHOLD,
    FAITHFULNESS_THRESHOLD,
    JudgeResult,
    assert_faithfulness_floors,
    is_abstention,
    judge_mean,
    parse_judge,
    worst_n,
)


def test_parse_plain_and_fenced_json():
    assert parse_judge('{"reasoning": "all claims grounded", "score": 0.9}') == JudgeResult(0.9, "all claims grounded")
    fenced = '```json\n{"reasoning": "two of three", "score": 0.67}\n```'
    assert parse_judge(fenced) == JudgeResult(0.67, "two of three")


def test_parse_never_fails_upward():
    # The incident shape: reasoning carries a number before a malformed score key.
    raw = '```json\n{"reasoning": "only [1] of 4 relevant", "score": 0.25,}\n```'
    result = parse_judge(raw)
    assert result.score == 0.25 and not result.parse_failed


def test_parse_failure_is_flagged_not_scored():
    result = parse_judge("The answer is faithful to the context because it restates it.")
    assert result.parse_failed is True and result.score == 0.0
    assert "Failed to parse" in result.reasoning


def test_parse_clamps_to_unit_interval():
    assert parse_judge('{"score": 7}').score == 1.0
    assert parse_judge('{"score": -2}').score == 0.0


def test_mean_excludes_parse_failures_and_abstentions():
    results = [
        JudgeResult(1.0, "grounded"),
        JudgeResult(0.5, "half"),
        JudgeResult(0.0, "Failed to parse: ...", parse_failed=True),
        JudgeResult(0.0, "abstained", abstained=True),
    ]
    summary = judge_mean(results)
    assert summary["mean"] == pytest.approx(0.75)
    assert summary == {
        "mean": pytest.approx(0.75), "n": 4, "n_scored": 2, "n_parse_failed": 1, "n_abstained": 1,
        "min": 0.5, "max": 1.0, "spread": 0.5, "non_discriminating": False,
    }


def test_mean_of_nothing_scored_is_none_not_zero():
    summary = judge_mean([JudgeResult(0.0, "x", parse_failed=True)])
    assert summary["mean"] is None and summary["n_scored"] == 0


def test_worst_n_orders_scored_items_lowest_first_and_keeps_failures_visible():
    records = [
        {"query": "a", "score": 0.9, "parse_failed": False, "abstained": False},
        {"query": "b", "score": 0.1, "parse_failed": False, "abstained": False},
        {"query": "c", "score": 0.0, "parse_failed": True, "abstained": False},
        {"query": "d", "score": 0.4, "parse_failed": False, "abstained": False},
    ]
    worst = worst_n(records, 2)
    assert [r["query"] for r in worst] == ["b", "d"]
    assert [r["query"] for r in worst_n(records, 5)] == ["b", "d", "a", "c"]


def test_abstention_detector():
    assert is_abstention("There is no information about Tim Cook in the memories.")
    assert is_abstention("I don't know. The provided context does not mention it.")
    assert not is_abstention("Tim Cook is the CEO of Apple. The memories do not record his birthday.")
    assert not is_abstention("")


def _run(*judged: tuple[str, JudgeResult]) -> tuple[dict, list[dict]]:
    """A faithfulness run as the gate sees it: the summary and the per-answer records."""
    records = [{"query": q, "metric": "faithfulness", **asdict(r)} for q, r in judged]
    return judge_mean([r for _, r in judged]), records


def test_floors_are_the_d28_ruling():
    assert FAITHFULNESS_THRESHOLD == 0.4
    assert FAITHFULNESS_ANSWER_THRESHOLD == 0.3


def test_one_ungrounded_answer_fails_the_per_answer_floor_and_is_named():
    # The 2026-10-06 shape: mean 0.52, one answer at 0.20 that used the model's
    # 2023 401(k) figures instead of the retrieved 2025 ones.
    summary, records = _run(
        ("What is the 401(k) contribution limit?", JudgeResult(0.2, "cites 2023 limits; context says 2025")),
        ("q2", JudgeResult(0.8, "grounded")),
        ("q3", JudgeResult(0.6, "mostly grounded")),
        ("q4", JudgeResult(0.5, "half")),
        ("q5", JudgeResult(0.5, "half")),
    )
    assert summary["mean"] == pytest.approx(0.52)
    with pytest.raises(AssertionError) as exc:
        assert_faithfulness_floors(summary, records)
    msg = str(exc.value)
    assert "below the per-answer floor 0.3" in msg
    assert "What is the 401(k) contribution limit?" in msg
    assert "0.20" in msg and "cites 2023 limits; context says 2025" in msg
    assert "avg faithfulness" not in msg
    assert "q2" not in msg


def test_low_mean_with_every_answer_above_the_answer_floor_fails_the_mean_floor():
    summary, records = _run(*((f"q{i}", JudgeResult(s, "ok")) for i, s in enumerate([0.3, 0.3, 0.4, 0.4, 0.35])))
    assert summary["mean"] == pytest.approx(0.35)
    with pytest.raises(AssertionError, match=r"avg faithfulness 0\.350 < 0\.4") as exc:
        assert_faithfulness_floors(summary, records)
    assert "per-answer floor" not in str(exc.value)


def test_mean_at_half_with_every_answer_above_the_answer_floor_passes():
    summary, records = _run(*((f"q{i}", JudgeResult(s, "ok")) for i, s in enumerate([0.3, 0.5, 0.6, 0.6, 0.5])))
    assert summary["mean"] == pytest.approx(0.5)
    assert_faithfulness_floors(summary, records)


def test_parse_failures_and_abstentions_do_not_trip_the_answer_floor():
    summary, records = _run(
        ("q1", JudgeResult(0.5, "half")),
        ("q2", JudgeResult(0.5, "half")),
        ("q3", JudgeResult(0.6, "mostly")),
        ("q4", JudgeResult(0.0, "Failed to parse: ...", parse_failed=True)),
        ("q5", JudgeResult(0.0, "abstained: the answer reports no grounding", abstained=True)),
    )
    assert summary["n_scored"] == 3
    assert_faithfulness_floors(summary, records)


def test_both_floors_report_together():
    summary, records = _run(("low", JudgeResult(0.1, "contradicts the context")), ("ok", JudgeResult(0.5, "half")))
    with pytest.raises(AssertionError) as exc:
        assert_faithfulness_floors(summary, records)
    msg = str(exc.value)
    assert "avg faithfulness 0.300 < 0.4" in msg
    assert "low" in msg and "contradicts the context" in msg
