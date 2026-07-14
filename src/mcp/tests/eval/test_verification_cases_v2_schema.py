# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Structural gate for ``datasets/verification_cases_v2.jsonl``.

Pure local JSON-parse checks — no live stack, no network. Runs in the
default fast tier so a malformed dataset edit fails immediately instead of
silently breaking ``verification_verdict_eval.py`` at eval time. Mirrors
the "structural" layer pattern used by ``test_keyword_harness.py`` /
``test_retrieval_baselines.py`` for their JSONL fixtures.
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.eval.verification_verdict_eval import _ALLOWED_CLAIM_TYPES, load_cases

_DATASET_PATH = Path(__file__).parent / "datasets" / "verification_cases_v2.jsonl"
_ALLOWED_FRESHNESS = {"timeless", "time_sensitive"}
_ALLOWED_EXPECTED_VERDICTS = {"verified", "unverified", "uncertain"}
_REQUIRED_CASE_FIELDS = (
    "id", "description", "claim_type", "freshness", "truth_asof",
    "response_text", "user_query", "expected_claims", "rationale",
)
_REQUIRED_EXPECTED_CLAIM_FIELDS = ("text_fragment", "type")


def test_dataset_file_exists() -> None:
    assert _DATASET_PATH.is_file(), f"missing {_DATASET_PATH}"


def test_every_line_is_valid_json() -> None:
    lines = [ln for ln in _DATASET_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines, "dataset is empty"
    for i, line in enumerate(lines, start=1):
        try:
            json.loads(line)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"line {i} is not valid JSON: {exc}") from exc


def test_case_count_at_least_one_hundred() -> None:
    cases = load_cases(_DATASET_PATH)
    assert len(cases) >= 100, f"expected >= 100 cases, got {len(cases)}"


def test_ids_are_unique() -> None:
    cases = load_cases(_DATASET_PATH)
    ids = [c["id"] for c in cases]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate case ids: {dupes}"


def test_required_fields_present() -> None:
    cases = load_cases(_DATASET_PATH)
    for c in cases:
        missing = [f for f in _REQUIRED_CASE_FIELDS if not c.get(f)]
        assert not missing, f"case {c.get('id')} missing required fields: {missing}"


def test_claim_type_and_freshness_enums() -> None:
    cases = load_cases(_DATASET_PATH)
    for c in cases:
        assert c["claim_type"] in _ALLOWED_CLAIM_TYPES, (
            f"case {c['id']} has invalid claim_type {c['claim_type']!r}"
        )
        assert c["freshness"] in _ALLOWED_FRESHNESS, (
            f"case {c['id']} has invalid freshness {c['freshness']!r}"
        )


def test_expected_claims_shape_and_verdict_enum() -> None:
    cases = load_cases(_DATASET_PATH)
    for c in cases:
        claims = c["expected_claims"]
        assert isinstance(claims, list) and claims, f"case {c['id']} has no expected_claims"
        for ec in claims:
            missing = [f for f in _REQUIRED_EXPECTED_CLAIM_FIELDS if f not in ec]
            assert not missing, f"case {c['id']} expected_claims entry missing {missing}"
            verdict = ec.get("expected_verdict")
            if verdict is not None:
                assert verdict in _ALLOWED_EXPECTED_VERDICTS, (
                    f"case {c['id']} has invalid expected_verdict {verdict!r}"
                )


def test_every_case_has_at_least_one_graded_claim() -> None:
    """Every case must carry at least one expected_claims entry with a
    populated expected_verdict — the concrete, testable form of "every case
    needs expected_verdict" for cases with multiple sub-claims."""
    cases = load_cases(_DATASET_PATH)
    ungraded = [c["id"] for c in cases if not any(ec.get("expected_verdict") for ec in c["expected_claims"])]
    assert not ungraded, f"cases with zero graded claims: {ungraded}"


def test_seed_content_implies_seed_domain_or_default_is_fine() -> None:
    """seed_content, when present, must be a non-empty string (seed_domain is
    optional — the harness defaults it to 'general')."""
    cases = load_cases(_DATASET_PATH)
    for c in cases:
        if "seed_content" in c:
            assert isinstance(c["seed_content"], str) and c["seed_content"].strip(), (
                f"case {c['id']} has an empty seed_content"
            )
