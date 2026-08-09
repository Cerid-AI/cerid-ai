# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Pure-logic tests for ``verification_verdict_eval.py``'s grading/aggregation.

No live stack, no network — mirrors ``test_verification_cases_v2_schema.py``'s
"structural" tier by exercising ``summarize()`` and ``ClaimGrade`` directly
against synthetic ``CaseResult`` data. Covers the Quality-Maximization
Phase 3.5 confidence-recording extension: ``ClaimGrade.confidence`` and the
``calibration_records`` aggregate that a band-boundary calibration pass reads.
"""
from __future__ import annotations

import tests.eval.verification_verdict_eval as vve
from tests.eval.verification_verdict_eval import CaseResult, ClaimGrade, summarize


def test_load_dotenv_survives_shallow_container_path(monkeypatch):
    """Regression: the harness ran ``Path(__file__).resolve().parents[4]`` at
    import, which raised ``IndexError`` in the CI container's shallow
    ``/eval-src/tests/eval/`` layout and silently failed the nightly
    quality-evals for days. ``_load_dotenv`` must walk parents and never index
    out of range regardless of how shallow the path is."""
    monkeypatch.setattr(vve, "__file__", "/eval-src/tests/eval/verification_verdict_eval.py")
    vve._load_dotenv()  # must not raise IndexError on a 3-deep path


def _case_result(case_id: str, *grades: ClaimGrade) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        claim_type="factual",
        freshness="timeless",
        latency_ms=100.0,
        n_claims_returned=len(grades),
        grades=list(grades),
    )


def test_claim_grade_confidence_and_method_default_to_none() -> None:
    grade = ClaimGrade(
        case_id="V-01",
        fragment="Guido van Rossum",
        bucket_type="factual",
        freshness="timeless",
        expected="verified",
        matched=False,
    )
    assert grade.confidence is None
    assert grade.verification_method is None


def test_summarize_calibration_records_capture_matched_claim_confidence() -> None:
    matched_correct = ClaimGrade(
        case_id="V-01", fragment="a", bucket_type="factual", freshness="timeless",
        expected="verified", matched=True, actual="verified", correct=True,
        confidence=0.93, verification_method="kb_nli",
    )
    matched_incorrect = ClaimGrade(
        case_id="V-02", fragment="b", bucket_type="factual", freshness="timeless",
        expected="unverified", matched=True, actual="uncertain", correct=False,
        confidence=0.5, verification_method="cross_model",
    )
    case_results = [
        _case_result("V-01", matched_correct),
        _case_result("V-02", matched_incorrect),
    ]

    summary = summarize(case_results, n_cases_total=2)

    records = summary["calibration_records"]
    assert len(records) == 2
    by_case = {r["case_id"]: r for r in records}
    assert by_case["V-01"] == {
        "case_id": "V-01",
        "claim_type": "factual",
        "expected": "verified",
        "actual": "verified",
        "confidence": 0.93,
        "verification_method": "kb_nli",
        "correct": True,
    }
    assert by_case["V-02"]["confidence"] == 0.5
    assert by_case["V-02"]["verification_method"] == "cross_model"
    assert by_case["V-02"]["correct"] is False


def test_summarize_calibration_records_excludes_unmatched_and_scoreless_claims() -> None:
    unmatched = ClaimGrade(
        case_id="V-03", fragment="c", bucket_type="factual", freshness="timeless",
        expected="verified", matched=False,
    )
    matched_no_confidence = ClaimGrade(
        case_id="V-04", fragment="d", bucket_type="factual", freshness="timeless",
        expected="verified", matched=True, actual="verified", correct=True,
        confidence=None,
    )
    case_results = [_case_result("V-03", unmatched, matched_no_confidence)]

    summary = summarize(case_results, n_cases_total=1)

    assert summary["calibration_records"] == []


def test_summarize_failures_carry_confidence_for_debugging() -> None:
    wrong = ClaimGrade(
        case_id="V-05", fragment="e", bucket_type="recency", freshness="time_sensitive",
        expected="unverified", matched=True, actual="verified", correct=False,
        confidence=0.71,
    )
    case_results = [_case_result("V-05", wrong)]

    summary = summarize(case_results, n_cases_total=1)

    assert len(summary["failures"]) == 1
    assert summary["failures"][0]["confidence"] == 0.71
